"""CorridorGapBaseline entity -- rolling gap-rate statistics per corridor."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class CorridorGapBaseline(Base):
    __tablename__ = "corridor_gap_baselines"

    baseline_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corridor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("corridors.corridor_id"), nullable=False, index=True
    )
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    mean_gap_count: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)

    corridor = relationship("Corridor")
