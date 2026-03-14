"""Pydantic schemas for analyst collaboration and handoff features."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HandoffRequest(BaseModel):
    """Request body for creating an alert handoff."""

    to_analyst_id: int = Field(..., description="Target analyst ID to hand off to")
    notes: str = Field("", description="Handoff notes explaining context and next steps")


class HandoffResponse(BaseModel):
    """Response for a completed handoff."""

    handoff_id: int
    from_analyst: str
    to_analyst: str
    notes: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PresenceInfo(BaseModel):
    """Analyst presence information."""

    analyst_id: int
    analyst_name: str
    is_online: bool
    current_alert_id: int | None = None
    last_seen: datetime


class WorkloadSummary(BaseModel):
    """Workload summary for a single analyst."""

    analyst_id: int
    analyst_name: str
    open_alerts: int
    assigned_alerts: int
    avg_resolution_hours: float | None = None
