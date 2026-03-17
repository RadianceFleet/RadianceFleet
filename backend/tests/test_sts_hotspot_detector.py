"""Tests for STS Transfer Hotspot Detection.

Covers: haversine, DBSCAN clustering, temporal trend, corridor overlap,
full pipeline, edge cases, GeoJSON output, API endpoints, feature flag gating.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.sts_hotspot_detector import (
    SCORE_BASE,
    SCORE_CORRIDOR_BONUS,
    SCORE_GROWING_BONUS,
    SCORE_MAX,
    SCORE_PER_EVENT,
    _build_distance_matrix,
    _compute_centroid,
    _compute_radius_nm,
    _compute_risk_score,
    _compute_trend,
    _dbscan,
    get_hotspots_geojson,
    haversine_nm,
    run_hotspot_detection,
)
from app.utils.geo import _EARTH_RADIUS_NM as EARTH_RADIUS_NM

# ── Haversine tests ──────────────────────────────────────────────────────────


class TestHaversine:
    def test_same_point_returns_zero(self):
        assert haversine_nm(0.0, 0.0, 0.0, 0.0) == 0.0

    def test_known_distance_equator(self):
        """1 degree of longitude at equator ~ 60 NM."""
        d = haversine_nm(0.0, 0.0, 0.0, 1.0)
        assert 59.5 < d < 60.5

    def test_known_distance_latitude(self):
        """1 degree of latitude ~ 60 NM everywhere."""
        d = haversine_nm(0.0, 0.0, 1.0, 0.0)
        assert 59.5 < d < 60.5

    def test_symmetry(self):
        d1 = haversine_nm(10.0, 20.0, 30.0, 40.0)
        d2 = haversine_nm(30.0, 40.0, 10.0, 20.0)
        assert abs(d1 - d2) < 1e-10

    def test_high_latitude_longitude_compression(self):
        """At 60N, 1 degree longitude ~ 30 NM (cos(60) = 0.5)."""
        d = haversine_nm(60.0, 0.0, 60.0, 1.0)
        assert 29.5 < d < 30.5

    def test_antipodal_points(self):
        """Distance from pole to pole ~ pi * earth_radius."""
        d = haversine_nm(90.0, 0.0, -90.0, 0.0)
        expected = math.pi * EARTH_RADIUS_NM
        assert abs(d - expected) < 1.0


# ── Distance matrix tests ───────────────────────────────────────────────────


class TestDistanceMatrix:
    def test_single_point(self):
        matrix = _build_distance_matrix([(0.0, 0.0)])
        assert matrix == [[0.0]]

    def test_symmetric(self):
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        matrix = _build_distance_matrix(points)
        for i in range(3):
            for j in range(3):
                assert abs(matrix[i][j] - matrix[j][i]) < 1e-10

    def test_diagonal_zero(self):
        points = [(10.0, 20.0), (30.0, 40.0)]
        matrix = _build_distance_matrix(points)
        assert matrix[0][0] == 0.0
        assert matrix[1][1] == 0.0


# ── DBSCAN tests ─────────────────────────────────────────────────────────────


class TestDBSCAN:
    def test_all_noise_when_sparse(self):
        """Points far apart should all be noise."""
        points = [(0.0, 0.0), (10.0, 0.0), (0.0, 20.0)]
        matrix = _build_distance_matrix(points)
        labels = _dbscan(matrix, eps=10.0, min_samples=3)
        assert all(label == -1 for label in labels)

    def test_single_cluster(self):
        """Points close together should form one cluster."""
        points = [(0.0, 0.0), (0.01, 0.0), (0.0, 0.01), (0.01, 0.01)]
        matrix = _build_distance_matrix(points)
        labels = _dbscan(matrix, eps=10.0, min_samples=3)
        assert all(label == 0 for label in labels)

    def test_two_clusters(self):
        """Two groups of close points should form two clusters."""
        cluster_a = [(0.0, 0.0), (0.01, 0.0), (0.0, 0.01)]
        cluster_b = [(5.0, 5.0), (5.01, 5.0), (5.0, 5.01)]
        points = cluster_a + cluster_b
        matrix = _build_distance_matrix(points)
        labels = _dbscan(matrix, eps=10.0, min_samples=3)
        unique_labels = set(labels)
        assert -1 not in unique_labels
        assert len(unique_labels) == 2
        assert labels[0] == labels[1] == labels[2]
        assert labels[3] == labels[4] == labels[5]
        assert labels[0] != labels[3]

    def test_noise_and_cluster(self):
        """Mix of clustered and isolated points."""
        points = [(0.0, 0.0), (0.01, 0.0), (0.0, 0.01), (20.0, 20.0)]
        matrix = _build_distance_matrix(points)
        labels = _dbscan(matrix, eps=10.0, min_samples=3)
        assert labels[3] == -1
        assert labels[0] == labels[1] == labels[2]

    def test_empty_input(self):
        labels = _dbscan([], eps=10.0, min_samples=3)
        assert labels == []


# ── Temporal trend tests ─────────────────────────────────────────────────────


class TestTemporalTrend:
    def test_growing_trend(self):
        """More events in recent windows should be growing."""
        base = datetime(2024, 1, 1, tzinfo=UTC)
        timestamps = (
            [base + timedelta(days=5)]
            + [base + timedelta(days=35 + i) for i in range(5)]
            + [base + timedelta(days=65 + i) for i in range(10)]
        )
        trend, slope = _compute_trend(timestamps)
        assert trend == "growing"
        assert slope > 0

    def test_declining_trend(self):
        """More events in earlier windows should be declining."""
        base = datetime(2024, 1, 1, tzinfo=UTC)
        timestamps = (
            [base + timedelta(days=i) for i in range(10)]
            + [base + timedelta(days=35 + i) for i in range(5)]
            + [base + timedelta(days=65)]
        )
        trend, slope = _compute_trend(timestamps)
        assert trend == "declining"
        assert slope < 0

    def test_stable_trend(self):
        """Even distribution should be stable."""
        base = datetime(2024, 1, 1, tzinfo=UTC)
        timestamps = (
            [base + timedelta(days=i * 10) for i in range(3)]
            + [base + timedelta(days=30 + i * 10) for i in range(3)]
            + [base + timedelta(days=60 + i * 10) for i in range(3)]
        )
        trend, slope = _compute_trend(timestamps)
        assert trend == "stable"

    def test_single_event(self):
        ts = [datetime(2024, 1, 1, tzinfo=UTC)]
        trend, slope = _compute_trend(ts)
        assert trend == "stable"
        assert slope == 0.0

    def test_short_time_range(self):
        """Events within a single window should be stable."""
        base = datetime(2024, 1, 1, tzinfo=UTC)
        timestamps = [base + timedelta(days=i) for i in range(5)]
        trend, slope = _compute_trend(timestamps)
        assert trend == "stable"
        assert slope == 0.0


# ── Centroid and radius tests ────────────────────────────────────────────────


class TestClusterGeometry:
    def test_centroid_single_point(self):
        lat, lon = _compute_centroid([(10.0, 20.0)])
        assert lat == 10.0
        assert lon == 20.0

    def test_centroid_average(self):
        lat, lon = _compute_centroid([(0.0, 0.0), (2.0, 4.0)])
        assert lat == 1.0
        assert lon == 2.0

    def test_radius_single_point(self):
        r = _compute_radius_nm([(10.0, 20.0)], 10.0, 20.0)
        assert r == 0.0

    def test_radius_positive(self):
        points = [(0.0, 0.0), (0.1, 0.0)]
        clat, clon = _compute_centroid(points)
        r = _compute_radius_nm(points, clat, clon)
        assert r > 0


# ── Risk score tests ─────────────────────────────────────────────────────────


class TestRiskScore:
    def test_base_score(self):
        score = _compute_risk_score(0, "stable", None)
        assert score == SCORE_BASE

    def test_growing_bonus(self):
        score = _compute_risk_score(0, "growing", None)
        assert score == SCORE_BASE + SCORE_GROWING_BONUS

    def test_corridor_bonus(self):
        score = _compute_risk_score(0, "stable", 1)
        assert score == SCORE_BASE + SCORE_CORRIDOR_BONUS

    def test_max_cap(self):
        score = _compute_risk_score(1000, "growing", 1)
        assert score == SCORE_MAX

    def test_event_scaling(self):
        score = _compute_risk_score(5, "stable", None)
        assert score == SCORE_BASE + 5 * SCORE_PER_EVENT


# ── Corridor overlap tests ───────────────────────────────────────────────────


class TestCorridorOverlap:
    def test_point_inside_corridor(self):
        from app.modules.sts_hotspot_detector import _find_corridor_overlap

        mock_corridor = MagicMock()
        mock_corridor.corridor_id = 42
        mock_corridor.geometry = "POLYGON ((10 10, 10 20, 20 20, 20 10, 10 10))"

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [mock_corridor]

        result = _find_corridor_overlap(mock_db, 15.0, 15.0)
        assert result == 42

    def test_point_outside_corridor(self):
        from app.modules.sts_hotspot_detector import _find_corridor_overlap

        mock_corridor = MagicMock()
        mock_corridor.corridor_id = 42
        mock_corridor.geometry = "POLYGON ((10 10, 10 20, 20 20, 20 10, 10 10))"

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [mock_corridor]

        result = _find_corridor_overlap(mock_db, 0.0, 0.0)
        assert result is None

    def test_no_corridors(self):
        from app.modules.sts_hotspot_detector import _find_corridor_overlap

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []

        result = _find_corridor_overlap(mock_db, 15.0, 15.0)
        assert result is None

    def test_corridor_no_geometry(self):
        from app.modules.sts_hotspot_detector import _find_corridor_overlap

        mock_corridor = MagicMock()
        mock_corridor.corridor_id = 1
        mock_corridor.geometry = None

        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = [mock_corridor]

        result = _find_corridor_overlap(mock_db, 15.0, 15.0)
        assert result is None


# ── Feature flag gating ──────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_disabled_by_default(self):
        """When STS_HOTSPOT_ENABLED is False, detection returns disabled."""
        mock_db = MagicMock()
        with patch("app.modules.sts_hotspot_detector.getattr", return_value=False):
            result = run_hotspot_detection(mock_db)
        assert result.get("disabled") is True
        assert result["hotspots_found"] == 0


# ── Full pipeline tests ─────────────────────────────────────────────────────


def _make_sts_event(lat, lon, start_time, vessel_1_id=1, vessel_2_id=2):
    event = MagicMock()
    event.mean_lat = lat
    event.mean_lon = lon
    event.start_time_utc = start_time
    event.vessel_1_id = vessel_1_id
    event.vessel_2_id = vessel_2_id
    return event


def _enable_hotspot_settings():
    """Create a mock settings with STS_HOTSPOT_ENABLED=True."""
    mock_settings = MagicMock()
    mock_settings.STS_HOTSPOT_ENABLED = True
    return mock_settings


class TestFullPipeline:
    @patch("app.modules.sts_hotspot_detector._find_corridor_overlap", return_value=None)
    def test_pipeline_with_cluster(self, mock_corridor):
        """Three close events should form a hotspot."""
        mock_db = MagicMock()

        base = datetime(2024, 1, 1, tzinfo=UTC)
        events = [
            _make_sts_event(0.0, 0.0, base),
            _make_sts_event(0.01, 0.0, base + timedelta(days=1)),
            _make_sts_event(0.0, 0.01, base + timedelta(days=2)),
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = events
        mock_db.query.return_value = mock_query

        with patch("app.modules.sts_hotspot_detector.getattr", return_value=True):
            result = run_hotspot_detection(mock_db)
        assert result["hotspots_found"] >= 1
        assert result["events_processed"] == 3

    def test_pipeline_no_events(self):
        """No events should produce empty result."""
        mock_db = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = []
        mock_db.query.return_value = mock_query

        with patch("app.modules.sts_hotspot_detector.getattr", return_value=True):
            result = run_hotspot_detection(mock_db)
        assert result["hotspots_found"] == 0
        assert result["events_processed"] == 0

    @patch("app.modules.sts_hotspot_detector._find_corridor_overlap", return_value=None)
    def test_pipeline_all_same_location(self, mock_corridor):
        """All events at same location should form a single hotspot with radius 0."""
        mock_db = MagicMock()

        base = datetime(2024, 1, 1, tzinfo=UTC)
        events = [
            _make_sts_event(25.0, 35.0, base + timedelta(days=i))
            for i in range(5)
        ]

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = events
        mock_db.query.return_value = mock_query

        with patch("app.modules.sts_hotspot_detector.getattr", return_value=True):
            result = run_hotspot_detection(mock_db)
        assert result["hotspots_found"] == 1
        assert result["noise_events"] == 0

    def test_pipeline_single_event(self):
        """Single event is below min_samples — no hotspot."""
        mock_db = MagicMock()

        events = [_make_sts_event(0.0, 0.0, datetime(2024, 1, 1, tzinfo=UTC))]

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = events
        mock_db.query.return_value = mock_query

        with patch("app.modules.sts_hotspot_detector.getattr", return_value=True):
            result = run_hotspot_detection(mock_db)
        assert result["hotspots_found"] == 0
        assert result["events_processed"] == 1

    @patch("app.modules.sts_hotspot_detector._find_corridor_overlap", return_value=None)
    def test_pipeline_two_clusters(self, mock_corridor):
        """Two groups of distant events should produce two hotspots."""
        mock_db = MagicMock()

        base = datetime(2024, 1, 1, tzinfo=UTC)
        # Cluster A near (0,0)
        events_a = [
            _make_sts_event(0.0, 0.0, base + timedelta(days=i))
            for i in range(3)
        ]
        # Cluster B near (10,10) — ~850nm away
        events_b = [
            _make_sts_event(10.0, 10.0, base + timedelta(days=i))
            for i in range(3)
        ]
        all_events = events_a + events_b

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = all_events
        mock_db.query.return_value = mock_query

        with patch("app.modules.sts_hotspot_detector.getattr", return_value=True):
            result = run_hotspot_detection(mock_db)
        assert result["hotspots_found"] == 2
        assert result["events_processed"] == 6
        assert result["noise_events"] == 0


# ── GeoJSON output tests ────────────────────────────────────────────────────


class TestGeoJSON:
    def test_geojson_structure(self):
        mock_hotspot = MagicMock()
        mock_hotspot.hotspot_id = 1
        mock_hotspot.centroid_lat = 25.0
        mock_hotspot.centroid_lon = 35.0
        mock_hotspot.radius_nm = 5.0
        mock_hotspot.event_count = 10
        mock_hotspot.first_seen = datetime(2024, 1, 1, tzinfo=UTC)
        mock_hotspot.last_seen = datetime(2024, 6, 1, tzinfo=UTC)
        mock_hotspot.trend = "growing"
        mock_hotspot.trend_slope = 1.5
        mock_hotspot.corridor_id = None
        mock_hotspot.risk_score_component = 30.0
        mock_hotspot.evidence_json = json.dumps({"event_count": 10})

        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = [mock_hotspot]

        result = get_hotspots_geojson(mock_db)
        assert result["type"] == "FeatureCollection"
        assert len(result["features"]) == 1

        feature = result["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Point"
        assert feature["geometry"]["coordinates"] == [35.0, 25.0]
        assert feature["properties"]["hotspot_id"] == 1
        assert feature["properties"]["trend"] == "growing"

    def test_geojson_empty(self):
        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = []

        result = get_hotspots_geojson(mock_db)
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []

    def test_geojson_invalid_evidence_json(self):
        mock_hotspot = MagicMock()
        mock_hotspot.hotspot_id = 1
        mock_hotspot.centroid_lat = 10.0
        mock_hotspot.centroid_lon = 20.0
        mock_hotspot.radius_nm = 3.0
        mock_hotspot.event_count = 3
        mock_hotspot.first_seen = datetime(2024, 1, 1, tzinfo=UTC)
        mock_hotspot.last_seen = datetime(2024, 2, 1, tzinfo=UTC)
        mock_hotspot.trend = "stable"
        mock_hotspot.trend_slope = 0.0
        mock_hotspot.corridor_id = None
        mock_hotspot.risk_score_component = 16.0
        mock_hotspot.evidence_json = "not-valid-json{"

        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = [mock_hotspot]

        result = get_hotspots_geojson(mock_db)
        assert result["features"][0]["properties"]["evidence"] is None


# ── API endpoint tests ───────────────────────────────────────────────────────


class TestAPIEndpoints:
    """Test the standalone STS hotspots router by mounting it on a fresh FastAPI app."""

    @pytest.fixture
    def api_client(self):
        from app.api.routes_sts_hotspots import router as sts_hotspot_router

        test_app = FastAPI()
        test_app.include_router(sts_hotspot_router, prefix="/api/v1")

        mock_db = MagicMock()

        def override_get_db():
            yield mock_db

        def override_auth():
            return {"analyst_id": 1, "username": "test_admin", "role": "admin"}

        from app.auth import require_auth
        from app.database import get_db

        test_app.dependency_overrides[get_db] = override_get_db
        test_app.dependency_overrides[require_auth] = override_auth
        with TestClient(test_app) as client:
            yield client, mock_db

    def test_post_detect(self, api_client):
        client, mock_db = api_client
        with patch(
            "app.modules.sts_hotspot_detector.run_hotspot_detection",
            return_value={"hotspots_found": 0, "events_processed": 0, "disabled": True},
        ):
            resp = client.post("/api/v1/detect/sts-hotspots")
            assert resp.status_code == 200

    def test_get_list(self, api_client):
        client, mock_db = api_client
        with patch(
            "app.modules.sts_hotspot_detector.get_hotspots",
            return_value=[],
        ):
            resp = client.get("/api/v1/detect/sts-hotspots")
            assert resp.status_code == 200

    def test_get_single_not_found(self, api_client):
        client, mock_db = api_client
        with patch(
            "app.modules.sts_hotspot_detector.get_hotspot",
            return_value=None,
        ):
            resp = client.get("/api/v1/detect/sts-hotspots/999")
            assert resp.status_code == 404

    def test_get_geojson(self, api_client):
        client, mock_db = api_client
        with patch(
            "app.modules.sts_hotspot_detector.get_hotspots_geojson",
            return_value={"type": "FeatureCollection", "features": []},
        ):
            resp = client.get("/api/v1/detect/sts-hotspots/geojson")
            assert resp.status_code == 200
            data = resp.json()
            assert data["type"] == "FeatureCollection"

    def test_get_single_found(self, api_client):
        client, mock_db = api_client
        hotspot_data = {
            "hotspot_id": 1,
            "centroid_lat": 25.0,
            "centroid_lon": 35.0,
            "radius_nm": 5.0,
            "event_count": 10,
            "trend": "stable",
        }
        with patch(
            "app.modules.sts_hotspot_detector.get_hotspot",
            return_value=hotspot_data,
        ):
            resp = client.get("/api/v1/detect/sts-hotspots/1")
            assert resp.status_code == 200
            assert resp.json()["hotspot_id"] == 1


# ── Model tests ──────────────────────────────────────────────────────────────


class TestModel:
    def test_model_import(self):
        """StsHotspot model can be imported directly from its file."""
        from app.models.sts_hotspot import StsHotspot

        assert StsHotspot.__tablename__ == "sts_hotspots"

    def test_model_columns(self):
        from app.models.sts_hotspot import StsHotspot

        columns = {c.name for c in StsHotspot.__table__.columns}
        expected = {
            "hotspot_id",
            "centroid_lat",
            "centroid_lon",
            "radius_nm",
            "event_count",
            "first_seen",
            "last_seen",
            "trend",
            "trend_slope",
            "corridor_id",
            "risk_score_component",
            "evidence_json",
            "created_at",
        }
        assert expected.issubset(columns)

    def test_model_inherits_base(self):
        from app.models.base import Base
        from app.models.sts_hotspot import StsHotspot

        assert issubclass(StsHotspot, Base)
