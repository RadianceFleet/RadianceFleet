"""LoiteringEvent entity — vessels stationary in open water (pre-STS indicator)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class LoiteringEvent(Base):
    __tablename__ = "loitering_events"
    __table_args__ = (Index("ix_loiter_vessel_start", "vessel_id", "start_time_utc"),)

    loiter_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    start_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_hours: Mapped[float] = mapped_column(Float, nullable=False)
    median_sog_kn: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    corridor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("corridors.corridor_id"), nullable=True, index=True
    )
    preceding_gap_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id", ondelete="SET NULL"), nullable=True, index=True
    )
    following_gap_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id", ondelete="SET NULL"), nullable=True, index=True
    )
    risk_score_component: Mapped[int] = mapped_column(Integer, default=0)

    corridor: Mapped[Corridor | None] = relationship("Corridor", back_populates="loitering_events")

    vessel: Mapped[Vessel] = relationship("Vessel")
