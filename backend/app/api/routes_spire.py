"""Spire Maritime AIS admin endpoints."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_senior_or_admin
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/spire", tags=["admin"])


@router.get("/status")
def spire_status(
    _auth=Depends(require_senior_or_admin),
    db: Session = Depends(get_db),
):
    """Spire AIS quota, last collection time, and circuit breaker state."""
    from app.models.collection_run import CollectionRun
    from app.modules.circuit_breakers import breakers

    # Get circuit breaker state
    cb = breakers.get("spire_ais")
    cb_state = {
        "state": cb.current_state if cb else "unknown",
        "fail_count": cb.fail_counter if cb else 0,
    }

    # Monthly quota
    monthly_quota = settings.SPIRE_MONTHLY_QUOTA
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    runs = (
        db.query(CollectionRun)
        .filter(
            CollectionRun.source == "spire",
            CollectionRun.started_at >= month_start,
            CollectionRun.status == "completed",
        )
        .all()
    )

    quota_used = 0
    for run in runs:
        if run.details_json:
            try:
                details = json.loads(run.details_json)
                quota_used += details.get("quota_used", 0)
            except (json.JSONDecodeError, TypeError):
                pass

    # Last collection
    last_run = (
        db.query(CollectionRun)
        .filter(CollectionRun.source == "spire")
        .order_by(CollectionRun.started_at.desc())
        .first()
    )

    return {
        "enabled": settings.SPIRE_AIS_COLLECTION_ENABLED,
        "api_key_configured": bool(settings.SPIRE_AIS_API_KEY),
        "monthly_quota": monthly_quota,
        "quota_used": quota_used,
        "quota_remaining": max(0, monthly_quota - quota_used),
        "last_collection": {
            "started_at": last_run.started_at.isoformat() if last_run else None,
            "status": last_run.status if last_run else None,
            "points_imported": last_run.points_imported if last_run else 0,
        },
        "circuit_breaker": cb_state,
    }


@router.post("/collect")
def spire_collect(
    _auth=Depends(require_senior_or_admin),
    db: Session = Depends(get_db),
):
    """Manually trigger Spire AIS collection for the Persian Gulf."""
    from app.modules.spire_ais_collector import collect_spire_gulf_ais

    if not settings.SPIRE_AIS_API_KEY:
        raise HTTPException(status_code=400, detail="SPIRE_AIS_API_KEY not configured")

    result = collect_spire_gulf_ais(db)
    return result


@router.get("/coverage")
def spire_coverage(
    days: int = 7,
    _auth=Depends(require_senior_or_admin),
    db: Session = Depends(get_db),
):
    """Persian Gulf coverage stats from Spire AIS data."""
    from app.models.ais_point import AISPoint

    cutoff = datetime.now(UTC) - __import__("datetime").timedelta(days=days)

    # Points per day
    daily_counts = (
        db.query(
            func.date(AISPoint.timestamp_utc).label("day"),
            func.count(AISPoint.ais_point_id).label("count"),
        )
        .filter(
            AISPoint.source == "spire",
            AISPoint.timestamp_utc >= cutoff,
        )
        .group_by(func.date(AISPoint.timestamp_utc))
        .order_by(func.date(AISPoint.timestamp_utc))
        .all()
    )

    # Distinct vessels
    from app.models.vessel import Vessel

    vessel_count = (
        db.query(func.count(func.distinct(AISPoint.vessel_id)))
        .filter(
            AISPoint.source == "spire",
            AISPoint.timestamp_utc >= cutoff,
        )
        .scalar()
    ) or 0

    total_points = sum(row.count for row in daily_counts)

    return {
        "days": days,
        "total_points": total_points,
        "vessel_count": vessel_count,
        "daily_breakdown": [
            {"date": str(row.day), "points": row.count} for row in daily_counts
        ],
    }
