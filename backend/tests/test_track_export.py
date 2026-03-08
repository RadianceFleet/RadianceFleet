"""Tests for vessel track export (GeoJSON and KML)."""

import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.modules.track_export import export_track_geojson, export_track_kml
from tests.conftest import make_mock_point

# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_db_with_points(points):
    """Return a MagicMock db session whose AISPoint query returns the given points."""
    db = MagicMock()
    q = db.query.return_value
    q.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = points
    q.filter.return_value.filter.return_value.order_by.return_value.all.return_value = points
    q.filter.return_value.order_by.return_value.all.return_value = points
    return db


# ── GeoJSON Tests ────────────────────────────────────────────────────────────


def test_geojson_feature_collection_structure():
    """GeoJSON output has valid FeatureCollection structure."""
    pts = [make_mock_point(lat=55.0, lon=12.5)]
    db = _mock_db_with_points(pts)
    result = export_track_geojson(db, vessel_id=1)

    assert result["type"] == "FeatureCollection"
    assert isinstance(result["features"], list)
    assert len(result["features"]) == 1
    feature = result["features"][0]
    assert feature["type"] == "Feature"
    assert "geometry" in feature
    assert "properties" in feature


def test_geojson_coordinate_order_lon_lat():
    """Coordinates must be [lon, lat] per RFC 7946."""
    pts = [make_mock_point(lat=55.0, lon=12.5)]
    db = _mock_db_with_points(pts)
    result = export_track_geojson(db, vessel_id=1)

    coords = result["features"][0]["geometry"]["coordinates"]
    # Single point → LineString with one coordinate pair
    assert coords[0] == [12.5, 55.0]


def test_geojson_empty_points_null_geometry():
    """Empty AISPoint set returns null geometry."""
    db = _mock_db_with_points([])
    result = export_track_geojson(db, vessel_id=1)

    feature = result["features"][0]
    assert feature["geometry"] is None
    assert feature["properties"]["point_count"] == 0


def test_geojson_date_filtering():
    """date_from/date_to parameters cause filter calls on the query."""
    db = _mock_db_with_points([])
    export_track_geojson(db, vessel_id=1, date_from=date(2024, 1, 1), date_to=date(2024, 6, 30))

    # The query chain should have had filter() called (at least for vessel_id + date bounds)
    q = db.query.return_value
    # We expect multiple filter calls chained; verify filter was called
    assert q.filter.called


def test_geojson_properties_contain_point_data():
    """Properties include timestamps and point_data arrays."""
    ts = datetime(2024, 6, 15, 10, 30, tzinfo=UTC)
    pts = [make_mock_point(lat=55.0, lon=12.5, ts=ts, sog=10.5, cog=180.0)]
    db = _mock_db_with_points(pts)
    result = export_track_geojson(db, vessel_id=42)

    props = result["features"][0]["properties"]
    assert props["vessel_id"] == 42
    assert props["point_count"] == 1
    assert len(props["timestamps"]) == 1
    assert len(props["point_data"]) == 1
    assert props["point_data"][0]["sog"] == 10.5
    assert props["point_data"][0]["cog"] == 180.0


# ── KML Tests ────────────────────────────────────────────────────────────────


def test_kml_valid_xml():
    """KML output is valid XML."""
    pts = [make_mock_point(lat=55.0, lon=12.5)]
    db = _mock_db_with_points(pts)
    kml_str = export_track_kml(db, vessel_id=1, vessel_name="Test Vessel")

    # Should not raise
    root = ET.fromstring(kml_str)
    assert root.tag.endswith("kml")


def test_kml_vessel_name_with_ampersand():
    """Vessel name with & char is properly XML-escaped."""
    pts = [make_mock_point(lat=55.0, lon=12.5)]
    db = _mock_db_with_points(pts)
    kml_str = export_track_kml(db, vessel_id=1, vessel_name="SHIP & CO")

    # Must parse without error (& would break if not escaped)
    root = ET.fromstring(kml_str)
    # Find the name element — it should contain the literal text
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    doc = root.find("kml:Document", ns)
    name_el = doc.find("kml:name", ns)
    assert name_el.text == "SHIP & CO"


def test_kml_gx_track_elements():
    """KML contains gx:Track with when and gx:coord elements."""
    ts = datetime(2024, 6, 15, 10, 30, tzinfo=UTC)
    pts = [make_mock_point(lat=55.0, lon=12.5, ts=ts)]
    db = _mock_db_with_points(pts)
    kml_str = export_track_kml(db, vessel_id=1, vessel_name="Test")

    root = ET.fromstring(kml_str)
    gx_ns = "http://www.google.com/kml/ext/2.2"
    kml_ns = "http://www.opengis.net/kml/2.2"

    track = root.find(f".//{{{gx_ns}}}Track")
    assert track is not None

    # 'when' elements are in the default KML namespace
    whens = track.findall(f"{{{kml_ns}}}when")
    coords = track.findall(f"{{{gx_ns}}}coord")
    assert len(whens) == 1
    assert len(coords) == 1
    assert "12.5 55.0 0" in coords[0].text


# ── API Endpoint Tests ───────────────────────────────────────────────────────


@pytest.fixture
def api_client_with_mock():
    """TestClient with DB override returning a controllable mock session."""
    mock_db = MagicMock()
    # Default: vessel not found
    mock_db.query.return_value.filter.return_value.first.return_value = None

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, mock_db
    app.dependency_overrides.clear()


def test_api_geojson_404_nonexistent_vessel(api_client_with_mock):
    """GET /api/v1/vessels/{id}/track.geojson returns 404 for nonexistent vessel."""
    client, mock_db = api_client_with_mock
    resp = client.get("/api/v1/vessels/99999/track.geojson")
    assert resp.status_code == 404


def test_api_kml_404_nonexistent_vessel(api_client_with_mock):
    """GET /api/v1/vessels/{id}/track.kml returns 404 for nonexistent vessel."""
    client, mock_db = api_client_with_mock
    resp = client.get("/api/v1/vessels/99999/track.kml")
    assert resp.status_code == 404
