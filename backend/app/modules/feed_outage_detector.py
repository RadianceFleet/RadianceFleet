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
  5. Exclude gaps whose vessel has a SpoofingAnomaly or StsTransferEvent
     within ±6h of the gap (E2: anomaly-aware suppression)
  6. Anti-decoy: if >30% of cluster vessels are high-risk, do NOT classify
     as feed outage (E7: coordinated decoy abuse protection)

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
# Minimum vessels required for outage classification (E7)
_MIN_VESSELS_FOR_OUTAGE = 5
# Window for checking evasion signals around a gap (E2)
_EVASION_CHECK_HOURS = 6


class _GapCluster(NamedTuple):
    """A cluster of gaps in the same corridor and time window."""
    corridor_id: int | None
    window_start: datetime
    gaps: list


def detect_feed_outages(db: Session, max_outage_ratio: float = 0.3) -> dict:
    """Scan unscored gap events for feed outage patterns.

    Args:
        max_outage_ratio: Maximum fraction of high-risk vessels (score >50 in
            previous run) allowed in a cluster for it to be classified as a
            feed outage. If exceeded, the cluster is treated as potential
            coordinated decoy abuse and NOT suppressed. Default 0.3 (30%).

    Returns:
        ``{"gaps_checked": N, "outages_detected": M, "gaps_marked": K,
           "evasion_excluded": E, "decoy_rejected": D}``
    """
    if not settings.FEED_OUTAGE_DETECTION_ENABLED:
        logger.debug("Feed outage detection: disabled — skipping.")
        return {"gaps_checked": 0, "outages_detected": 0, "gaps_marked": 0,
                "evasion_excluded": 0, "decoy_rejected": 0}

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
        return {"gaps_checked": 0, "outages_detected": 0, "gaps_marked": 0,
                "evasion_excluded": 0, "decoy_rejected": 0}

    # Group gaps by (corridor_id, 2h time window)
    clusters = _cluster_gaps(gaps)

    outages_detected = 0
    gaps_marked = 0
    evasion_excluded = 0
    decoy_rejected = 0

    # Pre-load high-risk vessel IDs from previous pipeline run (E7)
    high_risk_vessel_ids = _get_high_risk_vessel_ids(db)

    for cluster in clusters:
        # Count unique vessels in this cluster
        unique_vessels = {g.vessel_id for g in cluster.gaps}
        vessel_count = len(unique_vessels)

        if vessel_count < _MIN_VESSELS_FOR_OUTAGE:
            continue  # too few vessels — not a credible outage pattern

        # Determine threshold
        threshold = _get_threshold(db, cluster.corridor_id, cluster.window_start)

        if vessel_count >= threshold:
            # E7: Anti-decoy check — reject if too many high-risk vessels
            high_risk_count = sum(
                1 for vid in unique_vessels if vid in high_risk_vessel_ids
            )
            if vessel_count > 0 and (high_risk_count / vessel_count) > max_outage_ratio:
                decoy_rejected += 1
                logger.info(
                    "Feed outage cluster rejected (decoy): corridor=%s window=%s "
                    "high_risk=%d/%d (%.0f%% > %.0f%% threshold)",
                    cluster.corridor_id, cluster.window_start,
                    high_risk_count, vessel_count,
                    high_risk_count / vessel_count * 100,
                    max_outage_ratio * 100,
                )
                continue

            outages_detected += 1
            for gap in cluster.gaps:
                # E2: Exclude gaps with evasion signals (SpoofingAnomaly or STS ±6h)
                if _has_evasion_signals(db, gap):
                    evasion_excluded += 1
                    continue
                gap.is_feed_outage = True
                gaps_marked += 1

    if gaps_marked > 0:
        db.commit()

    logger.info(
        "Feed outage detection: checked %d gaps, found %d outages, marked %d gaps, "
        "evasion-excluded %d, decoy-rejected %d clusters.",
        len(gaps), outages_detected, gaps_marked, evasion_excluded, decoy_rejected,
    )
    return {
        "gaps_checked": len(gaps),
        "outages_detected": outages_detected,
        "gaps_marked": gaps_marked,
        "evasion_excluded": evasion_excluded,
        "decoy_rejected": decoy_rejected,
    }


def _has_evasion_signals(db: Session, gap: AISGapEvent) -> bool:
    """Check if a gap's vessel has SpoofingAnomaly or StsTransferEvent within ±6h.

    E2: These gaps should be scored despite the outage — the vessel may be
    deliberately using the outage for cover.
    """
    window = timedelta(hours=_EVASION_CHECK_HOURS)
    gap_start = gap.gap_start_utc
    gap_end = gap.gap_end_utc

    time_lo = gap_start - window
    time_hi = gap_end + window

    try:
        from app.models.spoofing_anomaly import SpoofingAnomaly

        spoof_count = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == gap.vessel_id,
            SpoofingAnomaly.start_time_utc <= time_hi,
            SpoofingAnomaly.start_time_utc >= time_lo,
        ).count()
        if spoof_count > 0:
            return True
    except Exception:
        pass

    try:
        from app.models.sts_transfer import StsTransferEvent
        from sqlalchemy import or_

        sts_count = db.query(StsTransferEvent).filter(
            or_(
                StsTransferEvent.vessel_1_id == gap.vessel_id,
                StsTransferEvent.vessel_2_id == gap.vessel_id,
            ),
            StsTransferEvent.start_time_utc <= time_hi,
            StsTransferEvent.end_time_utc >= time_lo,
        ).count()
        if sts_count > 0:
            return True
    except Exception:
        pass

    return False


def _get_high_risk_vessel_ids(db: Session) -> set[int]:
    """Return vessel IDs with score >50 from the most recent scored gaps.

    E7: Used to detect coordinated decoy abuse — if too many high-risk
    vessels are in a "feed outage" cluster, it's likely not a real outage.
    """
    high_risk: set[int] = set()
    try:
        rows = (
            db.query(AISGapEvent.vessel_id)
            .filter(AISGapEvent.risk_score > 50)
            .distinct()
            .all()
        )
        high_risk = {r[0] for r in rows}
    except Exception:
        pass
    return high_risk


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
