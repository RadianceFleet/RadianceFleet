"""VesselWatchlist entity — sanctions/shadow fleet list cross-reference."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Integer, String, Boolean, Date, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base


class VesselWatchlist(Base):
    __tablename__ = "vessel_watchlist"
    __table_args__ = (
        UniqueConstraint("vessel_id", "watchlist_source", name="uq_watchlist_vessel_source"),
    )

    watchlist_entry_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    watchlist_source: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    date_listed: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    match_confidence: Mapped[int] = mapped_column(Integer, default=0)
    match_type: Mapped[str] = mapped_column(String(50), default="unknown")

    vessel: Mapped["Vessel"] = relationship("Vessel", back_populates="watchlist_entries")
