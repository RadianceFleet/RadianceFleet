"""Tests for satellite_order_manager module."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.modules.satellite_order_manager import (
    get_satellite_budget_status,
    search_archive_for_alert,
    submit_order,
    poll_order_status,
    cancel_order,
    _compute_aoi,
)
from app.modules.satellite_providers.base import (
    ArchiveSearchResult,
    OrderSubmitResult,
    OrderStatusResult,
)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

def test_budget_no_orders(mock_db):
    """Budget status with no orders returns full budget."""
    mock_db.query.return_value.filter.return_value.scalar.return_value = 0.0
    result = get_satellite_budget_status(mock_db)
    assert result["budget_usd"] == 2000.0
    assert result["spent_usd"] == 0.0
    assert result["remaining_usd"] == 2000.0


def test_budget_with_existing_orders(mock_db):
    """Budget status reflects existing spend."""
    mock_db.query.return_value.filter.return_value.scalar.return_value = 750.0
    result = get_satellite_budget_status(mock_db)
    assert result["spent_usd"] == 750.0
    assert result["remaining_usd"] == 1250.0


# ---------------------------------------------------------------------------
# Archive search
# ---------------------------------------------------------------------------

def test_search_archive_creates_draft(mock_db):
    """search_archive_for_alert creates a draft order and returns scene list."""
    # Mock alert
    alert = MagicMock()
    alert.gap_event_id = 42
    alert.gap_start_utc = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    alert.gap_end_utc = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
    alert.gap_off_lat = 55.0
    alert.gap_off_lon = 20.0
    alert.gap_on_lat = 56.0
    alert.gap_on_lon = 21.0
    alert.start_point = None
    alert.end_point = None

    # query(AISGapEvent).filter().first() -> alert
    mock_db.query.return_value.filter.return_value.first.return_value = alert

    # Mock provider
    scene = ArchiveSearchResult(
        scene_id="PSScene:abc123",
        provider="planet",
        acquired_at=datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc),
        cloud_cover_pct=5.0,
        resolution_m=3.0,
        estimated_cost_usd=50.0,
    )
    mock_provider = MagicMock()
    mock_provider.return_value.search_archive.return_value = [scene]

    with patch("app.modules.satellite_providers.get_provider", return_value=mock_provider):
        # After first query(AISGapEvent).filter().first() returns alert,
        # the second query(SatelliteCheck).filter().first() needs to return None
        mock_db.query.return_value.filter.return_value.first.side_effect = [alert, None]
        result = search_archive_for_alert(mock_db, 42, "planet")

    assert result["provider"] == "planet"
    assert result["scenes_found"] == 1
    assert result["scenes"][0]["scene_id"] == "PSScene:abc123"
    mock_db.add.assert_called()
    mock_db.commit.assert_called()


def test_search_archive_alert_not_found(mock_db):
    """search_archive_for_alert raises ValueError if alert not found."""
    mock_db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(ValueError, match="Alert 999 not found"):
        search_archive_for_alert(mock_db, 999, "planet")


# ---------------------------------------------------------------------------
# Submit order
# ---------------------------------------------------------------------------

def test_submit_order_changes_status(mock_db):
    """submit_order changes order status to submitted."""
    order = MagicMock()
    order.satellite_order_id = 1
    order.status = "draft"
    order.provider = "planet"

    mock_db.query.return_value.filter.return_value.first.return_value = order
    # Budget query: coalesce(sum(...)) -> 0
    mock_db.query.return_value.filter.return_value.scalar.return_value = 0.0

    submit_result = OrderSubmitResult(
        external_order_id="ext-001",
        status="submitted",
        estimated_cost_usd=100.0,
    )
    mock_provider = MagicMock()
    mock_provider.return_value.submit_order.return_value = submit_result

    with patch("app.modules.satellite_providers.get_provider", return_value=mock_provider):
        result = submit_order(mock_db, 1, ["scene-a"])

    assert result["status"] == "submitted"
    assert result["external_order_id"] == "ext-001"
    assert order.status == "submitted"
    assert order.external_order_id == "ext-001"


def test_submit_order_budget_exceeded(mock_db):
    """submit_order rejects when budget is insufficient."""
    order = MagicMock()
    order.satellite_order_id = 1
    order.status = "draft"
    order.provider = "planet"

    mock_db.query.return_value.filter.return_value.first.return_value = order
    # Budget query: spent 1950 of 2000
    mock_db.query.return_value.filter.return_value.scalar.return_value = 1950.0

    with pytest.raises(ValueError, match="Insufficient budget"):
        submit_order(mock_db, 1, ["scene-a"])


def test_submit_order_not_draft(mock_db):
    """submit_order rejects non-draft orders."""
    order = MagicMock()
    order.satellite_order_id = 1
    order.status = "submitted"

    mock_db.query.return_value.filter.return_value.first.return_value = order

    with pytest.raises(ValueError, match="is not a draft"):
        submit_order(mock_db, 1, ["scene-a"])


# ---------------------------------------------------------------------------
# Poll status
# ---------------------------------------------------------------------------

def test_poll_order_status_updates(mock_db):
    """poll_order_status updates order status from provider."""
    order = MagicMock()
    order.satellite_order_id = 1
    order.external_order_id = "ext-001"
    order.provider = "planet"
    order.status = "submitted"

    mock_db.query.return_value.filter.return_value.all.return_value = [order]

    status_result = OrderStatusResult(
        external_order_id="ext-001",
        status="delivered",
        scene_urls=["https://example.com/scene.tif"],
        cost_usd=95.0,
    )
    mock_provider = MagicMock()
    mock_provider.return_value.check_order_status.return_value = status_result

    with patch("app.modules.satellite_providers.get_provider", return_value=mock_provider):
        results = poll_order_status(mock_db)

    assert len(results) == 1
    assert results[0]["status"] == "delivered"
    assert order.status == "delivered"
    assert order.cost_confirmed is True


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

def test_cancel_order_changes_status(mock_db):
    """cancel_order sets status to cancelled."""
    order = MagicMock()
    order.satellite_order_id = 1
    order.status = "submitted"
    order.provider = "planet"
    order.external_order_id = "ext-001"

    mock_db.query.return_value.filter.return_value.first.return_value = order

    mock_provider = MagicMock()
    with patch("app.modules.satellite_providers.get_provider", return_value=mock_provider):
        result = cancel_order(mock_db, 1)

    assert result["status"] == "cancelled"
    assert order.status == "cancelled"
    mock_db.commit.assert_called()


def test_cancel_delivered_raises_error(mock_db):
    """Cannot cancel an already-delivered order."""
    order = MagicMock()
    order.satellite_order_id = 1
    order.status = "delivered"

    mock_db.query.return_value.filter.return_value.first.return_value = order

    with pytest.raises(ValueError, match="Cannot cancel order in delivered state"):
        cancel_order(mock_db, 1)


# ---------------------------------------------------------------------------
# AOI computation
# ---------------------------------------------------------------------------

def test_compute_aoi_with_gap_positions():
    """_compute_aoi builds a bounding box from gap off/on positions."""
    alert = MagicMock()
    alert.gap_off_lat = 55.0
    alert.gap_off_lon = 20.0
    alert.gap_on_lat = 56.0
    alert.gap_on_lon = 21.0
    alert.start_point = None
    alert.end_point = None

    wkt = _compute_aoi(alert)
    assert wkt.startswith("POLYGON((")
    assert "19.9" in wkt  # min_lon - margin
    assert "21.1" in wkt  # max_lon + margin


def test_compute_aoi_fallback_no_positions():
    """_compute_aoi returns a fallback polygon when no positions available."""
    alert = MagicMock()
    alert.gap_off_lat = None
    alert.gap_off_lon = None
    alert.gap_on_lat = None
    alert.gap_on_lon = None
    alert.start_point = None
    alert.end_point = None

    wkt = _compute_aoi(alert)
    assert wkt == "POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))"
