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
# Fallback: if no baseline, use this fraction of corridor vessels as threshold
_FALLBACK_VESSEL_RATIO = 0.15  # 15% of corridor vessels
# Minimum vessels required for outage classification (E7)
_MIN_VESSELS_FOR_OUTAGE = 8
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
        return {
            "gaps_checked": 0,
            "outages_detected": 0,
            "gaps_marked": 0,
            "evasion_excluded": 0,
            "decoy_rejected": 0,
        }

    # First-run guard: if no baselines exist and no corridor has been seen,
    # skip feed outage detection entirely — we'd be using raw fallback thresholds
    # with no historical context, which causes massive over-classification.
    try:
        from app.models.corridor_gap_baseline import CorridorGapBaseline

        has_baselines = db.query(CorridorGapBaseline).first() is not None
    except Exception:
        has_baselines = False

    if not has_baselines:
        # Check if any corridors have gap data (i.e. this isn't the very first run)
        has_corridor_gaps = (
            db.query(AISGapEvent)
            .filter(AISGapEvent.corridor_id.isnot(None), AISGapEvent.risk_score > 0)
            .first()
        ) is not None
        if not has_corridor_gaps:
            logger.warning(
                "Feed outage detection: skipping — no baselines and no prior scored corridor gaps. "
                "Run compute_gap_rate_baseline() first."
            )
            return {
                "gaps_checked": 0,
                "outages_detected": 0,
                "gaps_marked": 0,
                "evasion_excluded": 0,
                "decoy_rejected": 0,
                "skipped_reason": "no_baselines",
            }

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
        return {
            "gaps_checked": 0,
            "outages_detected": 0,
            "gaps_marked": 0,
            "evasion_excluded": 0,
            "decoy_rejected": 0,
        }

    # Group gaps by (corridor_id, 2h time window)
    clusters = _cluster_gaps(gaps)

    outages_detected = 0
    gaps_marked = 0
    evasion_excluded = 0
    decoy_rejected = 0
    source_outages_detected = 0

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
            high_risk_count = sum(1 for vid in unique_vessels if vid in high_risk_vessel_ids)
            if vessel_count > 0 and (high_risk_count / vessel_count) > max_outage_ratio:
                decoy_rejected += 1
                logger.info(
                    "Feed outage cluster rejected (decoy): corridor=%s window=%s "
                    "high_risk=%d/%d (%.0f%% > %.0f%% threshold)",
                    cluster.corridor_id,
                    cluster.window_start,
                    high_risk_count,
                    vessel_count,
                    high_risk_count / vessel_count * 100,
                    max_outage_ratio * 100,
                )
                continue

            # Source-aware grouping: if >80% of gaps come from one AIS source
            # and that source had no recent collection activity, classify as
            # source_outage rather than generic feed_outage.
            dominant_source = _detect_dominant_source(cluster.gaps)

            if dominant_source is not None:
                source_outages_detected += 1
                logger.info(
                    "Source outage detected: corridor=%s window=%s source=%s (%d gaps)",
                    cluster.corridor_id,
                    cluster.window_start,
                    dominant_source,
                    len(cluster.gaps),
                )

            outages_detected += 1
            for gap in cluster.gaps:
                # E2: Exclude gaps with evasion signals (SpoofingAnomaly or STS ±6h)
                if _has_evasion_signals(db, gap):
                    evasion_excluded += 1
                    continue
                gap.is_feed_outage = True
                if dominant_source is not None:
                    gap.coverage_quality = f"SOURCE_OUTAGE:{dominant_source}"
                gaps_marked += 1

    if gaps_marked > 0:
        db.commit()

    logger.info(
        "Feed outage detection: checked %d gaps, found %d outages (%d source-specific), "
        "marked %d gaps, evasion-excluded %d, decoy-rejected %d clusters.",
        len(gaps),
        outages_detected,
        source_outages_detected,
        gaps_marked,
        evasion_excluded,
        decoy_rejected,
    )
    return {
        "gaps_checked": len(gaps),
        "outages_detected": outages_detected,
        "source_outages_detected": source_outages_detected,
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

        spoof_count = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == gap.vessel_id,
                SpoofingAnomaly.start_time_utc <= time_hi,
                SpoofingAnomaly.start_time_utc >= time_lo,
            )
            .count()
        )
        if spoof_count > 0:
            return True
    except Exception:
        logger.warning("SpoofingAnomaly query failed for vessel %s", gap.vessel_id, exc_info=True)

    try:
        from sqlalchemy import or_

        from app.models.sts_transfer import StsTransferEvent

        sts_count = (
            db.query(StsTransferEvent)
            .filter(
                or_(
                    StsTransferEvent.vessel_1_id == gap.vessel_id,
                    StsTransferEvent.vessel_2_id == gap.vessel_id,
                ),
                StsTransferEvent.start_time_utc <= time_hi,
                StsTransferEvent.end_time_utc >= time_lo,
            )
            .count()
        )
        if sts_count > 0:
            return True
    except Exception:
        logger.warning("StsTransferEvent query failed for vessel %s", gap.vessel_id, exc_info=True)

    return False


def _get_high_risk_vessel_ids(db: Session) -> set[int]:
    """Return vessel IDs with score >50 from the most recent scored gaps.

    E7: Used to detect coordinated decoy abuse — if too many high-risk
    vessels are in a "feed outage" cluster, it's likely not a real outage.
    """
    high_risk: set[int] = set()
    try:
        rows = db.query(AISGapEvent.vessel_id).filter(AISGapEvent.risk_score > 50).distinct().all()
        high_risk = {r[0] for r in rows}
    except Exception:
        pass
    return high_risk


def _detect_dominant_source(gaps: list) -> str | None:
    """Check if >80% of gaps in a cluster originate from a single AIS source.

    Looks at the ``source`` field on the gap event (set when provenance is known).
    Returns the dominant source name if one contributes >80% of gaps, else None.
    """
    if not gaps:
        return None

    source_counts: dict[str, int] = defaultdict(int)
    total_with_source = 0

    for gap in gaps:
        src = getattr(gap, "source", None)
        if src:
            source_counts[src] += 1
            total_with_source += 1

    if total_with_source == 0:
        return None

    for source, count in source_counts.items():
        if count / len(gaps) > 0.8:
            return source

    return None


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

    return [_GapCluster(corridor_id=k[0], window_start=k[1], gaps=v) for k, v in buckets.items()]


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

    Uses 3× P95 baseline if available, otherwise falls back to a proportional
    threshold based on the number of unique vessels in the corridor.
    """
    if corridor_id is None:
        from app.models.gap_event import AISGapEvent as _GE

        cutoff_7d = reference_time - timedelta(days=7)
        null_vessel_count = (
            db.query(_GE.vessel_id)
            .filter(_GE.corridor_id == None, _GE.gap_start_utc >= cutoff_7d)  # noqa: E711
            .distinct()
            .count()
        )
        return max(int(null_vessel_count * _FALLBACK_VESSEL_RATIO), 25)

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
            # Never go below _MIN_VESSELS_FOR_OUTAGE
            return max(adaptive, _MIN_VESSELS_FOR_OUTAGE)
    except Exception:
        logger.debug("Could not query corridor gap baseline — using fallback.", exc_info=True)

    # Proportional fallback: count unique vessels in this corridor and use
    # _FALLBACK_VESSEL_RATIO of them (minimum _MIN_VESSELS_FOR_OUTAGE).
    try:
        from app.models.gap_event import AISGapEvent as _GE

        corridor_vessel_count = (
            db.query(_GE.vessel_id).filter(_GE.corridor_id == corridor_id).distinct().count()
        )
        if corridor_vessel_count > 0:
            proportional = max(
                int(corridor_vessel_count * _FALLBACK_VESSEL_RATIO),
                _MIN_VESSELS_FOR_OUTAGE,
            )
            return proportional
    except Exception:
        logger.debug("Could not count corridor vessels — using minimum.", exc_info=True)

    return _MIN_VESSELS_FOR_OUTAGE
