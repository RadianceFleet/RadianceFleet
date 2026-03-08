"""Health check endpoints."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api._helpers import limiter
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", tags=["system"])
@limiter.exempt
def health_check(db: Session = Depends(get_db)):
    """Health check with DB latency measurement."""
    from sqlalchemy import text

    t0 = time.time()
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
    latency_ms = round((time.time() - t0) * 1000, 1)

    from app.modules.circuit_breakers import get_circuit_states

    return {
        "status": "ok",
        "version": getattr(settings, "VERSION", "1.0.0"),
        "database": {"status": db_status, "latency_ms": latency_ms},
        "circuit_breakers": get_circuit_states(),
    }


@router.get("/health/data-freshness", tags=["health"])
@limiter.exempt
def get_data_freshness(db: Session = Depends(get_db)):
    """Data freshness monitoring -- reports AIS data staleness."""
    from app.models.ais_point import AISPoint
    from app.models.gap_event import AISGapEvent as _GapEvent
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist

    now = datetime.now(UTC)

    latest = db.query(func.max(Vessel.last_ais_received_utc)).scalar()

    one_hour_ago = now - timedelta(hours=1)
    twenty_four_hours_ago = now - timedelta(hours=24)

    vessels_1h = (
        db.query(func.count(Vessel.vessel_id))
        .filter(Vessel.last_ais_received_utc >= one_hour_ago)
        .scalar()
        or 0
    )

    vessels_24h = (
        db.query(func.count(Vessel.vessel_id))
        .filter(Vessel.last_ais_received_utc >= twenty_four_hours_ago)
        .scalar()
        or 0
    )

    staleness_minutes = None
    if latest:
        staleness_minutes = int((now - latest.replace(tzinfo=UTC)).total_seconds() / 60)

    # Count watchlisted vessels that have neither a gap score nor a stub score
    active_watchlist_ids = (
        db.query(VesselWatchlist.vessel_id)
        .filter(VesselWatchlist.is_active == True)  # noqa: E712
        .distinct()
    )
    vessels_with_ais_ids = db.query(AISPoint.vessel_id).distinct()
    vessels_with_gaps_ids = db.query(_GapEvent.vessel_id).distinct()
    watchlist_stubs_unscored = (
        db.query(func.count(Vessel.vessel_id))
        .filter(
            Vessel.vessel_id.in_(active_watchlist_ids),
            Vessel.vessel_id.notin_(vessels_with_ais_ids),
            Vessel.vessel_id.notin_(vessels_with_gaps_ids),
            Vessel.merged_into_vessel_id.is_(None),
            Vessel.watchlist_stub_score.is_(None),
        )
        .scalar()
        or 0
    )

    return {
        "latest_ais_utc": latest.isoformat() if latest else None,
        "staleness_minutes": staleness_minutes,
        "vessels_updated_last_1h": vessels_1h,
        "vessels_updated_last_24h": vessels_24h,
        "watchlist_stubs_unscored": watchlist_stubs_unscored,
    }


@router.get("/health/collection-status", tags=["health"])
@limiter.exempt
def get_collection_status(
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Collection pipeline health and statistics.

    Returns collection run history, AIS density metrics, per-source breakdown,
    merge readiness diagnostics, and data quality warnings.
    """
    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel

    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)

    # Total vessels
    total_vessels = db.query(func.count(Vessel.vessel_id)).scalar() or 0
    vessels_with_imo = (
        db.query(func.count(Vessel.vessel_id))
        .filter(
            Vessel.imo.isnot(None),
            Vessel.imo != "",
        )
        .scalar()
        or 0
    )

    # Total AIS points and density
    total_points = db.query(func.count(AISPoint.ais_point_id)).scalar() or 0
    ais_density = round(total_points / total_vessels, 2) if total_vessels > 0 else 0.0

    # Points added in last 24h and last N days
    one_day_ago = now - timedelta(hours=24)
    points_last_24h = (
        db.query(func.count(AISPoint.ais_point_id))
        .filter(
            AISPoint.timestamp_utc >= one_day_ago,
        )
        .scalar()
        or 0
    )
    points_last_n_days = (
        db.query(func.count(AISPoint.ais_point_id))
        .filter(
            AISPoint.timestamp_utc >= cutoff,
        )
        .scalar()
        or 0
    )

    # Per-source breakdown
    per_source_breakdown = {}
    try:
        source_rows = (
            db.query(AISPoint.source, func.count(AISPoint.ais_point_id))
            .group_by(AISPoint.source)
            .all()
        )
        per_source_breakdown = {(row[0] or "unknown"): row[1] for row in source_rows}
    except Exception as e:
        logger.debug("Per-source AIS breakdown query failed: %s", e)

    # Collection runs (Agent B's CollectionRun model may not exist yet)
    collection_runs = []
    try:
        from app.models.collection_run import CollectionRun

        runs = (
            db.query(CollectionRun)
            .filter(CollectionRun.started_at >= cutoff)
            .order_by(CollectionRun.started_at.desc())
            .limit(50)
            .all()
        )
        collection_runs = [
            {
                "run_id": r.collection_run_id,
                "source": getattr(r, "source", None),
                "started_utc": r.started_at.isoformat() if r.started_at else None,
                "finished_utc": r.finished_at.isoformat()
                if getattr(r, "finished_at", None)
                else None,
                "points_imported": getattr(r, "points_imported", None),
                "status": getattr(r, "status", None),
            }
            for r in runs
        ]
    except Exception as e:
        logger.debug("CollectionRun query failed (table may not exist): %s", e)

    # Merge readiness diagnostic
    merge_readiness = {}
    try:
        from app.modules.identity_resolver import diagnose_merge_readiness

        merge_readiness = diagnose_merge_readiness(db)
    except Exception as e:
        logger.debug("Merge readiness diagnostic failed: %s", e)
        merge_readiness = {"error": "merge readiness diagnostic unavailable"}

    # Data quality warnings
    data_quality_warnings = []
    if total_vessels == 0:
        data_quality_warnings.append("No vessels in database")
    elif ais_density < 5:
        data_quality_warnings.append(
            f"Low AIS density: {ais_density} points/vessel (recommend >= 10)"
        )
    if points_last_24h == 0:
        data_quality_warnings.append("No AIS points ingested in last 24 hours")
    imo_pct = (vessels_with_imo / total_vessels * 100) if total_vessels > 0 else 0
    if total_vessels > 0 and imo_pct < 50:
        data_quality_warnings.append(
            f"Low IMO coverage: {imo_pct:.0f}% of vessels have IMO numbers"
        )

    return {
        "collection_runs": collection_runs,
        "ais_density": ais_density,
        "total_points": total_points,
        "total_vessels": total_vessels,
        "vessels_with_imo": vessels_with_imo,
        "points_last_24h": points_last_24h,
        "points_last_n_days": points_last_n_days,
        "days": days,
        "per_source_breakdown": per_source_breakdown,
        "merge_readiness": merge_readiness,
        "data_quality_warnings": data_quality_warnings,
    }
