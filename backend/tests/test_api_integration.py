"""API integration tests — happy path for core endpoints.

Verifies that the main read-only API endpoints return 200 with valid
JSON when the database is empty (mock).  Uses the shared conftest
fixtures (mock_db, api_client).
"""


class TestHealthEndpoint:
    def test_health_returns_200(self, api_client):
        """GET /api/v1/health returns 200 with status ok and DB latency."""
        resp = api_client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "database" in data
        assert data["database"]["status"] == "ok"


class TestAlertsEndpoint:
    def test_alerts_returns_200_empty(self, api_client, mock_db):
        """GET /api/v1/alerts returns 200 with empty list when no alerts exist."""
        # alerts chain: db.query(X).order_by(...).offset(0).limit(50).all()
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/alerts")
        assert resp.status_code == 200
        assert resp.json() == []


class TestStatsEndpoint:
    def test_stats_returns_200(self, api_client, mock_db):
        """GET /api/v1/stats returns 200 with stats object containing zeroes."""
        # stats chain: db.query(X).all() for alert list
        mock_db.query.return_value.all.return_value = []
        # stats subquery: db.query(func.count()).select_from(subq).scalar()
        mock_db.query.return_value.select_from.return_value.scalar.return_value = 0

        resp = api_client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "alert_counts" in data
        assert data["alert_counts"]["total"] == 0
        assert data["alert_counts"]["critical"] == 0
        assert data["vessels_with_multiple_gaps_7d"] == 0


class TestVesselsEndpoint:
    def test_vessels_returns_200(self, api_client, mock_db):
        """GET /api/v1/vessels returns 200 with empty list."""
        # vessels chain: db.query(Vessel).limit(20).all()
        mock_db.query.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/vessels")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
        assert resp.json() == []
