"""Smart workload balancer — shift-aware, specialization-aware assignment.

Provides weighted workload calculation, shift window checking, specialization
matching, and a composite scoring function for analyst assignment suggestions.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Statuses considered "terminal" — alerts in these states don't count as open
_TERMINAL_STATUSES = ("dismissed", "documented", "confirmed_fp")


def calculate_weighted_workload(db: Session, analyst_id: int) -> float:
    """Calculate utilization ratio for an analyst based on weighted open alerts.

    High-priority alerts (score >= 80) count 2x, medium (60-79) count 1.5x,
    low (< 60) count 1.0x.  Returns weighted_count / max_concurrent_alerts.

    Args:
        db: SQLAlchemy session.
        analyst_id: The analyst to evaluate.

    Returns:
        Utilization ratio (0.0 = idle, 1.0+ = at or over capacity).
    """
    from app.models.analyst import Analyst
    from app.models.gap_event import AISGapEvent

    analyst = db.query(Analyst).filter(Analyst.analyst_id == analyst_id).first()
    if analyst is None:
        return 1.0  # Unknown analyst — treat as fully loaded

    max_concurrent = getattr(analyst, "max_concurrent_alerts", None)
    if not isinstance(max_concurrent, int) or max_concurrent <= 0:
        max_concurrent = 10

    rows = (
        db.query(AISGapEvent.risk_score)
        .filter(
            AISGapEvent.assigned_to == analyst_id,
            AISGapEvent.status.notin_(list(_TERMINAL_STATUSES)),
        )
        .all()
    )

    weighted = 0.0
    for (score,) in rows:
        s = score or 0
        if s >= 80:
            weighted += 2.0
        elif s >= 60:
            weighted += 1.5
        else:
            weighted += 1.0

    return weighted / max_concurrent


def is_on_shift(analyst) -> bool:
    """Check whether an analyst is currently within their shift window (UTC).

    If both shift_start_hour and shift_end_hour are None, the analyst is
    considered always on shift.  Handles wraparound (e.g. start=22, end=6
    means 22:00-06:00 UTC).

    Args:
        analyst: Analyst model instance (or mock with shift_start_hour/shift_end_hour).

    Returns:
        True if the analyst is currently on shift.
    """
    start = getattr(analyst, "shift_start_hour", None)
    end = getattr(analyst, "shift_end_hour", None)

    if start is None and end is None:
        return True

    # If only one is set, treat as always-on (incomplete config)
    if start is None or end is None:
        return True

    now_hour = datetime.now(UTC).hour

    if start <= end:
        # Normal window: e.g. 9-17
        return start <= now_hour < end
    else:
        # Wraparound: e.g. 22-6 means 22,23,0,1,2,3,4,5
        return now_hour >= start or now_hour < end


def match_specialization(analyst, alert) -> float:
    """Score how well an analyst's specializations match an alert.

    Specializations are stored as a JSON list of corridor IDs (ints) or
    alert type strings in analyst.specializations_json.

    Args:
        analyst: Analyst model instance.
        alert: AISGapEvent model instance.

    Returns:
        1.0 for corridor match, 0.5 for type/status match, 0.0 for no match.
    """
    raw = getattr(analyst, "specializations_json", None)
    if not raw:
        return 0.0

    try:
        specs = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return 0.0

    if not isinstance(specs, list) or not specs:
        return 0.0

    # Check corridor match
    alert_corridor = getattr(alert, "corridor_id", None)
    if alert_corridor is not None:
        for s in specs:
            if isinstance(s, int) and s == alert_corridor:
                return 1.0
            if isinstance(s, str):
                try:
                    if int(s) == alert_corridor:
                        return 1.0
                except (ValueError, TypeError):
                    pass

    # Check type/status match (string specializations)
    alert_status = getattr(alert, "status", None)
    alert_source = getattr(alert, "source", None)
    for s in specs:
        if isinstance(s, str):
            if alert_status and s.lower() == str(alert_status).lower():
                return 0.5
            if alert_source and s.lower() == str(alert_source).lower():
                return 0.5

    return 0.0


def suggest_assignment(
    db: Session,
    alert_id: int | None = None,
    exclude_ids: list[int] | None = None,
) -> int | None:
    """Suggest the best analyst for assignment using composite scoring.

    Hard filters: inactive analysts, off-shift analysts, and excluded IDs.
    Composite score: (1-utilization)*0.5 + specialization*0.3 + fairness*0.2

    Fairness is based on time since last assignment — analysts who haven't
    been assigned anything recently get a boost.

    Args:
        db: SQLAlchemy session.
        alert_id: Optional alert ID for specialization matching.
        exclude_ids: Analyst IDs to skip.

    Returns:
        analyst_id of the best candidate, or None if no one is eligible.
    """
    from app.models.analyst import Analyst
    from app.models.gap_event import AISGapEvent

    exclude = set(exclude_ids or [])

    # Fetch all active analysts
    analysts = db.query(Analyst).filter(Analyst.is_active == True).all()  # noqa: E712
    if not analysts:
        return None

    # Hard filters: active, on-shift, not excluded
    candidates = []
    for a in analysts:
        if a.analyst_id in exclude:
            continue
        if not is_on_shift(a):
            continue
        candidates.append(a)

    if not candidates:
        return None

    candidate_ids = [a.analyst_id for a in candidates]

    # Fetch alert for specialization matching
    alert = None
    if alert_id is not None:
        alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()

    # Batch query: get all open alerts for candidates to compute workload
    open_alerts = (
        db.query(AISGapEvent.assigned_to, AISGapEvent.risk_score)
        .filter(
            AISGapEvent.assigned_to.in_(candidate_ids),
            AISGapEvent.status.notin_(list(_TERMINAL_STATUSES)),
        )
        .all()
    )

    # Build per-analyst weighted counts
    weighted_counts: dict[int, float] = {aid: 0.0 for aid in candidate_ids}
    for assigned_to, score in open_alerts:
        s = score or 0
        if s >= 80:
            weighted_counts[assigned_to] = weighted_counts.get(assigned_to, 0.0) + 2.0
        elif s >= 60:
            weighted_counts[assigned_to] = weighted_counts.get(assigned_to, 0.0) + 1.5
        else:
            weighted_counts[assigned_to] = weighted_counts.get(assigned_to, 0.0) + 1.0

    # Build per-analyst max_concurrent map
    max_map: dict[int, int] = {}
    for a in candidates:
        mc = getattr(a, "max_concurrent_alerts", None)
        if not isinstance(mc, int) or mc <= 0:
            mc = 10
        max_map[a.analyst_id] = mc

    # Fairness: time since last assignment per candidate
    last_assigned_rows = (
        db.query(
            AISGapEvent.assigned_to,
            func.max(AISGapEvent.assigned_at).label("last_assigned"),
        )
        .filter(AISGapEvent.assigned_to.in_(candidate_ids))
        .group_by(AISGapEvent.assigned_to)
        .all()
    )
    last_assigned: dict[int, datetime | None] = {aid: None for aid in candidate_ids}
    for row in last_assigned_rows:
        last_assigned[row[0]] = row[1]

    # Normalize fairness: larger time since last assignment = higher fairness score
    now = datetime.now(UTC)
    fairness_hours: dict[int, float] = {}
    for aid in candidate_ids:
        la = last_assigned.get(aid)
        if la is None:
            fairness_hours[aid] = 999999.0  # Never assigned — maximum fairness
        else:
            # Handle naive datetimes from SQLite
            if la.tzinfo is None:
                delta = (now.replace(tzinfo=None) - la).total_seconds() / 3600
            else:
                delta = (now - la).total_seconds() / 3600
            fairness_hours[aid] = max(delta, 0.0)

    max_fairness = max(fairness_hours.values()) if fairness_hours else 1.0
    if max_fairness == 0:
        max_fairness = 1.0

    # Score each candidate
    best_id = None
    best_score = -1.0

    for a in candidates:
        aid = a.analyst_id

        # Utilization component (lower is better)
        utilization = weighted_counts[aid] / max_map[aid]
        util_score = max(1.0 - utilization, 0.0)

        # Specialization component
        spec_score = match_specialization(a, alert) if alert else 0.0

        # Fairness component (normalized)
        fair_score = fairness_hours[aid] / max_fairness

        composite = util_score * 0.5 + spec_score * 0.3 + fair_score * 0.2

        if composite > best_score:
            best_score = composite
            best_id = aid

    return best_id
