"""ScoringRegion entity — groups corridors for regional FP tuning."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ScoringRegion(Base):
    __tablename__ = "scoring_regions"

    region_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    corridor_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of corridor IDs
    signal_overrides_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # region-level signal overrides
    corridor_multiplier_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap_duration_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
