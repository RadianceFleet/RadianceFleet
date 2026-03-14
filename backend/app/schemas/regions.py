"""Pydantic schemas for region grouping and shadow scoring."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RegionCreate(BaseModel):
    name: str = Field(..., max_length=200)
    description: str | None = None
    corridor_ids: list[int] = Field(default_factory=list)
    signal_overrides: dict[str, float] | None = None
    corridor_multiplier_override: float | None = None
    gap_duration_multiplier: float = 1.0


class RegionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    corridor_ids: list[int] | None = None
    signal_overrides: dict[str, float] | None = None
    corridor_multiplier_override: float | None = None
    gap_duration_multiplier: float | None = None
    is_active: bool | None = None


class RegionResponse(BaseModel):
    region_id: int
    name: str
    description: str | None = None
    corridor_ids: list[int] = []
    signal_overrides: dict[str, float] | None = None
    corridor_multiplier_override: float | None = None
    gap_duration_multiplier: float = 1.0
    is_active: bool = True
    created_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    fp_rate: float | None = None  # computed field
    model_config = {"from_attributes": True}


class ShadowScoreRequest(BaseModel):
    signal_overrides: dict[str, float] | None = None
    corridor_multiplier_override: float | None = None
    gap_duration_multiplier: float | None = None
    limit: int = Field(100, ge=1, le=500)


class ShadowScoreResult(BaseModel):
    alert_id: int
    original_score: int
    proposed_score: int
    original_band: str
    proposed_band: str
    band_changed: bool


class ShadowScoreResponse(BaseModel):
    corridor_id: int
    alerts_scored: int
    band_changes: int
    avg_score_delta: float
    predicted_fp_rate_change: float | None = None
    results: list[ShadowScoreResult]
