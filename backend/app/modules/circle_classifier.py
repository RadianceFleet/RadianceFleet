"""Circle spoofing pattern classifier.

Classifies circle spoofing detections into three categories:
- **stationary**: GPS jamming — vessel not moving, AIS shows circles (+25 pts)
- **deliberate**: Intentional circle spoofing while vessel moves (+35 pts)
- **equipment**: GPS equipment malfunction causing degraded circles (+10 pts)

Multi-vessel coherence check per arXiv 2603.11055: simultaneous circle
patterns from multiple vessels in the same area indicate regional GPS
jamming rather than single-vessel spoofing.
"""

from __future__ import annotations

import logging
import math
import statistics
from datetime import timedelta

from sqlalchemy.orm import Session

from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

# Classification thresholds
_CENTROID_MOVEMENT_LOW_NM = 0.5   # Below this → stationary
_CENTROID_MOVEMENT_HIGH_NM = 2.0  # Above this → deliberate
_SOG_LOW_KN = 2.0                 # Mean SOG below this → stationary
_SOG_HIGH_KN = 3.0                # Mean SOG above this → deliberate
_RADIUS_CV_THRESHOLD = 0.5        # Coefficient of variation above this → equipment

# Score mapping
CLASSIFICATION_SCORES: dict[str, int] = {
    "stationary": 25,
    "deliberate": 35,
    "equipment": 10,
}


def _get_lat(p) -> float:
    """Extract latitude from a point (dict or object)."""
    return p["lat"] if isinstance(p, dict) else p.lat


def _get_lon(p) -> float:
    """Extract longitude from a point (dict or object)."""
    return p["lon"] if isinstance(p, dict) else p.lon


def _get_sog(p) -> float | None:
    """Extract SOG from a point (dict or object)."""
    if isinstance(p, dict):
        return p.get("sog")
    return getattr(p, "sog", None)


def _get_timestamp(p):
    """Extract timestamp from a point (dict or object)."""
    if isinstance(p, dict):
        return p.get("timestamp_utc")
    return getattr(p, "timestamp_utc", None)


def compute_centroid_movement(points: list) -> float:
    """Total centroid movement in nautical miles across sequential segments.

    Divides points into 3 equal segments and measures how far the centroid
    moves between segments.  Returns total nm of centroid displacement.
    """
    if len(points) < 3:
        return 0.0

    n = len(points)
    seg_size = max(1, n // 3)
    segments = [
        points[:seg_size],
        points[seg_size : 2 * seg_size],
        points[2 * seg_size :],
    ]

    centroids = []
    for seg in segments:
        if not seg:
            continue
        clat = statistics.mean(_get_lat(p) for p in seg)
        clon = statistics.mean(_get_lon(p) for p in seg)
        centroids.append((clat, clon))

    total = 0.0
    for i in range(1, len(centroids)):
        total += haversine_nm(
            centroids[i - 1][0], centroids[i - 1][1],
            centroids[i][0], centroids[i][1],
        )
    return total


def compute_sog_stats(points: list) -> dict:
    """Compute SOG statistics from points.

    Returns dict with keys: mean, std, cv (coefficient of variation),
    count (number of valid SOG values).
    """
    sogs = [_get_sog(p) for p in points]
    sogs = [s for s in sogs if s is not None and not math.isnan(s)]

    if not sogs:
        return {"mean": 0.0, "std": 0.0, "cv": 0.0, "count": 0}

    mean = statistics.mean(sogs)
    std = statistics.stdev(sogs) if len(sogs) >= 2 else 0.0
    cv = std / mean if mean > 0 else 0.0

    return {"mean": mean, "std": std, "cv": cv, "count": len(sogs)}


def compute_radius_stats(points: list) -> dict:
    """Compute radius statistics — distance of each point from centroid.

    Returns dict with keys: mean, std, cv (coefficient of variation),
    trend (slope of linear regression on radii — negative = spiral inward).
    """
    if len(points) < 2:
        return {"mean": 0.0, "std": 0.0, "cv": 0.0, "trend": 0.0}

    clat = statistics.mean(_get_lat(p) for p in points)
    clon = statistics.mean(_get_lon(p) for p in points)

    radii = [
        haversine_nm(clat, clon, _get_lat(p), _get_lon(p))
        for p in points
    ]

    mean_r = statistics.mean(radii)
    std_r = statistics.stdev(radii) if len(radii) >= 2 else 0.0
    cv = std_r / mean_r if mean_r > 0 else 0.0

    # Linear trend: simple regression of radius vs index
    n = len(radii)
    if n >= 2:
        x_mean = (n - 1) / 2.0
        y_mean = mean_r
        num = sum((i - x_mean) * (r - y_mean) for i, r in enumerate(radii))
        den = sum((i - x_mean) ** 2 for i in range(n))
        trend = num / den if den > 0 else 0.0
    else:
        trend = 0.0

    return {"mean": mean_r, "std": std_r, "cv": cv, "trend": trend}


def classify_circle_pattern(points: list) -> str:
    """Classify a circle spoof pattern into a sub-type.

    Args:
        points: List of AIS point dicts/objects with .lat, .lon, .sog,
                .timestamp_utc attributes.

    Returns:
        Classification string: ``"stationary"``, ``"deliberate"``, or
        ``"equipment"``.
    """
    if len(points) < 3:
        # Insufficient data — default to deliberate (highest risk, most conservative)
        return "deliberate"

    centroid_movement = compute_centroid_movement(points)
    sog_stats = compute_sog_stats(points)
    radius_stats = compute_radius_stats(points)

    # Stationary: low centroid movement AND low SOG
    if centroid_movement < _CENTROID_MOVEMENT_LOW_NM and sog_stats["mean"] < _SOG_LOW_KN:
        return "stationary"

    # Deliberate: significant centroid movement AND consistent high SOG
    # Check this BEFORE equipment because linear transit naturally produces
    # high radius CV (endpoints far from centroid, middle close).
    if centroid_movement > _CENTROID_MOVEMENT_HIGH_NM and sog_stats["mean"] > _SOG_HIGH_KN:
        return "deliberate"

    # Equipment malfunction: high radius variance + decreasing radius (spiral)
    # Only classify as equipment when the vessel is NOT clearly transiting
    # (low centroid movement rules out linear track artifacts).
    if centroid_movement < _CENTROID_MOVEMENT_HIGH_NM:
        if radius_stats["cv"] > _RADIUS_CV_THRESHOLD and radius_stats["trend"] < 0:
            return "equipment"
        if radius_stats["cv"] > _RADIUS_CV_THRESHOLD * 1.5:
            return "equipment"

    # Equipment with erratic SOG (high SOG variance regardless of movement)
    if sog_stats["cv"] > 0.6 and radius_stats["cv"] > _RADIUS_CV_THRESHOLD:
        return "equipment"

    # Fallback heuristics
    if centroid_movement < _CENTROID_MOVEMENT_LOW_NM:
        # Low movement but high SOG — GPS jamming (reported SOG is spoofed too)
        return "stationary"

    if sog_stats["mean"] > _SOG_HIGH_KN and sog_stats["cv"] < 0.5:
        # Consistent high SOG with moderate movement → deliberate
        return "deliberate"

    # Default: deliberate (conservative — highest risk)
    return "deliberate"


def check_multi_vessel_coherence(
    db: Session,
    lat: float,
    lon: float,
    time,
    radius_nm: float = 30,
    time_window_h: float = 6,
) -> bool:
    """Check if other vessels show simultaneous circle patterns nearby.

    Per arXiv 2603.11055, multiple vessels showing circle patterns in the
    same area at the same time is a strong indicator of regional GPS jamming
    (rather than single-vessel intentional spoofing).

    Args:
        db: Database session.
        lat: Latitude of the detection.
        lon: Longitude of the detection.
        time: Timestamp of the detection.
        radius_nm: Search radius in nautical miles (default 30).
        time_window_h: Time window in hours (default 6).

    Returns:
        True if at least one other vessel has a circle spoof anomaly
        within the specified space/time window.
    """
    from app.models.base import SpoofingTypeEnum
    from app.models.spoofing_anomaly import SpoofingAnomaly

    t_start = time - timedelta(hours=time_window_h)
    t_end = time + timedelta(hours=time_window_h)

    # Query circle spoof anomalies in the time window (all sub-types)
    circle_types = [
        SpoofingTypeEnum.CIRCLE_SPOOF,
        SpoofingTypeEnum.CIRCLE_SPOOF_STATIONARY,
        SpoofingTypeEnum.CIRCLE_SPOOF_DELIBERATE,
        SpoofingTypeEnum.CIRCLE_SPOOF_EQUIPMENT,
    ]

    candidates = (
        db.query(SpoofingAnomaly)
        .filter(
            SpoofingAnomaly.anomaly_type.in_(circle_types),
            SpoofingAnomaly.start_time_utc >= t_start,
            SpoofingAnomaly.start_time_utc <= t_end,
        )
        .all()
    )

    for c in candidates:
        ej = c.evidence_json or {}
        c_lat = ej.get("mean_lat") or ej.get("centroid_lat")
        c_lon = ej.get("mean_lon") or ej.get("centroid_lon")
        if c_lat is None or c_lon is None:
            continue
        dist = haversine_nm(lat, lon, float(c_lat), float(c_lon))
        if dist <= radius_nm:
            return True

    return False
