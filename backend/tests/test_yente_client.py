"""Tests for yente sanctions screening client and watchlist integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pybreaker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _yente_match_response(results: list[dict] | None = None) -> dict:
    """Build a mock yente /match response."""
    if results is None:
        results = [
            {
                "id": "ofac-sdn-12345",
                "schema": "Vessel",
                "score": 0.85,
                "caption": "TANKER ONE",
                "datasets": ["us_ofac_sdn"],
                "properties": {"name": ["TANKER ONE"], "mmsi": ["123456789"]},
            }
        ]
    return {"responses": results}


def _yente_search_response(results: list[dict] | None = None) -> dict:
    """Build a mock yente /search response."""
    if results is None:
        results = [
            {
                "id": "eu-vessel-999",
                "schema": "Vessel",
                "score": 0.9,
                "caption": "SHADOW TANKER",
                "datasets": ["eu_fsf"],
                "properties": {"name": ["SHADOW TANKER"]},
            }
        ]
    return {"results": results}


# ---------------------------------------------------------------------------
# Entity construction
# ---------------------------------------------------------------------------


class TestBuildVesselEntity:
    def test_minimal(self):
        from app.modules.yente_client import _build_vessel_entity

        entity = _build_vessel_entity("MY VESSEL")
        assert entity == {
            "schema": "Vessel",
            "properties": {"name": ["MY VESSEL"]},
        }

    def test_full_properties(self):
        from app.modules.yente_client import _build_vessel_entity

        entity = _build_vessel_entity("MY VESSEL", mmsi="123456789", imo="9876543", flag="PA")
        assert entity["schema"] == "Vessel"
        props = entity["properties"]
        assert props["name"] == ["MY VESSEL"]
        assert props["mmsi"] == ["123456789"]
        assert props["imoNumber"] == ["9876543"]
        assert props["flag"] == ["PA"]

    def test_none_fields_excluded(self):
        from app.modules.yente_client import _build_vessel_entity

        entity = _build_vessel_entity("VESSEL", mmsi=None, imo=None, flag=None)
        props = entity["properties"]
        assert "mmsi" not in props
        assert "imoNumber" not in props
        assert "flag" not in props


# ---------------------------------------------------------------------------
# match_vessel
# ---------------------------------------------------------------------------


class TestMatchVessel:
    @patch("app.modules.yente_client.settings")
    def test_disabled_returns_empty(self, mock_settings):
        mock_settings.YENTE_ENABLED = False
        from app.modules.yente_client import match_vessel

        result = match_vessel("TANKER ONE")
        assert result == []

    @patch("app.modules.yente_client.httpx.Client")
    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_successful_match(self, mock_settings, mock_breakers, mock_client_cls):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None
        mock_settings.YENTE_MATCH_THRESHOLD = 0.7
        mock_settings.YENTE_DATASETS = "default"

        mock_response = MagicMock()
        mock_response.json.return_value = _yente_match_response()
        mock_response.raise_for_status = MagicMock()

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.return_value = mock_response

        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        from app.modules.yente_client import match_vessel

        result = match_vessel("TANKER ONE", mmsi="123456789")
        assert len(result) == 1
        assert result[0]["score"] == 0.85
        assert result[0]["name"] == "TANKER ONE"
        assert "us_ofac_sdn" in result[0]["datasets"]

    @patch("app.modules.yente_client.httpx.Client")
    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_threshold_filtering(self, mock_settings, mock_breakers, mock_client_cls):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None
        mock_settings.YENTE_MATCH_THRESHOLD = 0.9
        mock_settings.YENTE_DATASETS = "default"

        results = [
            {"id": "a", "schema": "Vessel", "score": 0.95, "caption": "HIGH MATCH", "datasets": ["us_ofac_sdn"], "properties": {}},
            {"id": "b", "schema": "Vessel", "score": 0.5, "caption": "LOW MATCH", "datasets": ["us_ofac_sdn"], "properties": {}},
            {"id": "c", "schema": "Vessel", "score": 0.9, "caption": "EXACT THRESHOLD", "datasets": ["eu_fsf"], "properties": {}},
        ]
        mock_response = MagicMock()
        mock_response.json.return_value = {"responses": results}
        mock_response.raise_for_status = MagicMock()

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.return_value = mock_response

        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        from app.modules.yente_client import match_vessel

        result = match_vessel("TEST VESSEL")
        assert len(result) == 2
        names = [r["name"] for r in result]
        assert "HIGH MATCH" in names
        assert "EXACT THRESHOLD" in names
        assert "LOW MATCH" not in names

    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_circuit_breaker_open(self, mock_settings, mock_breakers):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None
        mock_settings.YENTE_MATCH_THRESHOLD = 0.7
        mock_settings.YENTE_DATASETS = "default"

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.side_effect = pybreaker.CircuitBreakerError()

        from app.modules.yente_client import match_vessel

        result = match_vessel("TANKER ONE")
        assert result == []

    @patch("app.modules.yente_client.httpx.Client")
    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_http_error_returns_empty(self, mock_settings, mock_breakers, mock_client_cls):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None
        mock_settings.YENTE_MATCH_THRESHOLD = 0.7
        mock_settings.YENTE_DATASETS = "default"

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.side_effect = httpx.ConnectError("Connection refused")

        from app.modules.yente_client import match_vessel

        result = match_vessel("TANKER ONE")
        assert result == []

    @patch("app.modules.yente_client.httpx.Client")
    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_timeout_returns_empty(self, mock_settings, mock_breakers, mock_client_cls):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None
        mock_settings.YENTE_MATCH_THRESHOLD = 0.7
        mock_settings.YENTE_DATASETS = "default"

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.side_effect = httpx.ReadTimeout("Timeout")

        from app.modules.yente_client import match_vessel

        result = match_vessel("TANKER ONE")
        assert result == []

    @patch("app.modules.yente_client.httpx.Client")
    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_api_key_header_sent(self, mock_settings, mock_breakers, mock_client_cls):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = "test-key-123"
        mock_settings.YENTE_MATCH_THRESHOLD = 0.7
        mock_settings.YENTE_DATASETS = "default"

        mock_response = MagicMock()
        mock_response.json.return_value = _yente_match_response([])
        mock_response.raise_for_status = MagicMock()

        mock_breaker = MagicMock()
        mock_breaker.call.return_value = mock_response
        mock_breakers.__getitem__ = MagicMock(return_value=mock_breaker)

        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        from app.modules.yente_client import match_vessel

        match_vessel("TEST")

        call_kwargs = mock_breaker.call.call_args
        headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
        assert headers["Authorization"] == "ApiKey test-key-123"


# ---------------------------------------------------------------------------
# search_vessel
# ---------------------------------------------------------------------------


class TestSearchVessel:
    @patch("app.modules.yente_client.settings")
    def test_disabled_returns_empty(self, mock_settings):
        mock_settings.YENTE_ENABLED = False
        from app.modules.yente_client import search_vessel

        result = search_vessel("shadow tanker")
        assert result == []

    @patch("app.modules.yente_client.httpx.Client")
    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_successful_search(self, mock_settings, mock_breakers, mock_client_cls):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None
        mock_settings.YENTE_DATASETS = "default"

        mock_response = MagicMock()
        mock_response.json.return_value = _yente_search_response()
        mock_response.raise_for_status = MagicMock()

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.return_value = mock_response

        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        from app.modules.yente_client import search_vessel

        result = search_vessel("shadow tanker")
        assert len(result) == 1
        assert result[0]["name"] == "SHADOW TANKER"

    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_circuit_breaker_open(self, mock_settings, mock_breakers):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None
        mock_settings.YENTE_DATASETS = "default"

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.side_effect = pybreaker.CircuitBreakerError()

        from app.modules.yente_client import search_vessel

        result = search_vessel("test")
        assert result == []


# ---------------------------------------------------------------------------
# check_health
# ---------------------------------------------------------------------------


class TestCheckHealth:
    @patch("app.modules.yente_client.settings")
    def test_disabled_returns_false(self, mock_settings):
        mock_settings.YENTE_ENABLED = False
        from app.modules.yente_client import check_health

        assert check_health() is False

    @patch("app.modules.yente_client.httpx.Client")
    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_healthy(self, mock_settings, mock_breakers, mock_client_cls):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.return_value = mock_response

        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        from app.modules.yente_client import check_health

        assert check_health() is True

    @patch("app.modules.yente_client.httpx.Client")
    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_unhealthy_status(self, mock_settings, mock_breakers, mock_client_cls):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None

        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.return_value = mock_response

        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        from app.modules.yente_client import check_health

        assert check_health() is False

    @patch("app.modules.yente_client.breakers")
    @patch("app.modules.yente_client.settings")
    def test_connection_error_returns_false(self, mock_settings, mock_breakers):
        mock_settings.YENTE_ENABLED = True
        mock_settings.YENTE_API_URL = "http://yente:8100"
        mock_settings.YENTE_API_KEY = None

        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["yente"].call.side_effect = httpx.ConnectError("Connection refused")

        from app.modules.yente_client import check_health

        assert check_health() is False


# ---------------------------------------------------------------------------
# _build_headers
# ---------------------------------------------------------------------------


class TestBuildHeaders:
    @patch("app.modules.yente_client.settings")
    def test_no_api_key(self, mock_settings):
        mock_settings.YENTE_API_KEY = None
        from app.modules.yente_client import _build_headers

        headers = _build_headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    @patch("app.modules.yente_client.settings")
    def test_with_api_key(self, mock_settings):
        mock_settings.YENTE_API_KEY = "my-secret-key"
        from app.modules.yente_client import _build_headers

        headers = _build_headers()
        assert headers["Authorization"] == "ApiKey my-secret-key"


# ---------------------------------------------------------------------------
# screen_vessel_via_yente (watchlist integration)
# ---------------------------------------------------------------------------


class TestScreenVesselViaYente:
    @patch("app.config.settings")
    def test_disabled_returns_empty(self, mock_settings):
        mock_settings.YENTE_ENABLED = False
        from app.modules.watchlist_loader import screen_vessel_via_yente

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.name = "TANKER"
        db = MagicMock()

        result = screen_vessel_via_yente(db, vessel)
        assert result == {"matches": 0, "sources": []}

    @patch("app.modules.yente_client.match_vessel")
    @patch("app.config.settings")
    def test_no_name_skips(self, mock_settings, mock_match):
        mock_settings.YENTE_ENABLED = True
        mock_settings.FUZZY_MATCH_THRESHOLD = 85
        from app.modules.watchlist_loader import screen_vessel_via_yente

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.name = ""
        db = MagicMock()

        result = screen_vessel_via_yente(db, vessel)
        assert result["matches"] == 0
        mock_match.assert_not_called()

    @patch("app.modules.watchlist_loader._upsert_watchlist")
    @patch("app.modules.yente_client.match_vessel", new_callable=MagicMock)
    @patch("app.config.settings")
    def test_matches_upserted(self, mock_settings, mock_match_fn, mock_upsert):
        mock_settings.YENTE_ENABLED = True
        mock_settings.FUZZY_MATCH_THRESHOLD = 85
        # Need to re-import so the local import in screen_vessel_via_yente picks up mock
        import app.modules.watchlist_loader as wl

        mock_match_fn.return_value = [
            {"score": 0.85, "name": "TANKER ONE", "datasets": ["us_ofac_sdn"], "properties": {}},
            {"score": 0.75, "name": "TANKER TWO", "datasets": ["eu_fsf"], "properties": {}},
        ]

        vessel = MagicMock()
        vessel.vessel_id = 42
        vessel.name = "TANKER ONE"
        vessel.mmsi = "123456789"
        vessel.imo = "1234567"
        vessel.flag = "PA"
        db = MagicMock()

        # Patch match_vessel at the point it's imported inside screen_vessel_via_yente
        with patch("app.modules.yente_client.match_vessel", mock_match_fn):
            result = wl.screen_vessel_via_yente(db, vessel)

        assert result["matches"] == 2
        assert "OFAC_SDN" in result["sources"]
        assert "EU_COUNCIL" in result["sources"]
        assert mock_upsert.call_count == 2
        db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Dataset mapping
# ---------------------------------------------------------------------------


class TestYenteDatasetMapping:
    def test_ofac_mapping(self):
        from app.modules.watchlist_loader import _yente_dataset_to_source

        assert _yente_dataset_to_source(["us_ofac_sdn"]) == "OFAC_SDN"
        assert _yente_dataset_to_source(["us_ofac"]) == "OFAC_SDN"

    def test_eu_mapping(self):
        from app.modules.watchlist_loader import _yente_dataset_to_source

        assert _yente_dataset_to_source(["eu_fsf"]) == "EU_COUNCIL"
        assert _yente_dataset_to_source(["eu_sanctions"]) == "EU_COUNCIL"

    def test_un_mapping(self):
        from app.modules.watchlist_loader import _yente_dataset_to_source

        assert _yente_dataset_to_source(["un_sc_sanctions"]) == "UN_SANCTIONS"

    def test_unknown_falls_back(self):
        from app.modules.watchlist_loader import _yente_dataset_to_source

        assert _yente_dataset_to_source(["some_other_dataset"]) == "OPENSANCTIONS"

    def test_empty_list_falls_back(self):
        from app.modules.watchlist_loader import _yente_dataset_to_source

        assert _yente_dataset_to_source([]) == "OPENSANCTIONS"
