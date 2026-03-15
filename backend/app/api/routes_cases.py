"""Investigation case endpoints — create, manage, and suggest case groupings."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_auth, require_senior_or_admin
from app.database import get_db
from app.schemas.cases import (
    CaseActivityResponse,
    CaseAlertAdd,
    CaseAlertResponse,
    CaseAnalystAdd,
    CaseAnalystResponse,
    CaseAssign,
    CaseCreate,
    CaseHandoffRequest,
    CaseResponse,
    CaseSuggestion,
    CaseSuggestRequest,
    CaseUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cases"])

CASE_ALERT_CAP = 30


def _case_to_response(
    case, alert_count: int, assigned_username: str | None, analysts_data: list | None = None
) -> dict:
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
        "analysts": analysts_data or [],
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def _get_case_analysts_data(db: Session, case_id: int) -> list[dict]:
    """Fetch analyst membership data for a case."""
    from app.models.analyst import Analyst
    from app.models.case_analyst import CaseAnalyst

    memberships = (
        db.query(CaseAnalyst).filter(CaseAnalyst.case_id == case_id).all()
    )
    if not memberships:
        return []

    analyst_ids = [m.analyst_id for m in memberships]
    analysts = db.query(Analyst).filter(Analyst.analyst_id.in_(analyst_ids)).all()
    analyst_map = {a.analyst_id: (a.display_name or a.username) for a in analysts}

    return [
        {
            "analyst_id": m.analyst_id,
            "analyst_name": analyst_map.get(m.analyst_id, "unknown"),
            "role": m.role,
            "added_at": m.added_at,
        }
        for m in memberships
    ]


def _record_activity(
    db: Session,
    case_id: int,
    analyst_id: int | None,
    action: str,
    details: dict | None = None,
) -> None:
    """Record a CaseActivity entry."""
    from app.models.case_activity import CaseActivity

    activity = CaseActivity(
        case_id=case_id,
        analyst_id=analyst_id,
        action=action,
        details_json=json.dumps(details) if details else None,
    )
    db.add(activity)


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

    _record_activity(db, case.case_id, auth["analyst_id"], "created")
    db.commit()

    return _case_to_response(case, 0, None)


@router.get("/cases", response_model=list[CaseResponse])
def list_cases(
    status: str | None = Query(None),
    priority: str | None = Query(None),
    assigned_to: int | None = Query(None),
    my_cases: bool = Query(False),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """List investigation cases with optional filters."""
    from app.models.analyst import Analyst
    from app.models.case_alert import CaseAlert
    from app.models.case_analyst import CaseAnalyst
    from app.models.investigation_case import InvestigationCase

    q = db.query(InvestigationCase)
    if status:
        q = q.filter(InvestigationCase.status == status)
    if priority:
        q = q.filter(InvestigationCase.priority == priority)
    if assigned_to is not None:
        q = q.filter(InvestigationCase.assigned_to == assigned_to)

    if my_cases:
        analyst_id = auth["analyst_id"]
        # Cases where the analyst is assigned_to OR has a CaseAnalyst membership
        member_case_ids = (
            db.query(CaseAnalyst.case_id)
            .filter(CaseAnalyst.analyst_id == analyst_id)
            .all()
        )
        member_ids = {row[0] for row in member_case_ids}
        cases_all = q.order_by(InvestigationCase.updated_at.desc()).all()
        cases = [
            c
            for c in cases_all
            if c.assigned_to == analyst_id or c.case_id in member_ids
        ]
    else:
        cases = q.order_by(InvestigationCase.updated_at.desc()).all()

    if not cases:
        return []

    # Batch-fetch alert counts (avoid N+1)
    from sqlalchemy import func

    case_ids = [c.case_id for c in cases]
    count_rows = (
        db.query(CaseAlert.case_id, func.count())
        .filter(CaseAlert.case_id.in_(case_ids))
        .group_by(CaseAlert.case_id)
        .all()
    )
    counts = dict(count_rows)

    # Batch-fetch assigned analysts (avoid N+1)
    analyst_ids = {c.assigned_to for c in cases if c.assigned_to}
    analyst_map: dict[int, str] = {}
    if analyst_ids:
        analysts_list = db.query(Analyst).filter(Analyst.analyst_id.in_(analyst_ids)).all()
        analyst_map = {a.analyst_id: a.username for a in analysts_list}

    result = []
    for case in cases:
        alert_count = counts.get(case.case_id, 0)
        assigned_username = analyst_map.get(case.assigned_to) if case.assigned_to else None
        analysts_data = _get_case_analysts_data(db, case.case_id)
        result.append(_case_to_response(case, alert_count, assigned_username, analysts_data))
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
    analysts_data = _get_case_analysts_data(db, case_id)
    return _case_to_response(case, alert_count, assigned_username, analysts_data)


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

    # Only case owner, assignee, or senior/admin can update
    analyst_id = auth.get("analyst_id")
    role = auth.get("role", "analyst")
    is_owner = case.created_by == analyst_id or case.assigned_to == analyst_id
    is_elevated = role in ("senior_analyst", "admin")
    if not is_owner and not is_elevated:
        raise HTTPException(status_code=403, detail="Not authorized to update this case")

    old_status = case.status

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

    # Record status change activity
    if body.status is not None and body.status != old_status:
        _record_activity(
            db,
            case_id,
            auth["analyst_id"],
            "status_changed",
            {"old_status": old_status, "new_status": body.status},
        )
        db.commit()

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
    analysts_data = _get_case_analysts_data(db, case_id)
    return _case_to_response(case, alert_count, assigned_username, analysts_data)


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

    _record_activity(
        db,
        case_id,
        auth["analyst_id"],
        "alert_added",
        {"alert_id": body.alert_id},
    )

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

    _record_activity(
        db,
        case_id,
        auth["analyst_id"],
        "alert_removed",
        {"alert_id": alert_id},
    )

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
    analysts_data = _get_case_analysts_data(db, case_id)
    return _case_to_response(case, alert_count, analyst.username, analysts_data)


# ---------------------------------------------------------------------------
# Case analyst collaboration endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/cases/{case_id}/analysts",
    response_model=CaseAnalystResponse,
    status_code=201,
)
def add_analyst_to_case(
    case_id: int,
    body: CaseAnalystAdd,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Add an analyst to a case with a specific role."""
    from app.models.analyst import Analyst
    from app.models.case_analyst import CaseAnalyst
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

    # Check duplicate
    existing = (
        db.query(CaseAnalyst)
        .filter(CaseAnalyst.case_id == case_id, CaseAnalyst.analyst_id == body.analyst_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Analyst already on this case")

    membership = CaseAnalyst(
        case_id=case_id,
        analyst_id=body.analyst_id,
        role=body.role,
        added_by=auth["analyst_id"],
    )
    db.add(membership)

    # If adding as lead, also update InvestigationCase.assigned_to
    if body.role == "lead":
        case.assigned_to = body.analyst_id

    _record_activity(
        db,
        case_id,
        auth["analyst_id"],
        "assigned",
        {"analyst_id": body.analyst_id, "role": body.role},
    )

    db.commit()
    db.refresh(membership)

    # Notify all case analysts
    _notify_case_analysts(db, case_id, "analyst_added")

    analyst_name = analyst.display_name or analyst.username
    return {
        "analyst_id": membership.analyst_id,
        "analyst_name": analyst_name,
        "role": membership.role,
        "added_at": membership.added_at,
    }


@router.delete("/cases/{case_id}/analysts/{analyst_id}", status_code=204)
def remove_analyst_from_case(
    case_id: int,
    analyst_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Remove an analyst from a case. Cannot remove lead without handoff."""
    from app.models.case_analyst import CaseAnalyst
    from app.models.investigation_case import InvestigationCase

    case = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    membership = (
        db.query(CaseAnalyst)
        .filter(CaseAnalyst.case_id == case_id, CaseAnalyst.analyst_id == analyst_id)
        .first()
    )
    if not membership:
        raise HTTPException(status_code=404, detail="Analyst not on this case")

    if membership.role == "lead":
        raise HTTPException(
            status_code=400,
            detail="Cannot remove lead analyst without handoff",
        )

    db.delete(membership)
    _record_activity(
        db,
        case_id,
        auth["analyst_id"],
        "analyst_removed",
        {"analyst_id": analyst_id},
    )
    db.commit()
    return None


@router.get("/cases/{case_id}/analysts", response_model=list[CaseAnalystResponse])
def list_case_analysts(
    case_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """List all analysts assigned to a case."""
    from app.models.investigation_case import InvestigationCase

    case = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    return _get_case_analysts_data(db, case_id)


@router.get("/cases/{case_id}/activity", response_model=list[CaseActivityResponse])
def get_case_activity(
    case_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Get activity timeline for a case, newest first."""
    from app.models.analyst import Analyst
    from app.models.case_activity import CaseActivity
    from app.models.investigation_case import InvestigationCase

    case = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    activities = (
        db.query(CaseActivity)
        .filter(CaseActivity.case_id == case_id)
        .order_by(CaseActivity.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    # Batch-fetch analyst names
    analyst_ids = {a.analyst_id for a in activities if a.analyst_id}
    analyst_map: dict[int, str] = {}
    if analyst_ids:
        analysts = db.query(Analyst).filter(Analyst.analyst_id.in_(analyst_ids)).all()
        analyst_map = {a.analyst_id: (a.display_name or a.username) for a in analysts}

    result = []
    for activity in activities:
        details = None
        if activity.details_json:
            try:
                details = json.loads(activity.details_json)
            except (json.JSONDecodeError, TypeError):
                details = None
        result.append(
            {
                "activity_id": activity.activity_id,
                "analyst_name": analyst_map.get(activity.analyst_id) if activity.analyst_id else None,
                "action": activity.action,
                "details": details,
                "created_at": activity.created_at,
            }
        )
    return result


@router.post("/cases/{case_id}/handoff", response_model=CaseResponse)
def case_handoff(
    case_id: int,
    body: CaseHandoffRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Handoff case lead to another analyst."""
    from app.models.analyst import Analyst
    from app.models.case_alert import CaseAlert
    from app.models.case_analyst import CaseAnalyst
    from app.models.investigation_case import InvestigationCase

    case = (
        db.query(InvestigationCase)
        .filter(InvestigationCase.case_id == case_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    to_analyst = (
        db.query(Analyst)
        .filter(Analyst.analyst_id == body.to_analyst_id, Analyst.is_active == True)  # noqa: E712
        .first()
    )
    if not to_analyst:
        raise HTTPException(status_code=404, detail="Target analyst not found or inactive")

    old_assigned = case.assigned_to
    case.assigned_to = body.to_analyst_id

    # Update lead in CaseAnalyst — demote old lead, promote new
    old_lead = (
        db.query(CaseAnalyst)
        .filter(CaseAnalyst.case_id == case_id, CaseAnalyst.role == "lead")
        .first()
    )
    if old_lead:
        old_lead.role = "contributor"

    # Add or update new lead
    new_membership = (
        db.query(CaseAnalyst)
        .filter(CaseAnalyst.case_id == case_id, CaseAnalyst.analyst_id == body.to_analyst_id)
        .first()
    )
    if new_membership:
        new_membership.role = "lead"
    else:
        new_membership = CaseAnalyst(
            case_id=case_id,
            analyst_id=body.to_analyst_id,
            role="lead",
            added_by=auth["analyst_id"],
        )
        db.add(new_membership)

    _record_activity(
        db,
        case_id,
        auth["analyst_id"],
        "handoff",
        {
            "from_analyst_id": old_assigned,
            "to_analyst_id": body.to_analyst_id,
            "notes": body.notes,
        },
    )

    db.commit()
    db.refresh(case)

    # Notify all case analysts about the handoff
    _notify_case_analysts(db, case_id, "handoff")

    alert_count = (
        db.query(CaseAlert).filter(CaseAlert.case_id == case_id).count()
    )
    analysts_data = _get_case_analysts_data(db, case_id)
    return _case_to_response(case, alert_count, to_analyst.username, analysts_data)


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
        analysts_data = _get_case_analysts_data(db, case.case_id)
        result.append(_case_to_response(case, alert_count, assigned_username, analysts_data))
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _notify_case_analysts(db: Session, case_id: int, action: str) -> None:
    """Emit case_update notifications to all analysts on the case."""
    from app.models.case_analyst import CaseAnalyst
    from app.modules.collaboration_notifier import emit_case_update

    memberships = (
        db.query(CaseAnalyst).filter(CaseAnalyst.case_id == case_id).all()
    )
    for m in memberships:
        emit_case_update(db, m.analyst_id, case_id, action)
