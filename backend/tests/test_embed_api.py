"""Tests for embeddable widget API endpoints (/embed/*)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_embed import _require_embed_api_key, _risk_tier
from app.api.routes_embed import router as embed_router
from app.database import get_db
from tests.conftest import make_mock_gap, make_mock_point, make_mock_vessel

# Build a minimal test app with just the embed router
_test_app = FastAPI()
_test_app.include_router(embed_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    session.query.return_value.options.return_value = session.query.return_value
    return session


@pytest.fixture
def embed_client(mock_db):
    """TestClient with DB override and embed API key auth bypassed."""

    def override_get_db():
        yield mock_db

    def override_embed_auth():
        return {"key_id": 1, "scope": "read_only"}

    _test_app.dependency_overrides[get_db] = override_get_db
    _test_app.dependency_overrides[_require_embed_api_key] = override_embed_auth
    with TestClient(_test_app) as client:
        yield client
    _test_app.dependency_overrides.clear()


@pytest.fixture
def noauth_client(mock_db):
    """TestClient with DB override but NO auth bypass — tests 401."""

    def override_get_db():
        yield mock_db

    _test_app.dependency_overrides[get_db] = override_get_db
    with TestClient(_test_app) as client:
        yield client
    _test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Unit: _risk_tier
# ---------------------------------------------------------------------------


class TestRiskTier:
    def test_critical(self):
        assert _risk_tier(80) == "critical"
        assert _risk_tier(100) == "critical"

    def test_high(self):
        assert _risk_tier(60) == "high"
        assert _risk_tier(79) == "high"

    def test_medium(self):
        assert _risk_tier(40) == "medium"

    def test_low(self):
        assert _risk_tier(20) == "low"

    def test_minimal(self):
        assert _risk_tier(0) == "minimal"
        assert _risk_tier(19) == "minimal"

    def test_none(self):
        assert _risk_tier(None) == "unknown"


# ---------------------------------------------------------------------------
# Auth: API key required
# ---------------------------------------------------------------------------


class TestEmbedAuth:
    def test_no_api_key_returns_401(self, noauth_client):
        resp = noauth_client.get("/api/v1/embed/vessel/1/summary")
        assert resp.status_code == 401

    def test_invalid_api_key_returns_401(self, noauth_client):
        resp = noauth_client.get(
            "/api/v1/embed/vessel/1/summary",
            headers={"X-API-Key": "bad-key"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /embed/vessel/{id}/summary
# ---------------------------------------------------------------------------


class TestEmbedSummary:
    def test_vessel_not_found_404(self, embed_client, mock_db):
        resp = embed_client.get("/api/v1/embed/vessel/999/summary")
        assert resp.status_code == 404

    def test_returns_correct_shape(self, embed_client, mock_db):
        vessel = make_mock_vessel(
            vessel_id=1,
            mmsi="123456789",
            name="SHADOW TANKER",
            imo="1234567",
            flag="PA",
            vessel_type="Oil Tanker",
            watchlist_stub_score=None,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            None
        )

        resp = embed_client.get("/api/v1/embed/vessel/1/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vessel_id"] == 1
        assert data["name"] == "SHADOW TANKER"
        assert data["mmsi"] == "123456789"
        assert "risk_score" in data
        assert "risk_tier" in data
        assert "on_watchlist" in data

    def test_summary_with_risk_score(self, embed_client, mock_db):
        vessel = make_mock_vessel(vessel_id=1, watchlist_stub_score=None)
        gap = make_mock_gap(risk_score=75)
        # First call: vessel lookup, second: watchlist check
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            vessel,
            None,  # watchlist check
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            gap
        )

        resp = embed_client.get("/api/v1/embed/vessel/1/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["risk_score"] == 75
        assert data["risk_tier"] == "high"


# ---------------------------------------------------------------------------
# GET /embed/vessel/{id}/timeline
# ---------------------------------------------------------------------------


class TestEmbedTimeline:
    def test_vessel_not_found_404(self, embed_client, mock_db):
        resp = embed_client.get("/api/v1/embed/vessel/999/timeline")
        assert resp.status_code == 404

    def test_empty_timeline(self, embed_client, mock_db):
        vessel = make_mock_vessel(vessel_id=1)
        mock_db.query.return_value.filter.return_value.first.return_value = vessel

        resp = embed_client.get("/api/v1/embed/vessel/1/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vessel_id"] == 1
        assert data["items"] == []
        assert data["count"] == 0

    def test_timeline_limit_param(self, embed_client, mock_db):
        vessel = make_mock_vessel(vessel_id=1)
        gap1 = make_mock_gap(
            gap_event_id=1,
            risk_score=50,
            duration_minutes=120,
            gap_start=datetime(2026, 3, 10, tzinfo=UTC),
            status="new",
        )
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            gap1
        ]

        resp = embed_client.get("/api/v1/embed/vessel/1/timeline?limit=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] <= 1


# ---------------------------------------------------------------------------
# GET /embed/vessel/{id}/risk
# ---------------------------------------------------------------------------


class TestEmbedRisk:
    def test_vessel_not_found_404(self, embed_client, mock_db):
        resp = embed_client.get("/api/v1/embed/vessel/999/risk")
        assert resp.status_code == 404

    def test_risk_breakdown_shape(self, embed_client, mock_db):
        vessel = make_mock_vessel(vessel_id=1, watchlist_stub_score=None, watchlist_stub_breakdown=None)
        gap = make_mock_gap(
            risk_score=85,
            risk_breakdown_json={
                "flag_risk": 20,
                "gap_duration": 15,
                "dark_zone": 25,
                "impossible_speed": 10,
                "corridor_proximity": 15,
                "pi_coverage": 0,
            },
        )
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            gap
        )

        resp = embed_client.get("/api/v1/embed/vessel/1/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["risk_score"] == 85
        assert data["risk_tier"] == "critical"
        assert len(data["top_signals"]) <= 5
        # Signals should be sorted by value desc
        values = [s["value"] for s in data["top_signals"]]
        assert values == sorted(values, reverse=True)

    def test_risk_no_gaps_fallback_stub(self, embed_client, mock_db):
        vessel = make_mock_vessel(
            vessel_id=1,
            watchlist_stub_score=45,
            watchlist_stub_breakdown={"flag_risk": 25, "age": 20},
        )
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            None
        )

        resp = embed_client.get("/api/v1/embed/vessel/1/risk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["risk_score"] == 45
        assert data["risk_tier"] == "medium"
        assert len(data["top_signals"]) == 2


# ---------------------------------------------------------------------------
# GET /embed/vessel/{id}/position
# ---------------------------------------------------------------------------


class TestEmbedPosition:
    def test_vessel_not_found_404(self, embed_client, mock_db):
        resp = embed_client.get("/api/v1/embed/vessel/999/position")
        assert resp.status_code == 404

    def test_position_returns_lat_lon(self, embed_client, mock_db):
        vessel = make_mock_vessel(vessel_id=1)
        point = make_mock_point(
            vessel_id=1,
            lat=59.123,
            lon=28.456,
            ts=datetime(2026, 3, 10, 12, 0, tzinfo=UTC),
        )
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            point
        )

        resp = embed_client.get("/api/v1/embed/vessel/1/position")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lat"] == 59.123
        assert data["lon"] == 28.456
        assert data["timestamp"] is not None

    def test_position_no_ais_points(self, embed_client, mock_db):
        vessel = make_mock_vessel(vessel_id=1)
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            None
        )

        resp = embed_client.get("/api/v1/embed/vessel/1/position")
        assert resp.status_code == 200
        data = resp.json()
        assert data["lat"] is None
        assert data["lon"] is None
        assert data["timestamp"] is None
