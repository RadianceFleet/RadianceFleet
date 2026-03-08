"""Tests for PDF evidence card export."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db


def _make_gap(gap_event_id=1, vessel_id=1, status="under_review"):
    """Create a mock AISGapEvent."""
    g = MagicMock()
    g.gap_event_id = gap_event_id
    g.vessel_id = vessel_id
    g.gap_start_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    g.gap_end_utc = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    g.duration_minutes = 720
    g.corridor_id = None
    g.risk_score = 85
    g.risk_breakdown_json = {"gap_duration": 30, "corridor_risk": 20}
    g.status = status
    g.analyst_notes = "Test notes"
    g.max_plausible_distance_nm = 150.0
    g.actual_gap_distance_nm = 200.0
    g.velocity_plausibility_ratio = 1.33
    g.impossible_speed_flag = False
    return g


def _make_vessel(vessel_id=1):
    """Create a mock Vessel."""
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = "123456789"
    v.imo = "9876543"
    v.name = "TEST VESSEL"
    v.flag = "PA"
    v.vessel_type = "Crude Oil Tanker"
    v.ais_source = "aisstream"
    return v


def _setup_db(mock_db, gap=None, vessel=None):
    """Wire up mock_db.query().filter().first() to return gap/vessel/corridor."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel
    from app.models.corridor import Corridor
    from app.models.ais_point import AISPoint
    from app.models.satellite_check import SatelliteCheck

    def query_side_effect(model):
        q = MagicMock()

        def filter_side_effect(*args, **kwargs):
            f = MagicMock()

            def first_side_effect():
                if model is AISGapEvent:
                    return gap
                if model is Vessel:
                    return vessel
                if model is Corridor:
                    return None
                if model is AISPoint:
                    return None
                if model is SatelliteCheck:
                    return None
                return None

            f.first.return_value = first_side_effect()
            f.filter.return_value = f
            f.order_by.return_value = f
            return f

        q.filter.side_effect = filter_side_effect
        return q

    mock_db.query.side_effect = query_side_effect


@pytest.fixture
def mock_db():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    return session


@pytest.fixture
def api_client(mock_db):
    from app.auth import require_auth, require_senior_or_admin

    def override_get_db():
        yield mock_db

    def override_auth():
        return {"analyst_id": 1, "username": "test_admin", "role": "admin"}

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_auth] = override_auth
    app.dependency_overrides[require_senior_or_admin] = override_auth
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class TestPdfExportEndpoint:
    """Test POST /alerts/{id}/export?format=pdf"""

    def test_pdf_returns_valid_pdf(self, api_client, mock_db):
        """PDF export returns bytes starting with %PDF magic."""
        gap = _make_gap(status="under_review")
        vessel = _make_vessel()
        _setup_db(mock_db, gap=gap, vessel=vessel)

        resp = api_client.post("/api/v1/alerts/1/export?format=pdf")
        assert resp.status_code == 200
        assert resp.content[:5] == b"%PDF-"

    def test_pdf_content_type(self, api_client, mock_db):
        """PDF export sets application/pdf content type."""
        gap = _make_gap(status="documented")
        vessel = _make_vessel()
        _setup_db(mock_db, gap=gap, vessel=vessel)

        resp = api_client.post("/api/v1/alerts/1/export?format=pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"

    def test_pdf_status_new_rejected(self, api_client, mock_db):
        """Alert with status 'new' is rejected with 400."""
        gap = _make_gap(status="new")
        vessel = _make_vessel()
        _setup_db(mock_db, gap=gap, vessel=vessel)

        resp = api_client.post("/api/v1/alerts/1/export?format=pdf")
        assert resp.status_code == 400
        assert "analyst review" in resp.json()["detail"].lower()

    def test_pdf_not_found(self, api_client, mock_db):
        """Non-existent alert returns 400."""
        _setup_db(mock_db, gap=None, vessel=None)

        resp = api_client.post("/api/v1/alerts/999/export?format=pdf")
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()
