"""SatelliteTaskingCandidate entity -- recommended imagery requests for dark-dark STS candidates."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Integer, Float, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class SatelliteTaskingCandidate(Base):
    __tablename__ = "satellite_tasking_candidates"

    candidate_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corridor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("corridors.corridor_id"), nullable=True
    )
    vessel_a_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    vessel_b_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    gap_overlap_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    proximity_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recommended_imagery_window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recommended_imagery_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    risk_score_component: Mapped[int] = mapped_column(Integer, default=0)
    created_utc: Mapped[datetime] = mapped_column(
        DateTime, default=func.now()
    )

    corridor = relationship("Corridor")
    vessel_a = relationship("Vessel", foreign_keys=[vessel_a_id])
    vessel_b = relationship("Vessel", foreign_keys=[vessel_b_id])
