"""CorridorScoringOverride entity — per-corridor scoring parameter overrides."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class CorridorScoringOverride(Base):
    __tablename__ = "corridor_scoring_overrides"

    override_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corridor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("corridors.corridor_id"), unique=True, index=True, nullable=False
    )
    corridor_multiplier_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap_duration_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    corridor: Mapped[Corridor] = relationship("Corridor")
    creator: Mapped[Analyst | None] = relationship("Analyst", foreign_keys=[created_by])
