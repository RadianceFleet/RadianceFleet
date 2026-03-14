"""Investigation case endpoints — create, manage, and suggest case groupings."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_auth, require_senior_or_admin
from app.database import get_db
from app.schemas.cases import (
    CaseAlertAdd,
    CaseAlertResponse,
    CaseAssign,
    CaseCreate,
    CaseResponse,
    CaseSuggestion,
    CaseSuggestRequest,
    CaseUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cases"])

CASE_ALERT_CAP = 30


def _case_to_response(case, alert_count: int, assigned_username: str | None) -> dict:
    """Convert a case ORM object to a response dict."""
    tags: list[str] = []
    if case.tags_json:
        try:
            tags = json.loads(case.tags_json)
        except (json.JSONDecodeError, TypeError):
            tags = []
    return {
        "case_id": case.case_id,
        "title": case.title,
        "description": case.description,
        "status": case.status,
        "priority": case.priority,
        "assigned_to": case.assigned_to,
        "assigned_to_username": assigned_username,
        "created_by": case.created_by,
        "vessel_id": case.vessel_id,
        "corridor_id": case.corridor_id,
        "tags": tags,
        "alert_count": alert_count,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


@router.post("/cases", response_model=CaseResponse, status_code=201)
def create_case(
    body: CaseCreate,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Create a new investigation case."""
    from app.models.investigation_case import InvestigationCase

    tags_json = json.dumps(body.tags) if body.tags else None
    case = InvestigationCase(
        title=body.title,
        description=body.description,
        status="open",
        priority=body.priority,
        created_by=auth["analyst_id"],
        vessel_id=body.vessel_id,
        corridor_id=body.corridor_id,
        tags_json=tags_json,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    return _case_to_response(case, 0, None)


@router.get("/cases", response_model=list[CaseResponse])
def list_cases(
    status: str | None = Query(None),
    priority: str | None = Query(None),
    assigned_to: int | None = Query(None),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """List investigation cases with optional filters."""
    from app.models.analyst import Analyst
    from app.models.case_alert import CaseAlert
    from app.models.investigation_case import InvestigationCase

    q = db.query(InvestigationCase)
    if status:
        q = q.filter(InvestigationCase.status == status)
    if priority:
        q = q.filter(InvestigationCase.priority == priority)
    if assigned_to is not None:
        q = q.filter(InvestigationCase.assigned_to == assigned_to)

    cases = q.order_by(InvestigationCase.updated_at.desc()).all()
    result = []
    for case in cases:
        alert_count = (
            db.query(CaseAlert).filter(CaseAlert.case_id == case.case_id).count()
        )
        assigned_username = None
        if case.assigned_to:
            analyst = (
                db.query(Analyst)
                .filter(Analyst.analyst_id == case.assigned_to)
                .first()
            )
            if analyst:
                assigned_username = analyst.username
        result.append(_case_to_response(case, alert_count, assigned_username))
    return result


@router.get("/cases/{case_id}", response_model=CaseResponse)
def get_case(
    case_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Get a single investigation case with alert count."""
    from app.models.analyst import Analyst
    from app.models.case_alert import CaseAlert
    from app.models.investigation_case import InvestigationCase

    case = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    alert_count = (
        db.query(CaseAlert).filter(CaseAlert.case_id == case_id).count()
    )
    assigned_username = None
    if case.assigned_to:
        analyst = (
            db.query(Analyst)
            .filter(Analyst.analyst_id == case.assigned_to)
            .first()
        )
        if analyst:
            assigned_username = analyst.username
    return _case_to_response(case, alert_count, assigned_username)


@router.patch("/cases/{case_id}", response_model=CaseResponse)
def update_case(
    case_id: int,
    body: CaseUpdate,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Update investigation case fields."""
    from app.models.analyst import Analyst
    from app.models.case_alert import CaseAlert
    from app.models.investigation_case import InvestigationCase

    case = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    if body.title is not None:
        case.title = body.title
    if body.description is not None:
        case.description = body.description
    if body.status is not None:
        case.status = body.status
    if body.priority is not None:
        case.priority = body.priority
    if body.assigned_to is not None:
        case.assigned_to = body.assigned_to
    if body.tags is not None:
        case.tags_json = json.dumps(body.tags)

    db.commit()
    db.refresh(case)

    alert_count = (
        db.query(CaseAlert).filter(CaseAlert.case_id == case_id).count()
    )
    assigned_username = None
    if case.assigned_to:
        analyst = (
            db.query(Analyst)
            .filter(Analyst.analyst_id == case.assigned_to)
            .first()
        )
        if analyst:
            assigned_username = analyst.username
    return _case_to_response(case, alert_count, assigned_username)


@router.post("/cases/{case_id}/alerts", response_model=CaseAlertResponse, status_code=201)
def add_alert_to_case(
    case_id: int,
    body: CaseAlertAdd,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Add an alert to an investigation case. Enforces 30-alert cap."""
    from app.models.case_alert import CaseAlert
    from app.models.gap_event import AISGapEvent
    from app.models.investigation_case import InvestigationCase

    case = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    alert = (
        db.query(AISGapEvent)
        .filter(AISGapEvent.gap_event_id == body.alert_id)
        .first()
    )
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Check cap
    current_count = (
        db.query(CaseAlert).filter(CaseAlert.case_id == case_id).count()
    )
    if current_count >= CASE_ALERT_CAP:
        raise HTTPException(
            status_code=400,
            detail=f"Case alert cap of {CASE_ALERT_CAP} reached",
        )

    # Check duplicate
    existing = (
        db.query(CaseAlert)
        .filter(CaseAlert.case_id == case_id, CaseAlert.alert_id == body.alert_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Alert already linked to this case")

    link = CaseAlert(
        case_id=case_id,
        alert_id=body.alert_id,
        added_by=auth["analyst_id"],
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.delete("/cases/{case_id}/alerts/{alert_id}", status_code=204)
def remove_alert_from_case(
    case_id: int,
    alert_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Remove an alert from an investigation case."""
    from app.models.case_alert import CaseAlert

    link = (
        db.query(CaseAlert)
        .filter(CaseAlert.case_id == case_id, CaseAlert.alert_id == alert_id)
        .first()
    )
    if not link:
        raise HTTPException(status_code=404, detail="Alert not linked to this case")
    db.delete(link)
    db.commit()
    return None


@router.post("/cases/{case_id}/assign", response_model=CaseResponse)
def assign_case(
    case_id: int,
    body: CaseAssign,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Assign case and all linked alerts to an analyst. Requires senior/admin."""
    from app.models.analyst import Analyst
    from app.models.case_alert import CaseAlert
    from app.models.gap_event import AISGapEvent
    from app.models.investigation_case import InvestigationCase

    case = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    analyst = (
        db.query(Analyst)
        .filter(Analyst.analyst_id == body.analyst_id, Analyst.is_active == True)  # noqa: E712
        .first()
    )
    if not analyst:
        raise HTTPException(status_code=404, detail="Analyst not found or inactive")

    # Assign case
    case.assigned_to = body.analyst_id

    # Assign all linked alerts
    linked = db.query(CaseAlert).filter(CaseAlert.case_id == case_id).all()
    alert_ids = [link.alert_id for link in linked]
    if alert_ids:
        db.query(AISGapEvent).filter(AISGapEvent.gap_event_id.in_(alert_ids)).update(
            {"assigned_to": body.analyst_id}, synchronize_session="fetch"
        )

    db.commit()
    db.refresh(case)

    alert_count = len(alert_ids)
    return _case_to_response(case, alert_count, analyst.username)


@router.get("/alerts/{alert_id}/cases", response_model=list[CaseResponse])
def get_cases_for_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Get all investigation cases containing this alert."""
    from app.models.analyst import Analyst
    from app.models.case_alert import CaseAlert
    from app.models.investigation_case import InvestigationCase

    links = db.query(CaseAlert).filter(CaseAlert.alert_id == alert_id).all()
    if not links:
        return []

    case_ids = [link.case_id for link in links]
    cases = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id.in_(case_ids))
        .all()
    )

    result = []
    for case in cases:
        alert_count = (
            db.query(CaseAlert).filter(CaseAlert.case_id == case.case_id).count()
        )
        assigned_username = None
        if case.assigned_to:
            analyst = (
                db.query(Analyst)
                .filter(Analyst.analyst_id == case.assigned_to)
                .first()
            )
            if analyst:
                assigned_username = analyst.username
        result.append(_case_to_response(case, alert_count, assigned_username))
    return result


@router.post("/cases/suggest", response_model=CaseSuggestion)
def suggest_grouping(
    body: CaseSuggestRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Suggest case grouping for an alert based on related alerts."""
    from app.modules.case_grouper import suggest_case_grouping

    related = suggest_case_grouping(db, body.alert_id)
    return CaseSuggestion(alert_id=body.alert_id, related_alerts=related)
