"""Pydantic schemas for loitering event responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class LoiteringEventRead(BaseModel):
    loiter_id: int
    vessel_id: int
    start_time_utc: datetime
    end_time_utc: datetime
    duration_hours: float
    median_sog_kn: float | None = None
    mean_lat: float | None = None
    mean_lon: float | None = None
    corridor_id: int | None = None
    preceding_gap_id: int | None = None
    following_gap_id: int | None = None
    risk_score_component: int

    model_config = {"from_attributes": True}
