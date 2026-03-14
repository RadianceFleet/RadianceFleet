"""Collaboration notification system — SQLite-backed event queue."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.notification_event import NotificationEvent

logger = logging.getLogger(__name__)


def emit_event(
    db: Session,
    target_analyst_id: int,
    event_type: str,
    payload: dict | None = None,
) -> None:
    """Emit a notification event to a specific analyst."""
    event = NotificationEvent(
        target_analyst_id=target_analyst_id,
        event_type=event_type,
        payload_json=json.dumps(payload) if payload else None,
    )
    db.add(event)
    # Don't commit here — let the caller's transaction handle it


def get_pending_events(
    db: Session,
    analyst_id: int,
    since: datetime | None = None,
    limit: int = 50,
) -> list[dict]:
    """Get pending notification events for an analyst."""
    q = db.query(NotificationEvent).filter(
        NotificationEvent.target_analyst_id == analyst_id
    )
    if since:
        q = q.filter(NotificationEvent.created_at > since)
    events = q.order_by(NotificationEvent.created_at.desc()).limit(limit).all()
    return [
        {
            "event_id": e.event_id,
            "event_type": e.event_type,
            "payload": json.loads(e.payload_json) if e.payload_json else None,
            "is_read": e.is_read,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


def mark_read(db: Session, event_id: int, analyst_id: int) -> bool:
    """Mark a notification as read. Returns True if found and updated."""
    event = (
        db.query(NotificationEvent)
        .filter(
            NotificationEvent.event_id == event_id,
            NotificationEvent.target_analyst_id == analyst_id,
        )
        .first()
    )
    if event:
        event.is_read = True
        return True
    return False


def mark_all_read(db: Session, analyst_id: int) -> int:
    """Mark all notifications as read for an analyst. Returns count updated."""
    count = (
        db.query(NotificationEvent)
        .filter(
            NotificationEvent.target_analyst_id == analyst_id,
            NotificationEvent.is_read.is_(False),
        )
        .update({"is_read": True})
    )
    return count


def cleanup_old_events(db: Session, max_age_hours: int = 24) -> int:
    """Delete notification events older than max_age_hours."""
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    count = (
        db.query(NotificationEvent)
        .filter(NotificationEvent.created_at < cutoff)
        .delete()
    )
    db.commit()
    return count


# ---------------------------------------------------------------------------
# Convenience emitters
# ---------------------------------------------------------------------------


def emit_assignment(
    db: Session,
    analyst_id: int,
    alert_id: int,
    assigned_by: str | None = None,
) -> None:
    """Emit an assignment notification."""
    emit_event(
        db,
        analyst_id,
        "assignment",
        {"alert_id": alert_id, "assigned_by": assigned_by},
    )


def emit_handoff(
    db: Session,
    to_analyst_id: int,
    alert_id: int,
    from_analyst: str,
    notes: str = "",
) -> None:
    """Emit a handoff notification."""
    emit_event(
        db,
        to_analyst_id,
        "handoff",
        {"alert_id": alert_id, "from_analyst": from_analyst, "notes": notes},
    )


def emit_case_update(
    db: Session,
    analyst_id: int,
    case_id: int,
    action: str,
) -> None:
    """Emit a case update notification."""
    emit_event(
        db,
        analyst_id,
        "case_update",
        {"case_id": case_id, "action": action},
    )
