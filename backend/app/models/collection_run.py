"""CollectionRun entity — tracks each AIS data collection execution."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CollectionRun(Base):
    __tablename__ = "collection_runs"

    collection_run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    points_imported: Mapped[int] = mapped_column(Integer, default=0)
    vessels_seen: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="running")
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
