"""Webhook model — registered webhook endpoints for event notifications."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String

from app.models.base import Base


class Webhook(Base):
    __tablename__ = "webhooks"

    webhook_id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(String, nullable=False)
    events = Column(String, default="critical_alert")
    secret = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("analysts.analyst_id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
