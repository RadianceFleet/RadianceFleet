"""Alert deduplication engine — group related alerts by vessel/time/corridor."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.models.alert_group import AlertGroup

logger = logging.getLogger(__name__)

# Epoch for time bucket computation (Unix epoch)
_EPOCH = datetime(1970, 1, 1)


def compute_group_key(alert, config=None) -> str:
    """Compute a deterministic group key for an alert.

    Hash of: vessel_id + corridor_id + time_bucket (N-day windows since epoch).
    """
    time_window_days = settings.ALERT_DEDUP_TIME_WINDOW_DAYS
    if config and "time_window_days" in config:
        time_window_days = config["time_window_days"]

    gap_start = alert.gap_start_utc
    if gap_start.tzinfo is not None:
        # Strip timezone for consistent bucket computation
        gap_start = gap_start.replace(tzinfo=None)

    delta = gap_start - _EPOCH
    time_bucket = int(delta.days // time_window_days)

    corridor_id = getattr(alert, "corridor_id", None)
    raw = f"{alert.vessel_id}:{corridor_id}:{time_bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def assign_to_group(db: Session, alert) -> AlertGroup:
    """Assign an alert to an existing or new dedup group.

    1. Compute group key
    2. Find existing group with that key, or create new
    3. Update alert's alert_group_id
    4. Update group stats: alert_count, first/last_seen, max_risk_score
    5. If alert.risk_score > group.max_risk_score, update primary_alert_id
    """
    from app.models.alert_group import AlertGroup

    group_key = compute_group_key(alert)

    group = db.query(AlertGroup).filter(AlertGroup.group_key == group_key).first()

    gap_start = alert.gap_start_utc
    risk_score = alert.risk_score or 0

    if group is None:
        group = AlertGroup(
            vessel_id=alert.vessel_id,
            corridor_id=getattr(alert, "corridor_id", None),
            group_key=group_key,
            primary_alert_id=alert.gap_event_id,
            alert_count=1,
            first_seen_utc=gap_start,
            last_seen_utc=gap_start,
            max_risk_score=risk_score,
            status="active",
        )
        db.add(group)
        db.flush()
    else:
        group.alert_count += 1
        if gap_start < group.first_seen_utc:
            group.first_seen_utc = gap_start
        if gap_start > group.last_seen_utc:
            group.last_seen_utc = gap_start
        if risk_score > group.max_risk_score:
            group.max_risk_score = risk_score
            group.primary_alert_id = alert.gap_event_id

    # Update alert's group reference via raw SQL to avoid model import issues
    from sqlalchemy import text

    db.execute(
        text("UPDATE ais_gap_events SET alert_group_id = :gid WHERE gap_event_id = :aid"),
        {"gid": group.group_id, "aid": alert.gap_event_id},
    )

    return group


def run_dedup_pass(db: Session) -> dict:
    """Batch process all ungrouped alerts (where alert_group_id IS NULL).

    Returns stats: {groups_created, alerts_grouped, existing_groups_updated}.
    """
    from sqlalchemy import text

    from app.models.alert_group import AlertGroup
    from app.models.gap_event import AISGapEvent

    if not settings.ALERT_DEDUP_ENABLED:
        return {"groups_created": 0, "alerts_grouped": 0, "existing_groups_updated": 0}

    # Find ungrouped alerts using raw SQL to avoid column not existing on model
    ungrouped_rows = db.execute(
        text(
            "SELECT gap_event_id FROM ais_gap_events "
            "WHERE alert_group_id IS NULL "
            "ORDER BY gap_start_utc ASC"
        )
    ).fetchall()

    ungrouped_ids = [row[0] for row in ungrouped_rows]

    if not ungrouped_ids:
        return {"groups_created": 0, "alerts_grouped": 0, "existing_groups_updated": 0}

    # Load actual alert objects for processing
    alerts = (
        db.query(AISGapEvent).filter(AISGapEvent.gap_event_id.in_(ungrouped_ids)).all()
    )

    groups_created = 0
    alerts_grouped = 0
    existing_groups_updated = 0

    for alert in alerts:
        group_key = compute_group_key(alert)
        existing = db.query(AlertGroup).filter(AlertGroup.group_key == group_key).first()
        was_new = existing is None
        assign_to_group(db, alert)
        alerts_grouped += 1
        if was_new:
            groups_created += 1
        else:
            existing_groups_updated += 1

    db.commit()

    return {
        "groups_created": groups_created,
        "alerts_grouped": alerts_grouped,
        "existing_groups_updated": existing_groups_updated,
    }


def merge_groups(db: Session, group_ids: list[int]) -> AlertGroup:
    """Merge multiple groups into one (the first in the list).

    The surviving group absorbs all member alerts from the dissolved groups.
    """
    from sqlalchemy import text

    from app.models.alert_group import AlertGroup

    if len(group_ids) < 2:
        raise ValueError("Need at least 2 groups to merge")

    groups = db.query(AlertGroup).filter(AlertGroup.group_id.in_(group_ids)).all()
    if len(groups) != len(group_ids):
        raise ValueError("One or more group IDs not found")

    # Surviving group is the one with the lowest group_id
    groups.sort(key=lambda g: g.group_id)
    survivor = groups[0]
    dissolved = groups[1:]

    for g in dissolved:
        # Move member alerts to survivor
        db.execute(
            text(
                "UPDATE ais_gap_events SET alert_group_id = :survivor_id "
                "WHERE alert_group_id = :old_id"
            ),
            {"survivor_id": survivor.group_id, "old_id": g.group_id},
        )
        db.delete(g)

    # Recalculate survivor stats
    _recalculate_group_stats(db, survivor)
    db.commit()

    return survivor


def dissolve_group(db: Session, group_id: int) -> None:
    """Remove group, set member alert_group_id to NULL."""
    from sqlalchemy import text

    from app.models.alert_group import AlertGroup

    group = db.query(AlertGroup).filter(AlertGroup.group_id == group_id).first()
    if group is None:
        raise ValueError(f"Group {group_id} not found")

    db.execute(
        text("UPDATE ais_gap_events SET alert_group_id = NULL WHERE alert_group_id = :gid"),
        {"gid": group_id},
    )
    db.delete(group)
    db.commit()


def update_group_max_score(db: Session, group_id: int) -> None:
    """Recalculate max_risk_score from member alerts.

    This is the integration point for Task 41 (incremental scoring).
    """
    from app.models.alert_group import AlertGroup

    group = db.query(AlertGroup).filter(AlertGroup.group_id == group_id).first()
    if group is None:
        return
    _recalculate_group_stats(db, group)
    db.commit()


def _recalculate_group_stats(db: Session, group: AlertGroup) -> None:
    """Recalculate group statistics from its member alerts."""
    from sqlalchemy import text

    rows = db.execute(
        text(
            "SELECT gap_event_id, gap_start_utc, risk_score "
            "FROM ais_gap_events WHERE alert_group_id = :gid"
        ),
        {"gid": group.group_id},
    ).fetchall()

    if not rows:
        group.alert_count = 0
        group.max_risk_score = 0
        group.primary_alert_id = None
        return

    group.alert_count = len(rows)
    max_score = 0
    best_alert_id = None
    min_seen = None
    max_seen = None

    for row in rows:
        alert_id, gap_start, score = row[0], row[1], row[2] or 0
        if score > max_score:
            max_score = score
            best_alert_id = alert_id
        # Handle string timestamps from SQLite
        if isinstance(gap_start, str):
            gap_start = datetime.fromisoformat(gap_start)
        if min_seen is None or gap_start < min_seen:
            min_seen = gap_start
        if max_seen is None or gap_start > max_seen:
            max_seen = gap_start

    group.max_risk_score = max_score
    group.primary_alert_id = best_alert_id
    if min_seen is not None:
        group.first_seen_utc = min_seen
    if max_seen is not None:
        group.last_seen_utc = max_seen
