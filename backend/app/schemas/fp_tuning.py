"""Pydantic schemas for false-positive tuning endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CorridorFPRateSchema(BaseModel):
    """FP rate statistics for a single corridor."""

    corridor_id: int
    corridor_name: str
    total_alerts: int = 0
    false_positives: int = 0
    fp_rate: float = 0.0
    fp_rate_30d: float = 0.0
    fp_rate_90d: float = 0.0
    trend: str = "stable"

    model_config = {"from_attributes": True}


class CalibrationSuggestionSchema(BaseModel):
    """Auto-generated suggestion to tune a corridor's scoring multiplier."""

    corridor_id: int
    corridor_name: str
    current_multiplier: float
    suggested_multiplier: float
    reason: str
    fp_rate: float

    model_config = {"from_attributes": True}


class ScoringOverrideCreate(BaseModel):
    """Request body for creating/updating a corridor scoring override."""

    corridor_multiplier_override: float | None = Field(
        None, description="Override corridor multiplier (null to clear)"
    )
    gap_duration_multiplier: float = Field(
        1.0, ge=0.1, le=5.0, description="Gap duration scoring scale factor"
    )
    description: str | None = Field(None, max_length=2000, description="Reason for override")
    signal_overrides: dict[str, float] | None = Field(
        None, description="Per-signal overrides: {'section.key': value}"
    )


class ScoringOverrideResponse(BaseModel):
    """Response for a corridor scoring override."""

    override_id: int
    corridor_id: int
    corridor_name: str = ""
    corridor_multiplier_override: float | None = None
    gap_duration_multiplier: float = 1.0
    description: str | None = None
    created_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_active: bool = True
    signal_overrides: dict[str, float] | None = None
    region_id: int | None = None

    model_config = {"from_attributes": True}


class CalibrationEventResponse(BaseModel):
    """Response for a calibration audit trail event."""

    event_id: int
    corridor_id: int | None = None
    region_id: int | None = None
    event_type: str
    before_values: dict | None = None
    after_values: dict | None = None
    impact_summary: dict | None = None
    analyst_id: int | None = None
    reason: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
