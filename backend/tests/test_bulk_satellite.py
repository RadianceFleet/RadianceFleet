"""Tests for bulk satellite ordering workflow."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.satellite_bulk_order import SatelliteBulkOrder
from app.models.satellite_bulk_order_item import SatelliteBulkOrderItem
from app.models.satellite_order import SatelliteOrder
from app.models.vessel import Vessel


@pytest.fixture
def db():
    """Create an in-memory SQLite database with all tables for each test."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def vessel(db):
    """Create a test vessel."""
    v = Vessel(
        mmsi="123456789",
        name="TEST VESSEL",
        flag="PA",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@pytest.fixture
def vessel2(db):
    """Create a second test vessel."""
    v = Vessel(
        mmsi="987654321",
        name="TEST VESSEL 2",
        flag="LR",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@pytest.fixture
def mock_settings():
    """Mock settings with bulk ordering enabled."""
    with patch("app.modules.bulk_satellite_manager.settings") as mock:
        mock.SATELLITE_BULK_ORDER_ENABLED = True
        mock.SATELLITE_BULK_MAX_ITEMS = 100
        mock.SATELLITE_BULK_PROCESS_INTERVAL = 3600
        mock.SATELLITE_MONTHLY_BUDGET_USD = 2000.0
        yield mock


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


def test_create_bulk_order_model(db):
    """SatelliteBulkOrder can be created with minimal fields."""
    order = SatelliteBulkOrder(
        name="Test Bulk Order",
        status="draft",
        priority=5,
        total_orders=3,
    )
    db.add(order)
    db.commit()
    fetched = db.query(SatelliteBulkOrder).first()
    assert fetched is not None
    assert fetched.name == "Test Bulk Order"
    assert fetched.status == "draft"
    assert fetched.priority == 5
    assert fetched.total_orders == 3


def test_bulk_order_defaults(db):
    """SatelliteBulkOrder has correct default values."""
    order = SatelliteBulkOrder(name="Defaults", status="draft")
    db.add(order)
    db.commit()
    fetched = db.query(SatelliteBulkOrder).first()
    assert fetched.submitted_orders == 0
    assert fetched.delivered_orders == 0
    assert fetched.failed_orders == 0
    assert fetched.estimated_total_cost_usd is None
    assert fetched.actual_total_cost_usd is None
    assert fetched.budget_cap_usd is None


def test_create_bulk_order_item_model(db, vessel):
    """SatelliteBulkOrderItem can be created and linked to bulk order."""
    order = SatelliteBulkOrder(name="Parent", status="draft", total_orders=1)
    db.add(order)
    db.flush()

    item = SatelliteBulkOrderItem(
        bulk_order_id=order.bulk_order_id,
        vessel_id=vessel.vessel_id,
        priority_rank=1,
        status="pending",
    )
    db.add(item)
    db.commit()

    fetched = db.query(SatelliteBulkOrderItem).first()
    assert fetched is not None
    assert fetched.vessel_id == vessel.vessel_id
    assert fetched.status == "pending"
    assert fetched.bulk_order_id == order.bulk_order_id


def test_bulk_order_item_optional_fields(db, vessel):
    """SatelliteBulkOrderItem optional fields can be null."""
    order = SatelliteBulkOrder(name="Parent", status="draft", total_orders=1)
    db.add(order)
    db.flush()

    item = SatelliteBulkOrderItem(
        bulk_order_id=order.bulk_order_id,
        vessel_id=vessel.vessel_id,
        priority_rank=1,
        status="pending",
    )
    db.add(item)
    db.commit()
    fetched = db.query(SatelliteBulkOrderItem).first()
    assert fetched.satellite_order_id is None
    assert fetched.alert_id is None
    assert fetched.provider_preference is None
    assert fetched.aoi_wkt is None
    assert fetched.skip_reason is None


def test_bulk_order_relationship(db, vessel):
    """SatelliteBulkOrder.items relationship works."""
    order = SatelliteBulkOrder(name="With Items", status="draft", total_orders=2)
    db.add(order)
    db.flush()

    for i in range(2):
        db.add(SatelliteBulkOrderItem(
            bulk_order_id=order.bulk_order_id,
            vessel_id=vessel.vessel_id,
            priority_rank=i + 1,
            status="pending",
        ))
    db.commit()
    db.refresh(order)
    assert len(order.items) == 2


# ---------------------------------------------------------------------------
# create_bulk_order tests
# ---------------------------------------------------------------------------


def test_create_bulk_order(db, vessel, mock_settings):
    """create_bulk_order creates a draft order with items."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    result = create_bulk_order(
        db,
        name="Test Order",
        items=[{"vessel_id": vessel.vessel_id}],
        priority=7,
        budget_cap=500.0,
    )
    assert result.name == "Test Order"
    assert result.status == "draft"
    assert result.priority == 7
    assert result.total_orders == 1
    assert result.budget_cap_usd == 500.0
    assert result.estimated_total_cost_usd == 100.0


def test_create_bulk_order_multiple_items(db, vessel, vessel2, mock_settings):
    """create_bulk_order handles multiple items."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    result = create_bulk_order(
        db,
        name="Multi",
        items=[
            {"vessel_id": vessel.vessel_id, "provider_preference": "planet"},
            {"vessel_id": vessel2.vessel_id, "provider_preference": "capella"},
        ],
    )
    assert result.total_orders == 2
    assert result.estimated_total_cost_usd == 200.0

    items = db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == result.bulk_order_id
    ).all()
    assert len(items) == 2


def test_create_bulk_order_empty_items_fails(db, mock_settings):
    """create_bulk_order raises on empty items."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    with pytest.raises(ValueError, match="At least one item"):
        create_bulk_order(db, name="Empty", items=[])


def test_create_bulk_order_empty_name_fails(db, vessel, mock_settings):
    """create_bulk_order raises on empty name."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    with pytest.raises(ValueError, match="Name is required"):
        create_bulk_order(db, name="", items=[{"vessel_id": vessel.vessel_id}])


def test_create_bulk_order_max_items_exceeded(db, vessel, mock_settings):
    """create_bulk_order raises when exceeding max items."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    mock_settings.SATELLITE_BULK_MAX_ITEMS = 2
    with pytest.raises(ValueError, match="Too many items"):
        create_bulk_order(
            db,
            name="Too Many",
            items=[{"vessel_id": vessel.vessel_id}] * 3,
        )


def test_create_bulk_order_disabled(db, vessel, mock_settings):
    """create_bulk_order raises when feature is disabled."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    mock_settings.SATELLITE_BULK_ORDER_ENABLED = False
    with pytest.raises(ValueError, match="disabled"):
        create_bulk_order(db, name="Disabled", items=[{"vessel_id": vessel.vessel_id}])


def test_create_bulk_order_priority_clamping(db, vessel, mock_settings):
    """Priority is clamped to 1-10 range."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    result = create_bulk_order(
        db, name="Clamped", items=[{"vessel_id": vessel.vessel_id}], priority=15,
    )
    assert result.priority == 10

    result2 = create_bulk_order(
        db, name="Clamped2", items=[{"vessel_id": vessel.vessel_id}], priority=-1,
    )
    assert result2.priority == 1


def test_create_bulk_order_with_requested_by(db, vessel, mock_settings):
    """create_bulk_order stores requested_by analyst id."""
    from app.models.analyst import Analyst
    from app.modules.bulk_satellite_manager import create_bulk_order

    analyst = Analyst(
        username="testanalyst", password_hash="fakehash", role="analyst",
    )
    db.add(analyst)
    db.commit()

    result = create_bulk_order(
        db, name="Analyst", items=[{"vessel_id": vessel.vessel_id}],
        requested_by=analyst.analyst_id,
    )
    assert result.requested_by == analyst.analyst_id


# ---------------------------------------------------------------------------
# queue_bulk_order tests
# ---------------------------------------------------------------------------


def test_queue_bulk_order(db, vessel, mock_settings):
    """queue_bulk_order transitions draft to queued."""
    from app.modules.bulk_satellite_manager import create_bulk_order, queue_bulk_order

    order = create_bulk_order(db, name="Queue Me", items=[{"vessel_id": vessel.vessel_id}])
    assert order.status == "draft"

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        result = queue_bulk_order(db, order.bulk_order_id)

    assert result.status == "queued"
    # Items should also be queued
    items = db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).all()
    assert all(i.status == "queued" for i in items)


def test_queue_non_draft_fails(db, vessel, mock_settings):
    """queue_bulk_order raises when not in draft status."""
    from app.modules.bulk_satellite_manager import create_bulk_order, queue_bulk_order

    order = create_bulk_order(db, name="Not Draft", items=[{"vessel_id": vessel.vessel_id}])
    order.status = "queued"
    db.commit()

    with pytest.raises(ValueError, match="must be 'draft'"):
        queue_bulk_order(db, order.bulk_order_id)


def test_queue_not_found(db, mock_settings):
    """queue_bulk_order raises for nonexistent order."""
    from app.modules.bulk_satellite_manager import queue_bulk_order

    with pytest.raises(ValueError, match="not found"):
        queue_bulk_order(db, 9999)


def test_queue_insufficient_budget(db, vessel, mock_settings):
    """queue_bulk_order raises when monthly budget insufficient."""
    from app.modules.bulk_satellite_manager import create_bulk_order, queue_bulk_order

    order = create_bulk_order(db, name="Expensive", items=[{"vessel_id": vessel.vessel_id}])

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 1950.0,
            "remaining_usd": 50.0,
        }
        with pytest.raises(ValueError, match="Insufficient monthly budget"):
            queue_bulk_order(db, order.bulk_order_id)


def test_queue_exceeds_budget_cap(db, vessel, mock_settings):
    """queue_bulk_order raises when estimated cost exceeds budget cap."""
    from app.modules.bulk_satellite_manager import create_bulk_order, queue_bulk_order

    order = create_bulk_order(
        db, name="Capped", items=[{"vessel_id": vessel.vessel_id}] * 5,
        budget_cap=200.0,
    )
    # estimated cost = 5 * 100 = 500, but cap is 200

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        with pytest.raises(ValueError, match="exceeds budget cap"):
            queue_bulk_order(db, order.bulk_order_id)


# ---------------------------------------------------------------------------
# process_bulk_order_queue tests
# ---------------------------------------------------------------------------


def test_process_empty_queue(db, mock_settings):
    """process_bulk_order_queue returns ok with no queued orders."""
    from app.modules.bulk_satellite_manager import process_bulk_order_queue

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        result = process_bulk_order_queue(db)

    assert result["status"] == "ok"
    assert result["processed"] == 0
    assert result["bulk_orders_processed"] == 0


def test_process_queued_order(db, vessel, mock_settings):
    """process_bulk_order_queue processes items and creates satellite orders."""
    from app.modules.bulk_satellite_manager import create_bulk_order, process_bulk_order_queue

    order = create_bulk_order(db, name="Process Me", items=[{"vessel_id": vessel.vessel_id}])
    order.status = "queued"
    items = db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).all()
    for item in items:
        item.status = "queued"
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        result = process_bulk_order_queue(db)

    assert result["submitted"] == 1
    assert result["bulk_orders_processed"] == 1

    db.refresh(order)
    assert order.status == "completed"
    assert order.submitted_orders == 1

    # Check satellite order was created
    sat_orders = db.query(SatelliteOrder).filter(
        SatelliteOrder.order_type == "bulk_order"
    ).all()
    assert len(sat_orders) == 1


def test_process_priority_ordering(db, vessel, vessel2, mock_settings):
    """Higher priority bulk orders are processed first."""
    from app.modules.bulk_satellite_manager import create_bulk_order, process_bulk_order_queue

    low = create_bulk_order(db, name="Low Priority", items=[{"vessel_id": vessel.vessel_id}], priority=3)
    high = create_bulk_order(db, name="High Priority", items=[{"vessel_id": vessel2.vessel_id}], priority=9)

    for o in [low, high]:
        o.status = "queued"
        for item in db.query(SatelliteBulkOrderItem).filter(
            SatelliteBulkOrderItem.bulk_order_id == o.bulk_order_id
        ).all():
            item.status = "queued"
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        result = process_bulk_order_queue(db)

    assert result["submitted"] == 2
    assert result["bulk_orders_processed"] == 2


def test_process_budget_exhaustion(db, vessel, mock_settings):
    """Items are skipped when monthly budget is exhausted."""
    from app.modules.bulk_satellite_manager import create_bulk_order, process_bulk_order_queue

    order = create_bulk_order(
        db, name="Budget Test",
        items=[
            {"vessel_id": vessel.vessel_id},
            {"vessel_id": vessel.vessel_id},
            {"vessel_id": vessel.vessel_id},
        ],
    )
    order.status = "queued"
    for item in db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).all():
        item.status = "queued"
    db.commit()

    call_count = 0

    def budget_side_effect(db_arg):
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            return {"budget_usd": 2000.0, "spent_usd": 0.0, "remaining_usd": 150.0}
        return {"budget_usd": 2000.0, "spent_usd": 1950.0, "remaining_usd": 50.0}

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status", side_effect=budget_side_effect):
        result = process_bulk_order_queue(db)

    assert result["submitted"] >= 1
    assert result["skipped"] >= 1

    # Check skipped items have reason
    skipped_items = db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id,
        SatelliteBulkOrderItem.status == "skipped",
    ).all()
    assert len(skipped_items) >= 1
    assert all(i.skip_reason == "budget_exhausted" for i in skipped_items)


def test_process_budget_cap_exhaustion(db, vessel, mock_settings):
    """Items are skipped when bulk order budget cap is exceeded."""
    from app.modules.bulk_satellite_manager import create_bulk_order, process_bulk_order_queue

    order = create_bulk_order(
        db, name="Cap Test",
        items=[
            {"vessel_id": vessel.vessel_id},
            {"vessel_id": vessel.vessel_id},
        ],
        budget_cap=150.0,  # Only enough for 1 item at $100 each
    )
    order.status = "queued"
    for item in db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).all():
        item.status = "queued"
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        result = process_bulk_order_queue(db)

    assert result["submitted"] == 1
    assert result["skipped"] == 1

    skipped = db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id,
        SatelliteBulkOrderItem.status == "skipped",
    ).all()
    assert len(skipped) == 1
    assert skipped[0].skip_reason == "budget_cap_exceeded"


def test_process_disabled(db, mock_settings):
    """process_bulk_order_queue returns disabled status when feature flag off."""
    from app.modules.bulk_satellite_manager import process_bulk_order_queue

    mock_settings.SATELLITE_BULK_ORDER_ENABLED = False
    result = process_bulk_order_queue(db)
    assert result["status"] == "disabled"


# ---------------------------------------------------------------------------
# cancel_bulk_order tests
# ---------------------------------------------------------------------------


def test_cancel_draft_order(db, vessel, mock_settings):
    """cancel_bulk_order cancels a draft order and skips items."""
    from app.modules.bulk_satellite_manager import create_bulk_order, cancel_bulk_order

    order = create_bulk_order(db, name="Cancel Me", items=[{"vessel_id": vessel.vessel_id}])

    with patch("app.modules.satellite_order_manager.cancel_order"):
        result = cancel_bulk_order(db, order.bulk_order_id)

    assert result.status == "cancelled"
    items = db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).all()
    assert all(i.status == "skipped" for i in items)
    assert all(i.skip_reason == "bulk_order_cancelled" for i in items)


def test_cancel_queued_order(db, vessel, mock_settings):
    """cancel_bulk_order cancels a queued order."""
    from app.modules.bulk_satellite_manager import create_bulk_order, cancel_bulk_order

    order = create_bulk_order(db, name="Queued Cancel", items=[{"vessel_id": vessel.vessel_id}])
    order.status = "queued"
    for item in db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).all():
        item.status = "queued"
    db.commit()

    with patch("app.modules.satellite_order_manager.cancel_order"):
        result = cancel_bulk_order(db, order.bulk_order_id)

    assert result.status == "cancelled"


def test_cancel_already_cancelled_fails(db, vessel, mock_settings):
    """cancel_bulk_order raises for already cancelled order."""
    from app.modules.bulk_satellite_manager import create_bulk_order, cancel_bulk_order

    order = create_bulk_order(db, name="Already Done", items=[{"vessel_id": vessel.vessel_id}])
    order.status = "cancelled"
    db.commit()

    with pytest.raises(ValueError, match="already cancelled"):
        cancel_bulk_order(db, order.bulk_order_id)


def test_cancel_completed_fails(db, vessel, mock_settings):
    """cancel_bulk_order raises for completed order."""
    from app.modules.bulk_satellite_manager import create_bulk_order, cancel_bulk_order

    order = create_bulk_order(db, name="Completed", items=[{"vessel_id": vessel.vessel_id}])
    order.status = "completed"
    db.commit()

    with pytest.raises(ValueError, match="Cannot cancel a completed"):
        cancel_bulk_order(db, order.bulk_order_id)


def test_cancel_not_found(db, mock_settings):
    """cancel_bulk_order raises for nonexistent order."""
    from app.modules.bulk_satellite_manager import cancel_bulk_order

    with pytest.raises(ValueError, match="not found"):
        cancel_bulk_order(db, 9999)


def test_cancel_cascades_to_submitted_satellite_orders(db, vessel, mock_settings):
    """cancel_bulk_order attempts to cancel linked satellite orders."""
    from app.modules.bulk_satellite_manager import create_bulk_order, cancel_bulk_order

    order = create_bulk_order(db, name="With Sat Orders", items=[{"vessel_id": vessel.vessel_id}])

    # Create a satellite order and link it
    sat_order = SatelliteOrder(
        provider="planet", order_type="bulk_order", status="submitted",
    )
    db.add(sat_order)
    db.flush()

    item = db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).first()
    item.status = "submitted"
    item.satellite_order_id = sat_order.satellite_order_id
    db.commit()

    with patch("app.modules.satellite_order_manager.cancel_order") as mock_cancel:
        result = cancel_bulk_order(db, order.bulk_order_id)

    assert result.status == "cancelled"
    mock_cancel.assert_called_once_with(db, sat_order.satellite_order_id)


# ---------------------------------------------------------------------------
# get_budget_dashboard tests
# ---------------------------------------------------------------------------


def test_budget_dashboard_basic(db, mock_settings):
    """get_budget_dashboard returns budget summary."""
    from app.modules.bulk_satellite_manager import get_budget_dashboard

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 500.0,
            "remaining_usd": 1500.0,
        }
        result = get_budget_dashboard(db)

    assert result["budget_usd"] == 2000.0
    assert result["spent_usd"] == 500.0
    assert "committed_usd" in result
    assert "remaining_usd" in result
    assert "provider_breakdown" in result
    assert "daily_burn_rate_usd" in result
    assert "projected_monthly_spend_usd" in result
    assert "bulk_orders_by_status" in result


def test_budget_dashboard_with_orders(db, mock_settings):
    """get_budget_dashboard includes provider breakdown from satellite orders."""
    from app.modules.bulk_satellite_manager import get_budget_dashboard

    sat_order = SatelliteOrder(
        provider="planet", order_type="archive_search", status="submitted",
        cost_usd=200.0,
    )
    db.add(sat_order)
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 200.0,
            "remaining_usd": 1800.0,
        }
        result = get_budget_dashboard(db)

    assert len(result["provider_breakdown"]) >= 1
    planet_entry = next(
        (p for p in result["provider_breakdown"] if p["provider"] == "planet"), None
    )
    assert planet_entry is not None
    assert planet_entry["spent_usd"] == 200.0


def test_budget_dashboard_committed_from_bulk_orders(db, mock_settings):
    """get_budget_dashboard includes committed cost from queued bulk orders."""
    from app.modules.bulk_satellite_manager import get_budget_dashboard

    bulk = SatelliteBulkOrder(
        name="Queued", status="queued", total_orders=3,
        estimated_total_cost_usd=300.0,
    )
    db.add(bulk)
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        result = get_budget_dashboard(db)

    assert result["committed_usd"] == 300.0
    # remaining should account for committed
    assert result["remaining_usd"] == 2000.0 - 0.0 - 300.0


def test_budget_dashboard_bulk_status_counts(db, mock_settings):
    """get_budget_dashboard counts bulk orders by status."""
    from app.modules.bulk_satellite_manager import get_budget_dashboard

    for status in ["draft", "draft", "queued", "completed"]:
        db.add(SatelliteBulkOrder(name=f"Order {status}", status=status, total_orders=1))
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        result = get_budget_dashboard(db)

    assert result["bulk_orders_by_status"]["draft"] == 2
    assert result["bulk_orders_by_status"]["queued"] == 1
    assert result["bulk_orders_by_status"]["completed"] == 1


# ---------------------------------------------------------------------------
# State transition tests
# ---------------------------------------------------------------------------


def test_full_lifecycle(db, vessel, mock_settings):
    """Full lifecycle: draft -> queued -> processing -> completed."""
    from app.modules.bulk_satellite_manager import (
        create_bulk_order,
        queue_bulk_order,
        process_bulk_order_queue,
    )

    order = create_bulk_order(db, name="Lifecycle", items=[{"vessel_id": vessel.vessel_id}])
    assert order.status == "draft"

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        queued = queue_bulk_order(db, order.bulk_order_id)
        assert queued.status == "queued"

        result = process_bulk_order_queue(db)
        assert result["submitted"] == 1

    db.refresh(order)
    assert order.status == "completed"
    assert order.submitted_orders == 1


def test_item_provider_preference_used(db, vessel, mock_settings):
    """Provider preference from item is used when creating satellite order."""
    from app.modules.bulk_satellite_manager import create_bulk_order, process_bulk_order_queue

    order = create_bulk_order(
        db, name="Provider Test",
        items=[{"vessel_id": vessel.vessel_id, "provider_preference": "capella"}],
    )
    order.status = "queued"
    for item in db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).all():
        item.status = "queued"
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        process_bulk_order_queue(db)

    sat_order = db.query(SatelliteOrder).filter(
        SatelliteOrder.order_type == "bulk_order"
    ).first()
    assert sat_order is not None
    assert sat_order.provider == "capella"


def test_item_defaults_to_planet_provider(db, vessel, mock_settings):
    """Items without provider_preference default to planet."""
    from app.modules.bulk_satellite_manager import create_bulk_order, process_bulk_order_queue

    order = create_bulk_order(
        db, name="Default Provider", items=[{"vessel_id": vessel.vessel_id}],
    )
    order.status = "queued"
    for item in db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == order.bulk_order_id
    ).all():
        item.status = "queued"
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        process_bulk_order_queue(db)

    sat_order = db.query(SatelliteOrder).filter(
        SatelliteOrder.order_type == "bulk_order"
    ).first()
    assert sat_order.provider == "planet"


# ---------------------------------------------------------------------------
# Additional edge case tests
# ---------------------------------------------------------------------------


def test_create_bulk_order_with_aoi_wkt(db, vessel, mock_settings):
    """Items can include aoi_wkt geometry."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    wkt = "POLYGON((10 55, 11 55, 11 56, 10 56, 10 55))"
    result = create_bulk_order(
        db, name="With AOI",
        items=[{"vessel_id": vessel.vessel_id, "aoi_wkt": wkt}],
    )
    items = db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == result.bulk_order_id
    ).all()
    assert items[0].aoi_wkt == wkt


def test_create_bulk_order_whitespace_name_stripped(db, vessel, mock_settings):
    """Name is stripped of leading/trailing whitespace."""
    from app.modules.bulk_satellite_manager import create_bulk_order

    result = create_bulk_order(
        db, name="  Padded Name  ", items=[{"vessel_id": vessel.vessel_id}],
    )
    assert result.name == "Padded Name"


def test_process_completed_order_not_reprocessed(db, vessel, mock_settings):
    """Completed bulk orders are not picked up for reprocessing."""
    from app.modules.bulk_satellite_manager import process_bulk_order_queue

    order = SatelliteBulkOrder(
        name="Already Done", status="completed", total_orders=1,
        submitted_orders=1,
    )
    db.add(order)
    db.commit()

    with patch("app.modules.satellite_order_manager.get_satellite_budget_status") as mock_budget:
        mock_budget.return_value = {
            "budget_usd": 2000.0,
            "spent_usd": 0.0,
            "remaining_usd": 2000.0,
        }
        result = process_bulk_order_queue(db)

    assert result["bulk_orders_processed"] == 0
