"""Pydantic schemas for vessel track endpoints."""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel

from app.schemas.gap_event import AISPointSummary


class TrackQueryMeta(BaseModel):
    vessel_id: int
    date_from: date
    date_to: date
    total_points: int
    downsampling_applied: bool
    downsampling_interval: Optional[str] = None  # "1h", "6h", or None
    next_cursor: Optional[str] = None


class TrackResponse(BaseModel):
    meta: TrackQueryMeta
    points: list[AISPointSummary]
