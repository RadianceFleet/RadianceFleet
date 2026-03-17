"""Tests for public dashboard endpoints (no auth required)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api._helpers import limiter
from app.api.routes_public import _clear_cache
from app.api.routes_public import router as public_router
from app.database import get_db

# Build a lightweight test app that includes only the public router.
# The main app's SPA catch-all would shadow late-added routes, so we
# use a dedicated app for isolation.
_test_app = FastAPI()
_test_app.state.limiter = limiter
_test_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
_test_app.include_router(public_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear TTL cache before and after each test."""
    _clear_cache()
    yield
    _clear_cache()


def _make_mock_db():
    """Build a MagicMock session that satisfies public dashboard queries."""
    session = MagicMock()
    mock_query = MagicMock()

    # Default chains
    mock_query.filter.return_value = mock_query
    mock_query.group_by.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.options.return_value = mock_query
    mock_query.all.return_value = []
    mock_query.scalar.return_value = 0

    session.query.return_value = mock_query
    return session


def _make_public_client(mock_db=None):
    """Create a TestClient for the public test app."""
    db = mock_db or _make_mock_db()

    def override_get_db():
        yield db

    _test_app.dependency_overrides[get_db] = override_get_db
    client = TestClient(_test_app, raise_server_exceptions=False)
    return client, db


@pytest.fixture
def public_client():
    client, db = _make_public_client()
    yield client
    _test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Dashboard endpoint tests
# ---------------------------------------------------------------------------


class TestPublicDashboard:
    def test_returns_expected_shape(self, public_client):
        """Dashboard response has all required top-level keys."""
        resp = public_client.get("/api/v1/public/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "vessel_count" in data
        assert "alert_counts" in data
        assert "detection_coverage" in data
        assert "recent_alerts" in data
        assert "trend_buckets" in data
        assert "detections_by_type" in data

    def test_alert_counts_has_tiers(self, public_client):
        """Alert counts include high, medium, low."""
        resp = public_client.get("/api/v1/public/dashboard")
        data = resp.json()
        assert "high" in data["alert_counts"]
        assert "medium" in data["alert_counts"]
        assert "low" in data["alert_counts"]

    def test_detection_coverage_shape(self, public_client):
        """Detection coverage has monitored_zones and active_corridors."""
        resp = public_client.get("/api/v1/public/dashboard")
        data = resp.json()
        cov = data["detection_coverage"]
        assert "monitored_zones" in cov
        assert "active_corridors" in cov

    def test_detections_by_type_shape(self, public_client):
        """Detections by type includes gap, spoofing, sts."""
        resp = public_client.get("/api/v1/public/dashboard")
        data = resp.json()
        dbt = data["detections_by_type"]
        assert "gap" in dbt
        assert "spoofing" in dbt
        assert "sts" in dbt

    def test_empty_database_returns_zeros(self, public_client):
        """Empty database returns zero counts gracefully."""
        resp = public_client.get("/api/v1/public/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vessel_count"] == 0
        assert data["alert_counts"]["high"] == 0
        assert data["alert_counts"]["medium"] == 0
        assert data["alert_counts"]["low"] == 0
        assert data["recent_alerts"] == []
        assert data["trend_buckets"] == []

    def test_no_auth_required(self):
        """Public endpoint works without any auth headers."""
        client, _ = _make_public_client()
        resp = client.get("/api/v1/public/dashboard")
        assert resp.status_code == 200
        _test_app.dependency_overrides.clear()

    def test_no_vessel_name_in_response(self, public_client):
        """Response never contains vessel names -- only MMSI suffixes."""
        resp = public_client.get("/api/v1/public/dashboard")
        data = resp.json()
        # No key named "name" or "vessel_name" should appear in recent_alerts
        for alert in data["recent_alerts"]:
            assert "name" not in alert
            assert "vessel_name" not in alert

    def test_no_full_mmsi_in_response(self, public_client):
        """Response never contains a full 9-digit MMSI."""
        resp = public_client.get("/api/v1/public/dashboard")
        data = resp.json()
        for alert in data["recent_alerts"]:
            suffix = alert.get("mmsi_suffix", "")
            assert len(suffix) <= 4, f"MMSI suffix too long: {suffix}"

    def test_cache_ttl_returns_cached(self):
        """Second call within 5 minutes returns cached data."""
        client, db = _make_public_client()
        resp1 = client.get("/api/v1/public/dashboard")
        assert resp1.status_code == 200
        # The second call should still succeed (from cache)
        resp2 = client.get("/api/v1/public/dashboard")
        assert resp2.status_code == 200
        assert resp1.json() == resp2.json()
        _test_app.dependency_overrides.clear()

    def test_cache_expiry(self):
        """Cache expires after TTL."""
        from app.api.routes_public import _cache

        # Manually set a stale entry
        _cache["dashboard"] = (time.monotonic() - 301, {"stale": True})

        client, _ = _make_public_client()
        resp = client.get("/api/v1/public/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        # Should NOT return the stale data
        assert "stale" not in data
        _test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Trends endpoint tests
# ---------------------------------------------------------------------------


class TestPublicTrends:
    def test_trends_returns_days(self, public_client):
        """Trends endpoint returns a 'days' list."""
        resp = public_client.get("/api/v1/public/trends")
        assert resp.status_code == 200
        data = resp.json()
        assert "days" in data
        assert isinstance(data["days"], list)

    def test_trends_no_auth_required(self):
        """Trends endpoint works without auth."""
        client, _ = _make_public_client()
        resp = client.get("/api/v1/public/trends")
        assert resp.status_code == 200
        _test_app.dependency_overrides.clear()

    def test_trends_empty_database(self, public_client):
        """Trends endpoint returns empty list for empty DB."""
        resp = public_client.get("/api/v1/public/trends")
        data = resp.json()
        assert data["days"] == []

    def test_trends_cache(self):
        """Trends endpoint uses 15-minute cache."""
        client, _ = _make_public_client()
        resp1 = client.get("/api/v1/public/trends")
        resp2 = client.get("/api/v1/public/trends")
        assert resp1.json() == resp2.json()
        _test_app.dependency_overrides.clear()

    def test_trends_cache_expiry(self):
        """Trends cache expires after 15 min."""
        from app.api.routes_public import _cache

        _cache["trends"] = (time.monotonic() - 901, {"stale": True})

        client, _ = _make_public_client()
        resp = client.get("/api/v1/public/trends")
        assert resp.status_code == 200
        data = resp.json()
        assert "stale" not in data
        _test_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Anonymisation helper tests
# ---------------------------------------------------------------------------


class TestAnonymisation:
    def test_anonymize_mmsi_normal(self):
        from app.api.routes_public import _anonymize_mmsi

        assert _anonymize_mmsi("123456789") == "6789"

    def test_anonymize_mmsi_short(self):
        from app.api.routes_public import _anonymize_mmsi

        assert _anonymize_mmsi("12") == "????"

    def test_anonymize_mmsi_none(self):
        from app.api.routes_public import _anonymize_mmsi

        assert _anonymize_mmsi(None) == "????"

    def test_tier_from_score_high(self):
        from app.api.routes_public import _tier_from_score

        assert _tier_from_score(80) == "high"

    def test_tier_from_score_medium(self):
        from app.api.routes_public import _tier_from_score

        assert _tier_from_score(50) == "medium"

    def test_tier_from_score_low(self):
        from app.api.routes_public import _tier_from_score

        assert _tier_from_score(20) == "low"

    def test_tier_from_score_none(self):
        from app.api.routes_public import _tier_from_score

        assert _tier_from_score(None) == "low"
