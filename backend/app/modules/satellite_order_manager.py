"""Satellite imagery order management."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func, extract
from sqlalchemy.orm import Session

from app.models.satellite_order import SatelliteOrder
from app.models.satellite_order_log import SatelliteOrderLog
from app.config import settings

logger = logging.getLogger(__name__)


def get_satellite_budget_status(db: Session) -> dict:
    """Monthly spend vs budget."""
    now = datetime.now(timezone.utc)
    monthly_spend = db.query(
        func.coalesce(func.sum(SatelliteOrder.cost_usd), 0.0)
    ).filter(
        SatelliteOrder.status.notin_(["cancelled", "failed", "draft"]),
        extract("year", SatelliteOrder.created_utc) == now.year,
        extract("month", SatelliteOrder.created_utc) == now.month,
    ).scalar() or 0.0
    return {
        "budget_usd": settings.SATELLITE_MONTHLY_BUDGET_USD,
        "spent_usd": round(float(monthly_spend), 2),
        "remaining_usd": round(settings.SATELLITE_MONTHLY_BUDGET_USD - float(monthly_spend), 2),
    }


def search_archive_for_alert(db: Session, alert_id: int, provider_name: str) -> dict:
    """Search satellite archive and create draft order."""
    from app.models.gap_event import AISGapEvent
    from app.modules.satellite_providers import get_provider

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise ValueError(f"Alert {alert_id} not found")

    # Compute bounding box from gap positions
    aoi_wkt = _compute_aoi(alert)
    time_start = alert.gap_start_utc - timedelta(hours=12)
    time_end = alert.gap_end_utc + timedelta(hours=12)

    provider_cls = get_provider(provider_name)
    provider = provider_cls()

    results = provider.search_archive(aoi_wkt, time_start, time_end)

    # Log the API call
    log = SatelliteOrderLog(
        satellite_order_id=0,  # will be updated
        action="archive_search",
        provider=provider_name,
        request_summary=f"alert_id={alert_id}, aoi={aoi_wkt[:100]}",
        response_summary=f"{len(results)} scenes found",
    )

    # Create draft order
    order = SatelliteOrder(
        provider=provider_name,
        order_type="archive_search",
        status="draft",
        aoi_wkt=aoi_wkt,
        time_window_start=time_start,
        time_window_end=time_end,
        scene_urls_json={
            "scenes": [
                {
                    "scene_id": r.scene_id,
                    "acquired_at": r.acquired_at.isoformat(),
                    "cloud_cover_pct": r.cloud_cover_pct,
                    "resolution_m": r.resolution_m,
                    "estimated_cost_usd": r.estimated_cost_usd,
                }
                for r in results
            ]
        },
    )
    # Link to sat_check if exists
    from app.models.satellite_check import SatelliteCheck

    sat_check = db.query(SatelliteCheck).filter(
        SatelliteCheck.gap_event_id == alert_id
    ).first()
    if sat_check:
        order.sat_check_id = sat_check.sat_check_id

    db.add(order)
    db.flush()
    log.satellite_order_id = order.satellite_order_id
    db.add(log)
    db.commit()

    return {
        "order_id": order.satellite_order_id,
        "provider": provider_name,
        "scenes_found": len(results),
        "scenes": [
            {
                "scene_id": r.scene_id,
                "acquired_at": r.acquired_at.isoformat(),
                "cloud_cover_pct": r.cloud_cover_pct,
                "resolution_m": r.resolution_m,
                "estimated_cost_usd": r.estimated_cost_usd,
            }
            for r in results
        ],
    }


def submit_order(db: Session, order_id: int, scene_ids: list[str]) -> dict:
    """Submit a draft order after budget check."""
    order = db.query(SatelliteOrder).filter(
        SatelliteOrder.satellite_order_id == order_id
    ).first()
    if not order:
        raise ValueError(f"Order {order_id} not found")
    if order.status != "draft":
        raise ValueError(f"Order {order_id} is not a draft (status={order.status})")

    # Budget check
    budget = get_satellite_budget_status(db)
    estimated_cost = len(scene_ids) * 100.0  # rough estimate
    if budget["remaining_usd"] < estimated_cost:
        raise ValueError(
            f"Insufficient budget: ${budget['remaining_usd']:.2f} remaining, "
            f"${estimated_cost:.2f} needed"
        )

    from app.modules.satellite_providers import get_provider

    provider_cls = get_provider(order.provider)
    provider = provider_cls()

    result = provider.submit_order(scene_ids)

    order.external_order_id = result.external_order_id
    order.status = "submitted"
    order.cost_usd = result.estimated_cost_usd

    log = SatelliteOrderLog(
        satellite_order_id=order_id,
        action="submit",
        provider=order.provider,
        request_summary=f"scene_ids={scene_ids}",
        response_summary=f"external_id={result.external_order_id}, status={result.status}",
    )
    db.add(log)
    db.commit()

    return {
        "order_id": order_id,
        "external_order_id": result.external_order_id,
        "status": "submitted",
    }


def poll_order_status(db: Session, order_id: int = None) -> list[dict]:
    """Poll status for one or all active orders."""
    from app.modules.satellite_providers import get_provider

    q = db.query(SatelliteOrder).filter(
        SatelliteOrder.status.in_(["submitted", "accepted", "processing"])
    )
    if order_id:
        q = q.filter(SatelliteOrder.satellite_order_id == order_id)
    orders = q.all()

    results = []
    for order in orders:
        if not order.external_order_id:
            continue
        try:
            provider_cls = get_provider(order.provider)
            provider = provider_cls()
            status_result = provider.check_order_status(order.external_order_id)

            order.status = status_result.status
            if status_result.scene_urls:
                order.scene_urls_json = {"delivered_urls": status_result.scene_urls}
            if status_result.cost_usd:
                order.cost_usd = status_result.cost_usd
                order.cost_confirmed = True

            log = SatelliteOrderLog(
                satellite_order_id=order.satellite_order_id,
                action="poll_status",
                provider=order.provider,
                response_summary=f"status={status_result.status}",
            )
            db.add(log)
            results.append({
                "order_id": order.satellite_order_id,
                "status": status_result.status,
            })
        except Exception as e:
            logger.warning("Failed to poll order %s: %s", order.satellite_order_id, e)
            results.append({
                "order_id": order.satellite_order_id,
                "error": str(e),
            })

    db.commit()
    return results


def cancel_order(db: Session, order_id: int) -> dict:
    """Cancel a submitted order."""
    order = db.query(SatelliteOrder).filter(
        SatelliteOrder.satellite_order_id == order_id
    ).first()
    if not order:
        raise ValueError(f"Order {order_id} not found")

    if order.status in ("delivered", "cancelled"):
        raise ValueError(f"Cannot cancel order in {order.status} state")

    if order.external_order_id:
        from app.modules.satellite_providers import get_provider

        provider_cls = get_provider(order.provider)
        provider = provider_cls()
        provider.cancel_order(order.external_order_id)

    order.status = "cancelled"
    log = SatelliteOrderLog(
        satellite_order_id=order_id,
        action="cancel",
        provider=order.provider,
    )
    db.add(log)
    db.commit()
    return {"order_id": order_id, "status": "cancelled"}


def _compute_aoi(alert) -> str:
    """Compute WKT bounding box from alert gap positions."""
    lats, lons = [], []
    if alert.gap_off_lat and alert.gap_off_lon:
        lats.append(alert.gap_off_lat)
        lons.append(alert.gap_off_lon)
    if alert.gap_on_lat and alert.gap_on_lon:
        lats.append(alert.gap_on_lat)
        lons.append(alert.gap_on_lon)
    if alert.start_point:
        lats.append(alert.start_point.lat)
        lons.append(alert.start_point.lon)
    if alert.end_point:
        lats.append(alert.end_point.lat)
        lons.append(alert.end_point.lon)

    if not lats:
        return "POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))"  # fallback

    margin = 0.1  # ~11km buffer
    min_lat = min(lats) - margin
    max_lat = max(lats) + margin
    min_lon = min(lons) - margin
    max_lon = max(lons) + margin
    return (
        f"POLYGON(({min_lon} {min_lat}, {min_lon} {max_lat}, "
        f"{max_lon} {max_lat}, {max_lon} {min_lat}, {min_lon} {min_lat}))"
    )
