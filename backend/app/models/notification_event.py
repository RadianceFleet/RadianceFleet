"""NotificationEvent — SQLite-backed notification queue for collaboration events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NotificationEvent(Base):
    __tablename__ = "notification_events"

    event_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    target_analyst_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # event_type values: assignment, handoff, viewer_join, viewer_leave, case_update
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
