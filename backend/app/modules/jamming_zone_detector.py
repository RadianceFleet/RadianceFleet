"""GPS Jamming Zone Dynamic Detection.

Detects zones of concentrated AIS gaps that suggest GPS jamming activity.
Uses spatial-temporal DBSCAN on gap start positions from AISGapEvent records.

Algorithm:
1. Query recent gap events with valid off-positions (gap_off_lat/lon).
2. Spatial-temporal DBSCAN: spatial_eps=0.5deg (~30nm), temporal_eps=2h,
   min_vessels=3 distinct vessels per cluster.
3. Convex hull via Shapely + 0.1deg buffer -> WKT polygon.
4. Merge with existing active zones if IoU > 50%, else create new.
5. Decay logic: no new gaps for 7d -> "decaying" (confidence *= 0.9/day),
   30d -> "expired".
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

SPATIAL_EPS_DEG = 0.5  # ~30nm at mid-latitudes
TEMPORAL_EPS_HOURS = 2.0
MIN_VESSELS = 3
CONVEX_HULL_BUFFER_DEG = 0.1
IOU_MERGE_THRESHOLD = 0.5
DECAY_START_DAYS = 7
EXPIRE_DAYS = 30
DECAY_FACTOR_PER_DAY = 0.9


# ── Gap point representation ─────────────────────────────────────────────────


class GapPoint:
    """A gap event with spatial and temporal coordinates."""

    __slots__ = ("gap_event_id", "vessel_id", "lat", "lon", "timestamp")

    def __init__(
        self, gap_event_id: int, vessel_id: int, lat: float, lon: float, timestamp: datetime
    ):
        self.gap_event_id = gap_event_id
        self.vessel_id = vessel_id
        self.lat = lat
        self.lon = lon
        self.timestamp = timestamp


# ── Spatial-temporal DBSCAN ──────────────────────────────────────────────────


def _st_distance(a: GapPoint, b: GapPoint) -> tuple[float, float]:
    """Return (spatial_deg, temporal_hours) distance between two gap points."""
    spatial_deg = math.sqrt((a.lat - b.lat) ** 2 + (a.lon - b.lon) ** 2)
    temporal_hours = abs((a.timestamp - b.timestamp).total_seconds()) / 3600.0
    return spatial_deg, temporal_hours


def _st_neighbors(
    points: list[GapPoint],
    idx: int,
    spatial_eps: float,
    temporal_eps: float,
) -> list[int]:
    """Find indices of all spatial-temporal neighbors of points[idx]."""
    neighbors = []
    for j in range(len(points)):
        s_dist, t_dist = _st_distance(points[idx], points[j])
        if s_dist <= spatial_eps and t_dist <= temporal_eps:
            neighbors.append(j)
    return neighbors


def st_dbscan(
    points: list[GapPoint],
    spatial_eps: float = SPATIAL_EPS_DEG,
    temporal_eps: float = TEMPORAL_EPS_HOURS,
    min_points: int = 3,
) -> list[int]:
    """Spatial-temporal DBSCAN clustering.

    Returns list of cluster labels. -1 = noise, 0+ = cluster ID.
    """
    n = len(points)
    labels = [-2] * n  # -2 = unvisited
    cluster_id = 0

    for i in range(n):
        if labels[i] != -2:
            continue

        neighbors = _st_neighbors(points, i, spatial_eps, temporal_eps)

        if len(neighbors) < min_points:
            labels[i] = -1  # Noise
            continue

        labels[i] = cluster_id
        seed_set = list(neighbors)
        seed_set.remove(i)

        k = 0
        while k < len(seed_set):
            q = seed_set[k]
            if labels[q] == -1:
                labels[q] = cluster_id
            elif labels[q] == -2:
                labels[q] = cluster_id
                q_neighbors = _st_neighbors(points, q, spatial_eps, temporal_eps)
                if len(q_neighbors) >= min_points:
                    for nb in q_neighbors:
                        if nb not in seed_set:
                            seed_set.append(nb)
            k += 1

        cluster_id += 1

    return labels


# ── Geometry helpers ─────────────────────────────────────────────────────────


def _compute_convex_hull_wkt(
    lats: list[float], lons: list[float], buffer_deg: float = CONVEX_HULL_BUFFER_DEG
) -> str | None:
    """Compute convex hull of points with buffer, return WKT polygon."""
    try:
        from shapely.geometry import MultiPoint

        if len(lats) < 3:
            # Fewer than 3 points: create a buffered point at centroid
            clat = sum(lats) / len(lats)
            clon = sum(lons) / len(lons)
            from shapely.geometry import Point

            geom = Point(clon, clat).buffer(buffer_deg)
        else:
            points = [(lon, lat) for lat, lon in zip(lats, lons, strict=False)]
            hull = MultiPoint(points).convex_hull
            geom = hull.buffer(buffer_deg)

        return geom.wkt
    except Exception:
        logger.warning("Failed to compute convex hull, falling back to None")
        return None


def _compute_radius_nm(lats: list[float], lons: list[float]) -> float:
    """Max distance from centroid to any point, in NM."""
    if len(lats) <= 1:
        return 0.0
    clat = sum(lats) / len(lats)
    clon = sum(lons) / len(lons)
    return max(haversine_nm(clat, clon, lat, lon) for lat, lon in zip(lats, lons, strict=False))


def _compute_iou(wkt_a: str | None, wkt_b: str | None) -> float:
    """Compute intersection-over-union of two WKT geometries."""
    if not wkt_a or not wkt_b:
        return 0.0
    try:
        from shapely import wkt

        geom_a = wkt.loads(wkt_a)
        geom_b = wkt.loads(wkt_b)
        if geom_a.is_empty or geom_b.is_empty:
            return 0.0
        intersection = geom_a.intersection(geom_b).area
        union = geom_a.union(geom_b).area
        if union <= 0:
            return 0.0
        return intersection / union
    except Exception:
        logger.warning("Failed to compute IoU")
        return 0.0


# ── Decay logic ──────────────────────────────────────────────────────────────


def apply_zone_decay(db: Session, now: datetime | None = None) -> dict[str, int]:
    """Apply decay logic to all active/decaying zones.

    - No new gaps for 7+ days: status -> "decaying", confidence *= 0.9/day
    - No new gaps for 30+ days: status -> "expired"

    Returns counts of zones transitioned.
    """
    from app.models.jamming_zone import JammingZone

    if now is None:
        now = datetime.now(UTC)

    zones = (
        db.query(JammingZone)
        .filter(JammingZone.status.in_(["active", "decaying"]))
        .all()
    )

    decayed = 0
    expired = 0

    for zone in zones:
        last_gap = zone.last_gap_at
        if last_gap is None:
            continue

        # Ensure timezone-aware comparison
        if last_gap.tzinfo is None:

            last_gap = last_gap.replace(tzinfo=UTC)

        days_since = (now - last_gap).total_seconds() / 86400.0

        if days_since >= EXPIRE_DAYS:
            zone.status = "expired"
            zone.confidence = 0.0
            expired += 1
        elif days_since >= DECAY_START_DAYS:
            if zone.status != "decaying":
                zone.status = "decaying"
                decayed += 1
            # Apply decay factor for each day beyond the threshold
            decay_days = days_since - DECAY_START_DAYS
            zone.confidence = max(0.01, zone.confidence * (DECAY_FACTOR_PER_DAY ** decay_days))

    if decayed or expired:
        db.commit()
        logger.info("Zone decay: %d decaying, %d expired", decayed, expired)

    return {"decayed": decayed, "expired": expired}


# ── Main detection entry point ───────────────────────────────────────────────


def run_jamming_detection(
    db: Session,
    window_hours: int = 168,
) -> dict[str, Any]:
    """Run GPS jamming zone detection.

    1. Query gap events with valid off-positions within the time window.
    2. Spatial-temporal DBSCAN clustering.
    3. For each cluster: compute convex hull, check vessel diversity.
    4. Merge with existing zones (IoU > 50%) or create new.
    5. Apply decay to stale zones.

    Returns summary statistics.
    """
    if not getattr(settings, "JAMMING_DETECTION_ENABLED", False):
        return {
            "zones_created": 0,
            "zones_merged": 0,
            "gaps_processed": 0,
            "disabled": True,
        }

    from app.models.gap_event import AISGapEvent
    from app.models.jamming_zone import JammingZone, JammingZoneGap

    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=window_hours)

    # Step 1: Query recent gap events with valid off-positions
    gap_rows = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.gap_start_utc >= cutoff,
            AISGapEvent.gap_off_lat.isnot(None),
            AISGapEvent.gap_off_lon.isnot(None),
            AISGapEvent.is_feed_outage == False,  # noqa: E712
        )
        .all()
    )

    if not gap_rows:
        logger.info("No gap events with off-positions in the last %d hours", window_hours)
        decay_result = apply_zone_decay(db, now)
        return {
            "zones_created": 0,
            "zones_merged": 0,
            "gaps_processed": 0,
            **decay_result,
        }

    # Build GapPoints
    points = [
        GapPoint(
            gap_event_id=g.gap_event_id,
            vessel_id=g.vessel_id,
            lat=g.gap_off_lat,
            lon=g.gap_off_lon,
            timestamp=g.gap_start_utc,
        )
        for g in gap_rows
    ]

    logger.info("Running ST-DBSCAN on %d gap points", len(points))

    # Step 2: ST-DBSCAN
    labels = st_dbscan(
        points,
        spatial_eps=SPATIAL_EPS_DEG,
        temporal_eps=TEMPORAL_EPS_HOURS,
        min_points=MIN_VESSELS,
    )

    # Group points by cluster label
    clusters: dict[int, list[int]] = {}
    for idx, label in enumerate(labels):
        if label >= 0:
            clusters.setdefault(label, []).append(idx)

    # Load existing active/decaying zones for merge checking
    existing_zones = (
        db.query(JammingZone)
        .filter(JammingZone.status.in_(["active", "decaying"]))
        .all()
    )

    zones_created = 0
    zones_merged = 0
    total_gaps_linked = 0

    # Step 3: Process each cluster
    for cluster_label, member_indices in clusters.items():
        cluster_points = [points[i] for i in member_indices]

        # Check vessel diversity
        distinct_vessels = {p.vessel_id for p in cluster_points}
        if len(distinct_vessels) < MIN_VESSELS:
            continue

        lats = [p.lat for p in cluster_points]
        lons = [p.lon for p in cluster_points]
        centroid_lat = sum(lats) / len(lats)
        centroid_lon = sum(lons) / len(lons)
        radius = _compute_radius_nm(lats, lons)
        hull_wkt = _compute_convex_hull_wkt(lats, lons)
        timestamps = [p.timestamp for p in cluster_points]
        first_detected = min(timestamps)
        last_gap = max(timestamps)

        evidence = {
            "cluster_label": cluster_label,
            "vessel_ids": sorted(distinct_vessels),
            "gap_count": len(cluster_points),
            "vessel_count": len(distinct_vessels),
            "radius_nm": round(radius, 2),
        }

        # Step 4: Check for merge with existing zones
        merged = False
        for ez in existing_zones:
            iou = _compute_iou(hull_wkt, ez.geometry)
            if iou >= IOU_MERGE_THRESHOLD:
                # Merge into existing zone
                ez.geometry = hull_wkt
                ez.centroid_lat = centroid_lat
                ez.centroid_lon = centroid_lon
                ez.radius_nm = radius
                ez.confidence = min(1.0, ez.confidence + 0.1)
                ez.vessel_count = len(distinct_vessels)
                ez.gap_count = (ez.gap_count or 0) + len(cluster_points)
                if first_detected < (ez.first_detected_at or first_detected):
                    ez.first_detected_at = first_detected
                ez.last_gap_at = last_gap
                ez.status = "active"  # Re-activate if was decaying
                ez.detection_window_hours = window_hours

                # Merge evidence
                old_evidence = {}
                if ez.evidence_json:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        old_evidence = json.loads(ez.evidence_json)
                old_evidence.update(evidence)
                old_evidence["merged"] = True
                old_evidence["iou"] = round(iou, 3)
                ez.evidence_json = json.dumps(old_evidence)

                # Link gaps
                for cp in cluster_points:
                    existing_link = (
                        db.query(JammingZoneGap)
                        .filter_by(zone_id=ez.zone_id, gap_event_id=cp.gap_event_id)
                        .first()
                    )
                    if not existing_link:
                        db.add(
                            JammingZoneGap(zone_id=ez.zone_id, gap_event_id=cp.gap_event_id)
                        )
                        total_gaps_linked += 1

                zones_merged += 1
                merged = True
                logger.info(
                    "Merged cluster %d into existing zone %d (IoU=%.2f)",
                    cluster_label,
                    ez.zone_id,
                    iou,
                )
                break

        if not merged:
            # Create new zone
            new_zone = JammingZone(
                geometry=hull_wkt,
                centroid_lat=centroid_lat,
                centroid_lon=centroid_lon,
                radius_nm=radius,
                confidence=1.0,
                vessel_count=len(distinct_vessels),
                gap_count=len(cluster_points),
                first_detected_at=first_detected,
                last_gap_at=last_gap,
                status="active",
                detection_window_hours=window_hours,
                evidence_json=json.dumps(evidence),
            )
            db.add(new_zone)
            db.flush()  # Get zone_id

            for cp in cluster_points:
                db.add(
                    JammingZoneGap(zone_id=new_zone.zone_id, gap_event_id=cp.gap_event_id)
                )
                total_gaps_linked += 1

            existing_zones.append(new_zone)  # Available for future merges
            zones_created += 1
            logger.info(
                "Created new jamming zone %d (vessels=%d, gaps=%d)",
                new_zone.zone_id,
                len(distinct_vessels),
                len(cluster_points),
            )

    # Commit new/merged zones before decay (apply_zone_decay commits internally)
    db.commit()

    # Step 5: Apply decay
    decay_result = apply_zone_decay(db, now)

    result = {
        "zones_created": zones_created,
        "zones_merged": zones_merged,
        "gaps_processed": len(points),
        "gaps_linked": total_gaps_linked,
        "clusters_found": len(clusters),
        **decay_result,
    }
    logger.info("Jamming zone detection complete: %s", result)
    return result


# ── Query helpers ────────────────────────────────────────────────────────────


def get_jamming_zones(
    db: Session,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List all jamming zones, optionally filtered by status."""
    from app.models.jamming_zone import JammingZone

    query = db.query(JammingZone).order_by(JammingZone.last_gap_at.desc())
    if status:
        query = query.filter(JammingZone.status == status)

    zones = query.all()
    return [_zone_to_dict(z) for z in zones]


def get_jamming_zone(db: Session, zone_id: int) -> dict[str, Any] | None:
    """Get a single jamming zone by ID."""
    from app.models.jamming_zone import JammingZone

    zone = db.query(JammingZone).filter(JammingZone.zone_id == zone_id).first()
    if not zone:
        return None
    return _zone_to_dict(zone)


def get_jamming_zones_geojson(
    db: Session,
    status: str | None = None,
) -> dict[str, Any]:
    """Return jamming zones as a GeoJSON FeatureCollection."""
    from app.models.jamming_zone import JammingZone

    query = db.query(JammingZone).order_by(JammingZone.last_gap_at.desc())
    if status:
        query = query.filter(JammingZone.status == status)

    zones = query.all()
    features = []

    for z in zones:
        geometry = None
        if z.geometry:
            try:
                from shapely import wkt as shapely_wkt
                from shapely.geometry import mapping

                geom = shapely_wkt.loads(z.geometry)
                geometry = mapping(geom)
            except Exception:
                logger.warning("Failed to parse WKT for zone %d", z.zone_id)

        evidence = {}
        if z.evidence_json:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                evidence = json.loads(z.evidence_json)

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "zone_id": z.zone_id,
                    "status": z.status,
                    "confidence": z.confidence,
                    "vessel_count": z.vessel_count,
                    "gap_count": z.gap_count,
                    "radius_nm": z.radius_nm,
                    "first_detected_at": z.first_detected_at.isoformat()
                    if z.first_detected_at
                    else None,
                    "last_gap_at": z.last_gap_at.isoformat() if z.last_gap_at else None,
                    **evidence,
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


def _zone_to_dict(z: Any) -> dict[str, Any]:
    """Convert a JammingZone ORM object to a dictionary."""
    evidence = {}
    if z.evidence_json:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            evidence = json.loads(z.evidence_json)

    return {
        "zone_id": z.zone_id,
        "geometry": z.geometry,
        "centroid_lat": z.centroid_lat,
        "centroid_lon": z.centroid_lon,
        "radius_nm": z.radius_nm,
        "confidence": z.confidence,
        "vessel_count": z.vessel_count,
        "gap_count": z.gap_count,
        "first_detected_at": z.first_detected_at.isoformat() if z.first_detected_at else None,
        "last_gap_at": z.last_gap_at.isoformat() if z.last_gap_at else None,
        "status": z.status,
        "detection_window_hours": z.detection_window_hours,
        "evidence": evidence,
        "created_at": z.created_at.isoformat() if z.created_at else None,
        "updated_at": z.updated_at.isoformat() if z.updated_at else None,
    }
