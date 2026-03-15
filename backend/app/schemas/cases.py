"""Pydantic schemas for investigation cases."""

from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field


class CaseCreate(BaseModel):
    """Request body to create an investigation case."""

    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = None
    priority: str = Field("medium", description="low, medium, high, or critical")
    vessel_id: int | None = None
    corridor_id: int | None = None
    tags: list[str] | None = None


class CaseUpdate(BaseModel):
    """Request body to update an investigation case."""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assigned_to: int | None = None
    tags: list[str] | None = None


class CaseAnalystAdd(BaseModel):
    """Request body to add an analyst to a case."""

    analyst_id: int
    role: Literal["lead", "contributor", "reviewer"] = "contributor"


class CaseAnalystResponse(BaseModel):
    """Response for a case analyst membership."""

    analyst_id: int
    analyst_name: str
    role: str
    added_at: datetime

    model_config = {"from_attributes": True}


class CaseActivityResponse(BaseModel):
    """Response for a case activity entry."""

    activity_id: int
    analyst_name: str | None = None
    action: str
    details: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CaseHandoffRequest(BaseModel):
    """Request body for case-level handoff."""

    to_analyst_id: int
    notes: str | None = None


class CaseResponse(BaseModel):
    """Full investigation case response."""

    case_id: int
    title: str
    description: str | None = None
    status: str
    priority: str
    assigned_to: int | None = None
    assigned_to_username: str | None = None
    created_by: int | None = None
    vessel_id: int | None = None
    corridor_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    alert_count: int = 0
    analysts: list[CaseAnalystResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseAlertAdd(BaseModel):
    """Request body to add an alert to a case."""

    alert_id: int


class CaseAlertResponse(BaseModel):
    """Response for a case-alert link."""

    case_id: int
    alert_id: int
    added_at: datetime
    added_by: int | None = None

    model_config = {"from_attributes": True}


class CaseAssign(BaseModel):
    """Request body to assign a case to an analyst."""

    analyst_id: int


class CaseSuggestRequest(BaseModel):
    """Request body for case grouping suggestion."""

    alert_id: int


class RelatedAlert(BaseModel):
    """A single related alert in a suggestion."""

    alert_id: int
    reason: str
    score: int


class CaseSuggestion(BaseModel):
    """Suggested case grouping for an alert."""

    alert_id: int
    related_alerts: list[RelatedAlert] = Field(default_factory=list)
