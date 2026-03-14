"""VesselBehavioralProfile — per-vessel behavioral baseline and deviation scoring."""

from __future__ import annotations

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VesselBehavioralProfile(Base):
    __tablename__ = "vessel_behavioral_profiles"

    profile_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, unique=True, index=True
    )
    baseline_start: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    baseline_end: Mapped[str | None] = mapped_column(DateTime, nullable=True)
    speed_stats_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    port_pattern_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    route_pattern_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    gap_pattern_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    temporal_pattern_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    deviation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    deviation_signals_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_score_component: Mapped[float | None] = mapped_column(Float, nullable=True)
    tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[str | None] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[str | None] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
