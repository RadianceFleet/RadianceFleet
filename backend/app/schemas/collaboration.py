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


class DetailedWorkloadSummary(BaseModel):
    """Extended workload summary with utilization, online status, and specializations."""

    analyst_id: int
    analyst_name: str
    open_alerts: int
    assigned_alerts: int
    avg_resolution_hours: float | None = None
    utilization: float = 0.0
    is_online: bool = False
    specializations: list[str] = []
    shift_start_hour: int | None = None
    shift_end_hour: int | None = None


class ActivityFeedEntry(BaseModel):
    """A single event in the analyst activity feed."""

    event_type: str
    analyst_name: str
    description: str
    timestamp: str | None = None
    related_id: int | None = None


class QueueEntry(BaseModel):
    """An unassigned alert in the assignment queue."""

    alert_id: int
    risk_score: int
    vessel_name: str | None = None
    corridor_name: str | None = None
    suggested_analyst_id: int | None = None
    suggested_analyst_name: str | None = None
