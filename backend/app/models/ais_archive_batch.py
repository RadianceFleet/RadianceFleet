"""AisArchiveBatch entity — tracks archived AIS point batches."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AisArchiveBatch(Base):
    __tablename__ = "ais_archive_batches"

    batch_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archive_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    date_range_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    compression: Mapped[str] = mapped_column(String(20), nullable=False, default="gzip")
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")
    source_filter: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
