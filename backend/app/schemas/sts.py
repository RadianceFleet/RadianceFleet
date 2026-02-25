"""Pydantic schemas for STS transfer event responses."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class StsTransferEventRead(BaseModel):
    sts_id: int
    vessel_1_id: int
    vessel_2_id: int
    detection_type: str
    start_time_utc: datetime
    end_time_utc: datetime
    duration_minutes: Optional[int] = None
    mean_proximity_meters: Optional[float] = None
    mean_lat: Optional[float] = None
    mean_lon: Optional[float] = None
    corridor_id: Optional[int] = None
    satellite_confirmation_status: Optional[str] = None
    eta_minutes: Optional[int] = None
    risk_score_component: int

    model_config = {"from_attributes": True}
