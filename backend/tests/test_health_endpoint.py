"""Tests for health check endpoints.

Verifies both the root /health and the API-prefixed /api/v1/health endpoints
return expected shapes and status codes.

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock


class TestRootHealth:
    """GET /health — the non-API-prefixed health check in main.py."""

    def test_returns_200(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200

    def test_returns_status_ok(self, api_client):
        data = api_client.get("/health").json()
        assert data["status"] == "ok"

    def test_returns_version(self, api_client):
        data = api_client.get("/health").json()
        assert "version" in data
        assert data["version"] == "0.1.0"

    def test_response_is_json(self, api_client):
        resp = api_client.get("/health")
        assert "application/json" in resp.headers.get("content-type", "")


class TestAPIHealth:
    """GET /api/v1/health — the API-prefixed health check with DB latency."""

    def test_returns_200(self, api_client, mock_db):
        resp = api_client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_returns_status_ok(self, api_client, mock_db):
        data = api_client.get("/api/v1/health").json()
        assert data["status"] == "ok"

    def test_returns_database_status(self, api_client, mock_db):
        data = api_client.get("/api/v1/health").json()
        assert "database" in data
        assert "status" in data["database"]

    def test_returns_database_latency(self, api_client, mock_db):
        data = api_client.get("/api/v1/health").json()
        assert "latency_ms" in data["database"]
        assert isinstance(data["database"]["latency_ms"], (int, float))

    def test_database_status_is_ok(self, api_client, mock_db):
        """When DB is reachable (mock), status should be ok."""
        data = api_client.get("/api/v1/health").json()
        assert data["database"]["status"] == "ok"

    def test_returns_version_field(self, api_client, mock_db):
        data = api_client.get("/api/v1/health").json()
        assert "version" in data


class TestHealthResponseShape:
    """Verify the exact shape of health responses for integration monitoring."""

    def test_root_health_shape(self, api_client):
        data = api_client.get("/health").json()
        expected_keys = {"status", "version"}
        assert expected_keys.issubset(set(data.keys()))

    def test_api_health_shape(self, api_client, mock_db):
        data = api_client.get("/api/v1/health").json()
        expected_keys = {"status", "version", "database"}
        assert expected_keys.issubset(set(data.keys()))

    def test_api_health_database_shape(self, api_client, mock_db):
        data = api_client.get("/api/v1/health").json()
        db_info = data["database"]
        assert "status" in db_info
        assert "latency_ms" in db_info
