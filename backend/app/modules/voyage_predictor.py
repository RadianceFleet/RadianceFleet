"""Voyage prediction — route template building and destination prediction.

Provides:
  - build_route_templates()       — mine common port-call sequences into RouteTemplate records
  - predict_next_destination()    — predict next port based on matching route template
  - jaccard_similarity()          — set-level similarity for port sequences
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity: |A ∩ B| / |A ∪ B|.

    Returns 0.0 if both sets are empty.
    """
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _extract_subsequences(port_sequence: list[int], min_length: int = 3) -> list[tuple[int, ...]]:
    """Extract all contiguous subsequences of length >= min_length from a port sequence."""
    subsequences = []
    for length in range(min_length, len(port_sequence) + 1):
        for start in range(len(port_sequence) - length + 1):
            subseq = tuple(port_sequence[start : start + length])
            subsequences.append(subseq)
    return subsequences


def _find_existing_template(db: Session, vessel_type: str | None, route_ports: list[int]):
    """Find an existing RouteTemplate with matching vessel_type and route_ports_json.

    E4b: Prevents duplicate templates on repeated pipeline runs.
    Returns the existing template or None.
    """
    from app.models.route_template import RouteTemplate

    # Query all templates with matching vessel_type
    candidates = db.query(RouteTemplate).filter(RouteTemplate.vessel_type == vessel_type).all()

    for t in candidates:
        existing_ports = t.route_ports_json
        if existing_ports is not None and list(existing_ports) == list(route_ports):
            return t
    return None


def build_route_templates(db: Session) -> dict:
    """Build route templates from historical port call data.

    1. Query all PortCall records, grouped by vessel_id, ordered by arrival_utc
    2. Build port call sequences (list of port_id) per vessel
    3. Group common subsequences (>= 3 ports) using Jaccard similarity >= 0.7
    4. Store as RouteTemplate records
    5. Return stats

    Returns dict with templates_created, vessels_analyzed, sequences_found.
    """
    from app.models.port_call import PortCall
    from app.models.route_template import RouteTemplate
    from app.models.vessel import Vessel

    stats: dict[str, Any] = {
        "templates_created": 0,
        "vessels_analyzed": 0,
        "sequences_found": 0,
    }

    # Step 1: Query all port calls grouped by vessel
    port_calls = (
        db.query(PortCall)
        .filter(PortCall.port_id.isnot(None))
        .order_by(PortCall.vessel_id, PortCall.arrival_utc)
        .all()
    )

    # Step 2: Build per-vessel port sequences
    vessel_sequences: dict[int, list[int]] = defaultdict(list)
    vessel_types: dict[int, str | None] = {}
    for pc in port_calls:
        vessel_sequences[pc.vessel_id].append(pc.port_id)

    stats["vessels_analyzed"] = len(vessel_sequences)

    # Look up vessel types
    vessel_ids = list(vessel_sequences.keys())
    if vessel_ids:
        vessels = db.query(Vessel).filter(Vessel.vessel_id.in_(vessel_ids)).all()
        for v in vessels:
            vessel_types[v.vessel_id] = v.vessel_type

    # Step 3: Extract and group common subsequences
    # Collect all subsequences with vessel type and timing
    all_subseqs: list[tuple[tuple[int, ...], str | None]] = []
    for vid, seq in vessel_sequences.items():
        if len(seq) < 3:
            continue
        subseqs = _extract_subsequences(seq, min_length=3)
        vtype = vessel_types.get(vid)
        for ss in subseqs:
            all_subseqs.append((ss, vtype))

    stats["sequences_found"] = len(all_subseqs)

    if not all_subseqs:
        return stats

    # Group by Jaccard similarity >= 0.7
    clusters: list[dict[str, Any]] = []
    used = set()

    for i, (seq_i, vtype_i) in enumerate(all_subseqs):
        if i in used:
            continue
        cluster_seqs = [seq_i]
        cluster_vtypes = [vtype_i]
        used.add(i)

        set_i = set(seq_i)
        for j, (seq_j, vtype_j) in enumerate(all_subseqs):
            if j in used:
                continue
            set_j = set(seq_j)
            if jaccard_similarity(set_i, set_j) >= 0.7:
                cluster_seqs.append(seq_j)
                cluster_vtypes.append(vtype_j)
                used.add(j)

        if len(cluster_seqs) >= 2:
            # Use most common sequence as representative
            representative = max(set(cluster_seqs), key=cluster_seqs.count)
            # Most common vessel type
            type_counts: dict[str | None, int] = defaultdict(int)
            for vt in cluster_vtypes:
                type_counts[vt] += 1
            common_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]

            clusters.append(
                {
                    "route_ports": list(representative),
                    "frequency": len(cluster_seqs),
                    "vessel_type": common_type,
                }
            )

    # Step 4: Store as RouteTemplate records (with dedup — E4b)
    templates_updated = 0
    for cluster in clusters:
        route_ports = cluster["route_ports"]
        vessel_type = cluster["vessel_type"]

        # Dedup: check for existing template with matching vessel_type AND route_ports_json
        existing = _find_existing_template(db, vessel_type, route_ports)
        if existing is not None:
            # Increment frequency and update avg_duration_days (running average)
            old_freq = existing.frequency or 0
            new_freq = old_freq + cluster["frequency"]
            existing.frequency = new_freq
            # avg_duration_days stays as-is since we don't have timing yet for new data
            templates_updated += 1
        else:
            template = RouteTemplate(
                vessel_type=vessel_type,
                route_ports_json=route_ports,
                frequency=cluster["frequency"],
                avg_duration_days=0.0,  # Computed later with departure/arrival timing
            )
            db.add(template)
            stats["templates_created"] += 1

    stats["templates_updated"] = templates_updated
    db.commit()
    logger.info(
        "Built %d route templates from %d vessels",
        stats["templates_created"],
        stats["vessels_analyzed"],
    )
    return stats


def predict_next_destination(db: Session, vessel_id: int) -> dict | None:
    """Predict the next destination for a vessel based on route templates.

    1. Get vessel's recent port calls
    2. Find matching RouteTemplate (Jaccard >= 0.7 on port set)
    3. Predict next port based on template sequence
    4. Check if vessel is deviating >100nm toward an STS zone -> +25

    Returns dict with predicted_port_id, confidence, deviation_score or None.
    """
    from app.models.ais_point import AISPoint
    from app.models.corridor import Corridor
    from app.models.port_call import PortCall
    from app.models.route_template import RouteTemplate

    # Get recent port calls for vessel
    recent_calls = (
        db.query(PortCall)
        .filter(
            PortCall.vessel_id == vessel_id,
            PortCall.port_id.isnot(None),
        )
        .order_by(PortCall.arrival_utc.desc())
        .limit(10)
        .all()
    )

    if not recent_calls or len(recent_calls) < 2:
        return None

    # Build port sequence (most recent first -> reverse for chronological)
    recent_ports = [pc.port_id for pc in reversed(recent_calls)]
    recent_set = set(recent_ports)

    # Find best matching template
    templates = db.query(RouteTemplate).all()
    if not templates:
        return None

    best_template = None
    best_sim = 0.0

    for t in templates:
        template_ports = t.route_ports_json or []
        if not template_ports:
            continue
        template_set = set(template_ports)
        sim = jaccard_similarity(recent_set, template_set)
        if sim >= 0.7 and sim > best_sim:
            best_sim = sim
            best_template = t

    if best_template is None:
        return None

    # Predict next port: find where vessel is in template sequence
    template_ports = best_template.route_ports_json or []
    last_port = recent_ports[-1]
    predicted_port_id = None

    for i, port_id in enumerate(template_ports):
        if port_id == last_port and i + 1 < len(template_ports):
            predicted_port_id = template_ports[i + 1]
            break

    if predicted_port_id is None:
        # If last port not found in template, use first port as cycle start
        predicted_port_id = template_ports[0]

    result: dict[str, Any] = {
        "predicted_port_id": predicted_port_id,
        "template_id": best_template.template_id,
        "confidence": round(best_sim, 2),
        "deviation_score": 0,
    }

    # Check route deviation toward STS zone
    # Get latest vessel position
    latest_point = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id == vessel_id)
        .order_by(AISPoint.timestamp_utc.desc())
        .first()
    )

    if latest_point is not None:
        try:
            from app.utils.geo import haversine_nm

            # Get STS zone corridors
            sts_corridors = db.query(Corridor).filter(Corridor.corridor_type == "sts_zone").all()

            for corr in sts_corridors:
                if corr.geometry is None:
                    continue
                try:
                    from app.utils.geo import load_geometry

                    geom = load_geometry(corr.geometry)
                    if geom is None:
                        continue
                    centroid = geom.centroid
                    dist = haversine_nm(
                        latest_point.lat,
                        latest_point.lon,
                        centroid.y,
                        centroid.x,
                    )
                    if dist <= 100:
                        result["deviation_score"] = 25
                        result["deviation_toward_sts_zone"] = corr.name
                        break
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("Route deviation check failed: %s", exc)

    return result


def _port_coords(port) -> dict[str, float] | None:
    """Extract lat/lon from a Port's WKT geometry (POINT)."""
    if port is None or not port.geometry:
        return None
    try:
        from app.utils.geo import load_geometry

        geom = load_geometry(port.geometry)
        if geom is None:
            return None
        centroid = geom.centroid
        return {"lat": round(centroid.y, 4), "lon": round(centroid.x, 4)}
    except Exception:
        return None


def predict_next_destination_enriched(db: Session, vessel_id: int) -> dict | None:
    """Wrap predict_next_destination with enriched port/route details for the frontend."""
    from app.models.port import Port
    from app.models.port_call import PortCall
    from app.models.route_template import RouteTemplate

    base = predict_next_destination(db, vessel_id)
    if base is None:
        return None

    template_id = base.get("template_id")
    predicted_port_id = base.get("predicted_port_id")

    # Enrich predicted destination with port details
    predicted_destination = None
    if predicted_port_id is not None:
        port = db.query(Port).filter(Port.port_id == predicted_port_id).first()
        if port is not None:
            coords = _port_coords(port) or {}
            predicted_destination = {
                "port_id": port.port_id,
                "name": port.name,
                "lat": coords.get("lat"),
                "lon": coords.get("lon"),
                "country": port.country,
            }

    # Enrich template name and predicted route
    template_name = None
    predicted_route: list[dict] = []
    if template_id is not None:
        template = db.query(RouteTemplate).filter(RouteTemplate.template_id == template_id).first()
        if template is not None:
            # Derive template_name from vessel_type or port names
            route_port_ids = template.route_ports_json or []
            if route_port_ids:
                ports = db.query(Port).filter(Port.port_id.in_(route_port_ids)).all()
                port_map = {p.port_id: p for p in ports}
                # Build predicted_route as lat/lon array in template order
                for pid in route_port_ids:
                    p = port_map.get(pid)
                    if p is not None:
                        coords = _port_coords(p)
                        if coords:
                            predicted_route.append(coords)
                # Derive name from first and last port
                first_port = port_map.get(route_port_ids[0])
                last_port = port_map.get(route_port_ids[-1])
                if first_port and last_port:
                    template_name = f"{first_port.name} → {last_port.name}"
                elif template.vessel_type:
                    template_name = f"{template.vessel_type} Route"

    # Build actual_route from recent port calls
    actual_route: list[dict] = []
    recent_calls = (
        db.query(PortCall)
        .filter(PortCall.vessel_id == vessel_id, PortCall.port_id.isnot(None))
        .order_by(PortCall.arrival_utc.desc())
        .limit(10)
        .all()
    )
    if recent_calls:
        port_ids = [pc.port_id for pc in recent_calls]
        ports = db.query(Port).filter(Port.port_id.in_(port_ids)).all()
        port_map = {p.port_id: p for p in ports}
        for pc in reversed(recent_calls):  # chronological order
            p = port_map.get(pc.port_id)
            coords = _port_coords(p) if p else None
            entry: dict[str, Any] = {
                "lat": coords["lat"] if coords else None,
                "lon": coords["lon"] if coords else None,
                "port_name": p.name if p else None,
                "arrival_utc": pc.arrival_utc.isoformat() if pc.arrival_utc else None,
            }
            actual_route.append(entry)

    return {
        "vessel_id": vessel_id,
        "prediction": {
            "template_id": template_id,
            "template_name": template_name,
            "confidence": base.get("confidence"),
            "deviation_score": base.get("deviation_score", 0),
            "deviation_toward_sts_zone": base.get("deviation_toward_sts_zone"),
            "predicted_destination": predicted_destination,
            "predicted_route": predicted_route,
            "actual_route": actual_route,
        },
    }
