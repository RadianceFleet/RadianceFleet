"""AlertGroup entity — deduplication group for related AIS gap alerts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AlertGroup(Base):
    __tablename__ = "alert_groups"

    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    corridor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("corridors.corridor_id"), nullable=True, index=True
    )
    group_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    primary_alert_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=True
    )
    alert_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    max_risk_score: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())

    vessel: Mapped[Vessel] = relationship("Vessel")
    corridor: Mapped[Corridor | None] = relationship("Corridor")
    primary_alert: Mapped[AISGapEvent | None] = relationship(
        "AISGapEvent", foreign_keys=[primary_alert_id]
    )
