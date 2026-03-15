"""VerificationChecklist entity — evidence review checklist for alerts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VerificationChecklist(Base):
    __tablename__ = "verification_checklists"
    __table_args__ = (
        UniqueConstraint("alert_id", "checklist_template", name="uq_checklist_alert_template"),
    )

    checklist_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False, index=True
    )
    checklist_template: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
