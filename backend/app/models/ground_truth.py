"""Ground truth model — confirmed shadow fleet / clean vessel records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GroundTruthVessel(Base):
    __tablename__ = "ground_truth_vessels"
    __table_args__ = (UniqueConstraint("imo", "source", name="uq_ground_truth_imo_source"),)

    ground_truth_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    imo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mmsi: Mapped[str | None] = mapped_column(String(20), nullable=True)
    vessel_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expected_band: Mapped[str] = mapped_column(String(20), nullable=False)
    is_shadow_fleet: Mapped[bool] = mapped_column(Boolean, nullable=False)
    date_listed: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    vessel_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
