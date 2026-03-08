"""DataCoverageWindow entity — tracks which date ranges have been imported per source."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DataCoverageWindow(Base):
    __tablename__ = "data_coverage_windows"
    __table_args__ = (
        UniqueConstraint("source", "date_from", "date_to", "status", name="uq_coverage_window"),
    )

    window_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # completed/failed/partial
    points_imported: Mapped[int] = mapped_column(Integer, default=0)
    vessels_queried: Mapped[int] = mapped_column(Integer, default=0)
    vessels_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
