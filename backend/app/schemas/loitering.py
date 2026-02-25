"""Pydantic schemas for loitering event responses."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class LoiteringEventRead(BaseModel):
    loiter_id: int
    vessel_id: int
    start_time_utc: datetime
    end_time_utc: datetime
    duration_hours: float
    median_sog_kn: Optional[float] = None
    mean_lat: Optional[float] = None
    mean_lon: Optional[float] = None
    corridor_id: Optional[int] = None
    preceding_gap_id: Optional[int] = None
    following_gap_id: Optional[int] = None
    risk_score_component: int

    model_config = {"from_attributes": True}
