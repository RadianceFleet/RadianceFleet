"""Verification log for pay-per-query external data lookups.

Tracks every paid API call for budget management and audit trail.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VerificationLog(Base):
    __tablename__ = "verification_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    request_time_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    response_status: Mapped[str] = mapped_column(String(20), nullable=False)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
