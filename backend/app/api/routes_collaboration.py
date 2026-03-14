"""Analyst collaboration endpoints — handoffs, workload, and presence SSE."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.database import SessionLocal, get_db
from app.schemas.collaboration import (
    HandoffRequest,
    HandoffResponse,
    WorkloadSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["collaboration"])


# ---------------------------------------------------------------------------
# Handoff
# ---------------------------------------------------------------------------


@router.post("/alerts/{alert_id}/handoff", response_model=HandoffResponse)
def create_handoff(
    alert_id: int,
    body: HandoffRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Create a handoff for an alert from the current analyst to another.

    Updates the alert's assigned_to field and records the handoff note.
    """
    from app.models.analyst import Analyst
    from app.models.gap_event import AISGapEvent
    from app.models.handoff_note import HandoffNote

    from_analyst_id = auth["analyst_id"]

    # Prevent self-handoff
    if body.to_analyst_id == from_analyst_id:
        raise HTTPException(status_code=400, detail="Cannot hand off to yourself")

    # Verify alert exists
    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Verify target analyst exists and is active
    to_analyst = (
        db.query(Analyst)
        .filter(Analyst.analyst_id == body.to_analyst_id, Analyst.is_active == True)  # noqa: E712
        .first()
    )
    if not to_analyst:
        raise HTTPException(status_code=404, detail="Target analyst not found or inactive")

    # Verify from analyst exists (for display name)
    from_analyst = db.query(Analyst).filter(Analyst.analyst_id == from_analyst_id).first()
    from_name = from_analyst.display_name or from_analyst.username if from_analyst else auth["username"]

    # Create handoff record
    handoff = HandoffNote(
        alert_id=alert_id,
        from_analyst_id=from_analyst_id,
        to_analyst_id=body.to_analyst_id,
        notes=body.notes,
    )
    db.add(handoff)

    # Update alert assignment
    alert.assigned_to = body.to_analyst_id
    alert.assigned_at = datetime.now(UTC)

    db.commit()
    db.refresh(handoff)

    return HandoffResponse(
        handoff_id=handoff.handoff_id,
        from_analyst=from_name,
        to_analyst=to_analyst.display_name or to_analyst.username,
        notes=handoff.notes or "",
        created_at=handoff.created_at,
    )


@router.get("/alerts/{alert_id}/handoff-history", response_model=list[HandoffResponse])
def get_handoff_history(
    alert_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """List all handoffs for an alert, ordered chronologically."""
    from app.models.handoff_note import HandoffNote

    handoffs = (
        db.query(HandoffNote)
        .filter(HandoffNote.alert_id == alert_id)
        .order_by(HandoffNote.created_at.asc())
        .all()
    )

    results = []
    for h in handoffs:
        from_name = ""
        to_name = ""
        if h.from_analyst:
            from_name = h.from_analyst.display_name or h.from_analyst.username
        if h.to_analyst:
            to_name = h.to_analyst.display_name or h.to_analyst.username
        results.append(
            HandoffResponse(
                handoff_id=h.handoff_id,
                from_analyst=from_name,
                to_analyst=to_name,
                notes=h.notes or "",
                created_at=h.created_at,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Workload
# ---------------------------------------------------------------------------


@router.get("/analysts/workload", response_model=list[WorkloadSummary])
def get_analyst_workload(
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Get workload summary for all active analysts."""
    from app.models.analyst import Analyst
    from app.models.gap_event import AISGapEvent

    analysts = db.query(Analyst).filter(Analyst.is_active == True).all()  # noqa: E712

    results = []
    for analyst in analysts:
        # Count open (non-terminal) alerts
        open_count = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.assigned_to == analyst.analyst_id,
                AISGapEvent.status.notin_(["dismissed", "documented", "confirmed_fp"]),
            )
            .count()
        )

        # Count all assigned alerts
        assigned_count = (
            db.query(AISGapEvent)
            .filter(AISGapEvent.assigned_to == analyst.analyst_id)
            .count()
        )

        # Average resolution time (for resolved alerts)
        avg_hours = None
        resolved = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.assigned_to == analyst.analyst_id,
                AISGapEvent.status.in_(["dismissed", "documented", "confirmed_fp", "confirmed_tp"]),
                AISGapEvent.review_date.isnot(None),
                AISGapEvent.assigned_at.isnot(None),
            )
            .all()
        )
        if resolved:
            total_hours = 0.0
            count = 0
            for alert in resolved:
                if alert.review_date and alert.assigned_at:
                    delta = alert.review_date - alert.assigned_at
                    total_hours += delta.total_seconds() / 3600
                    count += 1
            if count > 0:
                avg_hours = round(total_hours / count, 1)

        results.append(
            WorkloadSummary(
                analyst_id=analyst.analyst_id,
                analyst_name=analyst.display_name or analyst.username,
                open_alerts=open_count,
                assigned_alerts=assigned_count,
                avg_resolution_hours=avg_hours,
            )
        )

    # Sort by open alerts ascending (least loaded first)
    results.sort(key=lambda w: w.open_alerts)
    return results


# ---------------------------------------------------------------------------
# Presence heartbeat
# ---------------------------------------------------------------------------


@router.post("/presence/heartbeat")
def presence_heartbeat(
    alert_id: int | None = None,
    auth: dict = Depends(require_auth),
):
    """Send a presence heartbeat. Call every ~15 seconds from the client."""
    from app.modules.analyst_presence import heartbeat

    heartbeat(auth["analyst_id"], alert_id=alert_id)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# SSE presence stream
# ---------------------------------------------------------------------------

_active_presence_connections = 0


def _query_presence_snapshot() -> list[dict]:
    """Get presence data enriched with analyst names. Runs in thread."""
    from app.modules.analyst_presence import get_presence_snapshot

    snapshot = get_presence_snapshot()
    if not snapshot:
        return []

    db = SessionLocal()
    try:
        from app.models.analyst import Analyst

        analyst_ids = [e["analyst_id"] for e in snapshot]
        analysts = db.query(Analyst).filter(Analyst.analyst_id.in_(analyst_ids)).all()
        name_map = {a.analyst_id: a.display_name or a.username for a in analysts}

        result = []
        for entry in snapshot:
            result.append(
                {
                    "analyst_id": entry["analyst_id"],
                    "analyst_name": name_map.get(entry["analyst_id"], "Unknown"),
                    "is_online": entry["is_online"],
                    "current_alert_id": entry["current_alert_id"],
                    "last_seen": entry["last_seen"],
                }
            )
        return result
    finally:
        db.close()


@router.get("/sse/presence", tags=["sse"])
async def sse_presence(
    request: Request,
    auth: dict = Depends(require_auth),
):
    """Stream analyst presence updates via SSE.

    Sends a snapshot every 5 seconds with all known analyst presence data.
    """
    global _active_presence_connections

    max_connections = 20
    if _active_presence_connections >= max_connections:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=503,
            content={"detail": f"Max presence SSE connections ({max_connections}) reached"},
        )

    async def event_generator():
        global _active_presence_connections
        _active_presence_connections += 1
        try:
            yield ServerSentEvent(data="", event="retry", retry=5000)

            while True:
                if await request.is_disconnected():
                    break

                snapshot = await anyio.to_thread.run_sync(_query_presence_snapshot)

                yield ServerSentEvent(
                    data=json.dumps(snapshot),
                    event="presence",
                )

                # Keepalive ping
                yield ServerSentEvent(data="", event="ping")

                await anyio.sleep(5.0)
        finally:
            _active_presence_connections -= 1

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Detailed workload
# ---------------------------------------------------------------------------


@router.get("/analysts/workload/detailed")
def get_detailed_workload(db: Session = Depends(get_db), auth: dict = Depends(require_auth)):
    """Per-analyst detailed workload with utilization and online status."""
    from app.models.analyst import Analyst
    from app.models.gap_event import AISGapEvent
    from app.modules.analyst_presence import get_online_analysts

    analysts = db.query(Analyst).filter(Analyst.is_active == True).all()  # noqa: E712
    online_ids = {a["analyst_id"] for a in get_online_analysts()}

    results = []
    for analyst in analysts:
        open_count = db.query(AISGapEvent).filter(
            AISGapEvent.assigned_to == analyst.analyst_id,
            AISGapEvent.status.notin_(["dismissed", "documented", "confirmed_fp"]),
        ).count()
        assigned_count = db.query(AISGapEvent).filter(
            AISGapEvent.assigned_to == analyst.analyst_id,
        ).count()

        # Simple utilization: ratio of open alerts to a reasonable max (10)
        utilization = min(open_count / 10.0, 1.0) if open_count > 0 else 0.0

        specializations: list[str] = []
        if getattr(analyst, "specializations_json", None):
            try:
                parsed = json.loads(analyst.specializations_json)
                if isinstance(parsed, list):
                    specializations = parsed
            except (json.JSONDecodeError, TypeError):
                pass

        results.append({
            "analyst_id": analyst.analyst_id,
            "analyst_name": analyst.display_name or analyst.username,
            "open_alerts": open_count,
            "assigned_alerts": assigned_count,
            "avg_resolution_hours": None,
            "utilization": round(utilization, 3),
            "is_online": analyst.analyst_id in online_ids,
            "specializations": specializations,
            "shift_start_hour": getattr(analyst, "shift_start_hour", None),
            "shift_end_hour": getattr(analyst, "shift_end_hour", None),
        })

    results.sort(key=lambda x: x["utilization"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------


@router.get("/analysts/activity-feed")
def get_activity_feed(
    limit: int = 20,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Recent handoffs and assignment changes."""
    from app.models.handoff_note import HandoffNote

    handoffs = (
        db.query(HandoffNote)
        .order_by(HandoffNote.created_at.desc())
        .limit(limit)
        .all()
    )

    feed = []
    for h in handoffs:
        from_name = (
            h.from_analyst.display_name or h.from_analyst.username
            if h.from_analyst
            else "Unknown"
        )
        to_name = (
            h.to_analyst.display_name or h.to_analyst.username
            if h.to_analyst
            else "Unknown"
        )
        feed.append({
            "event_type": "handoff",
            "analyst_name": from_name,
            "description": f"Handed off alert #{h.alert_id} to {to_name}",
            "timestamp": h.created_at.isoformat() if h.created_at else None,
            "related_id": h.alert_id,
        })

    feed.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return feed[:limit]


# ---------------------------------------------------------------------------
# Assignment queue
# ---------------------------------------------------------------------------


@router.get("/analysts/queue")
def get_assignment_queue(
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Unassigned high-risk alerts with suggested assignee."""
    from app.models.analyst import Analyst
    from app.models.gap_event import AISGapEvent
    from app.modules.analyst_presence import suggest_assignment

    unassigned = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.assigned_to.is_(None),
            AISGapEvent.risk_score >= 51,
            AISGapEvent.status.notin_(["dismissed", "documented", "confirmed_fp"]),
        )
        .order_by(AISGapEvent.risk_score.desc())
        .limit(50)
        .all()
    )

    # Get a single suggested analyst (same for all unassigned alerts)
    suggested = suggest_assignment(db)
    suggested_name = None
    if suggested:
        analyst = db.query(Analyst).filter(Analyst.analyst_id == suggested).first()
        if analyst:
            suggested_name = analyst.display_name or analyst.username

    results = []
    for alert in unassigned:
        results.append({
            "alert_id": alert.gap_event_id,
            "risk_score": alert.risk_score,
            "vessel_name": None,
            "corridor_name": None,
            "suggested_analyst_id": suggested,
            "suggested_analyst_name": suggested_name,
        })

    return results
