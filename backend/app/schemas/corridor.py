"""Pydantic schemas for corridor operations."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class CorridorCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    corridor_type: str = Field(default="import_route")
    risk_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    description: Optional[str] = None
    is_jamming_zone: bool = False
    geometry_wkt: Optional[str] = None


class CorridorUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    corridor_type: Optional[str] = None
    risk_weight: Optional[float] = Field(None, ge=0.0, le=10.0)
    description: Optional[str] = None
    is_jamming_zone: Optional[bool] = None
    geometry_wkt: Optional[str] = None
