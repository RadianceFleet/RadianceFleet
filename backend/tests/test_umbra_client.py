"""Tests for Umbra Space SAR satellite provider client."""

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

# -- Helpers -------------------------------------------------------------------

AOI_WKT = "POLYGON((10 55, 11 55, 11 56, 10 56, 10 55))"
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 1, 15, tzinfo=UTC)

_TOKEN_RESPONSE = {
    "access_token": "test-umbra-token",
    "expires_in": 86400,
    "token_type": "Bearer",
}


def _make_stac_response(features: list[dict] | None = None) -> dict:
    """Build a mock Umbra STAC v2 search response."""
    if features is None:
        features = [
            {
                "id": "umbra-sar-20260105-001",
                "properties": {
                    "datetime": "2026-01-05T14:30:00Z",
                    "sar:resolution_range": 0.25,
                    "sar:product_type": "GEC",
                    "sar:instrument_mode": "SPOTLIGHT",
                    "sar:polarizations": ["VV"],
                    "sat:orbit_state": "ascending",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 55], [11, 55], [11, 56], [10, 56], [10, 55]]],
                },
                "assets": {
                    "thumbnail": {"href": "https://umbra.space/thumb.png"},
                },
            }
        ]
    return {"type": "FeatureCollection", "features": features}


def _make_task_response(task_id: str = "task-umbra-abc", status: str = "SUBMITTED") -> dict:
    return {
        "taskId": task_id,
        "status": status,
        "statusMessage": "Task created",
        "deliveries": [],
    }


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.canopy.umbra.space/test"),
    )


@pytest.fixture(autouse=True)
def reset_umbra_breaker():
    """Reset the umbra circuit breaker and token cache between tests."""
    from app.modules.circuit_breakers import breakers
    from app.modules.satellite_providers import umbra_client

    breakers["umbra"] = pybreaker.CircuitBreaker(fail_max=5, reset_timeout=60, name="umbra")
    umbra_client._token_cache.clear()
    yield


@pytest.fixture
def umbra_provider():
    """Create an UmbraProvider with test credentials."""
    from app.modules.satellite_providers.umbra_client import UmbraProvider

    return UmbraProvider(client_id="test-client-id", client_secret="test-secret")


# -- Tests ---------------------------------------------------------------------


def test_search_archive_success(umbra_provider):
    """search_archive returns parsed ArchiveSearchResult list; cloud_cover_pct is None (SAR)."""
    token_resp = _mock_response(200, _TOKEN_RESPONSE)
    search_resp = _mock_response(200, _make_stac_response())

    with patch(
        "app.modules.satellite_providers.umbra_client.retry_request",
        side_effect=[token_resp, search_resp],
    ):
        results = umbra_provider.search_archive(AOI_WKT, START, END)

    assert len(results) == 1
    assert isinstance(results[0], ArchiveSearchResult)
    assert results[0].scene_id == "umbra-sar-20260105-001"
    assert results[0].provider == "umbra"
    # SAR is cloud-independent — cloud_cover_pct must always be None
    assert results[0].cloud_cover_pct is None
    assert results[0].resolution_m == 0.25
    assert results[0].estimated_cost_usd == 3000.0


def test_search_archive_no_results(umbra_provider):
    """search_archive returns empty list when no scenes found."""
    token_resp = _mock_response(200, _TOKEN_RESPONSE)
    search_resp = _mock_response(200, _make_stac_response(features=[]))

    with patch(
        "app.modules.satellite_providers.umbra_client.retry_request",
        side_effect=[token_resp, search_resp],
    ):
        results = umbra_provider.search_archive(AOI_WKT, START, END)

    assert results == []


def test_submit_order_success(umbra_provider):
    """submit_order returns OrderSubmitResult on success."""
    token_resp = _mock_response(200, _TOKEN_RESPONSE)
    task_resp = _mock_response(200, _make_task_response())

    with patch(
        "app.modules.satellite_providers.umbra_client.retry_request",
        side_effect=[token_resp, task_resp],
    ):
        result = umbra_provider.submit_order(["scene-1", "scene-2"])

    assert isinstance(result, OrderSubmitResult)
    assert result.external_order_id == "task-umbra-abc"
    assert result.status == "accepted"
    assert result.estimated_cost_usd == 6000.0


def test_check_order_status_delivered(umbra_provider):
    """check_order_status maps DELIVERED to 'delivered' with download URLs."""
    resp_data = _make_task_response(status="DELIVERED")
    resp_data["deliveries"] = [
        {"url": "https://umbra.space/download/scene1.tif"},
        {"url": "https://umbra.space/download/scene2.tif"},
    ]
    token_resp = _mock_response(200, _TOKEN_RESPONSE)
    status_resp = _mock_response(200, resp_data)

    with patch(
        "app.modules.satellite_providers.umbra_client.retry_request",
        side_effect=[token_resp, status_resp],
    ):
        result = umbra_provider.check_order_status("task-umbra-abc")

    assert isinstance(result, OrderStatusResult)
    assert result.status == "delivered"
    assert len(result.scene_urls) == 2
    assert result.metadata["umbra_status"] == "DELIVERED"


def test_check_order_status_processing(umbra_provider):
    """check_order_status maps SCHEDULED to 'processing'."""
    token_resp = _mock_response(200, _TOKEN_RESPONSE)
    status_resp = _mock_response(200, _make_task_response(status="SCHEDULED"))

    with patch(
        "app.modules.satellite_providers.umbra_client.retry_request",
        side_effect=[token_resp, status_resp],
    ):
        result = umbra_provider.check_order_status("task-umbra-abc")

    assert result.status == "processing"


def test_check_order_status_failed(umbra_provider):
    """check_order_status maps FAILED to 'failed'."""
    token_resp = _mock_response(200, _TOKEN_RESPONSE)
    status_resp = _mock_response(200, _make_task_response(status="FAILED"))

    with patch(
        "app.modules.satellite_providers.umbra_client.retry_request",
        side_effect=[token_resp, status_resp],
    ):
        result = umbra_provider.check_order_status("task-umbra-abc")

    assert result.status == "failed"


def test_cancel_order(umbra_provider):
    """cancel_order returns True on success."""
    token_resp = _mock_response(200, _TOKEN_RESPONSE)
    cancel_resp = _mock_response(200, {"status": "CANCELLED"})

    with patch(
        "app.modules.satellite_providers.umbra_client.retry_request",
        side_effect=[token_resp, cancel_resp],
    ):
        assert umbra_provider.cancel_order("task-umbra-abc") is True


def test_circuit_breaker_trips(umbra_provider):
    """Circuit breaker trips after 5 repeated failures."""

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch(
        "app.modules.satellite_providers.umbra_client.retry_request",
        side_effect=_raise,
    ):
        for _ in range(5):
            try:
                umbra_provider.check_order_status("task-umbra-abc")
            except (httpx.ConnectError, pybreaker.CircuitBreakerError):
                pass

        with pytest.raises(pybreaker.CircuitBreakerError):
            umbra_provider.check_order_status("task-umbra-abc")


def test_api_key_not_configured():
    """UmbraProvider raises ValueError when credentials are not set."""
    from app.modules.satellite_providers.umbra_client import UmbraProvider

    with patch("app.modules.satellite_providers.umbra_client.settings") as mock_settings:
        mock_settings.UMBRA_CLIENT_ID = None
        mock_settings.UMBRA_API_KEY = None
        with pytest.raises(ValueError, match="UMBRA_CLIENT_ID"):
            UmbraProvider(client_id=None, client_secret=None)
