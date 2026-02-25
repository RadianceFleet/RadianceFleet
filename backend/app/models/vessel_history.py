"""VesselHistory entity — tracks identity changes (renames, flag changes, etc.)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class VesselHistory(Base):
    __tablename__ = "vessel_history"
    __table_args__ = (
        UniqueConstraint(
            "vessel_id", "field_changed", "old_value", "new_value", "observed_at",
            name="uq_vessel_history_dedup"
        ),
    )

    vessel_history_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    field_changed: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    source: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    vessel: Mapped["Vessel"] = relationship("Vessel", back_populates="history")
