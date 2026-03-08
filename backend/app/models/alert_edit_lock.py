"""AlertEditLock — pessimistic edit lock for concurrent alert triage."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class AlertEditLock(Base):
    __tablename__ = "alert_edit_locks"
    __table_args__ = (
        UniqueConstraint("alert_id", name="uq_alert_edit_lock_alert"),
    )

    lock_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False, unique=True)
    analyst_id: Mapped[int] = mapped_column(Integer, ForeignKey("analysts.analyst_id"), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    analyst: Mapped["Analyst"] = relationship("Analyst")
