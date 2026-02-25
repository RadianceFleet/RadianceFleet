"""LoiteringEvent entity — vessels stationary in open water (pre-STS indicator)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class LoiteringEvent(Base):
    __tablename__ = "loitering_events"

    loiter_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    start_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_hours: Mapped[float] = mapped_column(Float, nullable=False)
    median_sog_kn: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mean_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mean_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    corridor_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("corridors.corridor_id"), nullable=True)
    preceding_gap_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=True)
    following_gap_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=True)
    risk_score_component: Mapped[int] = mapped_column(Integer, default=0)

    vessel: Mapped["Vessel"] = relationship("Vessel")
