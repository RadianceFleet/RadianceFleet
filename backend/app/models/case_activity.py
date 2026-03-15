"""CaseActivity — audit trail for case actions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CaseActivity(Base):
    __tablename__ = "case_activities"

    activity_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("investigation_cases.case_id"), nullable=False, index=True
    )
    analyst_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # created, assigned, alert_added, alert_removed, status_changed, handoff, note_added
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), nullable=False
    )
