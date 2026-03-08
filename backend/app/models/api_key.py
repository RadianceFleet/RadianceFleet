"""ApiKey entity — read-only API keys for public integrations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    key_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(50), nullable=False, default="read_only")
    rate_limit: Mapped[str] = mapped_column(String(50), nullable=False, default="30/minute")
    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
