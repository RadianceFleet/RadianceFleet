"""Notification rules CRUD and test/log endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_senior_or_admin
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class NotificationRuleCreate(BaseModel):
    name: str
    is_active: bool = True
    min_score: int | None = None
    max_score: int | None = None
    corridor_ids_json: list[int] | None = None
    vessel_flags_json: list[str] | None = None
    alert_statuses_json: list[str] | None = None
    vessel_types_json: list[str] | None = None
    scoring_signals_json: list[str] | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    channel: str
    destination: str
    message_template: str | None = None
    throttle_minutes: int = 30


class NotificationRuleUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    min_score: int | None = None
    max_score: int | None = None
    corridor_ids_json: list[int] | None = None
    vessel_flags_json: list[str] | None = None
    alert_statuses_json: list[str] | None = None
    vessel_types_json: list[str] | None = None
    scoring_signals_json: list[str] | None = None
    time_window_start: str | None = None
    time_window_end: str | None = None
    channel: str | None = None
    destination: str | None = None
    message_template: str | None = None
    throttle_minutes: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule_to_dict(rule) -> dict:
    return {
        "rule_id": rule.rule_id,
        "name": rule.name,
        "is_active": rule.is_active,
        "created_by": rule.created_by,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
        "min_score": rule.min_score,
        "max_score": rule.max_score,
        "corridor_ids_json": rule.corridor_ids_json,
        "vessel_flags_json": rule.vessel_flags_json,
        "alert_statuses_json": rule.alert_statuses_json,
        "vessel_types_json": rule.vessel_types_json,
        "scoring_signals_json": rule.scoring_signals_json,
        "time_window_start": rule.time_window_start,
        "time_window_end": rule.time_window_end,
        "channel": rule.channel,
        "destination": rule.destination,
        "message_template": rule.message_template,
        "throttle_minutes": rule.throttle_minutes,
    }


def _log_to_dict(log) -> dict:
    return {
        "log_id": log.log_id,
        "rule_id": log.rule_id,
        "alert_id": log.alert_id,
        "channel": log.channel,
        "destination": log.destination,
        "status": log.status,
        "error_message": log.error_message,
        "sent_at": log.sent_at.isoformat() if log.sent_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/admin/notification-rules", tags=["admin"])
def list_notification_rules(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
    active_only: bool = Query(False),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    """List all notification rules."""
    from app.models.notification_rule import NotificationRule

    query = db.query(NotificationRule)
    if active_only:
        query = query.filter(NotificationRule.is_active == True)  # noqa: E712
    total = query.count()
    rules = query.order_by(NotificationRule.rule_id.desc()).offset(offset).limit(limit).all()
    return {"rules": [_rule_to_dict(r) for r in rules], "total": total}


@router.post("/admin/notification-rules", tags=["admin"], status_code=201)
def create_notification_rule(
    body: NotificationRuleCreate,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Create a new notification rule."""
    from app.config import settings
    from app.models.notification_rule import NotificationRule

    if not settings.NOTIFICATION_RULES_ENABLED:
        raise HTTPException(status_code=400, detail="Notification rules engine is disabled")

    if body.channel not in ("slack", "email", "webhook"):
        raise HTTPException(status_code=400, detail="Channel must be slack, email, or webhook")

    rule = NotificationRule(
        name=body.name,
        is_active=body.is_active,
        created_by=auth.get("analyst_id"),
        min_score=body.min_score,
        max_score=body.max_score,
        corridor_ids_json=body.corridor_ids_json,
        vessel_flags_json=body.vessel_flags_json,
        alert_statuses_json=body.alert_statuses_json,
        vessel_types_json=body.vessel_types_json,
        scoring_signals_json=body.scoring_signals_json,
        time_window_start=body.time_window_start,
        time_window_end=body.time_window_end,
        channel=body.channel,
        destination=body.destination,
        message_template=body.message_template,
        throttle_minutes=body.throttle_minutes,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _rule_to_dict(rule)


@router.get("/admin/notification-rules/{rule_id}", tags=["admin"])
def get_notification_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Get a single notification rule by ID."""
    from app.models.notification_rule import NotificationRule

    rule = db.query(NotificationRule).filter(NotificationRule.rule_id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Notification rule not found")
    return _rule_to_dict(rule)


@router.put("/admin/notification-rules/{rule_id}", tags=["admin"])
def update_notification_rule(
    rule_id: int,
    body: NotificationRuleUpdate,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Update a notification rule."""
    from app.models.notification_rule import NotificationRule

    rule = db.query(NotificationRule).filter(NotificationRule.rule_id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Notification rule not found")

    update_data = body.model_dump(exclude_unset=True)
    if "channel" in update_data and update_data["channel"] not in ("slack", "email", "webhook"):
        raise HTTPException(status_code=400, detail="Channel must be slack, email, or webhook")

    for key, value in update_data.items():
        setattr(rule, key, value)
    rule.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(rule)
    return _rule_to_dict(rule)


@router.delete("/admin/notification-rules/{rule_id}", tags=["admin"])
def delete_notification_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Delete a notification rule."""
    from app.models.notification_rule import NotificationRule

    rule = db.query(NotificationRule).filter(NotificationRule.rule_id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Notification rule not found")
    db.delete(rule)
    db.commit()
    return {"detail": "Notification rule deleted"}


@router.post("/admin/notification-rules/{rule_id}/test", tags=["admin"])
def test_notification_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Send a test notification for a rule using a synthetic alert."""
    from app.models.notification_rule import NotificationRule
    from app.modules.notification_rules_engine import dispatch_notification

    rule = db.query(NotificationRule).filter(NotificationRule.rule_id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Notification rule not found")

    # Create a synthetic alert-like object for testing
    class _TestAlert:
        gap_event_id = 0
        vessel_id = None
        risk_score = 75
        corridor_id = 1
        status = "new"
        duration_minutes = 360
        risk_breakdown_json = {}

    class _TestVessel:
        name = "TEST VESSEL"
        vessel_name = "TEST VESSEL"
        flag_state = "XX"
        vessel_type = "tanker"

    result = dispatch_notification(rule, _TestAlert(), _TestVessel())
    return {"test_result": result}


@router.get("/admin/notification-rules/{rule_id}/logs", tags=["admin"])
def get_notification_rule_logs(
    rule_id: int,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
):
    """Get delivery logs for a notification rule."""
    from app.models.notification_rule import NotificationRule
    from app.models.notification_rule_log import NotificationRuleLog

    rule = db.query(NotificationRule).filter(NotificationRule.rule_id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Notification rule not found")

    query = db.query(NotificationRuleLog).filter(NotificationRuleLog.rule_id == rule_id)
    total = query.count()
    logs = query.order_by(NotificationRuleLog.sent_at.desc()).offset(offset).limit(limit).all()
    return {"logs": [_log_to_dict(rule_log) for rule_log in logs], "total": total}
