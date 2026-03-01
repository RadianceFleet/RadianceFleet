"""AISPoint entity — individual AIS broadcast records."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, Float, String, DateTime, ForeignKey, Enum as SAEnum, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, AISClassEnum


class AISPoint(Base):
    __tablename__ = "ais_points"
    __table_args__ = (
        CheckConstraint("lat >= -90 AND lat <= 90", name="ck_ais_lat_bounds"),
        CheckConstraint("lon >= -180 AND lon <= 180", name="ck_ais_lon_bounds"),
        Index("ix_ais_vessel_ts", "vessel_id", "timestamp_utc"),
    )

    ais_point_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    sog: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cog: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    heading: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    nav_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ais_class: Mapped[Optional[str]] = mapped_column(
        SAEnum(AISClassEnum), nullable=True, default=AISClassEnum.A
    )
    sog_delta: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cog_delta: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    raw_payload_ref: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    draught: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    destination: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    vessel: Mapped["Vessel"] = relationship("Vessel", back_populates="ais_points")
