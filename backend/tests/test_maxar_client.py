"""Tests for Maxar satellite provider client."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pybreaker

from app.modules.satellite_providers.base import (
    ArchiveSearchResult,
    OrderStatusResult,
    OrderSubmitResult,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

AOI_WKT = "POLYGON((10 55, 11 55, 11 56, 10 56, 10 55))"
START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _make_maxar_search_response(features: list[dict] | None = None) -> dict:
    """Build a mock Maxar Discovery API STAC response."""
    if features is None:
        features = [
            {
                "id": "WV03_20260105_120000_001",
                "properties": {
                    "datetime": "2026-01-05T12:00:00Z",
                    "eo:cloud_cover": 12.5,
                    "gsd": 0.31,
                    "platform": "WorldView-3",
                    "view:off_nadir": 15.2,
                    "view:sun_elevation": 35.0,
                    "constellation": "DigitalGlobe",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 55], [11, 55], [11, 56], [10, 56], [10, 55]]],
                },
                "assets": {
                    "thumbnail": {"href": "https://maxar.com/thumb.png"},
                },
            }
        ]
    return {"type": "FeatureCollection", "features": features}


def _make_maxar_order_response(
    order_id: str = "maxar-order-123", status: str = "SUBMITTED"
) -> dict:
    return {
        "order_id": order_id,
        "status": status,
        "message": "Order created",
        "output_files": [],
    }


def _make_maxar_status_response(
    order_id: str = "maxar-order-123",
    status: str = "SUCCEEDED",
    output_files: list[dict] | None = None,
) -> dict:
    if output_files is None:
        output_files = []
    return {
        "order_id": order_id,
        "status": status,
        "message": None,
        "output_files": output_files,
        "total_cost_usd": 375.0,
    }


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.maxar.com/test"),
    )
    return resp


@pytest.fixture(autouse=True)
def reset_maxar_breaker():
    """Reset the maxar circuit breaker between tests."""
    from app.modules.circuit_breakers import breakers

    breakers["maxar"] = pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="maxar"
    )
    yield


@pytest.fixture(autouse=True)
def clear_token_cache():
    """Clear the module-level token cache between tests."""
    from app.modules.satellite_providers import maxar_client

    maxar_client._token_cache.clear()
    yield


@pytest.fixture
def maxar_provider():
    """Create a MaxarProvider with a test API key (long enough to be treated as API key)."""
    from app.modules.satellite_providers.maxar_client import MaxarProvider

    # Use a >50 char alphanum string so _is_api_key returns True (no OAuth needed)
    long_key = "a" * 60
    return MaxarProvider(api_key=long_key)


# ── Tests ────────────────────────────────────────────────────────────────────


def test_search_archive_success(maxar_provider):
    """search_archive returns parsed ArchiveSearchResult list on success."""
    mock_resp = _mock_response(200, _make_maxar_search_response())

    with patch(
        "app.modules.satellite_providers.maxar_client.retry_request",
        return_value=mock_resp,
    ):
        results = maxar_provider.search_archive(AOI_WKT, START, END)

    assert len(results) == 1
    assert isinstance(results[0], ArchiveSearchResult)
    assert results[0].scene_id == "WV03_20260105_120000_001"
    assert results[0].provider == "maxar"
    assert results[0].cloud_cover_pct == 12.5
    assert results[0].resolution_m == 0.31


def test_search_archive_no_results(maxar_provider):
    """search_archive returns empty list when no scenes found."""
    mock_resp = _mock_response(200, _make_maxar_search_response(features=[]))

    with patch(
        "app.modules.satellite_providers.maxar_client.retry_request",
        return_value=mock_resp,
    ):
        results = maxar_provider.search_archive(AOI_WKT, START, END)

    assert results == []


def test_submit_order_success(maxar_provider):
    """submit_order returns OrderSubmitResult on success."""
    mock_resp = _mock_response(200, _make_maxar_order_response())

    with patch(
        "app.modules.satellite_providers.maxar_client.retry_request",
        return_value=mock_resp,
    ):
        result = maxar_provider.submit_order(["scene-1", "scene-2"])

    assert isinstance(result, OrderSubmitResult)
    assert result.external_order_id == "maxar-order-123"
    assert result.status == "SUBMITTED"


def test_check_order_status_delivered(maxar_provider):
    """check_order_status maps SUCCEEDED to 'delivered' with download URLs."""
    resp_data = _make_maxar_status_response(
        status="SUCCEEDED",
        output_files=[
            {"url": "https://maxar.com/download/scene1.tif"},
            {"url": "https://maxar.com/download/scene2.tif"},
        ],
    )
    mock_resp = _mock_response(200, resp_data)

    with patch(
        "app.modules.satellite_providers.maxar_client.retry_request",
        return_value=mock_resp,
    ):
        result = maxar_provider.check_order_status("maxar-order-123")

    assert isinstance(result, OrderStatusResult)
    assert result.status == "delivered"
    assert len(result.scene_urls) == 2


def test_check_order_status_processing(maxar_provider):
    """check_order_status maps RUNNING to 'processing'."""
    mock_resp = _mock_response(200, _make_maxar_status_response(status="RUNNING"))

    with patch(
        "app.modules.satellite_providers.maxar_client.retry_request",
        return_value=mock_resp,
    ):
        result = maxar_provider.check_order_status("maxar-order-123")

    assert result.status == "processing"


def test_check_order_status_failed(maxar_provider):
    """check_order_status maps FAILED to 'failed'."""
    mock_resp = _mock_response(200, _make_maxar_status_response(status="FAILED"))

    with patch(
        "app.modules.satellite_providers.maxar_client.retry_request",
        return_value=mock_resp,
    ):
        result = maxar_provider.check_order_status("maxar-order-123")

    assert result.status == "failed"


def test_cancel_order(maxar_provider):
    """cancel_order returns True on success."""
    mock_resp = _mock_response(200, {"status": "CANCELLED"})

    with patch(
        "app.modules.satellite_providers.maxar_client.retry_request",
        return_value=mock_resp,
    ):
        assert maxar_provider.cancel_order("maxar-order-123") is True


def test_circuit_breaker_trips(maxar_provider):
    """Circuit breaker trips after repeated failures."""
    from app.modules.circuit_breakers import breakers

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch(
        "app.modules.satellite_providers.maxar_client.retry_request",
        side_effect=_raise,
    ):
        for _ in range(5):
            try:
                maxar_provider.check_order_status("maxar-order-123")
            except (httpx.ConnectError, pybreaker.CircuitBreakerError):
                pass

        with pytest.raises(pybreaker.CircuitBreakerError):
            maxar_provider.check_order_status("maxar-order-123")


def test_api_key_not_configured():
    """MaxarProvider raises ValueError when API key is not set."""
    from app.modules.satellite_providers.maxar_client import MaxarProvider

    with patch("app.modules.satellite_providers.maxar_client.settings") as mock_settings:
        mock_settings.MAXAR_API_KEY = None
        with pytest.raises(ValueError, match="MAXAR_API_KEY"):
            MaxarProvider(api_key=None)
