"""Server-Sent Events for real-time alert notifications."""

from __future__ import annotations

import logging

import anyio
from fastapi import APIRouter, Depends, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.auth import require_auth
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter()

# Track active SSE connections
_active_connections = 0


def _query_new_alerts(last_id: int, min_score: int) -> list[dict]:
    """Query alerts newer than last_id with score >= min_score. Runs in thread."""
    db = SessionLocal()
    try:
        from app.models.gap_event import AISGapEvent

        q = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.gap_event_id > last_id,
                AISGapEvent.risk_score >= min_score,
            )
            .order_by(AISGapEvent.gap_event_id.asc())
            .limit(50)
        )
        results = []
        for alert in q.all():
            results.append(
                {
                    "gap_event_id": alert.gap_event_id,
                    "vessel_id": alert.vessel_id,
                    "risk_score": alert.risk_score,
                    "gap_start_utc": alert.gap_start_utc.isoformat()
                    if alert.gap_start_utc
                    else None,
                    "duration_minutes": alert.duration_minutes,
                    "status": str(alert.status.value)
                    if hasattr(alert.status, "value")
                    else str(alert.status),
                }
            )
        return results
    finally:
        db.close()


@router.get("/sse/alerts", tags=["sse"])
async def sse_alerts(
    request: Request,
    min_score: int = Query(51, ge=0, le=100, description="Minimum risk score to stream"),
    last_event_id: str | None = Query(None, alias="Last-Event-ID"),
    auth: dict = Depends(require_auth),
):
    """Stream new alerts via SSE. Supports Last-Event-ID for reconnection resume."""
    global _active_connections

    max_connections = getattr(settings, "SSE_MAX_CONNECTIONS", 20)
    if _active_connections >= max_connections:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=503,
            content={"detail": f"Max SSE connections ({max_connections}) reached"},
        )

    # Parse Last-Event-ID for resume
    last_id = 0
    if last_event_id:
        try:
            last_id = int(last_event_id)
        except ValueError:
            pass

    # Also check header (fetch-event-source sends it as header)
    header_last_id = request.headers.get("Last-Event-ID")
    if header_last_id and not last_event_id:
        try:
            last_id = int(header_last_id)
        except ValueError:
            pass

    async def event_generator():
        global _active_connections
        _active_connections += 1
        nonlocal last_id
        try:
            # Send retry interval
            yield ServerSentEvent(data="", event="retry", retry=3000)

            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Query for new alerts in a thread (don't block event loop)
                alerts = await anyio.to_thread.run_sync(
                    lambda: _query_new_alerts(last_id, min_score)
                )

                for alert in alerts:
                    import json

                    last_id = alert["gap_event_id"]
                    yield ServerSentEvent(
                        data=json.dumps(alert),
                        event="alert",
                        id=str(last_id),
                    )

                # Keepalive ping every 15 seconds
                yield ServerSentEvent(data="", event="ping")

                # Poll interval
                await anyio.sleep(5.0)
        finally:
            _active_connections -= 1

    return EventSourceResponse(event_generator())
