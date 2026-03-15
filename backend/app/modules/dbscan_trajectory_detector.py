"""DBSCAN Trajectory Clustering for maritime anomaly detection.

Clusters vessel trajectory segments using DBSCAN with a weighted haversine
distance metric. Identifies anomalous movement patterns by detecting:
- Noise points (segments that don't fit any cluster)
- Anomalous clusters (high deviation from normal patterns)
- Corridor-aware scoring (noise in known corridors is more suspicious)

Algorithm:
1. Extract 24-hour trajectory windows per vessel, downsampled to 30-min waypoints
2. Compute segment features: centroid, bearing, distance, straightness
3. Build brute-force distance matrix using weighted haversine
4. Run DBSCAN with configurable eps (nautical miles) and min_samples
5. Score anomalies based on cluster membership and corridor context
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

WINDOW_HOURS = 24
DOWNSAMPLE_MINUTES = 30

# Distance function weights
WEIGHT_SPATIAL = 1.0
WEIGHT_BEARING = 0.3  # bearing diff scaled to NM-equivalent
WEIGHT_STRAIGHTNESS = 0.2  # straightness diff scaled to NM-equivalent
BEARING_SCALE_NM = 30.0  # max NM penalty for 180° bearing diff
STRAIGHTNESS_SCALE_NM = 15.0  # max NM penalty for straightness diff of 1.0

# Scoring constants
SCORE_NOISE_IN_CORRIDOR = 25
SCORE_NOISE_OUTSIDE = 10
SCORE_ANOMALOUS_CLUSTER = 20
SCORE_HIGH_DEVIATION = 30
SCORE_MODERATE_DEVIATION = 15

# Deviation thresholds (ratio of cluster radius to global median radius)
HIGH_DEVIATION_THRESHOLD = 3.0
MODERATE_DEVIATION_THRESHOLD = 2.0


def compute_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing (forward azimuth) in degrees [0, 360)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlam = math.radians(lon2 - lon1)

    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360) % 360


def bearing_diff(b1: float, b2: float) -> float:
    """Absolute angular difference between two bearings, in [0, 180]."""
    diff = abs(b1 - b2) % 360
    return min(diff, 360 - diff)


# ── Segment feature extraction ───────────────────────────────────────────────


class TrajectorySegment:
    """Feature vector for a 24-hour vessel trajectory window."""

    __slots__ = (
        "vessel_id",
        "window_start",
        "window_end",
        "start_lat",
        "start_lon",
        "end_lat",
        "end_lon",
        "centroid_lat",
        "centroid_lon",
        "bearing",
        "total_distance_nm",
        "duration_hours",
        "mean_sog",
        "straightness_ratio",
        "waypoints",
    )

    def __init__(
        self,
        vessel_id: int,
        window_start: datetime,
        window_end: datetime,
        waypoints: list[tuple[datetime, float, float, float | None]],
    ):
        self.vessel_id = vessel_id
        self.window_start = window_start
        self.window_end = window_end
        self.waypoints = waypoints

        if len(waypoints) < 2:
            # Degenerate segment — single point
            ts, lat, lon, sog = waypoints[0]
            self.start_lat = lat
            self.start_lon = lon
            self.end_lat = lat
            self.end_lon = lon
            self.centroid_lat = lat
            self.centroid_lon = lon
            self.bearing = 0.0
            self.total_distance_nm = 0.0
            self.duration_hours = 0.0
            self.mean_sog = sog if sog is not None else 0.0
            self.straightness_ratio = 1.0
            return

        # Start / end
        self.start_lat = waypoints[0][1]
        self.start_lon = waypoints[0][2]
        self.end_lat = waypoints[-1][1]
        self.end_lon = waypoints[-1][2]

        # Centroid (mean of waypoints)
        self.centroid_lat = sum(wp[1] for wp in waypoints) / len(waypoints)
        self.centroid_lon = sum(wp[2] for wp in waypoints) / len(waypoints)

        # Bearing: initial heading from start to end
        self.bearing = compute_bearing(self.start_lat, self.start_lon, self.end_lat, self.end_lon)

        # Total distance along track
        total_dist = 0.0
        for i in range(1, len(waypoints)):
            total_dist += haversine_nm(
                waypoints[i - 1][1],
                waypoints[i - 1][2],
                waypoints[i][1],
                waypoints[i][2],
            )
        self.total_distance_nm = total_dist

        # Duration
        dt_seconds = (waypoints[-1][0] - waypoints[0][0]).total_seconds()
        self.duration_hours = dt_seconds / 3600.0 if dt_seconds > 0 else 0.0

        # Mean SOG (from AIS data, fallback to computed)
        sog_vals = [wp[3] for wp in waypoints if wp[3] is not None]
        if sog_vals:
            self.mean_sog = sum(sog_vals) / len(sog_vals)
        elif self.duration_hours > 0:
            self.mean_sog = self.total_distance_nm / self.duration_hours
        else:
            self.mean_sog = 0.0

        # Straightness ratio: direct distance / total track distance
        direct = haversine_nm(self.start_lat, self.start_lon, self.end_lat, self.end_lon)
        if self.total_distance_nm > 0:
            self.straightness_ratio = min(1.0, direct / self.total_distance_nm)
        else:
            self.straightness_ratio = 1.0


def _downsample_points(
    points: list[tuple[datetime, float, float, float | None]],
    interval_minutes: int = DOWNSAMPLE_MINUTES,
) -> list[tuple[datetime, float, float, float | None]]:
    """Downsample sorted AIS points to ~interval_minutes spacing.

    Always keeps the first and last point. Selects the point closest to each
    target timestamp in the interval grid.
    """
    if len(points) <= 2:
        return points

    result = [points[0]]
    interval_sec = interval_minutes * 60
    next_target = points[0][0] + timedelta(seconds=interval_sec)

    for pt in points[1:-1]:
        if pt[0] >= next_target:
            result.append(pt)
            next_target = pt[0] + timedelta(seconds=interval_sec)

    # Always include last point
    if result[-1] != points[-1]:
        result.append(points[-1])

    return result


def extract_segments(
    db: Session,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    vessel_ids: list[int] | None = None,
) -> list[TrajectorySegment]:
    """Extract trajectory segments from AIS points.

    Groups points by vessel into 24-hour windows, downsamples, and computes
    segment features. Requires at least 3 waypoints per window after
    downsampling to form a valid segment.
    """
    from app.models.ais_point import AISPoint

    query = db.query(
        AISPoint.vessel_id,
        AISPoint.timestamp_utc,
        AISPoint.lat,
        AISPoint.lon,
        AISPoint.sog,
    ).order_by(AISPoint.vessel_id, AISPoint.timestamp_utc)

    if date_from is not None:
        query = query.filter(AISPoint.timestamp_utc >= date_from)
    if date_to is not None:
        query = query.filter(AISPoint.timestamp_utc <= date_to)
    if vessel_ids is not None:
        query = query.filter(AISPoint.vessel_id.in_(vessel_ids))

    rows = query.all()

    # Group by vessel_id
    vessel_points: dict[int, list[tuple[datetime, float, float, float | None]]] = {}
    for vid, ts, lat, lon, sog in rows:
        vessel_points.setdefault(vid, []).append((ts, lat, lon, sog))

    segments: list[TrajectorySegment] = []

    for vid, points in vessel_points.items():
        if len(points) < 3:
            continue

        # Split into 24-hour windows
        points.sort(key=lambda p: p[0])
        window_start = points[0][0].replace(hour=0, minute=0, second=0, microsecond=0)

        while window_start < points[-1][0]:
            window_end = window_start + timedelta(hours=WINDOW_HOURS)
            window_pts = [p for p in points if window_start <= p[0] < window_end]

            if len(window_pts) >= 3:
                downsampled = _downsample_points(window_pts)
                if len(downsampled) >= 2:
                    seg = TrajectorySegment(vid, window_start, window_end, downsampled)
                    segments.append(seg)

            window_start = window_end

    logger.info("Extracted %d trajectory segments from %d vessels", len(segments), len(vessel_points))
    return segments


# ── DBSCAN implementation ────────────────────────────────────────────────────


def segment_distance(seg_a: TrajectorySegment, seg_b: TrajectorySegment) -> float:
    """Weighted distance between two trajectory segments in NM-equivalent units.

    Components:
    1. Haversine distance between centroids (NM)
    2. Bearing difference scaled to NM-equivalent
    3. Straightness ratio difference scaled to NM-equivalent
    """
    spatial = haversine_nm(
        seg_a.centroid_lat,
        seg_a.centroid_lon,
        seg_b.centroid_lat,
        seg_b.centroid_lon,
    )

    b_diff = bearing_diff(seg_a.bearing, seg_b.bearing)
    bearing_component = (b_diff / 180.0) * BEARING_SCALE_NM

    s_diff = abs(seg_a.straightness_ratio - seg_b.straightness_ratio)
    straightness_component = s_diff * STRAIGHTNESS_SCALE_NM

    return (
        WEIGHT_SPATIAL * spatial
        + WEIGHT_BEARING * bearing_component
        + WEIGHT_STRAIGHTNESS * straightness_component
    )


def compute_distance_matrix(segments: list[TrajectorySegment]) -> list[list[float]]:
    """Precompute full NxN symmetric distance matrix.

    At ~500 segments/day, this is ~124K haversine calls (0.5-2s).
    """
    n = len(segments)
    matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(i + 1, n):
            d = segment_distance(segments[i], segments[j])
            matrix[i][j] = d
            matrix[j][i] = d

    return matrix


def dbscan(
    distance_matrix: list[list[float]],
    eps: float,
    min_samples: int,
) -> list[int]:
    """Pure-Python DBSCAN clustering.

    Args:
        distance_matrix: Precomputed NxN symmetric distance matrix.
        eps: Maximum distance (in NM) for two segments to be neighbors.
        min_samples: Minimum number of points to form a dense region.

    Returns:
        List of cluster labels. -1 = noise, 0+ = cluster ID.
    """
    n = len(distance_matrix)
    labels = [-2] * n  # -2 = unvisited
    cluster_id = 0

    def _region_query(point_idx: int) -> list[int]:
        """Find all points within eps of point_idx."""
        return [j for j in range(n) if distance_matrix[point_idx][j] <= eps]

    for i in range(n):
        if labels[i] != -2:
            continue  # Already processed

        neighbors = _region_query(i)

        if len(neighbors) < min_samples:
            labels[i] = -1  # Noise
            continue

        # Start a new cluster
        labels[i] = cluster_id
        seed_set = list(neighbors)
        seed_set.remove(i)

        k = 0
        while k < len(seed_set):
            q = seed_set[k]
            if labels[q] == -1:
                labels[q] = cluster_id  # Border point
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


# ── Cluster analysis & scoring ───────────────────────────────────────────────


def _compute_cluster_centroid(
    segments: list[TrajectorySegment],
    member_indices: list[int],
) -> tuple[float, float]:
    """Compute mean centroid of cluster members."""
    lats = [segments[i].centroid_lat for i in member_indices]
    lons = [segments[i].centroid_lon for i in member_indices]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _compute_cluster_radius(
    segments: list[TrajectorySegment],
    member_indices: list[int],
    centroid_lat: float,
    centroid_lon: float,
) -> float:
    """Max distance (NM) from centroid to any member centroid."""
    if len(member_indices) <= 1:
        return 0.0
    return max(
        haversine_nm(centroid_lat, centroid_lon, segments[i].centroid_lat, segments[i].centroid_lon)
        for i in member_indices
    )


def _is_in_corridor(
    db: Session,
    lat: float,
    lon: float,
) -> bool:
    """Check if a point falls within any known corridor bounding box."""
    from app.models.corridor import Corridor

    corridors = db.query(Corridor).all()
    for corridor in corridors:
        bb = corridor.bounding_box_json
        if bb and isinstance(bb, dict):
            min_lat = bb.get("min_lat", -90)
            max_lat = bb.get("max_lat", 90)
            min_lon = bb.get("min_lon", -180)
            max_lon = bb.get("max_lon", 180)
            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                return True
    return False


def _score_noise_segment(
    db: Session,
    segment: TrajectorySegment,
) -> tuple[int, str]:
    """Score a noise segment based on corridor context."""
    in_corridor = _is_in_corridor(db, segment.centroid_lat, segment.centroid_lon)
    if in_corridor:
        return SCORE_NOISE_IN_CORRIDOR, "noise_point_in_corridor"
    return SCORE_NOISE_OUTSIDE, "noise_point_outside_corridor"


def _score_anomalous_cluster(
    cluster_radius: float,
    median_radius: float,
) -> tuple[int, str | None]:
    """Score a cluster based on its radius deviation from the global median."""
    if median_radius <= 0:
        return 0, None

    ratio = cluster_radius / median_radius

    if ratio >= HIGH_DEVIATION_THRESHOLD:
        return SCORE_HIGH_DEVIATION, "high_deviation_cluster"
    elif ratio >= MODERATE_DEVIATION_THRESHOLD:
        return SCORE_MODERATE_DEVIATION, "moderate_deviation_cluster"
    return 0, None


# ── Main entry point ─────────────────────────────────────────────────────────


def run_trajectory_clustering(
    db: Session,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    vessel_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Run DBSCAN trajectory clustering on AIS data.

    1. Extract trajectory segments (24h windows, 30-min downsampled)
    2. Compute brute-force distance matrix
    3. Run DBSCAN clustering
    4. Analyze clusters for anomalies
    5. Persist results to trajectory_clusters and trajectory_cluster_members

    Returns summary statistics.
    """
    if not settings.DBSCAN_CLUSTERING_ENABLED:
        return {
            "segments_processed": 0,
            "clusters_found": 0,
            "noise_segments": 0,
            "anomalous_clusters": 0,
            "disabled": True,
        }

    eps = settings.DBSCAN_EPS_NM
    min_samples = settings.DBSCAN_MIN_SAMPLES

    # Step 1: Extract segments
    segments = extract_segments(db, date_from=date_from, date_to=date_to, vessel_ids=vessel_ids)

    if len(segments) < min_samples:
        logger.info(
            "Too few segments (%d) for clustering (min_samples=%d)",
            len(segments),
            min_samples,
        )
        return {
            "segments_processed": len(segments),
            "clusters_found": 0,
            "noise_segments": 0,
            "anomalous_clusters": 0,
        }

    # Step 2: Distance matrix
    dist_matrix = compute_distance_matrix(segments)

    # Step 3: DBSCAN
    labels = dbscan(dist_matrix, eps=eps, min_samples=min_samples)

    # Step 4: Analyze clusters
    from app.models.trajectory_cluster import TrajectoryCluster
    from app.models.trajectory_cluster_member import TrajectoryClusterMember

    # Deduplicate: remove existing clusters in the same date range
    if date_from or date_to:
        dedup_query = db.query(TrajectoryCluster)
        if date_from:
            dedup_query = dedup_query.filter(TrajectoryCluster.created_at >= date_from)
        if date_to:
            dedup_query = dedup_query.filter(TrajectoryCluster.created_at <= date_to)
        existing_ids = [c.cluster_id for c in dedup_query.all()]
        if existing_ids:
            db.query(TrajectoryClusterMember).filter(
                TrajectoryClusterMember.cluster_id.in_(existing_ids)
            ).delete(synchronize_session=False)
            dedup_query.delete(synchronize_session=False)
            logger.info("Deduplicated %d existing clusters", len(existing_ids))

    # Group segments by cluster label
    cluster_groups: dict[int, list[int]] = {}
    noise_indices: list[int] = []

    for idx, label in enumerate(labels):
        if label == -1:
            noise_indices.append(idx)
        else:
            cluster_groups.setdefault(label, []).append(idx)

    # Compute cluster radii for deviation scoring
    cluster_radii: dict[int, float] = {}
    cluster_centroids: dict[int, tuple[float, float]] = {}
    for label, indices in cluster_groups.items():
        clat, clon = _compute_cluster_centroid(segments, indices)
        cluster_centroids[label] = (clat, clon)
        cluster_radii[label] = _compute_cluster_radius(segments, indices, clat, clon)

    # Median radius for deviation detection
    radii_values = sorted(cluster_radii.values())
    if radii_values:
        mid = len(radii_values) // 2
        if len(radii_values) % 2 == 0:
            median_radius = (radii_values[mid - 1] + radii_values[mid]) / 2
        else:
            median_radius = radii_values[mid]
    else:
        median_radius = 0.0

    anomalous_count = 0

    # Persist clusters
    for label, indices in cluster_groups.items():
        clat, clon = cluster_centroids[label]
        radius = cluster_radii[label]

        dev_score, dev_reason = _score_anomalous_cluster(radius, median_radius)
        is_anomalous = dev_score > 0

        if is_anomalous:
            anomalous_count += 1

        evidence: dict[str, Any] = {
            "cluster_label": label,
            "radius_nm": round(radius, 2),
            "median_radius_nm": round(median_radius, 2),
            "deviation_ratio": round(radius / median_radius, 2) if median_radius > 0 else None,
        }
        if dev_reason:
            evidence["anomaly_type"] = dev_reason
            evidence["score"] = dev_score

        tc = TrajectoryCluster(
            label=label,
            centroid_lat=clat,
            centroid_lon=clon,
            radius_nm=radius,
            member_count=len(indices),
            is_anomalous=is_anomalous,
            anomaly_reason=dev_reason,
            evidence_json=evidence,
        )
        db.add(tc)
        db.flush()  # Get cluster_id

        for idx in indices:
            seg = segments[idx]
            score = dev_score if is_anomalous else 0
            member = TrajectoryClusterMember(
                cluster_id=tc.cluster_id,
                vessel_id=seg.vessel_id,
                segment_start=seg.window_start,
                segment_end=seg.window_end,
                start_lat=seg.start_lat,
                start_lon=seg.start_lon,
                end_lat=seg.end_lat,
                end_lon=seg.end_lon,
                bearing=seg.bearing,
                distance_nm=seg.total_distance_nm,
                mean_sog=seg.mean_sog,
                straightness_ratio=seg.straightness_ratio,
                is_noise=False,
                risk_score_component=score,
            )
            db.add(member)

    # Persist noise segments (no cluster)
    # Create a single "noise" pseudo-cluster for storage
    if noise_indices:
        noise_cluster = TrajectoryCluster(
            label=-1,
            centroid_lat=0.0,
            centroid_lon=0.0,
            radius_nm=0.0,
            member_count=len(noise_indices),
            is_anomalous=False,
            anomaly_reason="noise",
            evidence_json={"cluster_label": -1, "type": "noise_collection"},
        )
        db.add(noise_cluster)
        db.flush()

        for idx in noise_indices:
            seg = segments[idx]
            score, reason = _score_noise_segment(db, seg)
            member = TrajectoryClusterMember(
                cluster_id=noise_cluster.cluster_id,
                vessel_id=seg.vessel_id,
                segment_start=seg.window_start,
                segment_end=seg.window_end,
                start_lat=seg.start_lat,
                start_lon=seg.start_lon,
                end_lat=seg.end_lat,
                end_lon=seg.end_lon,
                bearing=seg.bearing,
                distance_nm=seg.total_distance_nm,
                mean_sog=seg.mean_sog,
                straightness_ratio=seg.straightness_ratio,
                is_noise=True,
                risk_score_component=score,
            )
            db.add(member)

    db.commit()

    result = {
        "segments_processed": len(segments),
        "clusters_found": len(cluster_groups),
        "noise_segments": len(noise_indices),
        "anomalous_clusters": anomalous_count,
    }
    logger.info("Trajectory clustering complete: %s", result)
    return result


def get_clusters(
    db: Session,
    include_noise: bool = False,
) -> list[dict[str, Any]]:
    """Retrieve all trajectory clusters with summary info."""
    from app.models.trajectory_cluster import TrajectoryCluster

    query = db.query(TrajectoryCluster)
    if not include_noise:
        query = query.filter(TrajectoryCluster.label >= 0)

    clusters = query.order_by(TrajectoryCluster.created_at.desc()).all()
    return [
        {
            "cluster_id": c.cluster_id,
            "label": c.label,
            "centroid_lat": c.centroid_lat,
            "centroid_lon": c.centroid_lon,
            "radius_nm": c.radius_nm,
            "member_count": c.member_count,
            "is_anomalous": c.is_anomalous,
            "anomaly_reason": c.anomaly_reason,
            "evidence_json": c.evidence_json,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in clusters
    ]


def get_vessel_cluster_memberships(
    db: Session,
    vessel_id: int,
) -> list[dict[str, Any]]:
    """Retrieve cluster memberships for a specific vessel."""
    from app.models.trajectory_cluster import TrajectoryCluster
    from app.models.trajectory_cluster_member import TrajectoryClusterMember

    members = (
        db.query(TrajectoryClusterMember)
        .filter(TrajectoryClusterMember.vessel_id == vessel_id)
        .order_by(TrajectoryClusterMember.segment_start.desc())
        .all()
    )

    # Batch-load clusters to avoid N+1 queries
    cluster_ids = {m.cluster_id for m in members}
    clusters_by_id = {}
    if cluster_ids:
        clusters = db.query(TrajectoryCluster).filter(
            TrajectoryCluster.cluster_id.in_(cluster_ids)
        ).all()
        clusters_by_id = {c.cluster_id: c for c in clusters}

    result = []
    for m in members:
        cluster = clusters_by_id.get(m.cluster_id)
        result.append(
            {
                "member_id": m.member_id,
                "cluster_id": m.cluster_id,
                "cluster_label": cluster.label if cluster else None,
                "is_anomalous": cluster.is_anomalous if cluster else False,
                "segment_start": m.segment_start.isoformat() if m.segment_start else None,
                "segment_end": m.segment_end.isoformat() if m.segment_end else None,
                "start_lat": m.start_lat,
                "start_lon": m.start_lon,
                "end_lat": m.end_lat,
                "end_lon": m.end_lon,
                "bearing": m.bearing,
                "distance_nm": m.distance_nm,
                "mean_sog": m.mean_sog,
                "straightness_ratio": m.straightness_ratio,
                "is_noise": m.is_noise,
                "risk_score_component": m.risk_score_component,
            }
        )

    return result
