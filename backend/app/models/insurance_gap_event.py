"""InsuranceGapEvent entity — P&I club coverage gap timeline records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base


class InsuranceGapEvent(Base):
    __tablename__ = "insurance_gap_events"
    __table_args__ = (
        UniqueConstraint(
            "vessel_id",
            "gap_start_utc",
            name="uq_insurance_gap_vessel_start",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    gap_start_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    gap_end_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    gap_days: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_club: Mapped[str | None] = mapped_column(String(200), nullable=True)
    next_club: Mapped[str | None] = mapped_column(String(200), nullable=True)
    previous_club_is_ig: Mapped[bool] = mapped_column(Boolean, default=False)
    next_club_is_ig: Mapped[bool] = mapped_column(Boolean, default=False)
    coincides_with_flag_change: Mapped[bool] = mapped_column(Boolean, default=False)
    coincides_with_ownership_change: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_score_component: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())

    vessel: Mapped[Vessel] = relationship("Vessel")
