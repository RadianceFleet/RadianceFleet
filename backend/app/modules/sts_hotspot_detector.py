"""STS Transfer Hotspot Detection.

Clusters STS transfer events geographically using DBSCAN to identify
ship-to-ship transfer hotspots. Computes temporal trends (growing/stable/declining)
via linear regression on 30-day sliding windows, and detects corridor overlap
using Shapely geometry checks.

Algorithm:
1. Load STS transfer events with lat/lon positions
2. Build O(n^2) haversine distance matrix
3. Run DBSCAN (eps=10nm, min_samples=3)
4. For each cluster: compute centroid, radius, temporal trend
5. Check corridor overlap via Shapely
6. Persist StsHotspot records
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

EARTH_RADIUS_NM = 3440.065  # Mean Earth radius in nautical miles

DEFAULT_EPS_NM = 10.0
DEFAULT_MIN_SAMPLES = 3
TREND_WINDOW_DAYS = 30

# Trend classification thresholds for linear regression slope
TREND_GROWING_THRESHOLD = 0.5  # events per window increase
TREND_DECLINING_THRESHOLD = -0.5

# Risk scoring
SCORE_BASE = 10.0
SCORE_PER_EVENT = 2.0
SCORE_GROWING_BONUS = 15.0
SCORE_CORRIDOR_BONUS = 10.0
SCORE_MAX = 100.0


# ── Haversine (local copy — no cross-detector coupling) ─────────────────────


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles between two WGS-84 points.

    Uses the haversine formula which correctly handles high-latitude
    distortion (at 60N, 1 deg longitude ~ 30nm vs ~ 60nm for latitude).
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_NM * c


# ── Distance matrix ─────────────────────────────────────────────────────────


def _build_distance_matrix(points: list[tuple[float, float]]) -> list[list[float]]:
    """Precompute full NxN symmetric haversine distance matrix."""
    n = len(points)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = _haversine_nm(points[i][0], points[i][1], points[j][0], points[j][1])
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix


# ── DBSCAN ───────────────────────────────────────────────────────────────────


def _dbscan(
    distance_matrix: list[list[float]],
    eps: float,
    min_samples: int,
) -> list[int]:
    """Pure-Python DBSCAN clustering.

    Args:
        distance_matrix: Precomputed NxN symmetric distance matrix.
        eps: Maximum distance (in NM) for two points to be neighbors.
        min_samples: Minimum number of points to form a dense region.

    Returns:
        List of cluster labels. -1 = noise, 0+ = cluster ID.
    """
    n = len(distance_matrix)
    labels = [-2] * n  # -2 = unvisited
    cluster_id = 0

    def _region_query(point_idx: int) -> list[int]:
        return [j for j in range(n) if distance_matrix[point_idx][j] <= eps]

    for i in range(n):
        if labels[i] != -2:
            continue

        neighbors = _region_query(i)

        if len(neighbors) < min_samples:
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
                q_neighbors = _region_query(q)
                if len(q_neighbors) >= min_samples:
                    for nb in q_neighbors:
                        if nb not in seed_set:
                            seed_set.append(nb)
            k += 1

        cluster_id += 1

    return labels


# ── Temporal trend computation ───────────────────────────────────────────────


def _compute_trend(
    timestamps: list[datetime],
    window_days: int = TREND_WINDOW_DAYS,
) -> tuple[str, float]:
    """Compute temporal trend from event timestamps using sliding-window linear regression.

    Divides the time range into windows and counts events per window,
    then fits a simple linear regression to the window counts.

    Returns:
        (trend_label, slope) where trend_label is "growing", "stable", or "declining"
    """
    if len(timestamps) < 2:
        return "stable", 0.0

    sorted_ts = sorted(timestamps)
    start = sorted_ts[0]
    end = sorted_ts[-1]

    total_days = (end - start).total_seconds() / 86400.0
    if total_days < window_days:
        # Not enough time range for multiple windows
        return "stable", 0.0

    # Build window counts
    window_counts: list[float] = []
    window_start = start
    while window_start < end:
        window_end = window_start + timedelta(days=window_days)
        count = sum(1 for ts in sorted_ts if window_start <= ts < window_end)
        window_counts.append(float(count))
        window_start = window_end

    if len(window_counts) < 2:
        return "stable", 0.0

    # Simple linear regression: y = mx + b
    n = len(window_counts)
    x_vals = list(range(n))
    x_mean = sum(x_vals) / n
    y_mean = sum(window_counts) / n

    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, window_counts))
    denominator = sum((x - x_mean) ** 2 for x in x_vals)

    if denominator == 0:
        return "stable", 0.0

    slope = numerator / denominator

    if slope >= TREND_GROWING_THRESHOLD:
        trend = "growing"
    elif slope <= TREND_DECLINING_THRESHOLD:
        trend = "declining"
    else:
        trend = "stable"

    return trend, round(slope, 4)


# ── Corridor overlap via Shapely ─────────────────────────────────────────────


def _find_corridor_overlap(
    db: Session,
    lat: float,
    lon: float,
) -> int | None:
    """Check if a point falls within any corridor geometry using Shapely.

    Returns the corridor_id if the centroid is inside a corridor, else None.
    """
    from app.models.corridor import Corridor

    try:
        from shapely import wkt
        from shapely.geometry import Point
    except ImportError:
        logger.warning("Shapely not available — skipping corridor overlap detection")
        return None

    point = Point(lon, lat)  # Shapely uses (x=lon, y=lat)

    corridors = db.query(Corridor).all()
    for corridor in corridors:
        if not corridor.geometry:
            continue
        try:
            geom = wkt.loads(corridor.geometry)
            if geom.contains(point):
                return corridor.corridor_id
        except Exception:
            logger.debug("Could not parse geometry for corridor %s", corridor.corridor_id)
            continue

    return None


# ── Cluster analysis helpers ─────────────────────────────────────────────────


def _compute_centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Compute mean lat/lon centroid of a set of points."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _compute_radius_nm(
    points: list[tuple[float, float]],
    centroid_lat: float,
    centroid_lon: float,
) -> float:
    """Max haversine distance from centroid to any member point, in NM."""
    if len(points) <= 1:
        return 0.0
    return max(
        _haversine_nm(centroid_lat, centroid_lon, p[0], p[1])
        for p in points
    )


def _compute_risk_score(
    event_count: int,
    trend: str,
    corridor_id: int | None,
) -> float:
    """Compute risk score for a hotspot."""
    score = SCORE_BASE + event_count * SCORE_PER_EVENT
    if trend == "growing":
        score += SCORE_GROWING_BONUS
    if corridor_id is not None:
        score += SCORE_CORRIDOR_BONUS
    return min(score, SCORE_MAX)


# ── Main entry point ─────────────────────────────────────────────────────────


def run_hotspot_detection(
    db: Session,
    eps_nm: float = DEFAULT_EPS_NM,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    """Run STS transfer hotspot detection.

    1. Load STS transfer events
    2. Build distance matrix on mean_lat/mean_lon
    3. Run DBSCAN clustering
    4. Analyze each cluster: centroid, radius, temporal trend, corridor overlap
    5. Persist StsHotspot records

    Returns summary statistics.
    """
    from app.config import settings

    if not getattr(settings, "STS_HOTSPOT_ENABLED", False):
        return {
            "hotspots_found": 0,
            "events_processed": 0,
            "noise_events": 0,
            "disabled": True,
        }

    from app.models.sts_hotspot import StsHotspot
    from app.models.sts_transfer import StsTransferEvent

    # Step 1: Load STS events with valid positions
    events = (
        db.query(StsTransferEvent)
        .filter(
            StsTransferEvent.mean_lat.isnot(None),
            StsTransferEvent.mean_lon.isnot(None),
        )
        .all()
    )

    if len(events) < min_samples:
        logger.info(
            "Too few STS events (%d) for hotspot detection (min_samples=%d)",
            len(events),
            min_samples,
        )
        return {
            "hotspots_found": 0,
            "events_processed": len(events),
            "noise_events": 0,
        }

    # Step 2: Extract positions and build distance matrix
    points = [(e.mean_lat, e.mean_lon) for e in events]
    dist_matrix = _build_distance_matrix(points)

    # Step 3: DBSCAN
    labels = _dbscan(dist_matrix, eps=eps_nm, min_samples=min_samples)

    # Group by cluster label
    cluster_events: dict[int, list[int]] = {}
    noise_count = 0
    for idx, label in enumerate(labels):
        if label == -1:
            noise_count += 1
        else:
            cluster_events.setdefault(label, []).append(idx)

    # Step 4: Remove old hotspots before persisting new ones
    db.query(StsHotspot).delete(synchronize_session=False)

    # Step 5: Analyze and persist each cluster
    hotspots_created = 0
    for label, indices in cluster_events.items():
        cluster_points = [points[i] for i in indices]
        cluster_timestamps = [events[i].start_time_utc for i in indices]

        centroid_lat, centroid_lon = _compute_centroid(cluster_points)
        radius = _compute_radius_nm(cluster_points, centroid_lat, centroid_lon)
        trend, slope = _compute_trend(cluster_timestamps)
        corridor_id = _find_corridor_overlap(db, centroid_lat, centroid_lon)
        risk_score = _compute_risk_score(len(indices), trend, corridor_id)

        first_seen = min(cluster_timestamps)
        last_seen = max(cluster_timestamps)

        evidence = {
            "cluster_label": label,
            "event_indices": indices,
            "event_count": len(indices),
            "radius_nm": round(radius, 2),
            "trend": trend,
            "trend_slope": slope,
            "corridor_id": corridor_id,
            "vessel_pairs": [
                {
                    "vessel_1_id": events[i].vessel_1_id,
                    "vessel_2_id": events[i].vessel_2_id,
                    "start_time": events[i].start_time_utc.isoformat(),
                }
                for i in indices
            ],
        }

        hotspot = StsHotspot(
            centroid_lat=centroid_lat,
            centroid_lon=centroid_lon,
            radius_nm=radius,
            event_count=len(indices),
            first_seen=first_seen,
            last_seen=last_seen,
            trend=trend,
            trend_slope=slope,
            corridor_id=corridor_id,
            risk_score_component=risk_score,
            evidence_json=json.dumps(evidence),
        )
        db.add(hotspot)
        hotspots_created += 1

    db.commit()

    result = {
        "hotspots_found": hotspots_created,
        "events_processed": len(events),
        "noise_events": noise_count,
    }
    logger.info("STS hotspot detection complete: %s", result)
    return result


# ── Query helpers ────────────────────────────────────────────────────────────


def get_hotspots(db: Session) -> list[dict[str, Any]]:
    """Retrieve all STS hotspots."""
    from app.models.sts_hotspot import StsHotspot

    hotspots = db.query(StsHotspot).order_by(StsHotspot.last_seen.desc()).all()
    return [_hotspot_to_dict(h) for h in hotspots]


def get_hotspot(db: Session, hotspot_id: int) -> dict[str, Any] | None:
    """Retrieve a single hotspot by ID."""
    from app.models.sts_hotspot import StsHotspot

    h = db.query(StsHotspot).filter(StsHotspot.hotspot_id == hotspot_id).first()
    if h is None:
        return None
    return _hotspot_to_dict(h)


def get_hotspots_geojson(db: Session) -> dict[str, Any]:
    """Return all hotspots as a GeoJSON FeatureCollection."""
    from app.models.sts_hotspot import StsHotspot

    hotspots = db.query(StsHotspot).order_by(StsHotspot.last_seen.desc()).all()

    features = []
    for h in hotspots:
        evidence = None
        if h.evidence_json:
            try:
                evidence = json.loads(h.evidence_json)
            except (json.JSONDecodeError, TypeError):
                evidence = None

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [h.centroid_lon, h.centroid_lat],
            },
            "properties": {
                "hotspot_id": h.hotspot_id,
                "radius_nm": h.radius_nm,
                "event_count": h.event_count,
                "first_seen": h.first_seen.isoformat() if h.first_seen else None,
                "last_seen": h.last_seen.isoformat() if h.last_seen else None,
                "trend": h.trend,
                "trend_slope": h.trend_slope,
                "corridor_id": h.corridor_id,
                "risk_score_component": h.risk_score_component,
                "evidence": evidence,
            },
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def _hotspot_to_dict(h: Any) -> dict[str, Any]:
    """Convert an StsHotspot ORM object to a dictionary."""
    evidence = None
    if h.evidence_json:
        try:
            evidence = json.loads(h.evidence_json)
        except (json.JSONDecodeError, TypeError):
            evidence = None

    return {
        "hotspot_id": h.hotspot_id,
        "centroid_lat": h.centroid_lat,
        "centroid_lon": h.centroid_lon,
        "radius_nm": h.radius_nm,
        "event_count": h.event_count,
        "first_seen": h.first_seen.isoformat() if h.first_seen else None,
        "last_seen": h.last_seen.isoformat() if h.last_seen else None,
        "trend": h.trend,
        "trend_slope": h.trend_slope,
        "corridor_id": h.corridor_id,
        "risk_score_component": h.risk_score_component,
        "evidence": evidence,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
