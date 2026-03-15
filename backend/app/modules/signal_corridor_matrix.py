"""Signal-corridor FP rate cross-tabulation matrix.

Computes per-(signal, corridor) false-positive rates from analyst verdicts,
aggregates into regional views, and identifies suppression candidates where
a signal has an unusually high FP rate in a specific corridor.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session, load_only

from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent
from app.models.scoring_region import ScoringRegion
from app.schemas.signal_matrix import (
    SignalCorridorCell,
    SignalRegionCell,
    SuppressionCandidate,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_breakdown(raw) -> dict:
    """Parse risk_breakdown_json, handling both dict and string forms."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _is_signal_key(key: str, value) -> bool:
    """Return True if the key is a valid signal (not metadata) with a nonzero score."""
    if key.startswith("_"):
        return False
    if not isinstance(value, (int, float)):
        return False
    return value != 0


def _fetch_corridor_names(db: Session, corridor_ids: set[int]) -> dict[int, str]:
    """Batch-fetch corridor names to avoid N+1 queries."""
    if not corridor_ids:
        return {}
    corridors = (
        db.query(Corridor)
        .filter(Corridor.corridor_id.in_(corridor_ids))
        .options(load_only(Corridor.corridor_id, Corridor.name))
        .all()
    )
    return {c.corridor_id: c.name for c in corridors}


def _query_reviewed_alerts(db: Session, since: datetime | None = None):
    """Query reviewed alerts with corridor_id, loading only needed columns."""
    q = db.query(AISGapEvent).options(
        load_only(
            AISGapEvent.gap_event_id,
            AISGapEvent.corridor_id,
            AISGapEvent.risk_breakdown_json,
            AISGapEvent.is_false_positive,
            AISGapEvent.review_date,
        )
    ).filter(
        AISGapEvent.is_false_positive.isnot(None),
        AISGapEvent.corridor_id.isnot(None),
    )
    if since is not None:
        q = q.filter(AISGapEvent.review_date >= since)
    return q.all()


def _build_signal_corridor_counts(
    alerts: list,
) -> tuple[
    dict[tuple[str, int], dict[str, int]],
    dict[str, dict[str, int]],
]:
    """Parse alerts into per-(signal, corridor) and per-signal global counts.

    Returns:
        (cell_counts, global_counts) where each value dict has keys 'tp' and 'fp'.
    """
    cell_counts: dict[tuple[str, int], dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0}
    )
    global_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0}
    )

    for alert in alerts:
        breakdown = _parse_breakdown(alert.risk_breakdown_json)
        is_fp = alert.is_false_positive
        corridor_id = alert.corridor_id

        for key, value in breakdown.items():
            if not _is_signal_key(key, value):
                continue

            verdict_key = "fp" if is_fp else "tp"
            cell_counts[(key, corridor_id)][verdict_key] += 1
            global_counts[key][verdict_key] += 1

    return cell_counts, global_counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_signal_corridor_matrix(
    db: Session, since: datetime | None = None
) -> list[SignalCorridorCell]:
    """Compute the signal-corridor FP rate cross-tabulation.

    Args:
        db: Database session.
        since: If provided, only include alerts reviewed on or after this date.

    Returns:
        List of SignalCorridorCell objects, one per (signal, corridor) pair
        that meets the minimum-verdicts threshold.
    """
    from app.config import settings

    min_verdicts = getattr(settings, "SIGNAL_MATRIX_MIN_VERDICTS", 5)

    alerts = _query_reviewed_alerts(db, since)
    if not alerts:
        return []

    cell_counts, global_counts = _build_signal_corridor_counts(alerts)

    # Compute global FP rates per signal
    global_fp_rates: dict[str, float] = {}
    for signal, counts in global_counts.items():
        total = counts["tp"] + counts["fp"]
        global_fp_rates[signal] = counts["fp"] / total if total > 0 else 0.0

    # Collect corridor IDs for batch name lookup
    corridor_ids = {cid for _, cid in cell_counts.keys()}
    corridor_names = _fetch_corridor_names(db, corridor_ids)

    results: list[SignalCorridorCell] = []
    for (signal_name, corridor_id), counts in cell_counts.items():
        total = counts["tp"] + counts["fp"]
        if total < min_verdicts:
            continue

        fp_rate = counts["fp"] / total
        global_rate = global_fp_rates.get(signal_name, 0.0)
        lift = fp_rate / global_rate if global_rate > 0 else 0.0

        results.append(
            SignalCorridorCell(
                signal_name=signal_name,
                corridor_id=corridor_id,
                corridor_name=corridor_names.get(corridor_id, f"Corridor {corridor_id}"),
                tp_count=counts["tp"],
                fp_count=counts["fp"],
                total=total,
                fp_rate=round(fp_rate, 4),
                lift=round(lift, 4),
            )
        )

    return results


def compute_signal_region_matrix(
    db: Session, since: datetime | None = None
) -> list[SignalRegionCell]:
    """Compute the signal-region FP rate cross-tabulation.

    Groups corridors into their parent ScoringRegions and aggregates
    signal FP rates at the region level.

    Args:
        db: Database session.
        since: If provided, only include alerts reviewed on or after this date.

    Returns:
        List of SignalRegionCell objects.
    """
    from app.config import settings

    min_verdicts = getattr(settings, "SIGNAL_MATRIX_MIN_VERDICTS", 5)

    # Load active regions and build corridor_id -> region mapping
    regions = (
        db.query(ScoringRegion)
        .filter(ScoringRegion.is_active.is_(True))
        .all()
    )

    corridor_to_region: dict[int, int] = {}
    region_names: dict[int, str] = {}
    for region in regions:
        region_names[region.region_id] = region.name
        raw = region.corridor_ids_json
        if raw is None:
            continue
        if isinstance(raw, str):
            try:
                cids = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
        elif isinstance(raw, list):
            cids = raw
        else:
            continue
        if not isinstance(cids, list):
            continue
        for cid in cids:
            if isinstance(cid, int):
                corridor_to_region[cid] = region.region_id

    if not corridor_to_region:
        return []

    alerts = _query_reviewed_alerts(db, since)
    if not alerts:
        return []

    # Build per-(signal, region) counts
    region_cell_counts: dict[tuple[str, int], dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0}
    )
    global_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0}
    )

    for alert in alerts:
        region_id = corridor_to_region.get(alert.corridor_id)
        if region_id is None:
            continue

        breakdown = _parse_breakdown(alert.risk_breakdown_json)
        is_fp = alert.is_false_positive
        verdict_key = "fp" if is_fp else "tp"

        for key, value in breakdown.items():
            if not _is_signal_key(key, value):
                continue
            region_cell_counts[(key, region_id)][verdict_key] += 1
            global_counts[key][verdict_key] += 1

    # Compute global FP rates
    global_fp_rates: dict[str, float] = {}
    for signal, counts in global_counts.items():
        total = counts["tp"] + counts["fp"]
        global_fp_rates[signal] = counts["fp"] / total if total > 0 else 0.0

    results: list[SignalRegionCell] = []
    for (signal_name, region_id), counts in region_cell_counts.items():
        total = counts["tp"] + counts["fp"]
        if total < min_verdicts:
            continue

        fp_rate = counts["fp"] / total
        global_rate = global_fp_rates.get(signal_name, 0.0)
        lift = fp_rate / global_rate if global_rate > 0 else 0.0

        results.append(
            SignalRegionCell(
                signal_name=signal_name,
                region_id=region_id,
                region_name=region_names.get(region_id, f"Region {region_id}"),
                tp_count=counts["tp"],
                fp_count=counts["fp"],
                total=total,
                fp_rate=round(fp_rate, 4),
                lift=round(lift, 4),
            )
        )

    return results


def identify_regional_suppressions(
    db: Session,
    fp_rate_threshold: float | None = None,
    min_verdicts: int | None = None,
) -> list[SuppressionCandidate]:
    """Identify signal-corridor pairs with unusually high FP rates.

    Args:
        db: Database session.
        fp_rate_threshold: FP rate above which to flag. Defaults to
            settings.SIGNAL_MATRIX_FP_SUPPRESSION_THRESHOLD (0.50).
        min_verdicts: Minimum verdict count for a cell to be considered.
            Defaults to settings.SIGNAL_MATRIX_MIN_VERDICTS (5).

    Returns:
        List of SuppressionCandidate objects for cells exceeding the threshold.
    """
    from app.config import settings

    if fp_rate_threshold is None:
        fp_rate_threshold = getattr(
            settings, "SIGNAL_MATRIX_FP_SUPPRESSION_THRESHOLD", 0.50
        )
    if min_verdicts is not None:
        # Temporarily override setting for the matrix computation
        original = getattr(settings, "SIGNAL_MATRIX_MIN_VERDICTS", 5)
        try:
            settings.SIGNAL_MATRIX_MIN_VERDICTS = min_verdicts
            matrix = compute_signal_corridor_matrix(db)
        finally:
            settings.SIGNAL_MATRIX_MIN_VERDICTS = original
    else:
        matrix = compute_signal_corridor_matrix(db)

    # Compute global FP rates per signal from the matrix cells
    signal_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0}
    )
    for cell in matrix:
        signal_totals[cell.signal_name]["tp"] += cell.tp_count
        signal_totals[cell.signal_name]["fp"] += cell.fp_count

    global_fp_rates: dict[str, float] = {}
    for signal, counts in signal_totals.items():
        total = counts["tp"] + counts["fp"]
        global_fp_rates[signal] = counts["fp"] / total if total > 0 else 0.0

    candidates: list[SuppressionCandidate] = []
    for cell in matrix:
        if cell.fp_rate <= fp_rate_threshold:
            continue

        global_rate = global_fp_rates.get(cell.signal_name, 0.0)
        suggested_action = _suggest_action(cell.fp_rate, global_rate)

        candidates.append(
            SuppressionCandidate(
                signal_name=cell.signal_name,
                corridor_id=cell.corridor_id,
                corridor_name=cell.corridor_name,
                fp_rate=cell.fp_rate,
                total=cell.total,
                global_fp_rate=round(global_rate, 4),
                suggested_action=suggested_action,
            )
        )

    return candidates


def _suggest_action(fp_rate: float, global_fp_rate: float) -> str:
    """Generate a human-readable suggestion based on FP rate severity."""
    if fp_rate >= 0.90:
        return "Consider suppressing signal in this corridor"
    if fp_rate >= 0.75:
        return "Reduce weight by 75%"
    if fp_rate >= 0.60:
        return "Reduce weight by 50%"
    return "Reduce weight by 25%"
