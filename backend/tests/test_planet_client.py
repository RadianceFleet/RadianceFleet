"""Tests for Planet Labs satellite provider client."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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


def _make_planet_search_response(features: list[dict] | None = None) -> dict:
    """Build a mock Planet quick-search response."""
    if features is None:
        features = [
            {
                "id": "20260101_120000_ssc1",
                "properties": {
                    "acquired": "2026-01-05T12:00:00Z",
                    "cloud_cover": 0.15,
                    "pixel_resolution": 3.0,
                    "sun_elevation": 30.0,
                    "view_angle": 5.0,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[10, 55], [11, 55], [11, 56], [10, 56], [10, 55]]],
                },
                "_links": {"thumbnail": "https://planet.com/thumb.png"},
            }
        ]
    return {"features": features, "type": "FeatureCollection"}


def _make_planet_order_response(order_id: str = "order-abc", state: str = "queued") -> dict:
    return {
        "id": order_id,
        "state": state,
        "name": "radiancefleet-test",
        "_links": {"results": []},
    }


def _mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://api.planet.com/test"),
    )
    return resp


@pytest.fixture(autouse=True)
def reset_planet_breaker():
    """Reset the planet circuit breaker between tests."""
    from app.modules.circuit_breakers import breakers
    breakers["planet"] = pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="planet"
    )
    yield


@pytest.fixture
def planet_provider():
    """Create a PlanetProvider with a test API key."""
    from app.modules.satellite_providers.planet_client import PlanetProvider
    return PlanetProvider(api_key="test-planet-key")


# ── Tests ────────────────────────────────────────────────────────────────────


def test_search_archive_success(planet_provider):
    """search_archive returns parsed ArchiveSearchResult list on success."""
    mock_resp = _mock_response(200, _make_planet_search_response())

    with patch("app.modules.satellite_providers.planet_client.retry_request", return_value=mock_resp):
        results = planet_provider.search_archive(AOI_WKT, START, END)

    assert len(results) == 1
    assert isinstance(results[0], ArchiveSearchResult)
    assert results[0].scene_id == "20260101_120000_ssc1"
    assert results[0].provider == "planet"
    assert results[0].cloud_cover_pct == 15.0
    assert results[0].resolution_m == 3.0


def test_search_archive_no_results(planet_provider):
    """search_archive returns empty list when no scenes found."""
    mock_resp = _mock_response(200, _make_planet_search_response(features=[]))

    with patch("app.modules.satellite_providers.planet_client.retry_request", return_value=mock_resp):
        results = planet_provider.search_archive(AOI_WKT, START, END)

    assert results == []


def test_submit_order_success(planet_provider):
    """submit_order returns OrderSubmitResult on success."""
    mock_resp = _mock_response(200, _make_planet_order_response())

    with patch("app.modules.satellite_providers.planet_client.retry_request", return_value=mock_resp):
        result = planet_provider.submit_order(["scene-1", "scene-2"])

    assert isinstance(result, OrderSubmitResult)
    assert result.external_order_id == "order-abc"
    assert result.status == "queued"


def test_check_order_status_delivered(planet_provider):
    """check_order_status returns delivered status with download URLs."""
    resp_data = _make_planet_order_response(state="success")
    resp_data["_links"]["results"] = [
        {"location": "https://planet.com/download/scene1.tif"},
        {"location": "https://planet.com/download/scene2.tif"},
    ]
    mock_resp = _mock_response(200, resp_data)

    with patch("app.modules.satellite_providers.planet_client.retry_request", return_value=mock_resp):
        result = planet_provider.check_order_status("order-abc")

    assert isinstance(result, OrderStatusResult)
    assert result.status == "delivered"
    assert len(result.scene_urls) == 2


def test_check_order_status_processing(planet_provider):
    """check_order_status maps 'running' to 'processing'."""
    mock_resp = _mock_response(200, _make_planet_order_response(state="running"))

    with patch("app.modules.satellite_providers.planet_client.retry_request", return_value=mock_resp):
        result = planet_provider.check_order_status("order-abc")

    assert result.status == "processing"


def test_check_order_status_failed(planet_provider):
    """check_order_status maps 'failed' correctly."""
    mock_resp = _mock_response(200, _make_planet_order_response(state="failed"))

    with patch("app.modules.satellite_providers.planet_client.retry_request", return_value=mock_resp):
        result = planet_provider.check_order_status("order-abc")

    assert result.status == "failed"


def test_cancel_order(planet_provider):
    """cancel_order returns True on 200 response."""
    mock_resp = _mock_response(200, {"state": "cancelled"})

    with patch("app.modules.satellite_providers.planet_client.retry_request", return_value=mock_resp):
        assert planet_provider.cancel_order("order-abc") is True


def test_circuit_breaker_trips(planet_provider):
    """Circuit breaker trips after repeated failures."""
    from app.modules.circuit_breakers import breakers

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch("app.modules.satellite_providers.planet_client.retry_request", side_effect=_raise):
        for _ in range(5):
            try:
                planet_provider.check_order_status("order-abc")
            except (httpx.ConnectError, pybreaker.CircuitBreakerError):
                pass

        with pytest.raises(pybreaker.CircuitBreakerError):
            planet_provider.check_order_status("order-abc")


def test_api_key_not_configured():
    """PlanetProvider raises ValueError when API key is not set."""
    from app.modules.satellite_providers.planet_client import PlanetProvider

    with patch("app.modules.satellite_providers.planet_client.settings") as mock_settings:
        mock_settings.PLANET_API_KEY = None
        with pytest.raises(ValueError, match="PLANET_API_KEY"):
            PlanetProvider(api_key=None)
