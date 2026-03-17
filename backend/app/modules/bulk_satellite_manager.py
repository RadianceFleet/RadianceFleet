"""Bulk satellite ordering workflow — queue, prioritize, and budget-manage batch orders."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.satellite_bulk_order import SatelliteBulkOrder
from app.models.satellite_bulk_order_item import SatelliteBulkOrderItem
from app.models.satellite_order import SatelliteOrder

logger = logging.getLogger(__name__)


def create_bulk_order(
    db: Session,
    name: str,
    items: list[dict],
    priority: int = 5,
    budget_cap: float | None = None,
    requested_by: int | None = None,
) -> SatelliteBulkOrder:
    """Create a new bulk satellite order in draft status.

    Each item dict should have: vessel_id, and optionally alert_id,
    provider_preference, aoi_wkt, priority_rank.
    """
    if not settings.SATELLITE_BULK_ORDER_ENABLED:
        raise ValueError("Bulk satellite ordering is disabled")

    if len(items) > settings.SATELLITE_BULK_MAX_ITEMS:
        raise ValueError(
            f"Too many items: {len(items)} exceeds maximum of {settings.SATELLITE_BULK_MAX_ITEMS}"
        )

    if not items:
        raise ValueError("At least one item is required")

    if not name or not name.strip():
        raise ValueError("Name is required")

    priority = max(1, min(10, priority))

    bulk_order = SatelliteBulkOrder(
        name=name.strip(),
        status="draft",
        priority=priority,
        total_orders=len(items),
        budget_cap_usd=budget_cap,
        requested_by=requested_by,
    )
    db.add(bulk_order)
    db.flush()

    estimated_total = 0.0
    for rank, item_data in enumerate(items, start=1):
        item = SatelliteBulkOrderItem(
            bulk_order_id=bulk_order.bulk_order_id,
            vessel_id=item_data["vessel_id"],
            alert_id=item_data.get("alert_id"),
            provider_preference=item_data.get("provider_preference"),
            aoi_wkt=item_data.get("aoi_wkt"),
            priority_rank=item_data.get("priority_rank", rank),
            status="pending",
        )
        db.add(item)
        # Rough cost estimate per item
        estimated_total += 100.0

    bulk_order.estimated_total_cost_usd = estimated_total
    db.commit()
    db.refresh(bulk_order)
    return bulk_order


def queue_bulk_order(db: Session, bulk_order_id: int) -> SatelliteBulkOrder:
    """Transition a draft bulk order to queued status after budget validation."""
    bulk_order = (
        db.query(SatelliteBulkOrder)
        .filter(SatelliteBulkOrder.bulk_order_id == bulk_order_id)
        .first()
    )
    if not bulk_order:
        raise ValueError(f"Bulk order {bulk_order_id} not found")

    if bulk_order.status != "draft":
        raise ValueError(
            f"Cannot queue bulk order in '{bulk_order.status}' status (must be 'draft')"
        )

    # Budget validation
    from app.modules.satellite_order_manager import get_satellite_budget_status

    budget = get_satellite_budget_status(db)
    estimated_cost = bulk_order.estimated_total_cost_usd or 0.0

    if bulk_order.budget_cap_usd and estimated_cost > bulk_order.budget_cap_usd:
        raise ValueError(
            f"Estimated cost ${estimated_cost:.2f} exceeds budget cap ${bulk_order.budget_cap_usd:.2f}"
        )

    if budget["remaining_usd"] < estimated_cost:
        raise ValueError(
            f"Insufficient monthly budget: ${budget['remaining_usd']:.2f} remaining, "
            f"${estimated_cost:.2f} estimated"
        )

    bulk_order.status = "queued"
    # Mark all pending items as queued
    db.query(SatelliteBulkOrderItem).filter(
        SatelliteBulkOrderItem.bulk_order_id == bulk_order_id,
        SatelliteBulkOrderItem.status == "pending",
    ).update({"status": "queued"})

    db.commit()
    db.refresh(bulk_order)
    return bulk_order


def process_bulk_order_queue(db: Session) -> dict:
    """Process all queued bulk orders by priority; stop items on budget exhaustion."""
    if not settings.SATELLITE_BULK_ORDER_ENABLED:
        return {"status": "disabled", "processed": 0, "submitted": 0, "skipped": 0}

    from app.modules.satellite_order_manager import get_satellite_budget_status

    queued_orders = (
        db.query(SatelliteBulkOrder)
        .filter(SatelliteBulkOrder.status == "queued")
        .order_by(SatelliteBulkOrder.priority.desc(), SatelliteBulkOrder.created_at.asc())
        .all()
    )

    total_processed = 0
    total_submitted = 0
    total_skipped = 0
    total_failed = 0

    for bulk_order in queued_orders:
        bulk_order.status = "processing"
        db.flush()

        items = (
            db.query(SatelliteBulkOrderItem)
            .filter(
                SatelliteBulkOrderItem.bulk_order_id == bulk_order.bulk_order_id,
                SatelliteBulkOrderItem.status == "queued",
            )
            .order_by(SatelliteBulkOrderItem.priority_rank.asc())
            .all()
        )

        budget_exhausted = False
        for item in items:
            if budget_exhausted:
                item.status = "skipped"
                item.skip_reason = "budget_exhausted"
                total_skipped += 1
                bulk_order.failed_orders = (bulk_order.failed_orders or 0) + 1
                continue

            # Check remaining budget
            budget = get_satellite_budget_status(db)
            estimated_item_cost = 100.0  # rough estimate per item

            # Check against monthly budget
            if budget["remaining_usd"] < estimated_item_cost:
                item.status = "skipped"
                item.skip_reason = "budget_exhausted"
                budget_exhausted = True
                total_skipped += 1
                bulk_order.failed_orders = (bulk_order.failed_orders or 0) + 1
                continue

            # Check against bulk order budget cap
            if bulk_order.budget_cap_usd:
                current_spent = (bulk_order.actual_total_cost_usd or 0.0)
                if current_spent + estimated_item_cost > bulk_order.budget_cap_usd:
                    item.status = "skipped"
                    item.skip_reason = "budget_cap_exceeded"
                    budget_exhausted = True
                    total_skipped += 1
                    bulk_order.failed_orders = (bulk_order.failed_orders or 0) + 1
                    continue

            # Create a satellite order for the item
            try:
                sat_order = SatelliteOrder(
                    provider=item.provider_preference or "planet",
                    order_type="bulk_order",
                    status="submitted",
                    aoi_wkt=item.aoi_wkt,
                    cost_usd=estimated_item_cost,
                    requested_by=str(bulk_order.requested_by) if bulk_order.requested_by else None,
                )
                db.add(sat_order)
                db.flush()

                item.satellite_order_id = sat_order.satellite_order_id
                item.status = "submitted"
                bulk_order.submitted_orders = (bulk_order.submitted_orders or 0) + 1
                bulk_order.actual_total_cost_usd = (
                    (bulk_order.actual_total_cost_usd or 0.0) + estimated_item_cost
                )
                total_submitted += 1
            except Exception as e:
                logger.warning("Failed to submit item %s: %s", item.item_id, e)
                item.status = "failed"
                item.skip_reason = str(e)[:255]
                bulk_order.failed_orders = (bulk_order.failed_orders or 0) + 1
                total_failed += 1

            total_processed += 1

        # Determine final status
        submitted = bulk_order.submitted_orders or 0
        failed = bulk_order.failed_orders or 0
        if submitted + failed >= bulk_order.total_orders or budget_exhausted:
            bulk_order.status = "completed"

        db.flush()

    db.commit()

    return {
        "status": "ok",
        "processed": total_processed,
        "submitted": total_submitted,
        "skipped": total_skipped,
        "failed": total_failed,
        "bulk_orders_processed": len(queued_orders),
    }


def cancel_bulk_order(db: Session, bulk_order_id: int) -> SatelliteBulkOrder:
    """Cancel a bulk order and cascade cancellation to individual orders."""
    bulk_order = (
        db.query(SatelliteBulkOrder)
        .filter(SatelliteBulkOrder.bulk_order_id == bulk_order_id)
        .first()
    )
    if not bulk_order:
        raise ValueError(f"Bulk order {bulk_order_id} not found")

    if bulk_order.status == "cancelled":
        raise ValueError("Bulk order is already cancelled")

    if bulk_order.status == "completed":
        raise ValueError("Cannot cancel a completed bulk order")

    # Cancel individual satellite orders that are submitted
    items = (
        db.query(SatelliteBulkOrderItem)
        .filter(SatelliteBulkOrderItem.bulk_order_id == bulk_order_id)
        .all()
    )

    from app.modules.satellite_order_manager import cancel_order

    for item in items:
        if item.status in ("pending", "queued"):
            item.status = "skipped"
            item.skip_reason = "bulk_order_cancelled"
        elif item.status == "submitted" and item.satellite_order_id:
            try:
                cancel_order(db, item.satellite_order_id)
                item.status = "skipped"
                item.skip_reason = "bulk_order_cancelled"
            except Exception as e:
                logger.warning(
                    "Failed to cancel satellite order %s: %s", item.satellite_order_id, e
                )

    bulk_order.status = "cancelled"
    db.commit()
    db.refresh(bulk_order)
    return bulk_order


def get_budget_dashboard(db: Session) -> dict:
    """Extended budget dashboard with monthly spend, per-provider breakdown, and burn rate."""
    from app.modules.satellite_order_manager import get_satellite_budget_status

    base_budget = get_satellite_budget_status(db)
    now = datetime.now(UTC)

    # Per-provider breakdown for current month
    provider_rows = (
        db.query(
            SatelliteOrder.provider,
            func.coalesce(func.sum(SatelliteOrder.cost_usd), 0.0).label("spent"),
            func.count(SatelliteOrder.satellite_order_id).label("order_count"),
        )
        .filter(
            SatelliteOrder.status.notin_(["cancelled", "failed", "draft"]),
            extract("year", SatelliteOrder.created_utc) == now.year,
            extract("month", SatelliteOrder.created_utc) == now.month,
        )
        .group_by(SatelliteOrder.provider)
        .all()
    )

    provider_breakdown = [
        {
            "provider": row.provider if hasattr(row, "provider") else row[0],
            "spent_usd": round(float(row.spent if hasattr(row, "spent") else row[1]), 2),
            "order_count": int(row.order_count if hasattr(row, "order_count") else row[2]),
        }
        for row in provider_rows
    ]

    # Committed (queued/processing bulk orders)
    committed = (
        db.query(func.coalesce(func.sum(SatelliteBulkOrder.estimated_total_cost_usd), 0.0))
        .filter(SatelliteBulkOrder.status.in_(["queued", "processing"]))
        .scalar()
        or 0.0
    )

    # 7-day burn rate
    seven_days_ago = now - timedelta(days=7)
    week_spend = (
        db.query(func.coalesce(func.sum(SatelliteOrder.cost_usd), 0.0))
        .filter(
            SatelliteOrder.status.notin_(["cancelled", "failed", "draft"]),
            SatelliteOrder.created_utc >= seven_days_ago,
        )
        .scalar()
        or 0.0
    )
    daily_burn_rate = round(float(week_spend) / 7.0, 2)

    # Bulk order summary
    bulk_summary = (
        db.query(
            SatelliteBulkOrder.status,
            func.count(SatelliteBulkOrder.bulk_order_id).label("count"),
        )
        .group_by(SatelliteBulkOrder.status)
        .all()
    )

    bulk_by_status = {
        row.status if hasattr(row, "status") else row[0]: int(
            row.count if hasattr(row, "count") else row[1]
        )
        for row in bulk_summary
    }

    return {
        "budget_usd": base_budget["budget_usd"],
        "spent_usd": base_budget["spent_usd"],
        "committed_usd": round(float(committed), 2),
        "remaining_usd": round(
            base_budget["budget_usd"] - base_budget["spent_usd"] - float(committed), 2
        ),
        "provider_breakdown": provider_breakdown,
        "daily_burn_rate_usd": daily_burn_rate,
        "projected_monthly_spend_usd": round(daily_burn_rate * 30, 2),
        "bulk_orders_by_status": bulk_by_status,
    }
