"""Tests for GPS Jamming Zone Dynamic Detection.

Covers: DBSCAN clustering, convex hull computation, IoU merge logic,
decay transitions, GeoJSON output, API endpoints, feature flag gating,
and edge cases.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.jamming_zone import JammingZone, JammingZoneGap
from app.models.gap_event import AISGapEvent
from app.modules.jamming_zone_detector import (
    CONVEX_HULL_BUFFER_DEG,
    DECAY_FACTOR_PER_DAY,
    DECAY_START_DAYS,
    EXPIRE_DAYS,
    GapPoint,
    IOU_MERGE_THRESHOLD,
    MIN_VESSELS,
    SPATIAL_EPS_DEG,
    TEMPORAL_EPS_HOURS,
    _compute_convex_hull_wkt,
    _compute_iou,
    _compute_radius_nm,
    _st_distance,
    _st_neighbors,
    apply_zone_decay,
    get_jamming_zone,
    get_jamming_zones,
    get_jamming_zones_geojson,
    haversine_nm,
    run_jamming_detection,
    st_dbscan,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def db():
    """In-memory SQLite session with jamming zone + gap event tables."""
    engine = create_engine("sqlite:///:memory:")

    # Enable FK enforcement for SQLite
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_gap_point(
    gap_id: int, vessel_id: int, lat: float, lon: float, hours_ago: float = 0
) -> GapPoint:
    """Helper to create a GapPoint."""
    ts = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC) - timedelta(hours=hours_ago)
    return GapPoint(gap_event_id=gap_id, vessel_id=vessel_id, lat=lat, lon=lon, timestamp=ts)


def _make_gap_event(
    db: Session,
    gap_id: int,
    vessel_id: int,
    lat: float,
    lon: float,
    hours_ago: float = 0,
) -> AISGapEvent:
    """Insert a gap event into the test DB."""
    ts = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC) - timedelta(hours=hours_ago)
    gap = AISGapEvent(
        gap_event_id=gap_id,
        vessel_id=vessel_id,
        gap_start_utc=ts,
        gap_end_utc=ts + timedelta(hours=2),
        duration_minutes=120,
        gap_off_lat=lat,
        gap_off_lon=lon,
        is_feed_outage=False,
        status="new",
    )
    db.add(gap)
    db.flush()
    return gap


# ── Haversine tests ──────────────────────────────────────────────────────────


def test_haversine_zero_distance():
    assert haversine_nm(60.0, 25.0, 60.0, 25.0) == 0.0


def test_haversine_known_distance():
    # London (51.5, -0.1) to Paris (48.85, 2.35) ≈ 188nm
    dist = haversine_nm(51.5, -0.1, 48.85, 2.35)
    assert 180 < dist < 200


# ── ST-distance tests ────────────────────────────────────────────────────────


def test_st_distance_same_point():
    p = _make_gap_point(1, 1, 60.0, 25.0, 0)
    s_dist, t_dist = _st_distance(p, p)
    assert s_dist == 0.0
    assert t_dist == 0.0


def test_st_distance_spatial_only():
    a = _make_gap_point(1, 1, 60.0, 25.0, 0)
    b = _make_gap_point(2, 2, 60.5, 25.0, 0)
    s_dist, t_dist = _st_distance(a, b)
    assert s_dist == pytest.approx(0.5, abs=0.01)
    assert t_dist == 0.0


def test_st_distance_temporal_only():
    a = _make_gap_point(1, 1, 60.0, 25.0, 0)
    b = _make_gap_point(2, 2, 60.0, 25.0, 3)  # 3 hours apart
    s_dist, t_dist = _st_distance(a, b)
    assert s_dist == 0.0
    assert t_dist == pytest.approx(3.0, abs=0.01)


# ── ST-neighbors tests ───────────────────────────────────────────────────────


def test_st_neighbors_finds_close_points():
    points = [
        _make_gap_point(1, 1, 60.0, 25.0, 0),
        _make_gap_point(2, 2, 60.1, 25.1, 0.5),  # close
        _make_gap_point(3, 3, 65.0, 30.0, 0),  # far
    ]
    neighbors = _st_neighbors(points, 0, SPATIAL_EPS_DEG, TEMPORAL_EPS_HOURS)
    assert 0 in neighbors
    assert 1 in neighbors
    assert 2 not in neighbors


# ── DBSCAN tests ─────────────────────────────────────────────────────────────


def test_st_dbscan_single_cluster():
    """Three close points from different vessels should form one cluster."""
    points = [
        _make_gap_point(1, 1, 60.0, 25.0, 0),
        _make_gap_point(2, 2, 60.1, 25.1, 0.5),
        _make_gap_point(3, 3, 60.05, 25.05, 1.0),
    ]
    labels = st_dbscan(points, spatial_eps=SPATIAL_EPS_DEG, temporal_eps=TEMPORAL_EPS_HOURS, min_points=3)
    # All should be in the same cluster
    assert labels[0] == labels[1] == labels[2]
    assert labels[0] >= 0


def test_st_dbscan_noise_when_too_few():
    """Two points don't meet min_points=3."""
    points = [
        _make_gap_point(1, 1, 60.0, 25.0, 0),
        _make_gap_point(2, 2, 60.1, 25.1, 0.5),
    ]
    labels = st_dbscan(points, spatial_eps=SPATIAL_EPS_DEG, temporal_eps=TEMPORAL_EPS_HOURS, min_points=3)
    assert all(l == -1 for l in labels)


def test_st_dbscan_two_clusters():
    """Two separated groups should form two clusters."""
    points = [
        # Cluster A (60N, 25E)
        _make_gap_point(1, 1, 60.0, 25.0, 0),
        _make_gap_point(2, 2, 60.1, 25.1, 0.5),
        _make_gap_point(3, 3, 60.05, 25.05, 1.0),
        # Cluster B (50N, 10E) — far away
        _make_gap_point(4, 4, 50.0, 10.0, 0),
        _make_gap_point(5, 5, 50.1, 10.1, 0.5),
        _make_gap_point(6, 6, 50.05, 10.05, 1.0),
    ]
    labels = st_dbscan(points, spatial_eps=SPATIAL_EPS_DEG, temporal_eps=TEMPORAL_EPS_HOURS, min_points=3)
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]
    assert labels[0] != labels[3]


def test_st_dbscan_temporal_separation():
    """Points spatially close but temporally far should not cluster."""
    points = [
        _make_gap_point(1, 1, 60.0, 25.0, 0),
        _make_gap_point(2, 2, 60.1, 25.1, 0),
        _make_gap_point(3, 3, 60.05, 25.05, 10),  # 10 hours apart
    ]
    labels = st_dbscan(points, spatial_eps=SPATIAL_EPS_DEG, temporal_eps=TEMPORAL_EPS_HOURS, min_points=3)
    # Should all be noise since the third point is too far temporally
    assert all(l == -1 for l in labels)


# ── Convex hull tests ────────────────────────────────────────────────────────


def test_convex_hull_wkt_polygon():
    lats = [60.0, 60.5, 60.25]
    lons = [25.0, 25.0, 25.5]
    wkt = _compute_convex_hull_wkt(lats, lons)
    assert wkt is not None
    assert "POLYGON" in wkt


def test_convex_hull_two_points_buffered():
    """Two points should still produce a polygon via buffer."""
    lats = [60.0, 60.5]
    lons = [25.0, 25.0]
    wkt = _compute_convex_hull_wkt(lats, lons)
    assert wkt is not None
    assert "POLYGON" in wkt


def test_convex_hull_single_point():
    wkt = _compute_convex_hull_wkt([60.0], [25.0])
    assert wkt is not None
    assert "POLYGON" in wkt


# ── Radius tests ─────────────────────────────────────────────────────────────


def test_compute_radius_single_point():
    assert _compute_radius_nm([60.0], [25.0]) == 0.0


def test_compute_radius_multiple_points():
    lats = [60.0, 60.5, 60.25]
    lons = [25.0, 25.5, 25.0]
    r = _compute_radius_nm(lats, lons)
    assert r > 0


# ── IoU tests ────────────────────────────────────────────────────────────────


def test_iou_identical():
    wkt = "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))"
    iou = _compute_iou(wkt, wkt)
    assert iou == pytest.approx(1.0, abs=0.01)


def test_iou_no_overlap():
    wkt_a = "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))"
    wkt_b = "POLYGON ((10 10, 11 10, 11 11, 10 11, 10 10))"
    iou = _compute_iou(wkt_a, wkt_b)
    assert iou == pytest.approx(0.0, abs=0.01)


def test_iou_partial_overlap():
    wkt_a = "POLYGON ((0 0, 2 0, 2 2, 0 2, 0 0))"
    wkt_b = "POLYGON ((1 0, 3 0, 3 2, 1 2, 1 0))"
    iou = _compute_iou(wkt_a, wkt_b)
    # Overlap area = 2, Union area = 6, IoU = 1/3
    assert 0.3 < iou < 0.4


def test_iou_none_geometry():
    assert _compute_iou(None, "POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))") == 0.0
    assert _compute_iou("POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))", None) == 0.0


# ── Decay tests ──────────────────────────────────────────────────────────────


def test_decay_active_to_decaying(db):
    """Zone with last gap > 7 days ago should transition to decaying."""
    now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)
    zone = JammingZone(
        centroid_lat=60.0,
        centroid_lon=25.0,
        confidence=1.0,
        vessel_count=3,
        gap_count=5,
        status="active",
        detection_window_hours=168,
        last_gap_at=now - timedelta(days=10),
        first_detected_at=now - timedelta(days=20),
    )
    db.add(zone)
    db.commit()

    result = apply_zone_decay(db, now)
    assert result["decayed"] == 1
    assert result["expired"] == 0

    db.refresh(zone)
    assert zone.status == "decaying"
    assert zone.confidence < 1.0


def test_decay_to_expired(db):
    """Zone with last gap > 30 days ago should transition to expired."""
    now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)
    zone = JammingZone(
        centroid_lat=60.0,
        centroid_lon=25.0,
        confidence=0.5,
        vessel_count=3,
        gap_count=5,
        status="active",
        detection_window_hours=168,
        last_gap_at=now - timedelta(days=35),
        first_detected_at=now - timedelta(days=60),
    )
    db.add(zone)
    db.commit()

    result = apply_zone_decay(db, now)
    assert result["expired"] == 1

    db.refresh(zone)
    assert zone.status == "expired"
    assert zone.confidence == 0.0


def test_decay_no_change_recent(db):
    """Zone with recent gap should stay active."""
    now = datetime(2026, 3, 14, 12, 0, 0, tzinfo=UTC)
    zone = JammingZone(
        centroid_lat=60.0,
        centroid_lon=25.0,
        confidence=1.0,
        vessel_count=3,
        gap_count=5,
        status="active",
        detection_window_hours=168,
        last_gap_at=now - timedelta(days=2),
        first_detected_at=now - timedelta(days=5),
    )
    db.add(zone)
    db.commit()

    result = apply_zone_decay(db, now)
    assert result["decayed"] == 0
    assert result["expired"] == 0

    db.refresh(zone)
    assert zone.status == "active"
    assert zone.confidence == 1.0


# ── Feature flag gating ─────────────────────────────────────────────────────


def test_disabled_returns_early(db):
    """Detection should return immediately when disabled."""
    with patch("app.modules.jamming_zone_detector.settings") as mock_settings:
        mock_settings.JAMMING_DETECTION_ENABLED = False
        result = run_jamming_detection(db)
        assert result["disabled"] is True
        assert result["zones_created"] == 0


def test_enabled_flag(db):
    """Detection should run when enabled (even if no gaps)."""
    with patch("app.modules.jamming_zone_detector.settings") as mock_settings:
        mock_settings.JAMMING_DETECTION_ENABLED = True
        result = run_jamming_detection(db)
        assert "disabled" not in result
        assert result["zones_created"] == 0
        assert result["gaps_processed"] == 0


# ── Query helper tests ───────────────────────────────────────────────────────


def test_get_jamming_zones_empty(db):
    zones = get_jamming_zones(db)
    assert zones == []


def test_get_jamming_zone_not_found(db):
    assert get_jamming_zone(db, 999) is None


def test_get_jamming_zones_filter_by_status(db):
    db.add(
        JammingZone(
            centroid_lat=60.0, centroid_lon=25.0, confidence=1.0,
            vessel_count=3, gap_count=5, status="active",
            detection_window_hours=168,
        )
    )
    db.add(
        JammingZone(
            centroid_lat=50.0, centroid_lon=10.0, confidence=0.1,
            vessel_count=2, gap_count=3, status="expired",
            detection_window_hours=168,
        )
    )
    db.commit()

    active = get_jamming_zones(db, status="active")
    assert len(active) == 1
    assert active[0]["status"] == "active"

    expired = get_jamming_zones(db, status="expired")
    assert len(expired) == 1
    assert expired[0]["status"] == "expired"


def test_get_jamming_zone_by_id(db):
    zone = JammingZone(
        centroid_lat=60.0, centroid_lon=25.0, confidence=1.0,
        vessel_count=3, gap_count=5, status="active",
        detection_window_hours=168,
        evidence_json=json.dumps({"test": True}),
    )
    db.add(zone)
    db.commit()

    result = get_jamming_zone(db, zone.zone_id)
    assert result is not None
    assert result["centroid_lat"] == 60.0
    assert result["evidence"]["test"] is True


# ── GeoJSON output ───────────────────────────────────────────────────────────


def test_geojson_empty(db):
    geojson = get_jamming_zones_geojson(db)
    assert geojson["type"] == "FeatureCollection"
    assert geojson["features"] == []


def test_geojson_with_zone(db):
    zone = JammingZone(
        centroid_lat=60.0,
        centroid_lon=25.0,
        confidence=0.8,
        vessel_count=5,
        gap_count=10,
        status="active",
        detection_window_hours=168,
        geometry="POLYGON ((24 59, 26 59, 26 61, 24 61, 24 59))",
        evidence_json=json.dumps({"cluster_label": 0}),
    )
    db.add(zone)
    db.commit()

    geojson = get_jamming_zones_geojson(db)
    assert len(geojson["features"]) == 1
    feat = geojson["features"][0]
    assert feat["type"] == "Feature"
    assert feat["geometry"] is not None
    assert feat["properties"]["zone_id"] == zone.zone_id
    assert feat["properties"]["status"] == "active"
    assert feat["properties"]["vessel_count"] == 5


# ── API endpoint tests ───────────────────────────────────────────────────────


def _make_api_test_app():
    """Create a FastAPI test app with in-memory DB for API tests."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from app.api.routes_jamming_zones import router
    from app.models.jamming_zone import JammingZone, JammingZoneGap  # noqa: F811 ensure registered

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    def _override_db():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    from app.database import get_db

    app.dependency_overrides[get_db] = _override_db

    client = TestClient(app)
    return client, engine


def test_api_list_zones():
    """Test the list endpoint via FastAPI test client."""
    client, engine = _make_api_test_app()
    resp = client.get("/detect/jamming-zones")
    assert resp.status_code == 200
    assert resp.json() == []
    engine.dispose()


def test_api_post_disabled():
    """POST should return 400 when feature is disabled."""
    client, engine = _make_api_test_app()

    with patch("app.api.routes_jamming_zones.settings") as mock_settings:
        mock_settings.JAMMING_DETECTION_ENABLED = False
        resp = client.post("/detect/jamming-zones")
        assert resp.status_code == 400

    engine.dispose()


def test_api_get_zone_not_found():
    """GET single zone should return 404 for non-existent ID."""
    client, engine = _make_api_test_app()
    resp = client.get("/detect/jamming-zones/9999")
    assert resp.status_code == 404
    engine.dispose()


def test_api_geojson_endpoint():
    """GET geojson should return valid FeatureCollection."""
    client, engine = _make_api_test_app()
    resp = client.get("/detect/jamming-zones/geojson")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    engine.dispose()


# ── Model tests ──────────────────────────────────────────────────────────────


def test_jamming_zone_model_defaults(db):
    zone = JammingZone(
        centroid_lat=60.0,
        centroid_lon=25.0,
        vessel_count=3,
        gap_count=5,
        detection_window_hours=168,
    )
    db.add(zone)
    db.commit()

    db.refresh(zone)
    assert zone.status == "active"
    assert zone.confidence == 1.0
    assert zone.radius_nm == 0.0
    assert zone.zone_id is not None


def test_jamming_zone_gap_link(db):
    zone = JammingZone(
        centroid_lat=60.0,
        centroid_lon=25.0,
        vessel_count=3,
        gap_count=5,
        detection_window_hours=168,
    )
    db.add(zone)
    db.flush()

    link = JammingZoneGap(zone_id=zone.zone_id, gap_event_id=42)
    db.add(link)
    db.commit()

    db.refresh(zone)
    assert len(zone.gap_links) == 1
    assert zone.gap_links[0].gap_event_id == 42
