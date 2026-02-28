"""Tests for H1: Data freshness monitoring endpoint."""
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta


class TestDataFreshness:
    def test_returns_expected_keys(self, api_client, mock_db):
        mock_db.query.return_value.scalar.return_value = None
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        resp = api_client.get("/api/v1/health/data-freshness")
        assert resp.status_code == 200
        data = resp.json()
        assert "latest_ais_utc" in data
        assert "staleness_minutes" in data
        assert "vessels_updated_last_1h" in data
        assert "vessels_updated_last_24h" in data

    def test_empty_db_returns_null_latest(self, api_client, mock_db):
        mock_db.query.return_value.scalar.return_value = None
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        resp = api_client.get("/api/v1/health/data-freshness")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latest_ais_utc"] is None
        assert data["staleness_minutes"] is None

    def test_staleness_computation(self, api_client, mock_db):
        # Mock: latest AIS was 120 minutes ago
        now = datetime.now(timezone.utc)
        latest = now - timedelta(minutes=120)
        # Remove tzinfo for the mock (DB returns naive datetime)
        latest_naive = latest.replace(tzinfo=None)

        call_count = [0]
        def scalar_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return latest_naive
            return 5
        mock_db.query.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.filter.return_value.scalar.return_value = 5

        resp = api_client.get("/api/v1/health/data-freshness")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latest_ais_utc"] is not None
        # staleness should be approximately 120 (within some tolerance)
        assert data["staleness_minutes"] is not None
        assert data["staleness_minutes"] >= 118  # allow 2-min clock drift
