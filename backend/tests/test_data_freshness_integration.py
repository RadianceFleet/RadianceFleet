"""Tests for data freshness and health check endpoints.

The /api/v1/health endpoint returns database connectivity status.
Data freshness is monitored via vessel updated_at timestamps.

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

import pytest


# ---------------------------------------------------------------------------
# Health Endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """GET /api/v1/health returns health info with DB status."""

    def test_health_returns_200(self, api_client, mock_db):
        resp = api_client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_returns_status_ok(self, api_client, mock_db):
        resp = api_client.get("/api/v1/health")
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_returns_database_info(self, api_client, mock_db):
        resp = api_client.get("/api/v1/health")
        data = resp.json()
        assert "database" in data
        assert "status" in data["database"]

    def test_health_returns_version(self, api_client, mock_db):
        resp = api_client.get("/api/v1/health")
        data = resp.json()
        assert "version" in data


# ---------------------------------------------------------------------------
# Root Health (non-API-v1 health)
# ---------------------------------------------------------------------------

class TestRootHealthEndpoint:
    """GET /health (non-prefixed) returns minimal status."""

    def test_root_health_returns_200(self, api_client):
        resp = api_client.get("/health")
        assert resp.status_code == 200

    def test_root_health_returns_status_ok(self, api_client):
        resp = api_client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# Stats Endpoint — Freshness Proxy
# ---------------------------------------------------------------------------

class TestStatsDataFreshness:
    """GET /api/v1/stats includes alert_counts as freshness proxy."""

    def test_stats_returns_200(self, api_client, mock_db):
        mock_db.query.return_value.all.return_value = []
        mock_db.query.return_value.select_from.return_value.scalar.return_value = 0
        resp = api_client.get("/api/v1/stats")
        assert resp.status_code == 200

    def test_stats_has_alert_counts(self, api_client, mock_db):
        mock_db.query.return_value.all.return_value = []
        mock_db.query.return_value.select_from.return_value.scalar.return_value = 0
        resp = api_client.get("/api/v1/stats")
        data = resp.json()
        assert "alert_counts" in data
        assert "total" in data["alert_counts"]

    def test_stats_has_vessel_multi_gap_count(self, api_client, mock_db):
        mock_db.query.return_value.all.return_value = []
        mock_db.query.return_value.select_from.return_value.scalar.return_value = 0
        resp = api_client.get("/api/v1/stats")
        data = resp.json()
        assert "vessels_with_multiple_gaps_7d" in data


# ---------------------------------------------------------------------------
# Ingestion Status — Tracks Data Flow
# ---------------------------------------------------------------------------

class TestIngestionStatus:
    """GET /api/v1/ingestion-status tracks whether data is flowing."""

    def test_ingestion_status_idle(self, api_client, mock_db):
        resp = api_client.get("/api/v1/ingestion-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"

    def test_ingestion_status_has_required_fields(self, api_client, mock_db):
        resp = api_client.get("/api/v1/ingestion-status")
        data = resp.json()
        assert "status" in data
        assert "processed" in data
        assert "accepted" in data
        assert "rejected" in data


# ---------------------------------------------------------------------------
# Vessel Model — updated_at for Freshness Tracking
# ---------------------------------------------------------------------------

class TestVesselUpdatedAt:
    """Vessel model has updated_at for tracking data freshness."""

    def test_vessel_model_has_updated_at(self):
        """Vessel model includes updated_at column for freshness tracking."""
        from app.models.vessel import Vessel

        column_names = {c.name for c in Vessel.__table__.columns}
        assert "updated_at" in column_names

    def test_vessel_model_has_mmsi_first_seen(self):
        """Vessel model includes mmsi_first_seen_utc for age-based scoring."""
        from app.models.vessel import Vessel

        column_names = {c.name for c in Vessel.__table__.columns}
        assert "mmsi_first_seen_utc" in column_names
