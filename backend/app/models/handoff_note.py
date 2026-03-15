"""HandoffNote entity — analyst-to-analyst alert handoff with notes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class HandoffNote(Base):
    __tablename__ = "handoff_notes"

    handoff_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False, index=True
    )
    from_analyst_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=False, index=True
    )
    to_analyst_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    from_analyst = relationship(
        "Analyst",
        foreign_keys=[from_analyst_id],
        lazy="joined",
    )
    to_analyst = relationship(
        "Analyst",
        foreign_keys=[to_analyst_id],
        lazy="joined",
    )
