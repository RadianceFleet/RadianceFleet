"""Tests for OpenCorporates API client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pybreaker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _search_response(companies: list[dict] | None = None) -> dict:
    """Build a mock OpenCorporates /companies/search response."""
    if companies is None:
        companies = [
            {
                "company": {
                    "name": "SHADOW SHIPPING LTD",
                    "company_number": "12345",
                    "jurisdiction_code": "pa",
                    "opencorporates_url": "https://opencorporates.com/companies/pa/12345",
                    "incorporation_date": "2023-01-15",
                    "registered_address_in_full": "Panama City, Panama",
                    "current_status": "Active",
                }
            }
        ]
    return {"results": {"companies": companies}}


def _company_response(company: dict | None = None) -> dict:
    """Build a mock OpenCorporates /companies/{jur}/{num} response."""
    if company is None:
        company = {
            "name": "SHADOW SHIPPING LTD",
            "company_number": "12345",
            "jurisdiction_code": "pa",
            "opencorporates_url": "https://opencorporates.com/companies/pa/12345",
            "incorporation_date": "2023-01-15",
            "registered_address_in_full": "Panama City, Panama",
            "current_status": "Active",
        }
    return {"results": {"company": company}}


def _officers_response(officers: list[dict] | None = None) -> dict:
    """Build a mock officers response."""
    if officers is None:
        officers = [
            {
                "officer": {
                    "name": "John Smith",
                    "position": "director",
                    "start_date": "2023-02-01",
                }
            }
        ]
    return {"results": {"officers": officers}}


# ---------------------------------------------------------------------------
# Disabled
# ---------------------------------------------------------------------------


class TestDisabled:
    def test_search_returns_empty_when_disabled(self):
        with patch("app.modules.opencorporates_client.settings") as mock_settings:
            mock_settings.OPENCORPORATES_ENABLED = False
            from app.modules.opencorporates_client import search_companies

            result = search_companies("SHADOW SHIPPING")
            assert result == []

    def test_fetch_company_returns_none_when_disabled(self):
        with patch("app.modules.opencorporates_client.settings") as mock_settings:
            mock_settings.OPENCORPORATES_ENABLED = False
            from app.modules.opencorporates_client import fetch_company

            result = fetch_company("pa", "12345")
            assert result is None

    def test_fetch_officers_returns_empty_when_disabled(self):
        with patch("app.modules.opencorporates_client.settings") as mock_settings:
            mock_settings.OPENCORPORATES_ENABLED = False
            from app.modules.opencorporates_client import fetch_officers

            result = fetch_officers("pa", "12345")
            assert result == []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearchCompanies:
    def test_search_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _search_response()

        with (
            patch("app.modules.opencorporates_client.settings") as mock_settings,
            patch("app.modules.opencorporates_client.breakers") as mock_breakers,
            patch("app.modules.opencorporates_client._rate_limit"),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_API_URL = "https://api.opencorporates.com/v0.4"
            mock_settings.OPENCORPORATES_API_KEY = "test-key"
            mock_settings.OPENCORPORATES_RATE_LIMIT_S = 0.0

            mock_breakers.__getitem__ = MagicMock(
                return_value=MagicMock(call=MagicMock(return_value=mock_resp))
            )

            from app.modules.opencorporates_client import search_companies

            result = search_companies("SHADOW SHIPPING")
            assert len(result) == 1
            assert result[0]["name"] == "SHADOW SHIPPING LTD"
            assert result[0]["jurisdiction_code"] == "pa"

    def test_search_with_jurisdiction_filter(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _search_response()

        with (
            patch("app.modules.opencorporates_client.settings") as mock_settings,
            patch("app.modules.opencorporates_client.breakers") as mock_breakers,
            patch("app.modules.opencorporates_client._rate_limit"),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_API_URL = "https://api.opencorporates.com/v0.4"
            mock_settings.OPENCORPORATES_API_KEY = ""
            mock_settings.OPENCORPORATES_RATE_LIMIT_S = 0.0

            mock_cb = MagicMock()
            mock_cb.call = MagicMock(return_value=mock_resp)
            mock_breakers.__getitem__ = MagicMock(return_value=mock_cb)

            from app.modules.opencorporates_client import search_companies

            result = search_companies("SHADOW SHIPPING", jurisdiction_code="PA")
            assert len(result) == 1

            # Verify the jurisdiction was passed in the call
            call_args = mock_cb.call.call_args
            params = call_args[1].get("params", {})
            assert params.get("jurisdiction_code") == "pa"


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_circuit_breaker_open(self):
        with (
            patch("app.modules.opencorporates_client.settings") as mock_settings,
            patch("app.modules.opencorporates_client.breakers") as mock_breakers,
            patch("app.modules.opencorporates_client._rate_limit"),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_API_URL = "https://api.opencorporates.com/v0.4"
            mock_settings.OPENCORPORATES_API_KEY = ""
            mock_settings.OPENCORPORATES_RATE_LIMIT_S = 0.0

            mock_cb = MagicMock()
            mock_cb.call.side_effect = pybreaker.CircuitBreakerError()
            mock_breakers.__getitem__ = MagicMock(return_value=mock_cb)

            from app.modules.opencorporates_client import search_companies

            result = search_companies("SHADOW SHIPPING")
            assert result == []


# ---------------------------------------------------------------------------
# HTTP Error
# ---------------------------------------------------------------------------


class TestHTTPError:
    def test_http_error_returns_empty(self):
        with (
            patch("app.modules.opencorporates_client.settings") as mock_settings,
            patch("app.modules.opencorporates_client.breakers") as mock_breakers,
            patch("app.modules.opencorporates_client._rate_limit"),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_API_URL = "https://api.opencorporates.com/v0.4"
            mock_settings.OPENCORPORATES_API_KEY = ""
            mock_settings.OPENCORPORATES_RATE_LIMIT_S = 0.0

            mock_cb = MagicMock()
            mock_cb.call.side_effect = httpx.HTTPError("Connection failed")
            mock_breakers.__getitem__ = MagicMock(return_value=mock_cb)

            from app.modules.opencorporates_client import search_companies

            result = search_companies("SHADOW SHIPPING")
            assert result == []


# ---------------------------------------------------------------------------
# Fetch Company
# ---------------------------------------------------------------------------


class TestFetchCompany:
    def test_fetch_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _company_response()

        with (
            patch("app.modules.opencorporates_client.settings") as mock_settings,
            patch("app.modules.opencorporates_client.breakers") as mock_breakers,
            patch("app.modules.opencorporates_client._rate_limit"),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_API_URL = "https://api.opencorporates.com/v0.4"
            mock_settings.OPENCORPORATES_API_KEY = "test-key"
            mock_settings.OPENCORPORATES_RATE_LIMIT_S = 0.0

            mock_breakers.__getitem__ = MagicMock(
                return_value=MagicMock(call=MagicMock(return_value=mock_resp))
            )

            from app.modules.opencorporates_client import fetch_company

            result = fetch_company("pa", "12345")
            assert result is not None
            assert result["name"] == "SHADOW SHIPPING LTD"

    def test_fetch_404_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with (
            patch("app.modules.opencorporates_client.settings") as mock_settings,
            patch("app.modules.opencorporates_client.breakers") as mock_breakers,
            patch("app.modules.opencorporates_client._rate_limit"),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_API_URL = "https://api.opencorporates.com/v0.4"
            mock_settings.OPENCORPORATES_API_KEY = ""
            mock_settings.OPENCORPORATES_RATE_LIMIT_S = 0.0

            mock_breakers.__getitem__ = MagicMock(
                return_value=MagicMock(call=MagicMock(return_value=mock_resp))
            )

            from app.modules.opencorporates_client import fetch_company

            result = fetch_company("pa", "99999")
            assert result is None


# ---------------------------------------------------------------------------
# Fetch Officers
# ---------------------------------------------------------------------------


class TestFetchOfficers:
    def test_fetch_officers_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _officers_response()

        with (
            patch("app.modules.opencorporates_client.settings") as mock_settings,
            patch("app.modules.opencorporates_client.breakers") as mock_breakers,
            patch("app.modules.opencorporates_client._rate_limit"),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_API_URL = "https://api.opencorporates.com/v0.4"
            mock_settings.OPENCORPORATES_API_KEY = ""
            mock_settings.OPENCORPORATES_RATE_LIMIT_S = 0.0

            mock_breakers.__getitem__ = MagicMock(
                return_value=MagicMock(call=MagicMock(return_value=mock_resp))
            )

            from app.modules.opencorporates_client import fetch_officers

            result = fetch_officers("pa", "12345")
            assert len(result) == 1
            assert result[0]["name"] == "John Smith"
            assert result[0]["position"] == "director"


# ---------------------------------------------------------------------------
# API Key
# ---------------------------------------------------------------------------


class TestAPIKey:
    def test_api_key_passed_as_param(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _search_response([])

        with (
            patch("app.modules.opencorporates_client.settings") as mock_settings,
            patch("app.modules.opencorporates_client.breakers") as mock_breakers,
            patch("app.modules.opencorporates_client._rate_limit"),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_API_URL = "https://api.opencorporates.com/v0.4"
            mock_settings.OPENCORPORATES_API_KEY = "my-secret-key"
            mock_settings.OPENCORPORATES_RATE_LIMIT_S = 0.0

            mock_cb = MagicMock()
            mock_cb.call = MagicMock(return_value=mock_resp)
            mock_breakers.__getitem__ = MagicMock(return_value=mock_cb)

            from app.modules.opencorporates_client import search_companies

            search_companies("test")

            call_args = mock_cb.call.call_args
            params = call_args[1].get("params", {})
            assert params.get("api_token") == "my-secret-key"


# ---------------------------------------------------------------------------
# Secrecy Jurisdictions
# ---------------------------------------------------------------------------


class TestSecrecyJurisdictions:
    def test_known_secrecy_jurisdictions(self):
        from app.modules.opencorporates_client import SECRECY_JURISDICTIONS

        # Key shadow fleet jurisdictions
        assert "MH" in SECRECY_JURISDICTIONS  # Marshall Islands
        assert "LR" in SECRECY_JURISDICTIONS  # Liberia
        assert "PA" in SECRECY_JURISDICTIONS  # Panama
        assert "MT" in SECRECY_JURISDICTIONS  # Malta
        assert "CY" in SECRECY_JURISDICTIONS  # Cyprus
        assert "VG" in SECRECY_JURISDICTIONS  # British Virgin Islands
        assert "KY" in SECRECY_JURISDICTIONS  # Cayman Islands

    def test_non_secrecy_jurisdictions_excluded(self):
        from app.modules.opencorporates_client import SECRECY_JURISDICTIONS

        assert "US" not in SECRECY_JURISDICTIONS
        assert "GB" not in SECRECY_JURISDICTIONS
        assert "NO" not in SECRECY_JURISDICTIONS
        assert "DE" not in SECRECY_JURISDICTIONS
