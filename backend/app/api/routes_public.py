"""Public dashboard endpoints — no authentication required.

Provides anonymised aggregate statistics for public-facing dashboards.
Vessel names and full MMSI numbers are never exposed.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api._helpers import limiter
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["public"])

# ---------------------------------------------------------------------------
# In-memory TTL cache (per-process)
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, dict]] = {}


def _get_cached(key: str, ttl_seconds: int) -> dict | None:
    """Return cached value if still within TTL, else None."""
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.monotonic() - ts > ttl_seconds:
        return None
    return data


def _set_cached(key: str, data: dict) -> None:
    _cache[key] = (time.monotonic(), data)


def _clear_cache() -> None:
    """Clear all cached entries. Used by tests."""
    _cache.clear()


# Startup warning for multi-worker deployments
_WORKER_WARNING_EMITTED = False


def _emit_worker_warning() -> None:
    global _WORKER_WARNING_EMITTED
    if not _WORKER_WARNING_EMITTED:
        workers = getattr(settings, "WEB_CONCURRENCY", None)
        if workers is not None and int(workers) > 1:
            logger.warning(
                "Public dashboard cache is per-process; "
                "with %s workers each process maintains its own cache.",
                workers,
            )
        _WORKER_WARNING_EMITTED = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _anonymize_mmsi(mmsi: str | None) -> str:
    """Return only the last 4 digits of an MMSI."""
    if not mmsi or len(mmsi) < 4:
        return "????"
    return mmsi[-4:]


def _tier_from_score(score: int | None) -> str:
    """Map numeric risk_score to high/medium/low tier."""
    if score is None:
        return "low"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# GET /public/dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard")
@limiter.limit(settings.RATE_LIMIT_VIEWER)
def public_dashboard(request: Request, db: Session = Depends(get_db)):
    """Aggregated public dashboard data with 5-minute TTL cache.

    All vessel identity information is anonymised — only MMSI last-4 digits
    and flag state are returned.
    """
    _emit_worker_warning()

    cached = _get_cached("dashboard", 300)
    if cached is not None:
        return cached

    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.vessel import Vessel

    # Vessel count (exclude merged)
    vessel_count = (
        db.query(func.count(Vessel.vessel_id))
        .filter(Vessel.merged_into_vessel_id == None)  # noqa: E711
        .scalar()
        or 0
    )

    # Alert counts by tier
    gaps = db.query(AISGapEvent.risk_score).all()
    alert_counts = {"high": 0, "medium": 0, "low": 0}
    for (score,) in gaps:
        tier = _tier_from_score(score)
        alert_counts[tier] += 1

    # Detection coverage
    monitored_zones = db.query(func.count(Corridor.corridor_id)).scalar() or 0
    active_corridors = monitored_zones  # all corridors are active

    # Recent alerts — top 10, anonymised
    recent_rows = (
        db.query(AISGapEvent)
        .order_by(AISGapEvent.gap_start_utc.desc())
        .limit(10)
        .all()
    )
    recent_alerts = []
    for gap in recent_rows:
        vessel = gap.vessel
        mmsi_raw = getattr(vessel, "mmsi", None) if vessel else None
        flag = getattr(vessel, "flag", None) if vessel else None
        recent_alerts.append(
            {
                "mmsi_suffix": _anonymize_mmsi(mmsi_raw),
                "flag": flag or "XX",
                "tier": _tier_from_score(gap.risk_score),
                "created_at": gap.gap_start_utc.isoformat() if gap.gap_start_utc else None,
            }
        )

    # Trend buckets — last 30 days, daily
    now = datetime.now(UTC)
    thirty_days_ago = now - timedelta(days=30)
    trend_rows = (
        db.query(
            func.date(AISGapEvent.gap_start_utc).label("day"),
            func.count(AISGapEvent.gap_event_id).label("cnt"),
        )
        .filter(AISGapEvent.gap_start_utc >= thirty_days_ago)
        .group_by(func.date(AISGapEvent.gap_start_utc))
        .order_by(func.date(AISGapEvent.gap_start_utc))
        .all()
    )
    trend_buckets = [{"date": str(row.day), "count": row.cnt} for row in trend_rows]

    # Detections by type
    gap_count = len(gaps)
    spoofing_count = db.query(func.count(SpoofingAnomaly.anomaly_id)).scalar() or 0

    # STS count — import lazily to avoid circular import if model not loaded
    sts_count = 0
    try:
        from app.models.sts_transfer import StsTransferEvent

        sts_count = db.query(func.count(StsTransferEvent.sts_event_id)).scalar() or 0
    except Exception:
        logger.debug("StsTransferEvent not available for public dashboard")

    detections_by_type = {
        "gap": gap_count,
        "spoofing": spoofing_count,
        "sts": sts_count,
    }

    result = {
        "vessel_count": vessel_count,
        "alert_counts": alert_counts,
        "detection_coverage": {
            "monitored_zones": monitored_zones,
            "active_corridors": active_corridors,
        },
        "recent_alerts": recent_alerts,
        "trend_buckets": trend_buckets,
        "detections_by_type": detections_by_type,
    }

    _set_cached("dashboard", result)
    return result


# ---------------------------------------------------------------------------
# GET /public/trends
# ---------------------------------------------------------------------------


@router.get("/trends")
@limiter.limit(settings.RATE_LIMIT_VIEWER)
def public_trends(request: Request, db: Session = Depends(get_db)):
    """90-day daily alert counts with 15-minute TTL cache."""
    _emit_worker_warning()

    cached = _get_cached("trends", 900)
    if cached is not None:
        return cached

    from app.models.gap_event import AISGapEvent

    now = datetime.now(UTC)
    ninety_days_ago = now - timedelta(days=90)

    rows = (
        db.query(
            func.date(AISGapEvent.gap_start_utc).label("day"),
            func.count(AISGapEvent.gap_event_id).label("cnt"),
        )
        .filter(AISGapEvent.gap_start_utc >= ninety_days_ago)
        .group_by(func.date(AISGapEvent.gap_start_utc))
        .order_by(func.date(AISGapEvent.gap_start_utc))
        .all()
    )

    result = {"days": [{"date": str(row.day), "count": row.cnt} for row in rows]}

    _set_cached("trends", result)
    return result
