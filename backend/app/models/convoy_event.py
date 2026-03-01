"""ConvoyEvent entity — detected synchronized vessel movement (convoy) events."""
from __future__ import annotations

import datetime

from sqlalchemy import Column, Integer, String, DateTime, Float, JSON


from app.models.base import Base


class ConvoyEvent(Base):
    __tablename__ = "convoy_events"

    convoy_id = Column(Integer, primary_key=True, autoincrement=True)
    vessel_a_id = Column(Integer, nullable=False, index=True)
    vessel_b_id = Column(Integer, nullable=False, index=True)
    start_time_utc = Column(DateTime)
    end_time_utc = Column(DateTime)
    duration_hours = Column(Float)
    mean_distance_nm = Column(Float)
    mean_heading_delta = Column(Float)
    corridor_id = Column(Integer, nullable=True)
    risk_score_component = Column(Integer, default=0)
    evidence_json = Column(JSON)
    created_at = Column(
        DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
