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
        """GET /api/v1/alerts returns 200 with paginated response when no alerts exist."""
        # alerts chain: q.count() for total, q.offset(...).limit(...).all() for results
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["items"] == []


class TestStatsEndpoint:
    def test_stats_returns_200(self, api_client, mock_db):
        """GET /api/v1/stats returns 200 with stats object containing zeroes."""
        # Stats now uses SQL aggregation with with_entities instead of loading all rows.
        # count aggregation result: (total, critical, high, medium, low)
        count_result = (0, 0, 0, 0, 0)
        mock_db.query.return_value.with_entities.return_value.first.return_value = count_result
        # status/corridor group_by queries
        mock_db.query.return_value.with_entities.return_value.group_by.return_value.all.return_value = []
        # stats subquery: db.query(func.count()).select_from(subq).scalar()
        mock_db.query.return_value.select_from.return_value.scalar.return_value = 0
        mock_db.query.return_value.scalar.return_value = 0
        # multi-gap subquery chain
        mock_db.query.return_value.filter.return_value.group_by.return_value.having.return_value.subquery.return_value = "subq"

        resp = api_client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "alert_counts" in data
        assert data["alert_counts"]["total"] == 0
        assert data["alert_counts"]["critical"] == 0
        assert data["vessels_with_multiple_gaps_7d"] == 0


class TestAlertsPagination:
    def test_negative_skip_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/alerts?skip=-1")
        assert resp.status_code == 422

    def test_zero_limit_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/alerts?limit=0")
        assert resp.status_code == 422

    def test_date_range_inverted_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/alerts?date_from=2026-03-01&date_to=2026-01-01")
        assert resp.status_code == 422

    def test_alerts_paginated_response_format(self, api_client, mock_db):
        """GET /alerts returns {items: [], total: N} format."""
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/alerts")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data


class TestVesselsEndpoint:
    def test_vessels_returns_200(self, api_client, mock_db):
        """GET /api/v1/vessels returns 200 with paginated response."""
        # vessels chain: q.count() for total, q.offset(...).limit(...).all() for results
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/vessels")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert data["items"] == []
