"""CreaVoyage model — persisted CREA Russia Fossil Tracker voyage data."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class CreaVoyage(Base):
    __tablename__ = "crea_voyages"
    __table_args__ = (
        UniqueConstraint("vessel_id", "departure_port", "departure_date", name="uq_crea_voyage_dedup"),
    )

    voyage_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    departure_port: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    arrival_port: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    commodity: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cargo_volume_tonnes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    departure_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    arrival_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    import_run_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
