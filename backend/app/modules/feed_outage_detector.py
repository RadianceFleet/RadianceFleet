"""Feed outage detection — identify gaps caused by data feed failures.

When a terrestrial AIS receiver goes offline, ALL vessels in range lose
coverage simultaneously.  This creates a burst of gap events that look
like coordinated darkness but are actually infrastructure failures.

Algorithm:
  1. Group new (unscored, risk_score=0) gap events by corridor + 2h window
  2. Adaptive threshold: gap count > 3× the corridor's P95 baseline
     (from CorridorGapBaseline) AND affecting unrelated vessels → feed outage
  3. Fallback (no baseline available): ≥5 unrelated vessels in the window
  4. Mark matching gaps with ``is_feed_outage=True`` — scoring skips them

Feature-gated by ``settings.FEED_OUTAGE_DETECTION_ENABLED``.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import NamedTuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models.gap_event import AISGapEvent

logger = logging.getLogger(__name__)

# Gaps within this window are grouped for outage detection
_WINDOW_HOURS = 2
# Multiplier applied to P95 baseline for adaptive threshold
_P95_MULTIPLIER = 3.0
# Fallback: if no baseline, this many unrelated vessels = outage
_FALLBACK_VESSEL_COUNT = 5


class _GapCluster(NamedTuple):
    """A cluster of gaps in the same corridor and time window."""
    corridor_id: int | None
    window_start: datetime
    gaps: list


def detect_feed_outages(db: Session) -> dict:
    """Scan unscored gap events for feed outage patterns.

    Returns:
        ``{"gaps_checked": N, "outages_detected": M, "gaps_marked": K}``
    """
    if not settings.FEED_OUTAGE_DETECTION_ENABLED:
        logger.debug("Feed outage detection: disabled — skipping.")
        return {"gaps_checked": 0, "outages_detected": 0, "gaps_marked": 0}

    # Fetch unscored gaps (risk_score == 0 means not yet scored)
    gaps = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.risk_score == 0,
            AISGapEvent.is_feed_outage == False,  # noqa: E712
        )
        .all()
    )

    if not gaps:
        return {"gaps_checked": 0, "outages_detected": 0, "gaps_marked": 0}

    # Group gaps by (corridor_id, 2h time window)
    clusters = _cluster_gaps(gaps)

    outages_detected = 0
    gaps_marked = 0

    for cluster in clusters:
        # Count unique vessels in this cluster
        unique_vessels = {g.vessel_id for g in cluster.gaps}
        vessel_count = len(unique_vessels)

        if vessel_count < 2:
            continue  # single vessel — not an outage pattern

        # Determine threshold
        threshold = _get_threshold(db, cluster.corridor_id, cluster.window_start)

        if vessel_count >= threshold:
            outages_detected += 1
            for gap in cluster.gaps:
                gap.is_feed_outage = True
                gaps_marked += 1

    if gaps_marked > 0:
        db.commit()

    logger.info(
        "Feed outage detection: checked %d gaps, found %d outages, marked %d gaps.",
        len(gaps), outages_detected, gaps_marked,
    )
    return {
        "gaps_checked": len(gaps),
        "outages_detected": outages_detected,
        "gaps_marked": gaps_marked,
    }


def _cluster_gaps(gaps: list[AISGapEvent]) -> list[_GapCluster]:
    """Group gaps by corridor_id and 2h time window."""
    # Key: (corridor_id, window_bucket) → list of gaps
    buckets: dict[tuple, list] = defaultdict(list)

    for gap in gaps:
        # Bucket by 2-hour window (floor gap_start_utc to nearest 2h)
        ts = gap.gap_start_utc
        bucket_hours = (ts.hour // _WINDOW_HOURS) * _WINDOW_HOURS
        window_start = ts.replace(hour=bucket_hours, minute=0, second=0, microsecond=0)
        key = (gap.corridor_id, window_start)
        buckets[key].append(gap)

    return [
        _GapCluster(corridor_id=k[0], window_start=k[1], gaps=v)
        for k, v in buckets.items()
    ]


def tag_coverage_quality(db: Session) -> dict:
    """Tag unscored gap events with coverage quality from corridor metadata.

    This is metadata only — coverage quality NEVER reduces risk scores.
    Analysts use it for contextual filtering in the UI.

    Returns:
        ``{"gaps_tagged": N}``
    """
    if not settings.COVERAGE_QUALITY_TAGGING_ENABLED:
        logger.debug("Coverage quality tagging: disabled — skipping.")
        return {"gaps_tagged": 0}

    gaps = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.risk_score == 0,
            AISGapEvent.coverage_quality.is_(None),
        )
        .all()
    )

    if not gaps:
        return {"gaps_tagged": 0}

    # Lazy import to avoid circular dependency with routes
    try:
        from app.api.routes import _get_coverage_quality
    except ImportError:
        logger.warning("Could not import _get_coverage_quality — skipping tagging.")
        return {"gaps_tagged": 0}

    tagged = 0
    for gap in gaps:
        corridor = getattr(gap, "corridor", None)
        if corridor is not None and hasattr(corridor, "name"):
            quality = _get_coverage_quality(corridor.name)
            gap.coverage_quality = quality
            tagged += 1
        else:
            gap.coverage_quality = "UNKNOWN"
            tagged += 1

    if tagged > 0:
        db.commit()

    logger.info("Coverage quality tagging: tagged %d gaps.", tagged)
    return {"gaps_tagged": tagged}


def _get_threshold(db: Session, corridor_id: int | None, reference_time: datetime) -> int:
    """Get the adaptive feed outage threshold for a corridor.

    Uses 3× P95 baseline if available, otherwise falls back to
    _FALLBACK_VESSEL_COUNT.
    """
    if corridor_id is None:
        return _FALLBACK_VESSEL_COUNT

    try:
        from app.models.corridor_gap_baseline import CorridorGapBaseline

        baseline = (
            db.query(CorridorGapBaseline)
            .filter(
                CorridorGapBaseline.corridor_id == corridor_id,
                CorridorGapBaseline.window_start <= reference_time,
                CorridorGapBaseline.window_end > reference_time,
            )
            .first()
        )

        if baseline is not None and baseline.p95_threshold is not None:
            adaptive = int(baseline.p95_threshold * _P95_MULTIPLIER)
            # Never go below 3 vessels (even with very low baselines)
            return max(adaptive, 3)
    except Exception:
        logger.debug("Could not query corridor gap baseline — using fallback.", exc_info=True)

    return _FALLBACK_VESSEL_COUNT
