"""In-memory analyst presence tracker for collaboration features.

Tracks which analysts are currently online and what alert they are viewing.
Uses a simple heartbeat/TTL approach. This is in-memory only, so it works
correctly with a single application worker. A startup warning is logged when
multiple workers are detected (e.g. uvicorn --workers > 1).

Limitation: with multiple workers, each worker has its own presence dict,
so presence data will be inconsistent across workers. For production
multi-worker deployments, replace this with a Redis-backed implementation.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# TTL in seconds — entries older than this are considered offline
PRESENCE_TTL_SECONDS = 30


@dataclass
class PresenceEntry:
    """A single analyst's presence state."""

    analyst_id: int
    last_seen: float = field(default_factory=time.time)
    current_alert_id: int | None = None


# Module-level presence store: analyst_id -> PresenceEntry
_presence: dict[int, PresenceEntry] = {}

_startup_warned = False


def _warn_if_multiworker() -> None:
    """Log a warning once if running with multiple workers."""
    global _startup_warned
    if _startup_warned:
        return
    _startup_warned = True
    # Check common multi-worker indicators
    worker_count = os.environ.get("WEB_CONCURRENCY")
    if worker_count and int(worker_count) > 1:
        logger.warning(
            "analyst_presence: in-memory presence tracker detected WEB_CONCURRENCY=%s. "
            "Presence data will be inconsistent across workers. "
            "Consider a Redis-backed implementation for multi-worker deployments.",
            worker_count,
        )


def heartbeat(analyst_id: int, alert_id: int | None = None) -> None:
    """Update an analyst's presence. Called periodically by clients.

    Args:
        analyst_id: The analyst sending the heartbeat.
        alert_id: Optional ID of the alert they are currently viewing.
    """
    _warn_if_multiworker()
    _presence[analyst_id] = PresenceEntry(
        analyst_id=analyst_id,
        last_seen=time.time(),
        current_alert_id=alert_id,
    )


def _is_online(entry: PresenceEntry) -> bool:
    """Check if a presence entry is still within the TTL window."""
    return (time.time() - entry.last_seen) < PRESENCE_TTL_SECONDS


def get_online_analysts() -> list[dict]:
    """Return all analysts whose last heartbeat is within the TTL.

    Returns:
        List of dicts with analyst_id, current_alert_id, last_seen.
    """
    _cleanup_stale()
    result = []
    for entry in _presence.values():
        if _is_online(entry):
            result.append(
                {
                    "analyst_id": entry.analyst_id,
                    "current_alert_id": entry.current_alert_id,
                    "last_seen": entry.last_seen,
                }
            )
    return result


def get_alert_viewers(alert_id: int) -> list[int]:
    """Return analyst IDs currently viewing a specific alert.

    Args:
        alert_id: The alert to check.

    Returns:
        List of analyst_id values for analysts viewing this alert.
    """
    _cleanup_stale()
    return [
        entry.analyst_id
        for entry in _presence.values()
        if _is_online(entry) and entry.current_alert_id == alert_id
    ]


def suggest_assignment(
    db: Session,
    alert_id: int | None = None,
    exclude_ids: list[int] | None = None,
) -> int | None:
    """Suggest an analyst for assignment based on workload balancing.

    Finds the active analyst with the fewest open (non-dismissed/non-documented)
    alerts currently assigned to them.  When WORKLOAD_PRIORITY_WEIGHTING_ENABLED
    is True, delegates to the smart workload balancer which considers shift
    windows, specializations, and fairness.

    Args:
        db: SQLAlchemy session.
        alert_id: Optional alert ID for specialization matching.
        exclude_ids: Analyst IDs to exclude from consideration.

    Returns:
        analyst_id of the suggested analyst, or None if no analysts available.
    """
    from app.config import settings

    if getattr(settings, "WORKLOAD_PRIORITY_WEIGHTING_ENABLED", False):
        from app.modules.workload_balancer import suggest_assignment as smart_suggest

        return smart_suggest(db, alert_id=alert_id, exclude_ids=exclude_ids)

    from app.models.analyst import Analyst
    from app.models.gap_event import AISGapEvent

    exclude = set(exclude_ids or [])

    analysts = db.query(Analyst).filter(Analyst.is_active == True).all()  # noqa: E712
    if not analysts:
        return None

    candidates = [a for a in analysts if a.analyst_id not in exclude]
    if not candidates:
        return None

    # Count open alerts per analyst
    best_analyst_id = None
    best_count = float("inf")

    for analyst in candidates:
        count = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.assigned_to == analyst.analyst_id,
                AISGapEvent.status.notin_(["dismissed", "documented", "confirmed_fp"]),
            )
            .count()
        )
        if count < best_count:
            best_count = count
            best_analyst_id = analyst.analyst_id

    return best_analyst_id


def get_presence_snapshot() -> list[dict]:
    """Return full presence snapshot for SSE streaming.

    Returns all known entries with their online/offline status.
    """
    _cleanup_stale()
    result = []
    for entry in _presence.values():
        result.append(
            {
                "analyst_id": entry.analyst_id,
                "current_alert_id": entry.current_alert_id,
                "last_seen": entry.last_seen,
                "is_online": _is_online(entry),
            }
        )
    return result


def _cleanup_stale() -> None:
    """Remove entries that are more than 5x TTL old (garbage collection)."""
    cutoff = time.time() - (PRESENCE_TTL_SECONDS * 5)
    stale_ids = [aid for aid, entry in _presence.items() if entry.last_seen < cutoff]
    for aid in stale_ids:
        del _presence[aid]


def reset() -> None:
    """Clear all presence data. Used by tests."""
    _presence.clear()
