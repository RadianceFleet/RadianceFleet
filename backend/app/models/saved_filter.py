"""SavedFilter — persisted alert filter configurations per analyst."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Integer, String, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class SavedFilter(Base):
    __tablename__ = "saved_filters"

    filter_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analyst_id: Mapped[int] = mapped_column(Integer, ForeignKey("analysts.analyst_id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    filter_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
