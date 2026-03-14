"""Tests for Datalastic API client and vessel enrichment integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pybreaker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    """Build a fake httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data or {},
        request=httpx.Request("GET", "https://api.datalastic.com/api/v0/vessel_info"),
    )
    return resp


SAMPLE_VESSEL = {
    "data": {
        "mmsi": "273999000",
        "imo": "9999999",
        "name": "TEST TANKER",
        "deadweight": 120000,
        "type_specific": "Crude Oil Tanker",
        "year_built": 2005,
        "callsign": "UBAA",
        "country_iso": "RU",
        "gross_tonnage": 65000,
    }
}


# ---------------------------------------------------------------------------
# fetch_vessel_info tests
# ---------------------------------------------------------------------------

class TestFetchVesselInfo:
    @patch("app.modules.datalastic_client.time.sleep")
    @patch("app.modules.datalastic_client.breakers")
    @patch("app.modules.datalastic_client.settings")
    def test_fetch_by_mmsi(self, mock_settings, mock_breakers, mock_sleep):
        mock_settings.DATALASTIC_API_KEY = "test-key"
        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["datalastic"].call.return_value = _mock_response(200, SAMPLE_VESSEL)

        from app.modules.datalastic_client import fetch_vessel_info

        result = fetch_vessel_info(mmsi="273999000")

        assert result is not None
        assert result["deadweight"] == 120000.0
        assert result["vessel_type"] == "Crude Oil Tanker"
        assert result["year_built"] == 2005
        assert result["callsign"] == "UBAA"
        assert result["flag"] == "RU"
        assert result["gross_tonnage"] == 65000.0

    @patch("app.modules.datalastic_client.time.sleep")
    @patch("app.modules.datalastic_client.breakers")
    @patch("app.modules.datalastic_client.settings")
    def test_fetch_by_imo(self, mock_settings, mock_breakers, mock_sleep):
        mock_settings.DATALASTIC_API_KEY = "test-key"
        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["datalastic"].call.return_value = _mock_response(200, SAMPLE_VESSEL)

        from app.modules.datalastic_client import fetch_vessel_info

        result = fetch_vessel_info(imo="9999999")

        assert result is not None
        assert result["deadweight"] == 120000.0

    @patch("app.modules.datalastic_client.breakers")
    @patch("app.modules.datalastic_client.settings")
    def test_fetch_not_found(self, mock_settings, mock_breakers):
        mock_settings.DATALASTIC_API_KEY = "test-key"
        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["datalastic"].call.return_value = _mock_response(404, {})

        from app.modules.datalastic_client import fetch_vessel_info

        result = fetch_vessel_info(mmsi="000000000")
        assert result is None

    @patch("app.modules.datalastic_client.breakers")
    @patch("app.modules.datalastic_client.settings")
    def test_fetch_rate_limit_trips_breaker(self, mock_settings, mock_breakers):
        mock_settings.DATALASTIC_API_KEY = "test-key"
        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        mock_breakers["datalastic"].call.side_effect = pybreaker.CircuitBreakerError()

        from app.modules.datalastic_client import fetch_vessel_info

        result = fetch_vessel_info(mmsi="273999000")
        assert result is None

    def test_fetch_no_api_key(self):
        with patch("app.modules.datalastic_client.settings") as mock_settings:
            mock_settings.DATALASTIC_API_KEY = None

            from app.modules.datalastic_client import fetch_vessel_info

            result = fetch_vessel_info(mmsi="273999000")
            assert result is None

    def test_fetch_no_identifiers(self):
        with patch("app.modules.datalastic_client.settings") as mock_settings:
            mock_settings.DATALASTIC_API_KEY = "test-key"

            from app.modules.datalastic_client import fetch_vessel_info

            result = fetch_vessel_info()
            assert result is None

    @patch("app.modules.datalastic_client.time.sleep")
    @patch("app.modules.datalastic_client.breakers")
    @patch("app.modules.datalastic_client.settings")
    def test_fetch_partial_data(self, mock_settings, mock_breakers, mock_sleep):
        """Vessel with only some fields populated."""
        mock_settings.DATALASTIC_API_KEY = "test-key"
        mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
        partial = {"data": {"deadweight": 50000, "callsign": "XYZQ"}}
        mock_breakers["datalastic"].call.return_value = _mock_response(200, partial)

        from app.modules.datalastic_client import fetch_vessel_info

        result = fetch_vessel_info(mmsi="273999000")
        assert result is not None
        assert result["deadweight"] == 50000.0
        assert result["callsign"] == "XYZQ"
        assert "vessel_type" not in result
        assert "year_built" not in result


# ---------------------------------------------------------------------------
# enrich_vessels_from_datalastic tests
# ---------------------------------------------------------------------------

class TestEnrichVesselsFromDatalastic:
    @patch("app.modules.vessel_enrichment.settings")
    @patch("app.modules.datalastic_client.fetch_vessel_info")
    def test_enrich_updates_vessel(self, mock_fetch, mock_settings):
        """Integration: enrichment updates vessel fields and creates VesselHistory."""
        from app.modules.vessel_enrichment import enrich_vessels_from_datalastic

        mock_settings.DATALASTIC_API_KEY = "test-key"

        # Mock vessel
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "273999000"
        vessel.imo = "9999999"
        vessel.deadweight = None
        vessel.is_heuristic_dwt = True
        vessel.vessel_type = None
        vessel.year_built = None
        vessel.callsign = None
        vessel.flag = None

        # Mock DB session
        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]

        # Mock fetch_vessel_info
        mock_fetch.return_value = {
            "deadweight": 120000.0,
            "vessel_type": "Crude Oil Tanker",
            "year_built": 2005,
            "callsign": "UBAA",
            "flag": "RU",
        }

        result = enrich_vessels_from_datalastic(db, limit=10)

        assert result["enriched"] == 1
        assert result["failed"] == 0
        assert vessel.deadweight == 120000.0
        assert vessel.is_heuristic_dwt is False
        assert vessel.vessel_type == "Crude Oil Tanker"
        assert vessel.year_built == 2005
        assert vessel.callsign == "UBAA"
        assert vessel.flag == "RU"
        assert db.add.called  # VesselHistory records added

    @patch("app.modules.vessel_enrichment.settings")
    def test_enrich_disabled_without_key(self, mock_settings):
        from app.modules.vessel_enrichment import enrich_vessels_from_datalastic

        mock_settings.DATALASTIC_API_KEY = None
        db = MagicMock()
        result = enrich_vessels_from_datalastic(db)

        assert result.get("disabled") is True
        assert result["enriched"] == 0

    @patch("app.modules.vessel_enrichment.settings")
    @patch("app.modules.datalastic_client.fetch_vessel_info")
    def test_enrich_skips_when_no_data(self, mock_fetch, mock_settings):
        from app.modules.vessel_enrichment import enrich_vessels_from_datalastic

        mock_settings.DATALASTIC_API_KEY = "test-key"

        vessel = MagicMock()
        vessel.vessel_id = 2
        vessel.mmsi = "111222333"
        vessel.imo = None
        vessel.deadweight = None
        vessel.is_heuristic_dwt = False

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]

        mock_fetch.return_value = None
        result = enrich_vessels_from_datalastic(db, limit=10)

        assert result["skipped"] == 1
        assert result["enriched"] == 0
