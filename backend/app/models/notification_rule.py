"""NotificationRule entity — configurable alert routing rules."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NotificationRule(Base):
    __tablename__ = "notification_rules"

    rule_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=func.now(), nullable=True
    )

    # Conditions — all nullable = match any, AND logic
    min_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corridor_ids_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vessel_flags_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    alert_statuses_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vessel_types_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scoring_signals_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    time_window_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    time_window_end: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # Action
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    destination: Mapped[str] = mapped_column(String(500), nullable=False)
    message_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    throttle_minutes: Mapped[int] = mapped_column(Integer, default=30)
