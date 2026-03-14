"""Unified SSE stream — multiplexes alerts, presence, and notifications."""

from __future__ import annotations

import contextlib
import json
import logging

import anyio
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.auth import require_auth
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter()

_active_unified_connections = 0


def _query_new_alerts(last_id: int, min_score: int) -> list[dict]:
    """Query alerts newer than last_id. Runs in thread."""
    db = SessionLocal()
    try:
        from app.models.gap_event import AISGapEvent

        alerts = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.gap_event_id > last_id,
                AISGapEvent.risk_score >= min_score,
            )
            .order_by(AISGapEvent.gap_event_id.asc())
            .limit(50)
            .all()
        )
        return [
            {
                "gap_event_id": a.gap_event_id,
                "vessel_id": a.vessel_id,
                "risk_score": a.risk_score,
                "gap_start_utc": a.gap_start_utc.isoformat()
                if a.gap_start_utc
                else None,
                "duration_minutes": a.duration_minutes,
                "status": str(a.status.value)
                if hasattr(a.status, "value")
                else str(a.status),
            }
            for a in alerts
        ]
    finally:
        db.close()


def _query_presence() -> list[dict]:
    """Get presence snapshot. Runs in thread."""
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
        return [
            {
                "analyst_id": e["analyst_id"],
                "analyst_name": name_map.get(e["analyst_id"], "Unknown"),
                "is_online": e["is_online"],
                "current_alert_id": e["current_alert_id"],
                "last_seen": e["last_seen"],
            }
            for e in snapshot
        ]
    finally:
        db.close()


def _query_notifications(analyst_id: int, last_notification_id: int) -> list[dict]:
    """Get new notifications. Runs in thread."""
    db = SessionLocal()
    try:
        from app.models.notification_event import NotificationEvent

        events = (
            db.query(NotificationEvent)
            .filter(
                NotificationEvent.target_analyst_id == analyst_id,
                NotificationEvent.event_id > last_notification_id,
            )
            .order_by(NotificationEvent.event_id.asc())
            .limit(20)
            .all()
        )
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
    finally:
        db.close()


@router.get("/sse/events", tags=["sse"])
async def sse_events(
    request: Request,
    min_score: int = Query(51, ge=0, le=100),
    last_alert_id: int = Query(0, alias="last_alert_id"),
    last_notification_id: int = Query(0, alias="last_notification_id"),
    auth: dict = Depends(require_auth),
):
    """Unified SSE stream multiplexing alerts, presence, and notifications."""
    global _active_unified_connections

    max_connections = getattr(settings, "SSE_MAX_CONNECTIONS", 20)
    if _active_unified_connections >= max_connections:
        return JSONResponse(
            status_code=503,
            content={
                "detail": f"Max SSE connections ({max_connections}) reached"
            },
        )

    analyst_id = auth.get("analyst_id", 0)

    # Also check Last-Event-ID header
    header_last_id = request.headers.get("Last-Event-ID")
    if header_last_id:
        with contextlib.suppress(ValueError):
            if ":" in header_last_id:
                prefix, val = header_last_id.split(":", 1)
                if prefix == "alert":
                    last_alert_id = int(val)
                elif prefix == "notification":
                    last_notification_id = int(val)
            else:
                last_alert_id = int(header_last_id)

    async def event_generator():
        global _active_unified_connections
        _active_unified_connections += 1
        nonlocal last_alert_id, last_notification_id
        try:
            yield ServerSentEvent(data="", event="retry", retry=3000)

            while True:
                if await request.is_disconnected():
                    break

                # Query all sources with per-source error isolation
                try:
                    alerts = await anyio.to_thread.run_sync(
                        lambda _lid=last_alert_id: _query_new_alerts(_lid, min_score)
                    )
                except Exception:
                    logger.exception("Error querying alerts for SSE")
                    alerts = []

                try:
                    presence = await anyio.to_thread.run_sync(_query_presence)
                except Exception:
                    logger.exception("Error querying presence for SSE")
                    presence = []

                try:
                    notifications = await anyio.to_thread.run_sync(
                        lambda _nid=last_notification_id: _query_notifications(
                            analyst_id, _nid
                        )
                    )
                except Exception:
                    logger.exception("Error querying notifications for SSE")
                    notifications = []

                # Emit alert events
                for alert in alerts:
                    last_alert_id = alert["gap_event_id"]
                    yield ServerSentEvent(
                        data=json.dumps(alert),
                        event="alert",
                        id=f"alert:{last_alert_id}",
                    )

                # Emit presence event
                if presence:
                    yield ServerSentEvent(
                        data=json.dumps(presence), event="presence"
                    )

                # Emit notification events
                for notif in notifications:
                    last_notification_id = notif["event_id"]
                    yield ServerSentEvent(
                        data=json.dumps(notif),
                        event="notification",
                        id=f"notification:{last_notification_id}",
                    )

                # Keepalive
                yield ServerSentEvent(data="", event="ping")

                await anyio.sleep(5.0)
        finally:
            _active_unified_connections -= 1

    return EventSourceResponse(event_generator())
