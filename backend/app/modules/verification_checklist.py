"""Evidence verification checklist — ensures analysts complete review before verdicts."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

CHECKLIST_TEMPLATES: dict[str, list[dict[str, str]]] = {
    "standard": [
        {"key": "check_ais_gap_duration", "label": "Verify AIS gap duration is above threshold"},
        {"key": "check_vessel_history", "label": "Review vessel history for prior incidents"},
        {"key": "check_coverage_quality", "label": "Assess AIS coverage quality in the area"},
        {"key": "check_nearby_vessels", "label": "Check for nearby vessels during gap period"},
        {"key": "check_risk_score_components", "label": "Review all risk score components"},
    ],
    "high_risk": [
        {"key": "check_ais_gap_duration", "label": "Verify AIS gap duration is above threshold"},
        {"key": "check_vessel_history", "label": "Review vessel history for prior incidents"},
        {"key": "check_coverage_quality", "label": "Assess AIS coverage quality in the area"},
        {"key": "check_nearby_vessels", "label": "Check for nearby vessels during gap period"},
        {"key": "check_risk_score_components", "label": "Review all risk score components"},
        {"key": "check_satellite_imagery", "label": "Request and review satellite imagery"},
        {"key": "check_ownership_chain", "label": "Verify ownership chain and beneficial owners"},
        {"key": "check_sanctions_status", "label": "Check sanctions screening results"},
        {"key": "check_previous_alerts", "label": "Review previous alerts for this vessel"},
    ],
    "sts_zone": [
        {"key": "check_ais_gap_duration", "label": "Verify AIS gap duration is above threshold"},
        {"key": "check_vessel_history", "label": "Review vessel history for prior incidents"},
        {"key": "check_coverage_quality", "label": "Assess AIS coverage quality in the area"},
        {"key": "check_nearby_vessels", "label": "Check for nearby vessels during gap period"},
        {"key": "check_risk_score_components", "label": "Review all risk score components"},
        {"key": "check_sts_partner_vessel", "label": "Identify and verify STS partner vessel"},
        {"key": "check_transfer_evidence", "label": "Review ship-to-ship transfer evidence"},
        {"key": "check_dark_fleet_indicators", "label": "Check dark fleet indicators for both vessels"},
    ],
}


def get_checklist_template(alert) -> str:
    """Select checklist template based on alert risk tier and corridor type."""
    if hasattr(alert, "risk_score") and alert.risk_score is not None and alert.risk_score >= 70:
        return "high_risk"
    if hasattr(alert, "corridor") and alert.corridor is not None:
        corridor = alert.corridor
        corridor_type = getattr(corridor, "corridor_type", None)
        if corridor_type is not None:
            ct = corridor_type.value if hasattr(corridor_type, "value") else str(corridor_type)
            if ct == "sts_zone":
                return "sts_zone"
    return "standard"


def create_checklist_for_alert(
    db: Session,
    alert_id: int,
    template: str,
    analyst_id: int,
) -> dict:
    """Create a verification checklist and populate items from the template."""
    from app.models.verification_checklist import VerificationChecklist
    from app.models.verification_checklist_item import VerificationChecklistItem

    if template not in CHECKLIST_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown template '{template}'. Must be one of: {sorted(CHECKLIST_TEMPLATES)}",
        )

    # Check for existing checklist with same template
    existing = (
        db.query(VerificationChecklist)
        .filter(
            VerificationChecklist.alert_id == alert_id,
            VerificationChecklist.checklist_template == template,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Checklist with template '{template}' already exists for alert {alert_id}",
        )

    checklist = VerificationChecklist(
        alert_id=alert_id,
        checklist_template=template,
        created_by=analyst_id,
        created_at=datetime.now(UTC),
    )
    db.add(checklist)
    db.flush()

    items_data = CHECKLIST_TEMPLATES[template]
    items = []
    for i, item_def in enumerate(items_data):
        item = VerificationChecklistItem(
            checklist_id=checklist.checklist_id,
            item_key=item_def["key"],
            label=item_def["label"],
            is_checked=False,
            sort_order=i,
        )
        db.add(item)
        items.append(item)

    db.flush()

    return {
        "checklist_id": checklist.checklist_id,
        "alert_id": alert_id,
        "checklist_template": template,
        "created_by": analyst_id,
        "created_at": checklist.created_at.isoformat(),
        "completed_at": None,
        "completed_by": None,
        "items": [
            {
                "item_id": item.item_id,
                "item_key": item.item_key,
                "label": item.label,
                "is_checked": False,
                "checked_by": None,
                "checked_at": None,
                "notes": None,
                "sort_order": item.sort_order,
            }
            for item in items
        ],
    }


def check_item(db: Session, item_id: int, analyst_id: int, notes: str | None = None) -> dict:
    """Mark a checklist item as checked."""
    from app.models.verification_checklist_item import VerificationChecklistItem

    item = (
        db.query(VerificationChecklistItem)
        .filter(VerificationChecklistItem.item_id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    item.is_checked = True
    item.checked_by = analyst_id
    item.checked_at = datetime.now(UTC)
    if notes is not None:
        item.notes = notes
    db.flush()

    # Check if all items in the checklist are now complete
    _maybe_mark_complete(db, item.checklist_id, analyst_id)

    return _serialize_item(item)


def uncheck_item(db: Session, item_id: int) -> dict:
    """Uncheck a checklist item."""
    from app.models.verification_checklist_item import VerificationChecklistItem

    item = (
        db.query(VerificationChecklistItem)
        .filter(VerificationChecklistItem.item_id == item_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Checklist item not found")

    item.is_checked = False
    item.checked_by = None
    item.checked_at = None
    db.flush()

    # Un-complete the checklist if it was marked complete
    from app.models.verification_checklist import VerificationChecklist

    checklist = (
        db.query(VerificationChecklist)
        .filter(VerificationChecklist.checklist_id == item.checklist_id)
        .first()
    )
    if checklist and checklist.completed_at is not None:
        checklist.completed_at = None
        checklist.completed_by = None
        db.flush()

    return _serialize_item(item)


def is_checklist_complete(db: Session, alert_id: int) -> bool:
    """Check whether all checklist items for the alert are checked."""
    from app.models.verification_checklist import VerificationChecklist
    from app.models.verification_checklist_item import VerificationChecklistItem

    checklists = (
        db.query(VerificationChecklist)
        .filter(VerificationChecklist.alert_id == alert_id)
        .all()
    )
    if not checklists:
        return True  # No checklist created yet — don't block verdicts

    for checklist in checklists:
        items = (
            db.query(VerificationChecklistItem)
            .filter(VerificationChecklistItem.checklist_id == checklist.checklist_id)
            .all()
        )
        if not items:
            return False
        if not all(item.is_checked for item in items):
            return False

    return True


def enforce_checklist_before_verdict(db: Session, alert_id: int) -> None:
    """Raise HTTPException 400 if the checklist is incomplete."""
    if not is_checklist_complete(db, alert_id):
        raise HTTPException(
            status_code=400,
            detail="Verification checklist must be completed before submitting a verdict",
        )


def get_checklist_for_alert(db: Session, alert_id: int) -> dict | None:
    """Return the checklist and its items for an alert, or None if no checklist exists."""
    from app.models.verification_checklist import VerificationChecklist
    from app.models.verification_checklist_item import VerificationChecklistItem

    checklist = (
        db.query(VerificationChecklist)
        .filter(VerificationChecklist.alert_id == alert_id)
        .first()
    )
    if not checklist:
        return None

    items = (
        db.query(VerificationChecklistItem)
        .filter(VerificationChecklistItem.checklist_id == checklist.checklist_id)
        .order_by(VerificationChecklistItem.sort_order)
        .all()
    )

    return {
        "checklist_id": checklist.checklist_id,
        "alert_id": checklist.alert_id,
        "checklist_template": checklist.checklist_template,
        "created_by": checklist.created_by,
        "created_at": checklist.created_at.isoformat() if checklist.created_at else None,
        "completed_at": checklist.completed_at.isoformat() if checklist.completed_at else None,
        "completed_by": checklist.completed_by,
        "items": [_serialize_item(item) for item in items],
    }


def _maybe_mark_complete(db: Session, checklist_id: int, analyst_id: int) -> None:
    """Mark checklist as complete if all items are checked."""
    from app.models.verification_checklist import VerificationChecklist
    from app.models.verification_checklist_item import VerificationChecklistItem

    items = (
        db.query(VerificationChecklistItem)
        .filter(VerificationChecklistItem.checklist_id == checklist_id)
        .all()
    )
    if items and all(item.is_checked for item in items):
        checklist = (
            db.query(VerificationChecklist)
            .filter(VerificationChecklist.checklist_id == checklist_id)
            .first()
        )
        if checklist:
            checklist.completed_at = datetime.now(UTC)
            checklist.completed_by = analyst_id
            db.flush()


def _serialize_item(item) -> dict:
    return {
        "item_id": item.item_id,
        "item_key": item.item_key,
        "label": item.label,
        "is_checked": item.is_checked,
        "checked_by": item.checked_by,
        "checked_at": item.checked_at.isoformat() if item.checked_at else None,
        "notes": item.notes,
        "sort_order": item.sort_order,
    }
