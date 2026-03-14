"""CalibrationEvent entity — audit trail for scoring calibration changes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CalibrationEvent(Base):
    __tablename__ = "calibration_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corridor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("corridors.corridor_id"), nullable=True, index=True
    )
    region_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    before_values_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_values_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyst_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
