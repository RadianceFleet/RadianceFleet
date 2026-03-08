"""Pydantic schemas for STS transfer event responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StsTransferEventRead(BaseModel):
    sts_id: int
    vessel_1_id: int
    vessel_2_id: int
    detection_type: str
    start_time_utc: datetime
    end_time_utc: datetime
    duration_minutes: int | None = None
    mean_proximity_meters: float | None = None
    mean_lat: float | None = None
    mean_lon: float | None = None
    corridor_id: int | None = None
    satellite_confirmation_status: str | None = None
    eta_minutes: int | None = None
    risk_score_component: int
    user_validated: bool | None = None
    confidence_override: float | None = None

    model_config = {"from_attributes": True}
