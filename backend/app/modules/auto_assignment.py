"""Auto-assignment queue — batch-assign unassigned high-priority alerts.

Uses the smart workload balancer to find the best analyst for each alert,
processing in descending risk score order.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Statuses considered "terminal" — alerts in these states are not assignable
_TERMINAL_STATUSES = ("dismissed", "documented", "confirmed_fp")


def auto_assign_alert(db: Session, alert) -> int | None:
    """Auto-assign a single alert to the best available analyst.

    Uses the smart workload balancer's suggest_assignment to pick the analyst,
    then updates the alert's assigned_to and assigned_at fields.

    Args:
        db: SQLAlchemy session.
        alert: AISGapEvent model instance (must be unassigned).

    Returns:
        analyst_id if assigned, or None if no eligible analyst was found.
    """
    from app.modules.workload_balancer import suggest_assignment

    analyst_id = suggest_assignment(db, alert_id=alert.gap_event_id)
    if analyst_id is None:
        return None

    alert.assigned_to = analyst_id
    alert.assigned_at = datetime.now(UTC)
    return analyst_id


def process_assignment_queue(db: Session) -> list[dict]:
    """Process all unassigned alerts eligible for auto-assignment.

    Queries unassigned alerts with risk_score >= AUTO_ASSIGN_MIN_SCORE and
    non-terminal status, sorted by risk_score DESC.  For each, calls
    auto_assign_alert and collects results.

    Args:
        db: SQLAlchemy session.

    Returns:
        List of dicts with alert_id, analyst_id, risk_score for each assignment.
    """
    from app.config import settings
    from app.models.gap_event import AISGapEvent

    min_score = getattr(settings, "AUTO_ASSIGN_MIN_SCORE", 51)

    alerts = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.assigned_to.is_(None),
            AISGapEvent.risk_score >= min_score,
            AISGapEvent.status.notin_(list(_TERMINAL_STATUSES)),
        )
        .order_by(AISGapEvent.risk_score.desc())
        .all()
    )

    results: list[dict] = []
    for alert in alerts:
        analyst_id = auto_assign_alert(db, alert)
        if analyst_id is not None:
            results.append(
                {
                    "alert_id": alert.gap_event_id,
                    "analyst_id": analyst_id,
                    "risk_score": alert.risk_score,
                }
            )

    if results:
        db.commit()
        logger.info("Auto-assigned %d alerts", len(results))

    return results
