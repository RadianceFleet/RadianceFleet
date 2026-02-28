"""Gap rate baseline computation for corridor-level anomaly detection.

Computes rolling 7-day gap counts per corridor and stores mean / P95
thresholds in the CorridorGapBaseline table.  When a corridor's current
gap count exceeds P95, the zone is flagged as potential jamming rather
than individual vessel evasion -- this suppresses dark-dark STS false
positives in areas with broad AIS outages.

Feature-gated by ``settings.DARK_STS_DETECTION_ENABLED``.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.corridor import Corridor
from app.models.corridor_gap_baseline import CorridorGapBaseline
from app.models.gap_event import AISGapEvent
from app.modules.sts_detector import _parse_wkt_bbox, _in_bbox

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 7
_MIN_WINDOWS_FOR_STATS = 2  # need at least 2 windows to compute a meaningful mean/P95


def compute_gap_rate_baseline(db: Session) -> dict:
    """Compute rolling 7-day gap rate baseline for each corridor.

    For each corridor with geometry, counts gap events whose start/end
    positions fall within the corridor bounding box, grouped into 7-day
    windows.  Stores the mean and P95 threshold so downstream detectors
    can suppress zone-wide jamming noise.

    Returns:
        ``{"corridors_processed": N, "baselines_created": M}``
    """
    if not settings.DARK_STS_DETECTION_ENABLED:
        logger.debug("Gap rate baseline: DARK_STS_DETECTION_ENABLED is False -- skipping.")
        return {"corridors_processed": 0, "baselines_created": 0}

    corridors = db.query(Corridor).all()
    all_gaps = db.query(AISGapEvent).all()

    if not all_gaps:
        logger.info("Gap rate baseline: no gap events found.")
        return {"corridors_processed": 0, "baselines_created": 0}

    # Find the global time range across all gaps
    min_time = min(g.gap_start_utc for g in all_gaps)
    max_time = max(g.gap_end_utc for g in all_gaps)

    corridors_processed = 0
    baselines_created = 0

    for corridor in corridors:
        bbox = _parse_wkt_bbox(corridor.geometry)
        if bbox is None:
            continue

        # Find gaps within this corridor's bbox (using gap_off/gap_on positions if available,
        # or corridor_id match as fallback)
        corridor_gaps = []
        for gap in all_gaps:
            # Direct corridor_id match
            if gap.corridor_id == corridor.corridor_id:
                corridor_gaps.append(gap)
                continue
            # Position-based match from gap start/end coordinates
            if gap.gap_off_lat is not None and gap.gap_off_lon is not None:
                if _in_bbox(gap.gap_off_lat, gap.gap_off_lon, bbox):
                    corridor_gaps.append(gap)
                    continue
            if gap.gap_on_lat is not None and gap.gap_on_lon is not None:
                if _in_bbox(gap.gap_on_lat, gap.gap_on_lon, bbox):
                    corridor_gaps.append(gap)
                    continue

        if not corridor_gaps:
            continue

        corridors_processed += 1

        # Build 7-day windows
        window_counts: list[int] = []
        window_start = min_time
        while window_start < max_time:
            window_end = window_start + timedelta(days=_WINDOW_DAYS)
            count = 0
            for gap in corridor_gaps:
                # Gap overlaps window if gap_start < window_end AND gap_end > window_start
                if gap.gap_start_utc < window_end and gap.gap_end_utc > window_start:
                    count += 1
            window_counts.append(count)

            # Store baseline record for this window
            baseline = CorridorGapBaseline(
                corridor_id=corridor.corridor_id,
                window_start=window_start,
                window_end=window_end,
                gap_count=count,
            )
            db.add(baseline)
            baselines_created += 1

            window_start = window_end

        # Compute mean and P95 across all windows for this corridor
        if len(window_counts) >= _MIN_WINDOWS_FOR_STATS:
            mean_count = sum(window_counts) / len(window_counts)
            p95 = _percentile(window_counts, 95)

            # Update all baselines for this corridor with the computed stats
            db.query(CorridorGapBaseline).filter(
                CorridorGapBaseline.corridor_id == corridor.corridor_id,
            ).update(
                {"mean_gap_count": mean_count, "p95_threshold": p95},
                synchronize_session="fetch",
            )

    db.commit()
    logger.info(
        "Gap rate baseline: processed %d corridors, created %d baselines.",
        corridors_processed,
        baselines_created,
    )
    return {"corridors_processed": corridors_processed, "baselines_created": baselines_created}


def is_above_p95(db: Session, corridor_id: int, reference_time: datetime) -> bool:
    """Check if a corridor's current gap rate exceeds the P95 threshold.

    Returns True if the reference_time falls in a window where the gap count
    exceeds the corridor's P95 threshold -- indicating zone-wide jamming
    rather than individual vessel evasion.
    """
    baseline = (
        db.query(CorridorGapBaseline)
        .filter(
            CorridorGapBaseline.corridor_id == corridor_id,
            CorridorGapBaseline.window_start <= reference_time,
            CorridorGapBaseline.window_end > reference_time,
        )
        .first()
    )
    if baseline is None or baseline.p95_threshold is None:
        return False
    return baseline.gap_count > baseline.p95_threshold


def _percentile(values: list[int | float], pct: float) -> float:
    """Compute the pct-th percentile of a list of values using linear interpolation."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    # rank = (pct/100) * (n - 1)
    rank = (pct / 100.0) * (n - 1)
    lower = int(math.floor(rank))
    upper = min(lower + 1, n - 1)
    frac = rank - lower
    return sorted_vals[lower] + frac * (sorted_vals[upper] - sorted_vals[lower])
