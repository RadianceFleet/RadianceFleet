"""CaseAlert — junction table linking investigation cases to alerts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CaseAlert(Base):
    __tablename__ = "case_alerts"
    __table_args__ = (UniqueConstraint("case_id", "alert_id", name="uq_case_alert"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("investigation_cases.case_id"), nullable=False, index=True
    )
    alert_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False, index=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    added_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True
    )
