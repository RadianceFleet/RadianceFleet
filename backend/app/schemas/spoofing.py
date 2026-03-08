"""Pydantic schemas for spoofing anomaly responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SpoofingAnomalyRead(BaseModel):
    anomaly_id: int
    vessel_id: int
    gap_event_id: int | None = None
    anomaly_type: str
    start_time_utc: datetime
    end_time_utc: datetime | None = None
    evidence_json: dict[str, Any] | None = None
    implied_speed_kn: float | None = None
    plausibility_score: float | None = None
    risk_score_component: int

    model_config = {"from_attributes": True}
