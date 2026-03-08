"""Vessel entity — core vessel identity and characteristics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import AISClassEnum, Base, FlagRiskEnum, PIStatusEnum


class Vessel(Base):
    __tablename__ = "vessels"
    __table_args__ = (
        CheckConstraint("vessel_id != merged_into_vessel_id", name="ck_no_self_merge"),
    )

    vessel_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mmsi: Mapped[str] = mapped_column(String(9), unique=True, nullable=False, index=True)
    imo: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    flag: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vessel_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deadweight: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_heuristic_dwt: Mapped[bool] = mapped_column(Boolean, default=False)
    year_built: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ais_class: Mapped[str | None] = mapped_column(
        SAEnum(AISClassEnum), nullable=True, default=AISClassEnum.UNKNOWN
    )
    flag_risk_category: Mapped[str | None] = mapped_column(
        SAEnum(FlagRiskEnum), nullable=True, default=FlagRiskEnum.UNKNOWN
    )
    pi_coverage_status: Mapped[str | None] = mapped_column(
        SAEnum(PIStatusEnum), nullable=True, default=PIStatusEnum.UNKNOWN
    )
    psc_detained_last_12m: Mapped[bool] = mapped_column(Boolean, default=False)
    psc_major_deficiencies_last_12m: Mapped[int] = mapped_column(Integer, default=0)
    # Set on first ingestion, never updated — enables new-MMSI scoring (Phase 6.10)
    mmsi_first_seen_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    callsign: Mapped[str | None] = mapped_column(String(20), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ais_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Laid-up detection flags (Phase 4.2)
    vessel_laid_up_30d: Mapped[bool] = mapped_column(Boolean, default=False)
    vessel_laid_up_60d: Mapped[bool] = mapped_column(Boolean, default=False)
    vessel_laid_up_in_sts_zone: Mapped[bool] = mapped_column(Boolean, default=False)
    # Identity merge: points to canonical vessel when this identity was absorbed
    merged_into_vessel_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=True, index=True
    )
    last_ais_received_utc: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Stage 1-B: multi-signal confidence classification
    dark_fleet_confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence_evidence_json: Mapped[str | None] = mapped_column(String, nullable=True)
    # AIS cargo type parsed from AIS Message Type 5 ship_type field
    ais_cargo_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Watchlist stub scoring — for vessels with no AIS history
    watchlist_stub_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    watchlist_stub_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    ais_points: Mapped[list] = relationship(
        "AISPoint", back_populates="vessel", cascade="all, delete-orphan"
    )
    gap_events: Mapped[list] = relationship(
        "AISGapEvent", back_populates="vessel", cascade="all, delete-orphan"
    )
    history: Mapped[list] = relationship(
        "VesselHistory", back_populates="vessel", cascade="all, delete-orphan"
    )
    watchlist_entries: Mapped[list] = relationship(
        "VesselWatchlist", back_populates="vessel", cascade="all, delete-orphan"
    )
    sts_events_as_vessel_1: Mapped[list] = relationship(
        "StsTransferEvent",
        foreign_keys="StsTransferEvent.vessel_1_id",
        back_populates="vessel_1",
        cascade="all, delete-orphan",
    )
    sts_events_as_vessel_2: Mapped[list] = relationship(
        "StsTransferEvent",
        foreign_keys="StsTransferEvent.vessel_2_id",
        back_populates="vessel_2",
        cascade="all, delete-orphan",
    )
    fingerprints: Mapped[list] = relationship("VesselFingerprint", back_populates="vessel")
    psc_detentions: Mapped[list[PscDetention]] = relationship(
        "PscDetention", back_populates="vessel", cascade="all, delete-orphan"
    )
