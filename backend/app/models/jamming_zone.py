"""GPS Jamming Zone models — detected zones of concentrated AIS gaps
suggesting GPS jamming activity."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.base import Base


class JammingZone(Base):
    """A spatial-temporal cluster of AIS gaps suggesting GPS jamming."""

    __tablename__ = "jamming_zones"

    zone_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geometry: Mapped[str | None] = mapped_column(Text, nullable=True)  # WKT polygon
    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_nm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    vessel_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_detected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_gap_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Status: "active", "decaying", "expired"
    # NOTE: When jamming zone enums are consolidated, add JammingZoneStatusEnum to base.py
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    detection_window_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=168)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    gap_links: Mapped[list[JammingZoneGap]] = relationship(
        "JammingZoneGap", back_populates="zone", cascade="all, delete-orphan"
    )


class JammingZoneGap(Base):
    """Link table between JammingZone and AISGapEvent."""

    __tablename__ = "jamming_zone_gaps"
    __table_args__ = (
        UniqueConstraint("zone_id", "gap_event_id", name="uq_jamming_zone_gap"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    zone_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("jamming_zones.zone_id"),
        nullable=False,
        index=True,
    )
    gap_event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("ais_gap_events.gap_event_id"),
        nullable=False,
        index=True,
    )

    zone: Mapped[JammingZone] = relationship("JammingZone", back_populates="gap_links")
