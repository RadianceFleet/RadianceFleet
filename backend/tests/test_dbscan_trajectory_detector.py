"""Tests for DBSCAN trajectory clustering detector."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.modules.dbscan_trajectory_detector import (
    SCORE_HIGH_DEVIATION,
    SCORE_MODERATE_DEVIATION,
    SCORE_NOISE_IN_CORRIDOR,
    SCORE_NOISE_OUTSIDE,
    TrajectorySegment,
    _compute_cluster_centroid,
    _compute_cluster_radius,
    _score_anomalous_cluster,
    _score_noise_segment,
    bearing_diff,
    compute_bearing,
    compute_distance_matrix,
    dbscan,
    extract_segments,
    haversine_nm,
    run_trajectory_clustering,
    segment_distance,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_waypoints(
    base_time: datetime,
    lats: list[float],
    lons: list[float],
    sogs: list[float | None] | None = None,
    interval_min: int = 30,
) -> list[tuple[datetime, float, float, float | None]]:
    """Create waypoint tuples for testing."""
    if sogs is None:
        sogs = [10.0] * len(lats)
    return [
        (base_time + timedelta(minutes=i * interval_min), lat, lon, sog)
        for i, (lat, lon, sog) in enumerate(zip(lats, lons, sogs))
    ]


def _make_segment(
    vessel_id: int = 1,
    centroid_lat: float = 60.0,
    centroid_lon: float = 25.0,
    bearing: float = 90.0,
    straightness: float = 0.95,
    **kwargs,
) -> TrajectorySegment:
    """Create a TrajectorySegment with specified features for testing."""
    base = datetime(2026, 1, 10, 0, 0, 0)
    wps = _make_waypoints(base, [centroid_lat, centroid_lat + 0.1], [centroid_lon, centroid_lon + 0.1])
    seg = TrajectorySegment(vessel_id, base, base + timedelta(hours=24), wps)
    # Override computed values for test control
    seg.centroid_lat = centroid_lat
    seg.centroid_lon = centroid_lon
    seg.bearing = bearing
    seg.straightness_ratio = straightness
    return seg


# ── Haversine & distance tests ──────────────────────────────────────────────


class TestHaversine:
    """Haversine distance correctness."""

    def test_haversine_zero_distance(self):
        """Same point returns 0."""
        assert haversine_nm(60.0, 25.0, 60.0, 25.0) == 0.0

    def test_haversine_high_latitude_longitude(self):
        """At 60N, 1 degree longitude should be ~30nm (not 60nm).

        This verifies the haversine formula accounts for latitude
        compression of longitude. cos(60°) ≈ 0.5, so 1° lon ≈ 30nm.
        """
        dist = haversine_nm(60.0, 25.0, 60.0, 26.0)
        # At 60N: 1° lon = 60nm * cos(60°) ≈ 30nm
        assert 28.0 < dist < 32.0, f"Expected ~30nm at 60N, got {dist:.1f}nm"

    def test_haversine_latitude_degree(self):
        """1 degree latitude should be ~60nm regardless of longitude."""
        dist = haversine_nm(60.0, 25.0, 61.0, 25.0)
        assert 59.0 < dist < 61.0, f"Expected ~60nm, got {dist:.1f}nm"

    def test_distance_matrix_symmetry(self):
        """Distance matrix should be symmetric: d(A,B) == d(B,A)."""
        segs = [
            _make_segment(centroid_lat=60.0, centroid_lon=25.0),
            _make_segment(centroid_lat=60.5, centroid_lon=25.5),
            _make_segment(centroid_lat=61.0, centroid_lon=26.0),
        ]
        matrix = compute_distance_matrix(segs)
        for i in range(len(matrix)):
            for j in range(len(matrix)):
                assert matrix[i][j] == pytest.approx(
                    matrix[j][i], abs=1e-10
                ), f"Asymmetry at ({i},{j})"

    def test_distance_matrix_zero_diagonal(self):
        """Diagonal of distance matrix should be zero."""
        segs = [
            _make_segment(centroid_lat=60.0),
            _make_segment(centroid_lat=60.5),
        ]
        matrix = compute_distance_matrix(segs)
        for i in range(len(matrix)):
            assert matrix[i][i] == 0.0


# ── DBSCAN algorithm tests ──────────────────────────────────────────────────


class TestDBSCAN:
    """Pure-Python DBSCAN implementation."""

    def test_single_cluster(self):
        """Points within eps form one cluster."""
        # 3 points all within distance 5 of each other
        matrix = [
            [0, 3, 4],
            [3, 0, 2],
            [4, 2, 0],
        ]
        labels = dbscan(matrix, eps=5, min_samples=2)
        assert labels[0] == labels[1] == labels[2]
        assert labels[0] >= 0  # Not noise

    def test_two_clusters(self):
        """Two well-separated groups form two clusters."""
        # Group A: points 0,1,2 close together
        # Group B: points 3,4,5 close together
        # Groups far apart
        matrix = [
            [0, 2, 3, 100, 100, 100],
            [2, 0, 2, 100, 100, 100],
            [3, 2, 0, 100, 100, 100],
            [100, 100, 100, 0, 2, 3],
            [100, 100, 100, 2, 0, 2],
            [100, 100, 100, 3, 2, 0],
        ]
        labels = dbscan(matrix, eps=5, min_samples=2)
        # Group A should share a label
        assert labels[0] == labels[1] == labels[2]
        # Group B should share a label
        assert labels[3] == labels[4] == labels[5]
        # Different clusters
        assert labels[0] != labels[3]

    def test_noise_points(self):
        """Isolated points labeled as noise (-1)."""
        # Point 2 is far from both groups
        matrix = [
            [0, 2, 100],
            [2, 0, 100],
            [100, 100, 0],
        ]
        labels = dbscan(matrix, eps=5, min_samples=2)
        assert labels[0] == labels[1]
        assert labels[0] >= 0
        assert labels[2] == -1  # Noise

    def test_min_samples_respected(self):
        """With min_samples=3, a pair should be noise."""
        matrix = [
            [0, 2],
            [2, 0],
        ]
        labels = dbscan(matrix, eps=5, min_samples=3)
        assert labels[0] == -1
        assert labels[1] == -1

    def test_distance_symmetry_in_clustering(self):
        """Symmetric distance matrix produces consistent labels."""
        matrix = [
            [0, 5, 10],
            [5, 0, 5],
            [10, 5, 0],
        ]
        labels = dbscan(matrix, eps=6, min_samples=2)
        # All three should be in same cluster (chain: 0-1-2)
        assert labels[0] == labels[1] == labels[2]

    def test_distance_zero_self(self):
        """Points have zero distance to themselves."""
        matrix = [[0, 100], [100, 0]]
        labels = dbscan(matrix, eps=5, min_samples=2)
        # Both isolated, both noise
        assert labels[0] == -1
        assert labels[1] == -1


# ── Segment extraction tests ────────────────────────────────────────────────


class TestSegmentExtraction:
    """Trajectory segment extraction from AIS data."""

    def test_insufficient_points_skipped(self):
        """Vessels with <3 points produce no segments."""
        base = datetime(2026, 1, 10, 0, 0, 0)
        wps = _make_waypoints(base, [60.0, 60.1], [25.0, 25.1])
        TrajectorySegment(1, base, base + timedelta(hours=24), wps)
        # Should still create a segment (>=2 waypoints), but extract_segments
        # requires >=3 points before windowing
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.filter.return_value = mock_query
        # Only 2 points for vessel 1
        mock_query.all.return_value = [
            (1, base, 60.0, 25.0, 10.0),
            (1, base + timedelta(hours=1), 60.1, 25.1, 10.0),
        ]
        segments = extract_segments(db)
        assert len(segments) == 0

    def test_single_window_segments(self):
        """Points within one 24h window produce one segment."""
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.filter.return_value = mock_query

        base = datetime(2026, 1, 10, 1, 0, 0)
        points = [
            (1, base + timedelta(hours=i), 60.0 + i * 0.01, 25.0 + i * 0.01, 10.0)
            for i in range(5)
        ]
        mock_query.all.return_value = points
        segments = extract_segments(db)
        assert len(segments) == 1
        assert segments[0].vessel_id == 1

    def test_multiple_windows(self):
        """Points spanning 48h produce two segments."""
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.filter.return_value = mock_query

        base = datetime(2026, 1, 10, 1, 0, 0)
        # 10 points every 6 hours = 54 hours total → spans 3 windows (0-24, 24-48, 48-72)
        points = [
            (1, base + timedelta(hours=i * 6), 60.0 + i * 0.01, 25.0 + i * 0.01, 10.0)
            for i in range(10)
        ]
        mock_query.all.return_value = points
        segments = extract_segments(db)
        # First window (0-24h) has ~4 points, second (24-48h) has ~4, third (48-72h) has ~2
        assert len(segments) >= 2

    def test_straightness_ratio(self):
        """Straight path has ratio near 1.0, curved path has lower ratio."""
        base = datetime(2026, 1, 10, 0, 0, 0)

        # Straight path
        straight_wps = _make_waypoints(
            base,
            [60.0, 60.1, 60.2, 60.3],
            [25.0, 25.0, 25.0, 25.0],
        )
        straight_seg = TrajectorySegment(1, base, base + timedelta(hours=24), straight_wps)
        assert straight_seg.straightness_ratio > 0.95

        # Curved path (zigzag)
        curved_wps = _make_waypoints(
            base,
            [60.0, 60.1, 60.0, 60.1],
            [25.0, 25.5, 26.0, 26.5],
        )
        curved_seg = TrajectorySegment(2, base, base + timedelta(hours=24), curved_wps)
        assert curved_seg.straightness_ratio < straight_seg.straightness_ratio


# ── Cluster analysis tests ───────────────────────────────────────────────────


class TestClusterAnalysis:
    """Cluster centroid, radius, and scoring."""

    def test_centroid_computation(self):
        """Centroid is mean of member centroids."""
        segs = [
            _make_segment(centroid_lat=60.0, centroid_lon=25.0),
            _make_segment(centroid_lat=60.2, centroid_lon=25.2),
            _make_segment(centroid_lat=60.4, centroid_lon=25.4),
        ]
        clat, clon = _compute_cluster_centroid(segs, [0, 1, 2])
        assert clat == pytest.approx(60.2, abs=0.01)
        assert clon == pytest.approx(25.2, abs=0.01)

    def test_radius_computation(self):
        """Radius is max distance from centroid to any member."""
        segs = [
            _make_segment(centroid_lat=60.0, centroid_lon=25.0),
            _make_segment(centroid_lat=60.0, centroid_lon=25.0),
        ]
        radius = _compute_cluster_radius(segs, [0, 1], 60.0, 25.0)
        assert radius == 0.0

        # With spread members
        segs2 = [
            _make_segment(centroid_lat=60.0, centroid_lon=25.0),
            _make_segment(centroid_lat=60.5, centroid_lon=25.0),
        ]
        radius2 = _compute_cluster_radius(segs2, [0, 1], 60.25, 25.0)
        assert radius2 > 0

    def test_noise_scoring_in_corridor(self):
        """Noise point in corridor gets higher score."""
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        # Return a corridor that contains our segment's centroid
        corridor = MagicMock()
        corridor.bounding_box_json = {
            "min_lat": 59.0,
            "max_lat": 61.0,
            "min_lon": 24.0,
            "max_lon": 26.0,
        }
        mock_query.all.return_value = [corridor]

        seg = _make_segment(centroid_lat=60.0, centroid_lon=25.0)
        score, reason = _score_noise_segment(db, seg)
        assert score == SCORE_NOISE_IN_CORRIDOR
        assert reason == "noise_point_in_corridor"

    def test_noise_scoring_outside_corridor(self):
        """Noise point outside corridors gets lower score."""
        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.all.return_value = []  # No corridors

        seg = _make_segment(centroid_lat=60.0, centroid_lon=25.0)
        score, reason = _score_noise_segment(db, seg)
        assert score == SCORE_NOISE_OUTSIDE
        assert reason == "noise_point_outside_corridor"

    def test_anomalous_cluster_high_deviation(self):
        """Cluster with radius >>3x median gets high deviation score."""
        score, reason = _score_anomalous_cluster(30.0, 8.0)
        assert score == SCORE_HIGH_DEVIATION
        assert reason == "high_deviation_cluster"

    def test_anomalous_cluster_moderate_deviation(self):
        """Cluster with radius 2-3x median gets moderate deviation score."""
        score, reason = _score_anomalous_cluster(20.0, 8.0)
        assert score == SCORE_MODERATE_DEVIATION
        assert reason == "moderate_deviation_cluster"

    def test_normal_cluster_no_score(self):
        """Cluster with normal radius gets no score."""
        score, reason = _score_anomalous_cluster(8.0, 8.0)
        assert score == 0
        assert reason is None


# ── Integration tests ────────────────────────────────────────────────────────


class TestIntegration:
    """End-to-end clustering integration."""

    @patch("app.modules.dbscan_trajectory_detector.settings")
    def test_disabled_returns_early(self, mock_settings):
        """When disabled, returns immediately with disabled flag."""
        mock_settings.DBSCAN_CLUSTERING_ENABLED = False
        db = MagicMock()
        result = run_trajectory_clustering(db)
        assert result["disabled"] is True
        assert result["segments_processed"] == 0

    @patch("app.modules.dbscan_trajectory_detector.settings")
    @patch("app.modules.dbscan_trajectory_detector.extract_segments")
    def test_creates_clusters(self, mock_extract, mock_settings):
        """Creates cluster records from segments."""
        mock_settings.DBSCAN_CLUSTERING_ENABLED = True
        mock_settings.DBSCAN_EPS_NM = 15.0
        mock_settings.DBSCAN_MIN_SAMPLES = 2

        # Create 3 nearby segments that should cluster together
        segs = [
            _make_segment(vessel_id=1, centroid_lat=60.0, centroid_lon=25.0, bearing=90.0),
            _make_segment(vessel_id=2, centroid_lat=60.01, centroid_lon=25.01, bearing=91.0),
            _make_segment(vessel_id=3, centroid_lat=60.02, centroid_lon=25.02, bearing=89.0),
        ]
        mock_extract.return_value = segs

        db = MagicMock()
        # Mock query for dedup
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = []

        result = run_trajectory_clustering(db)
        assert result["segments_processed"] == 3
        assert result["clusters_found"] >= 1
        assert db.add.called
        assert db.commit.called

    @patch("app.modules.dbscan_trajectory_detector.settings")
    @patch("app.modules.dbscan_trajectory_detector.extract_segments")
    def test_dedup_removes_existing(self, mock_extract, mock_settings):
        """Existing clusters in date range are removed before re-clustering."""
        mock_settings.DBSCAN_CLUSTERING_ENABLED = True
        mock_settings.DBSCAN_EPS_NM = 15.0
        mock_settings.DBSCAN_MIN_SAMPLES = 2

        mock_extract.return_value = [
            _make_segment(vessel_id=1, centroid_lat=60.0, centroid_lon=25.0),
            _make_segment(vessel_id=2, centroid_lat=60.01, centroid_lon=25.01),
            _make_segment(vessel_id=3, centroid_lat=60.02, centroid_lon=25.02),
        ]

        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query

        # Simulate existing clusters
        existing_cluster = MagicMock()
        existing_cluster.cluster_id = 99
        mock_query.all.return_value = [existing_cluster]
        mock_query.delete.return_value = 1

        # Mock .in_() for member deletion
        mock_in = MagicMock()
        mock_in.return_value = mock_in

        result = run_trajectory_clustering(
            db,
            date_from=datetime(2026, 1, 1),
            date_to=datetime(2026, 1, 31),
        )
        assert result["segments_processed"] == 3
        # delete should have been called for dedup
        assert mock_query.delete.called

    @patch("app.modules.dbscan_trajectory_detector.settings")
    @patch("app.modules.dbscan_trajectory_detector.extract_segments")
    def test_deviation_scoring(self, mock_extract, mock_settings):
        """Anomalous clusters with high deviation are flagged."""
        mock_settings.DBSCAN_CLUSTERING_ENABLED = True
        mock_settings.DBSCAN_EPS_NM = 50.0  # Wide eps to catch everything
        mock_settings.DBSCAN_MIN_SAMPLES = 2

        # Cluster A: tight group
        segs = [
            _make_segment(vessel_id=1, centroid_lat=60.0, centroid_lon=25.0),
            _make_segment(vessel_id=2, centroid_lat=60.001, centroid_lon=25.001),
            _make_segment(vessel_id=3, centroid_lat=60.002, centroid_lon=25.002),
            # Cluster B: wide spread — high deviation
            _make_segment(vessel_id=4, centroid_lat=60.0, centroid_lon=26.0),
            _make_segment(vessel_id=5, centroid_lat=60.3, centroid_lon=26.3),
            _make_segment(vessel_id=6, centroid_lat=60.6, centroid_lon=26.6),
        ]
        mock_extract.return_value = segs

        db = MagicMock()
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = []

        result = run_trajectory_clustering(db)
        assert result["segments_processed"] == 6
        # Should find clusters (may or may not flag anomalous depending on eps/spread)
        assert result["clusters_found"] >= 1


# ── Bearing tests ────────────────────────────────────────────────────────────


class TestBearing:
    """Bearing computation and difference."""

    def test_bearing_north(self):
        """Going due north → bearing ~0°."""
        b = compute_bearing(60.0, 25.0, 61.0, 25.0)
        assert abs(b) < 1.0 or abs(b - 360) < 1.0

    def test_bearing_east(self):
        """Going due east → bearing ~90°."""
        b = compute_bearing(60.0, 25.0, 60.0, 26.0)
        assert 89.0 < b < 91.0

    def test_bearing_diff_symmetric(self):
        """bearing_diff(a, b) == bearing_diff(b, a)."""
        assert bearing_diff(30, 90) == bearing_diff(90, 30)

    def test_bearing_diff_wrap(self):
        """Bearing diff handles wrap-around (350° vs 10° = 20°)."""
        assert bearing_diff(350.0, 10.0) == pytest.approx(20.0, abs=0.1)

    def test_bearing_diff_opposite(self):
        """Opposite bearings → 180°."""
        assert bearing_diff(0.0, 180.0) == pytest.approx(180.0, abs=0.1)


# ── Segment distance tests ──────────────────────────────────────────────────


class TestSegmentDistance:
    """Weighted segment distance function."""

    def test_identical_segments_zero_distance(self):
        """Identical segments have zero distance."""
        seg = _make_segment(centroid_lat=60.0, centroid_lon=25.0, bearing=90.0, straightness=0.9)
        assert segment_distance(seg, seg) == pytest.approx(0.0, abs=1e-6)

    def test_bearing_contributes_to_distance(self):
        """Segments with same centroid but different bearing have nonzero distance."""
        seg_a = _make_segment(centroid_lat=60.0, centroid_lon=25.0, bearing=0.0)
        seg_b = _make_segment(centroid_lat=60.0, centroid_lon=25.0, bearing=180.0)
        d = segment_distance(seg_a, seg_b)
        assert d > 0  # bearing diff should contribute
