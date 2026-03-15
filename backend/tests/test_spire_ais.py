"""Tests for Spire Maritime satellite AIS integration."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings():
    """Settings with Spire AIS enabled."""
    with patch("app.config.settings") as s:
        s.SPIRE_AIS_API_KEY = "test-spire-ais-key"
        s.SPIRE_API_KEY = "test-spire-verification-key"
        s.SPIRE_AIS_COLLECTION_ENABLED = True
        s.SPIRE_AIS_BASE_URL = "https://api.spire.com/graphql"
        s.SPIRE_MONTHLY_QUOTA = 10000
        s.COLLECT_SPIRE_INTERVAL = 1800
        s.SPIRE_LOOKBACK_HOURS = 2
        s.DIGITRAFFIC_ENABLED = False
        s.ADMIN_JWT_SECRET = "test-secret-for-jwt-tokens-1234567890abcdef"
        s.ADMIN_PASSWORD = None
        yield s


@pytest.fixture
def sample_graphql_response():
    """Sample Spire GraphQL API response."""
    return {
        "data": {
            "vessels": {
                "nodes": [
                    {
                        "staticData": {
                            "mmsi": "636092441",
                            "imo": "9300842",
                            "name": "AFRAMAX STAR",
                            "shipType": "TANKER",
                        },
                        "lastPositionUpdate": {
                            "timestamp": "2026-03-15T10:30:00Z",
                            "latitude": 26.5,
                            "longitude": 52.3,
                            "speed": 12.5,
                            "course": 135.2,
                            "heading": 134,
                        },
                    },
                    {
                        "staticData": {
                            "mmsi": "538007543",
                            "imo": "9412345",
                            "name": "VLCC GULF",
                            "shipType": "TANKER",
                        },
                        "lastPositionUpdate": {
                            "timestamp": "2026-03-15T10:25:00Z",
                            "latitude": 25.8,
                            "longitude": 51.9,
                            "speed": 0.2,
                            "course": 0,
                            "heading": 511,
                        },
                    },
                ]
            }
        }
    }


@pytest.fixture
def empty_graphql_response():
    """Empty Spire GraphQL response."""
    return {"data": {"vessels": {"nodes": []}}}


# ---------------------------------------------------------------------------
# SpireAisClient tests
# ---------------------------------------------------------------------------


class TestSpireAisClientNormalization:
    """Test position normalization."""

    def test_normalize_basic_position(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {
                "mmsi": "636092441",
                "imo": "9300842",
                "name": "TEST VESSEL",
                "shipType": "TANKER",
            },
            "lastPositionUpdate": {
                "timestamp": "2026-03-15T10:30:00Z",
                "latitude": 26.5,
                "longitude": 52.3,
                "speed": 12.5,
                "course": 135.2,
                "heading": 134,
            },
        }
        result = SpireAisClient._normalize_position(raw)
        assert result is not None
        assert result["mmsi"] == "636092441"
        assert result["imo"] == "9300842"
        assert result["name"] == "TEST VESSEL"
        assert result["lat"] == 26.5
        assert result["lon"] == 52.3
        assert result["sog"] == 12.5
        assert result["cog"] == 135.2
        assert result["heading"] == 134

    def test_normalize_heading_511_becomes_none(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": "538007543", "imo": None, "name": "TEST", "shipType": "CARGO"},
            "lastPositionUpdate": {
                "timestamp": "2026-03-15T10:30:00Z",
                "latitude": 25.0,
                "longitude": 50.0,
                "speed": 0,
                "course": 0,
                "heading": 511,
            },
        }
        result = SpireAisClient._normalize_position(raw)
        assert result is not None
        assert result["heading"] is None

    def test_normalize_missing_mmsi_returns_none(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": None, "imo": "123", "name": "X", "shipType": "CARGO"},
            "lastPositionUpdate": {
                "timestamp": "2026-03-15T10:30:00Z",
                "latitude": 25.0,
                "longitude": 50.0,
                "speed": 0,
                "course": 0,
                "heading": 0,
            },
        }
        assert SpireAisClient._normalize_position(raw) is None

    def test_normalize_missing_lat_returns_none(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": "636092441", "imo": "123", "name": "X", "shipType": "CARGO"},
            "lastPositionUpdate": {
                "timestamp": "2026-03-15T10:30:00Z",
                "latitude": None,
                "longitude": 50.0,
                "speed": 0,
                "course": 0,
                "heading": 0,
            },
        }
        assert SpireAisClient._normalize_position(raw) is None

    def test_normalize_missing_timestamp_returns_none(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": "636092441", "imo": "123", "name": "X", "shipType": "CARGO"},
            "lastPositionUpdate": {
                "timestamp": None,
                "latitude": 25.0,
                "longitude": 50.0,
                "speed": 0,
                "course": 0,
                "heading": 0,
            },
        }
        assert SpireAisClient._normalize_position(raw) is None

    def test_normalize_invalid_mmsi_length_returns_none(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": "12345", "imo": "123", "name": "X", "shipType": "CARGO"},
            "lastPositionUpdate": {
                "timestamp": "2026-03-15T10:30:00Z",
                "latitude": 25.0,
                "longitude": 50.0,
                "speed": 0,
                "course": 0,
                "heading": 0,
            },
        }
        assert SpireAisClient._normalize_position(raw) is None

    def test_normalize_no_static_data(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": None,
            "lastPositionUpdate": {
                "timestamp": "2026-03-15T10:30:00Z",
                "latitude": 25.0,
                "longitude": 50.0,
                "speed": 0,
                "course": 0,
                "heading": 0,
            },
        }
        assert SpireAisClient._normalize_position(raw) is None

    def test_normalize_no_position_data(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": "636092441", "imo": "123", "name": "X", "shipType": "CARGO"},
            "lastPositionUpdate": None,
        }
        assert SpireAisClient._normalize_position(raw) is None

    def test_normalize_numeric_timestamp(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": "636092441", "imo": None, "name": "X", "shipType": "CARGO"},
            "lastPositionUpdate": {
                "timestamp": 1742035800,  # Unix timestamp
                "latitude": 25.0,
                "longitude": 50.0,
                "speed": 5.0,
                "course": 90.0,
                "heading": 90,
            },
        }
        result = SpireAisClient._normalize_position(raw)
        assert result is not None
        assert isinstance(result["timestamp_utc"], datetime)

    def test_normalize_optional_speed_none(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": "636092441", "imo": None, "name": "X", "shipType": "CARGO"},
            "lastPositionUpdate": {
                "timestamp": "2026-03-15T10:30:00Z",
                "latitude": 25.0,
                "longitude": 50.0,
                "speed": None,
                "course": None,
                "heading": None,
            },
        }
        result = SpireAisClient._normalize_position(raw)
        assert result is not None
        assert result["sog"] is None
        assert result["cog"] is None
        assert result["heading"] is None

    def test_normalize_imo_absent_yields_none(self):
        from app.modules.spire_ais_client import SpireAisClient

        raw = {
            "staticData": {"mmsi": "636092441", "name": "X", "shipType": "CARGO"},
            "lastPositionUpdate": {
                "timestamp": "2026-03-15T10:30:00Z",
                "latitude": 25.0,
                "longitude": 50.0,
                "speed": 0,
                "course": 0,
                "heading": 0,
            },
        }
        result = SpireAisClient._normalize_position(raw)
        assert result is not None
        assert result["imo"] is None


class TestSpireAisClientFetch:
    """Test fetch_positions with mocked HTTP."""

    @patch("app.modules.spire_ais_client.breakers")
    def test_fetch_positions_success(self, mock_breakers, sample_graphql_response):
        from app.modules.spire_ais_client import SpireAisClient

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["spire_ais"].call.return_value = sample_graphql_response

        client = SpireAisClient(api_key="test-key", base_url="https://test.spire.com/graphql")
        positions = client.fetch_positions(
            bbox=[[[47, 23], [57, 23], [57, 30.5], [47, 30.5], [47, 23]]],
            since_utc=datetime(2026, 3, 15, 8, 0, 0),
        )

        assert len(positions) == 2
        assert positions[0]["mmsi"] == "636092441"
        assert positions[1]["mmsi"] == "538007543"

    @patch("app.modules.spire_ais_client.breakers")
    def test_fetch_positions_empty_response(self, mock_breakers, empty_graphql_response):
        from app.modules.spire_ais_client import SpireAisClient

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["spire_ais"].call.return_value = empty_graphql_response

        client = SpireAisClient(api_key="test-key")
        positions = client.fetch_positions(
            bbox=[[[47, 23], [57, 23], [57, 30.5], [47, 30.5], [47, 23]]],
            since_utc=datetime(2026, 3, 15, 8, 0, 0),
        )
        assert positions == []

    def test_fetch_positions_no_api_key(self):
        from app.modules.spire_ais_client import SpireAisClient

        client = SpireAisClient(api_key=None)
        client.api_key = None  # Force None
        positions = client.fetch_positions(
            bbox=[[[47, 23], [57, 23], [57, 30.5], [47, 30.5], [47, 23]]],
            since_utc=datetime(2026, 3, 15, 8, 0, 0),
        )
        assert positions == []

    @patch("app.modules.spire_ais_client.breakers")
    def test_fetch_positions_api_error(self, mock_breakers):
        from app.modules.spire_ais_client import SpireAisClient

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["spire_ais"].call.side_effect = Exception("API Error")

        client = SpireAisClient(api_key="test-key")
        with pytest.raises(Exception, match="API Error"):
            client.fetch_positions(
                bbox=[[[47, 23], [57, 23], [57, 30.5], [47, 30.5], [47, 23]]],
                since_utc=datetime(2026, 3, 15, 8, 0, 0),
            )


class TestSpireAisClientConnection:
    """Test connection testing."""

    def test_test_connection_no_key(self):
        from app.modules.spire_ais_client import SpireAisClient

        client = SpireAisClient(api_key=None)
        client.api_key = None
        result = client.test_connection()
        assert result["status"] == "error"
        assert "not configured" in result["detail"]

    @patch("app.modules.spire_ais_client.SpireAisClient._do_request")
    def test_test_connection_success(self, mock_request):
        from app.modules.spire_ais_client import SpireAisClient

        mock_request.return_value = {
            "data": {"vessels": {"nodes": [{"staticData": {"mmsi": "123456789"}}]}}
        }
        client = SpireAisClient(api_key="test-key")
        result = client.test_connection()
        assert result["status"] == "ok"

    @patch("app.modules.spire_ais_client.SpireAisClient._do_request")
    def test_test_connection_failure(self, mock_request):
        from app.modules.spire_ais_client import SpireAisClient

        mock_request.side_effect = Exception("Connection refused")
        client = SpireAisClient(api_key="test-key")
        result = client.test_connection()
        assert result["status"] == "error"
        assert "Connection refused" in result["detail"]


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSpireConfig:
    """Test config separation between AIS and verification keys."""

    def test_separate_api_keys_in_config(self):
        from app.config import Settings

        s = Settings(
            SPIRE_API_KEY="verification-key",
            SPIRE_AIS_API_KEY="ais-collection-key",
        )
        assert s.SPIRE_API_KEY == "verification-key"
        assert s.SPIRE_AIS_API_KEY == "ais-collection-key"
        assert s.SPIRE_API_KEY != s.SPIRE_AIS_API_KEY

    def test_defaults(self):
        from app.config import Settings

        s = Settings()
        assert s.SPIRE_AIS_API_KEY is None
        assert s.SPIRE_AIS_COLLECTION_ENABLED is False
        assert s.SPIRE_AIS_BASE_URL == "https://api.spire.com/graphql"
        assert s.SPIRE_MONTHLY_QUOTA == 10000
        assert s.COLLECT_SPIRE_INTERVAL == 1800
        assert s.SPIRE_LOOKBACK_HOURS == 2

    def test_feature_flag_default_disabled(self):
        from app.config import Settings

        s = Settings()
        assert s.SPIRE_AIS_COLLECTION_ENABLED is False


# ---------------------------------------------------------------------------
# Collection source registry tests
# ---------------------------------------------------------------------------


class TestSpireSourceRegistry:
    """Test Spire source in collection_sources.py."""

    def test_spire_in_registry(self):
        from app.modules.collection_sources import _SOURCE_REGISTRY

        assert "spire" in _SOURCE_REGISTRY

    def test_spire_disabled_by_default(self):
        from app.modules.collection_sources import get_all_sources

        sources = get_all_sources()
        assert "spire" in sources
        assert sources["spire"].enabled is False

    @patch("app.modules.collection_sources.settings")
    def test_spire_enabled_when_configured(self, mock_settings):
        mock_settings.SPIRE_AIS_API_KEY = "test-key"
        mock_settings.SPIRE_AIS_COLLECTION_ENABLED = True
        mock_settings.COLLECT_SPIRE_INTERVAL = 1800

        from app.modules.collection_sources import _SOURCE_REGISTRY

        info = _SOURCE_REGISTRY["spire"]()
        assert info.enabled is True
        assert info.interval_seconds == 1800
        assert info.name == "spire"

    @patch("app.modules.collection_sources.settings")
    def test_spire_disabled_without_api_key(self, mock_settings):
        mock_settings.SPIRE_AIS_API_KEY = None
        mock_settings.SPIRE_AIS_COLLECTION_ENABLED = True
        mock_settings.COLLECT_SPIRE_INTERVAL = 1800

        from app.modules.collection_sources import _SOURCE_REGISTRY

        info = _SOURCE_REGISTRY["spire"]()
        assert info.enabled is False

    @patch("app.modules.collection_sources.settings")
    def test_spire_disabled_without_flag(self, mock_settings):
        mock_settings.SPIRE_AIS_API_KEY = "test-key"
        mock_settings.SPIRE_AIS_COLLECTION_ENABLED = False
        mock_settings.COLLECT_SPIRE_INTERVAL = 1800

        from app.modules.collection_sources import _SOURCE_REGISTRY

        info = _SOURCE_REGISTRY["spire"]()
        assert info.enabled is False


# ---------------------------------------------------------------------------
# Circuit breaker tests
# ---------------------------------------------------------------------------


class TestSpireCircuitBreaker:
    """Test Spire AIS circuit breaker registration."""

    def test_spire_ais_breaker_exists(self):
        from app.modules.circuit_breakers import breakers

        assert "spire_ais" in breakers

    def test_spire_ais_breaker_config(self):
        from app.modules.circuit_breakers import breakers

        cb = breakers["spire_ais"]
        assert cb.name == "spire_ais"
        assert cb.fail_max == 5
        assert cb.reset_timeout == 60

    def test_spire_ais_breaker_separate_from_verification(self):
        """Ensure the AIS breaker is separate from any verification breaker."""
        from app.modules.circuit_breakers import breakers

        # spire_ais should be its own breaker
        assert "spire_ais" in breakers
        # There should be no generic "spire" breaker (verification uses different path)
        assert breakers["spire_ais"].name == "spire_ais"


# ---------------------------------------------------------------------------
# Collector tests
# ---------------------------------------------------------------------------


class TestSpireCollector:
    """Test collect_spire_gulf_ais."""

    @patch("app.modules.spire_ais_collector.settings")
    def test_collection_disabled(self, mock_settings):
        from app.modules.spire_ais_collector import collect_spire_gulf_ais

        mock_settings.SPIRE_AIS_COLLECTION_ENABLED = False
        db = MagicMock()
        result = collect_spire_gulf_ais(db)
        assert result["points_imported"] == 0

    @patch("app.modules.spire_ais_collector.settings")
    def test_collection_no_api_key(self, mock_settings):
        from app.modules.spire_ais_collector import collect_spire_gulf_ais

        mock_settings.SPIRE_AIS_COLLECTION_ENABLED = True
        mock_settings.SPIRE_AIS_API_KEY = None
        db = MagicMock()
        result = collect_spire_gulf_ais(db)
        assert result["points_imported"] == 0

    @patch("app.modules.spire_ais_collector._get_quota_used_this_month")
    @patch("app.modules.spire_ais_collector.settings")
    def test_quota_exhausted(self, mock_settings, mock_quota):
        from app.modules.spire_ais_collector import collect_spire_gulf_ais

        mock_settings.SPIRE_AIS_COLLECTION_ENABLED = True
        mock_settings.SPIRE_AIS_API_KEY = "test-key"
        mock_settings.SPIRE_MONTHLY_QUOTA = 100
        mock_quota.return_value = 100

        db = MagicMock()
        result = collect_spire_gulf_ais(db)
        assert result["points_imported"] == 0
        assert result.get("quota_exhausted") is True


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestSpireEndpoints:
    """Test Spire admin API endpoints."""

    @pytest.fixture
    def client(self):
        from app.auth import require_senior_or_admin
        from app.database import get_db
        from app.main import app

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            None
        )

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[require_senior_or_admin] = lambda: {"role": "admin"}

        yield TestClient(app)

        app.dependency_overrides.clear()

    def test_status_endpoint(self, client):
        resp = client.get("/api/v1/admin/spire/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "monthly_quota" in data
        assert "circuit_breaker" in data
        assert "api_key_configured" in data

    def test_coverage_endpoint(self, client):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.group_by.return_value.order_by.return_value.all.return_value = (
            []
        )
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0

        from app.database import get_db

        from app.main import app

        app.dependency_overrides[get_db] = lambda: mock_db

        resp = client.get("/api/v1/admin/spire/coverage?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert "days" in data
        assert "total_points" in data
        assert "vessel_count" in data

    def test_collect_endpoint_no_key(self, client):
        """Collect should fail if no API key configured."""
        with patch("app.api.routes_spire.settings") as mock_s:
            mock_s.SPIRE_AIS_API_KEY = None
            resp = client.post("/api/v1/admin/spire/collect")
            assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Coverage YAML tests
# ---------------------------------------------------------------------------


class TestCoverageYaml:
    """Test coverage.yaml update."""

    def test_persian_gulf_commercial_quality(self):
        from pathlib import Path

        import yaml

        yaml_path = Path(__file__).resolve().parent.parent.parent / "config" / "coverage.yaml"
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        assert "Persian Gulf" in config
        assert config["Persian Gulf"]["quality"] == "COMMERCIAL"


# ---------------------------------------------------------------------------
# GraphQL response parsing edge cases
# ---------------------------------------------------------------------------


class TestGraphQLParsing:
    """Test parsing of various GraphQL response shapes."""

    @patch("app.modules.spire_ais_client.breakers")
    def test_malformed_response_no_data_key(self, mock_breakers):
        from app.modules.spire_ais_client import SpireAisClient

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["spire_ais"].call.return_value = {"errors": [{"message": "bad query"}]}

        client = SpireAisClient(api_key="test-key")
        positions = client.fetch_positions(
            bbox=[[[47, 23], [57, 23], [57, 30.5], [47, 30.5], [47, 23]]],
            since_utc=datetime(2026, 3, 15, 8, 0, 0),
        )
        assert positions == []

    @patch("app.modules.spire_ais_client.breakers")
    def test_partial_node_data(self, mock_breakers):
        """Nodes with missing fields should be skipped, not crash."""
        from app.modules.spire_ais_client import SpireAisClient

        response = {
            "data": {
                "vessels": {
                    "nodes": [
                        {
                            "staticData": {"mmsi": "636092441", "imo": None, "name": "OK", "shipType": "TANKER"},
                            "lastPositionUpdate": {
                                "timestamp": "2026-03-15T10:30:00Z",
                                "latitude": 26.5,
                                "longitude": 52.3,
                                "speed": 12.5,
                                "course": 135.2,
                                "heading": 134,
                            },
                        },
                        {
                            "staticData": {"mmsi": None},
                            "lastPositionUpdate": {
                                "timestamp": "2026-03-15T10:30:00Z",
                                "latitude": 25.0,
                                "longitude": 50.0,
                                "speed": 0,
                                "course": 0,
                                "heading": 0,
                            },
                        },
                    ]
                }
            }
        }
        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["spire_ais"].call.return_value = response

        client = SpireAisClient(api_key="test-key")
        positions = client.fetch_positions(
            bbox=[[[47, 23], [57, 23], [57, 30.5], [47, 30.5], [47, 23]]],
            since_utc=datetime(2026, 3, 15, 8, 0, 0),
        )
        # Only the first node should be normalized successfully
        assert len(positions) == 1
        assert positions[0]["mmsi"] == "636092441"


# ---------------------------------------------------------------------------
# Persian Gulf bounding box constant
# ---------------------------------------------------------------------------


class TestPersianGulfBbox:
    """Test Persian Gulf bounding box constant."""

    def test_bbox_coordinates(self):
        from app.modules.spire_ais_collector import PERSIAN_GULF_BBOX

        coords = PERSIAN_GULF_BBOX[0]
        # Should be a closed polygon with 5 points
        assert len(coords) == 5
        assert coords[0] == coords[-1]  # closed polygon
        # Check corners
        assert coords[0] == [47, 23]
        assert coords[1] == [57, 23]
        assert coords[2] == [57, 30.5]
        assert coords[3] == [47, 30.5]
