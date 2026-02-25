"""Pydantic schemas for spoofing anomaly responses."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class SpoofingAnomalyRead(BaseModel):
    anomaly_id: int
    vessel_id: int
    gap_event_id: Optional[int] = None
    anomaly_type: str
    start_time_utc: datetime
    end_time_utc: Optional[datetime] = None
    evidence_json: Optional[dict[str, Any]] = None
    implied_speed_kn: Optional[float] = None
    plausibility_score: Optional[float] = None
    risk_score_component: int

    model_config = {"from_attributes": True}
