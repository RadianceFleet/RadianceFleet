"""FPRateSnapshot — stored historical FP rate data for trend tracking."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FPRateSnapshot(Base):
    __tablename__ = "fp_rate_snapshots"

    snapshot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corridor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    region_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    signal_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    period_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    total_reviewed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    false_positives: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fp_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
