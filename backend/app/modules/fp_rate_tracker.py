"""False-positive rate tracking and calibration suggestion engine.

Computes per-corridor FP rates from analyst verdicts on AISGapEvent records,
provides time-windowed trend analysis, and generates calibration suggestions
to reduce alert fatigue in high-FP corridors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Integer, and_, func
from sqlalchemy.orm import Session

from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CorridorFPRate:
    """FP rate statistics for a single corridor."""

    corridor_id: int
    corridor_name: str
    total_alerts: int = 0
    false_positives: int = 0
    fp_rate: float = 0.0
    fp_rate_30d: float = 0.0
    fp_rate_90d: float = 0.0
    trend: str = "stable"  # "increasing", "decreasing", "stable"


@dataclass
class CalibrationSuggestion:
    """Auto-generated suggestion to tune a corridor's scoring multiplier."""

    corridor_id: int
    corridor_name: str
    current_multiplier: float
    suggested_multiplier: float
    reason: str
    fp_rate: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _reviewed_gaps_query(db: Session, corridor_id: int | None = None):
    """Base query for gap events that have analyst verdicts."""
    q = db.query(AISGapEvent).filter(AISGapEvent.is_false_positive.isnot(None))
    if corridor_id is not None:
        q = q.filter(AISGapEvent.corridor_id == corridor_id)
    return q


def _fp_rate_for_window(
    db: Session, corridor_id: int, since: datetime | None = None
) -> tuple[int, int, float]:
    """Return (total_reviewed, false_positives, fp_rate) for a time window."""
    filters = [
        AISGapEvent.corridor_id == corridor_id,
        AISGapEvent.is_false_positive.isnot(None),
    ]
    if since is not None:
        filters.append(AISGapEvent.review_date >= since)

    rows = (
        db.query(
            func.count(AISGapEvent.gap_event_id).label("total"),
            func.sum(
                func.cast(AISGapEvent.is_false_positive, Integer)
            ).label("fp_count"),
        )
        .filter(and_(*filters))
        .one()
    )
    total = rows.total or 0
    fp_count = rows.fp_count or 0
    rate = fp_count / total if total > 0 else 0.0
    return total, fp_count, rate


def _compute_trend(
    db: Session, corridor_id: int, now: datetime | None = None
) -> str:
    """Compare 30-day FP rate to previous 30-day window to detect trend.

    Returns "increasing", "decreasing", or "stable".
    """
    now = now or datetime.utcnow()
    boundary_recent = now - timedelta(days=30)
    boundary_prev = now - timedelta(days=60)

    _, _, rate_recent = _fp_rate_for_window(db, corridor_id, since=boundary_recent)

    # Previous window: 60d ago to 30d ago
    filters = [
        AISGapEvent.corridor_id == corridor_id,
        AISGapEvent.is_false_positive.isnot(None),
        AISGapEvent.review_date >= boundary_prev,
        AISGapEvent.review_date < boundary_recent,
    ]
    rows = (
        db.query(
            func.count(AISGapEvent.gap_event_id).label("total"),
            func.sum(
                func.cast(AISGapEvent.is_false_positive, Integer)
            ).label("fp_count"),
        )
        .filter(and_(*filters))
        .one()
    )
    total_prev = rows.total or 0
    fp_prev = rows.fp_count or 0
    rate_prev = fp_prev / total_prev if total_prev > 0 else 0.0

    # Need enough data in both windows to declare a trend
    if total_prev < 3:
        return "stable"

    diff = rate_recent - rate_prev
    if diff > 0.05:
        return "increasing"
    elif diff < -0.05:
        return "decreasing"
    return "stable"


def _get_corridor_multiplier(corridor: Corridor, config: dict | None = None) -> float:
    """Get the current scoring multiplier for a corridor type.

    Mirrors logic from risk_scoring._corridor_multiplier without importing it
    (to avoid circular deps and keep this module testable standalone).
    """
    if config is None:
        config = {}
    corridor_cfg = config.get("corridor", {})

    ct = str(
        corridor.corridor_type.value
        if hasattr(corridor.corridor_type, "value")
        else corridor.corridor_type
    )

    if ct == "sts_zone":
        return float(corridor_cfg.get("known_sts_zone", 1.5))
    elif ct == "export_route":
        return float(corridor_cfg.get("high_risk_export_corridor", 1.5))
    elif ct == "legitimate_trade_route":
        return float(corridor_cfg.get("legitimate_trade_route", 0.7))
    else:
        return float(corridor_cfg.get("standard_corridor", 1.0))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_fp_rate(db: Session, corridor_id: int) -> CorridorFPRate | None:
    """Compute FP rate statistics for a single corridor."""
    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if corridor is None:
        return None

    now = datetime.utcnow()
    total, fp_count, rate = _fp_rate_for_window(db, corridor_id)
    _, _, rate_30d = _fp_rate_for_window(db, corridor_id, since=now - timedelta(days=30))
    _, _, rate_90d = _fp_rate_for_window(db, corridor_id, since=now - timedelta(days=90))
    trend = _compute_trend(db, corridor_id, now=now)

    return CorridorFPRate(
        corridor_id=corridor_id,
        corridor_name=corridor.name,
        total_alerts=total,
        false_positives=fp_count,
        fp_rate=round(rate, 4),
        fp_rate_30d=round(rate_30d, 4),
        fp_rate_90d=round(rate_90d, 4),
        trend=trend,
    )


def compute_fp_rates(db: Session) -> list[CorridorFPRate]:
    """Compute FP rates for all corridors that have reviewed gap events."""
    # Get corridors that have at least one reviewed gap event
    corridor_ids = (
        db.query(AISGapEvent.corridor_id)
        .filter(
            AISGapEvent.corridor_id.isnot(None),
            AISGapEvent.is_false_positive.isnot(None),
        )
        .distinct()
        .all()
    )

    results: list[CorridorFPRate] = []
    for (cid,) in corridor_ids:
        rate = compute_fp_rate(db, cid)
        if rate is not None:
            results.append(rate)

    # Sort by FP rate descending so worst corridors appear first
    results.sort(key=lambda r: r.fp_rate, reverse=True)
    return results


def generate_calibration_suggestions(
    db: Session, config: dict | None = None
) -> list[CalibrationSuggestion]:
    """Generate auto-calibration suggestions for corridors with extreme FP rates.

    Rules:
    - FP rate > 50%: suggest halving the corridor multiplier
    - FP rate > 30%: suggest reducing by 25%
    - FP rate > 15%: suggest reducing by 10%
    - FP rate < 5% (with >= 20 alerts): suggest increasing by 15%
    - FP rate < 2% (with >= 20 alerts): suggest increasing by 25%
    """
    rates = compute_fp_rates(db)
    suggestions: list[CalibrationSuggestion] = []

    for fp in rates:
        if fp.total_alerts < 5:
            # Not enough data to make a reliable suggestion
            continue

        corridor = db.query(Corridor).filter(Corridor.corridor_id == fp.corridor_id).first()
        if corridor is None:
            continue

        current_mult = _get_corridor_multiplier(corridor, config)
        suggestion = None

        if fp.fp_rate > 0.50:
            suggested = round(current_mult * 0.50, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} is critically high (>50%). "
                f"Recommend halving corridor multiplier from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )
        elif fp.fp_rate > 0.30:
            suggested = round(current_mult * 0.75, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} exceeds 30% threshold. "
                f"Recommend reducing corridor multiplier from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )
        elif fp.fp_rate > 0.15:
            suggested = round(current_mult * 0.90, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} exceeds 15% threshold. "
                f"Recommend modest reduction from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )
        elif fp.fp_rate < 0.02 and fp.total_alerts >= 20:
            suggested = round(current_mult * 1.25, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} is very low (<2%) with {fp.total_alerts} alerts. "
                f"Corridor may be under-weighted. Suggest increasing from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )
        elif fp.fp_rate < 0.05 and fp.total_alerts >= 20:
            suggested = round(current_mult * 1.15, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} is low (<5%) with {fp.total_alerts} alerts. "
                f"Suggest modest increase from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )

        if suggestion is not None:
            suggestions.append(suggestion)

    return suggestions
