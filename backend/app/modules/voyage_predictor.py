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

from app.config import settings

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
            subseq = tuple(port_sequence[start:start + length])
            subsequences.append(subseq)
    return subsequences


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
    from app.models.vessel import Vessel
    from app.models.route_template import RouteTemplate

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

            clusters.append({
                "route_ports": list(representative),
                "frequency": len(cluster_seqs),
                "vessel_type": common_type,
            })

    # Step 4: Store as RouteTemplate records
    for cluster in clusters:
        template = RouteTemplate(
            vessel_type=cluster["vessel_type"],
            route_ports_json=cluster["route_ports"],
            frequency=cluster["frequency"],
            avg_duration_days=0.0,  # Computed later with departure/arrival timing
        )
        db.add(template)
        stats["templates_created"] += 1

    db.commit()
    logger.info("Built %d route templates from %d vessels", stats["templates_created"], stats["vessels_analyzed"])
    return stats


def predict_next_destination(db: Session, vessel_id: int) -> dict | None:
    """Predict the next destination for a vessel based on route templates.

    1. Get vessel's recent port calls
    2. Find matching RouteTemplate (Jaccard >= 0.7 on port set)
    3. Predict next port based on template sequence
    4. Check if vessel is deviating >100nm toward an STS zone -> +25

    Returns dict with predicted_port_id, confidence, deviation_score or None.
    """
    from app.models.port_call import PortCall
    from app.models.route_template import RouteTemplate
    from app.models.corridor import Corridor
    from app.models.ais_point import AISPoint

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
            sts_corridors = (
                db.query(Corridor)
                .filter(Corridor.corridor_type == "sts_zone")
                .all()
            )

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
                        latest_point.lat, latest_point.lon,
                        centroid.y, centroid.x,
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
