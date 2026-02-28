"""Vessel entity — core vessel identity and characteristics."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Enum as SAEnum, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, AISClassEnum, FlagRiskEnum, PIStatusEnum


class Vessel(Base):
    __tablename__ = "vessels"
    __table_args__ = (
        CheckConstraint("vessel_id != merged_into_vessel_id", name="ck_no_self_merge"),
    )

    vessel_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String(9), unique=True, nullable=False, index=True)
    imo: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    flag: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    vessel_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    deadweight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    year_built: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ais_class: Mapped[Optional[str]] = mapped_column(
        SAEnum(AISClassEnum), nullable=True, default=AISClassEnum.UNKNOWN
    )
    flag_risk_category: Mapped[Optional[str]] = mapped_column(
        SAEnum(FlagRiskEnum), nullable=True, default=FlagRiskEnum.UNKNOWN
    )
    pi_coverage_status: Mapped[Optional[str]] = mapped_column(
        SAEnum(PIStatusEnum), nullable=True, default=PIStatusEnum.UNKNOWN
    )
    psc_detained_last_12m: Mapped[bool] = mapped_column(Boolean, default=False)
    psc_major_deficiencies_last_12m: Mapped[int] = mapped_column(Integer, default=0)
    # Set on first ingestion, never updated — enables new-MMSI scoring (Phase 6.10)
    mmsi_first_seen_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    callsign: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    owner_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ais_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Laid-up detection flags (Phase 4.2)
    vessel_laid_up_30d: Mapped[bool] = mapped_column(Boolean, default=False)
    vessel_laid_up_60d: Mapped[bool] = mapped_column(Boolean, default=False)
    vessel_laid_up_in_sts_zone: Mapped[bool] = mapped_column(Boolean, default=False)
    # Identity merge: points to canonical vessel when this identity was absorbed
    merged_into_vessel_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=True, index=True
    )
    last_ais_received_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    # Relationships
    ais_points: Mapped[list] = relationship("AISPoint", back_populates="vessel", cascade="all, delete-orphan")
    gap_events: Mapped[list] = relationship("AISGapEvent", back_populates="vessel", cascade="all, delete-orphan")
    history: Mapped[list] = relationship("VesselHistory", back_populates="vessel", cascade="all, delete-orphan")
    watchlist_entries: Mapped[list] = relationship("VesselWatchlist", back_populates="vessel", cascade="all, delete-orphan")
    sts_events_as_vessel_1: Mapped[list] = relationship(
        "StsTransferEvent", foreign_keys="StsTransferEvent.vessel_1_id",
        back_populates="vessel_1", cascade="all, delete-orphan"
    )
    sts_events_as_vessel_2: Mapped[list] = relationship(
        "StsTransferEvent", foreign_keys="StsTransferEvent.vessel_2_id",
        back_populates="vessel_2", cascade="all, delete-orphan"
    )
