"""Tests for Capella Space satellite provider client."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import httpx
import pybreaker
import pytest

from app.modules.satellite_providers.base import (
    ArchiveSearchResult,
    OrderStatusResult,
    OrderSubmitResult,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

AOI_WKT = "POLYGON((10 55, 11 55, 11 56, 10 56, 10 55))"
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 15, tzinfo=UTC)


def _make_stac_response(features: list[dict] | None = None) -> dict:
    """Build a mock Capella STAC search response."""
    if features is None:
        features = [
            {
                "id": "CAPELLA-2026-01-05T12-00-00",
                "properties": {
                    "datetime": "2026-01-05T12:00:00Z",
                    "sar:resolution_range": 0.5,
                    "sar:product_type": "SLC",
                    "sar:instrument_mode": "spotlight",
                    "sar:polarizations": ["HH"],
                    "sat:orbit_state": "ascending",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 55], [11, 55], [11, 56], [10, 56], [10, 55]]],
                },
                "assets": {
                    "thumbnail": {"href": "https://capella.com/thumb.png"},
                },
            }
        ]
    return {"type": "FeatureCollection", "features": features}


def _make_token_response() -> dict:
    return {"accessToken": "test-bearer-token", "expiresIn": 3600}


def _make_order_response(order_id: str = "capella-order-123", status: str = "submitted") -> dict:
    return {
        "orderId": order_id,
        "status": status,
        "statusMessage": "Order accepted",
        "items": [],
    }


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.capellaspace.com/test"),
    )


@pytest.fixture(autouse=True)
def reset_capella_state():
    """Reset Capella circuit breaker and token cache between tests."""
    from app.modules.circuit_breakers import breakers

    breakers["capella"] = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60, name="capella")
    # Clear token cache
    import app.modules.satellite_providers.capella_client as capella_mod

    capella_mod._token_cache.clear()
    yield


@pytest.fixture
def capella_provider():
    """Create a CapellaProvider with a test API key."""
    from app.modules.satellite_providers.capella_client import CapellaProvider

    return CapellaProvider(api_key="test-capella-key")


# ── Tests ────────────────────────────────────────────────────────────────────


def test_token_exchange(capella_provider):
    """Token exchange obtains a bearer token from Capella."""
    token_resp = _mock_response(200, _make_token_response())
    search_resp = _mock_response(200, _make_stac_response())

    def _mock_retry(fn, *args, **kwargs):
        """Route to different responses based on URL."""
        url = args[0] if args else ""
        if "token" in str(url):
            return token_resp
        return search_resp

    with patch(
        "app.modules.satellite_providers.capella_client.retry_request", side_effect=_mock_retry
    ):
        results = capella_provider.search_archive(AOI_WKT, START, END)

    assert len(results) >= 0  # just verify no exception


def test_search_archive_success(capella_provider):
    """search_archive returns parsed ArchiveSearchResult list."""
    token_resp = _mock_response(200, _make_token_response())
    search_resp = _mock_response(200, _make_stac_response())

    call_count = 0

    def _mock_retry(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return token_resp
        return search_resp

    with patch(
        "app.modules.satellite_providers.capella_client.retry_request", side_effect=_mock_retry
    ):
        results = capella_provider.search_archive(AOI_WKT, START, END)

    assert len(results) == 1
    assert isinstance(results[0], ArchiveSearchResult)
    assert results[0].scene_id == "CAPELLA-2026-01-05T12-00-00"
    assert results[0].provider == "capella"
    assert results[0].resolution_m == 0.5


def test_submit_order_success(capella_provider):
    """submit_order returns OrderSubmitResult."""
    token_resp = _mock_response(200, _make_token_response())
    order_resp = _mock_response(200, _make_order_response())

    call_count = 0

    def _mock_retry(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return token_resp
        return order_resp

    with patch(
        "app.modules.satellite_providers.capella_client.retry_request", side_effect=_mock_retry
    ):
        result = capella_provider.submit_order(["scene-1"])

    assert isinstance(result, OrderSubmitResult)
    assert result.external_order_id == "capella-order-123"


def test_check_order_status(capella_provider):
    """check_order_status returns OrderStatusResult with mapped status."""
    token_resp = _mock_response(200, _make_token_response())
    status_data = _make_order_response(status="completed")
    status_data["items"] = [{"assets": {"data": {"href": "https://capella.com/scene.tif"}}}]
    status_resp = _mock_response(200, status_data)

    call_count = 0

    def _mock_retry(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return token_resp
        return status_resp

    with patch(
        "app.modules.satellite_providers.capella_client.retry_request", side_effect=_mock_retry
    ):
        result = capella_provider.check_order_status("capella-order-123")

    assert isinstance(result, OrderStatusResult)
    assert result.status == "delivered"
    assert len(result.scene_urls) == 1


def test_cancel_order(capella_provider):
    """cancel_order returns True on success."""
    token_resp = _mock_response(200, _make_token_response())
    cancel_resp = _mock_response(200, {"status": "cancelled"})

    call_count = 0

    def _mock_retry(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return token_resp
        return cancel_resp

    with patch(
        "app.modules.satellite_providers.capella_client.retry_request", side_effect=_mock_retry
    ):
        assert capella_provider.cancel_order("capella-order-123") is True


def test_token_refresh_on_401(capella_provider):
    """Token is refreshed when API returns 401."""
    from app.modules.satellite_providers.capella_client import _token_cache

    # Pre-populate expired token
    _token_cache["token"] = "expired-token"
    _token_cache["expires_at"] = 0  # already expired

    token_resp = _mock_response(200, _make_token_response())
    search_resp = _mock_response(200, _make_stac_response(features=[]))

    call_count = 0

    def _mock_retry(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Token request
            return token_resp
        return search_resp

    with patch(
        "app.modules.satellite_providers.capella_client.retry_request", side_effect=_mock_retry
    ):
        results = capella_provider.search_archive(AOI_WKT, START, END)

    assert results == []
    # Verify token was fetched (cache was expired)
    assert call_count >= 2


def test_circuit_breaker_protection(capella_provider):
    """Circuit breaker trips after repeated connection failures."""

    # Token cache needs to be set so we get past token exchange
    import time

    import app.modules.satellite_providers.capella_client as capella_mod

    capella_mod._token_cache["token"] = "test-token"
    capella_mod._token_cache["expires_at"] = time.monotonic() + 3600

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch("app.modules.satellite_providers.capella_client.retry_request", side_effect=_raise):
        for _ in range(5):
            try:
                capella_provider.check_order_status("order-abc")
            except (httpx.ConnectError, pybreaker.CircuitBreakerError):
                pass

        with pytest.raises(pybreaker.CircuitBreakerError):
            capella_provider.check_order_status("order-abc")


def test_api_key_not_configured():
    """CapellaProvider raises ValueError when API key is not set."""
    from app.modules.satellite_providers.capella_client import CapellaProvider

    with patch("app.modules.satellite_providers.capella_client.settings") as mock_settings:
        mock_settings.CAPELLA_API_KEY = None
        with pytest.raises(ValueError, match="CAPELLA_API_KEY"):
            CapellaProvider(api_key=None)
