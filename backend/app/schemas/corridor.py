"""Pydantic schemas for corridor operations."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CorridorCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    corridor_type: str = Field(default="import_route")
    risk_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    description: str | None = None
    is_jamming_zone: bool = False
    geometry_wkt: str | None = None


class CorridorUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    corridor_type: str | None = None
    risk_weight: float | None = Field(None, ge=0.0, le=10.0)
    description: str | None = None
    is_jamming_zone: bool | None = None
    geometry_wkt: str | None = None
