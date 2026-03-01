"""AISGapEvent entity — detected AIS transmission gap."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, Float, Boolean, DateTime, String, JSON, ForeignKey, Enum as SAEnum, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, AlertStatusEnum


class AISGapEvent(Base):
    __tablename__ = "ais_gap_events"

    gap_event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    start_point_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("ais_points.ais_point_id"), nullable=True)
    end_point_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("ais_points.ais_point_id"), nullable=True)
    gap_start_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    gap_end_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    corridor_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("corridors.corridor_id"), nullable=True, index=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    risk_breakdown_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum(AlertStatusEnum), nullable=False, default=AlertStatusEnum.NEW, index=True
    )
    analyst_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    impossible_speed_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    velocity_plausibility_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_plausible_distance_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_gap_distance_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    in_dark_zone: Mapped[bool] = mapped_column(Boolean, default=False)
    dark_zone_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("dark_zones.zone_id"), nullable=True)
    pre_gap_sog: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # SOG of the last AIS point before the gap; captured at detection time

    # GFW gap event fields — positions where AIS went off/on (null for local-AIS gaps)
    gap_off_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gap_off_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gap_on_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gap_on_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Provenance: "gfw" for GFW-imported, NULL/missing for local AIS detection
    source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Forward provenance: which vessel identity generated this gap.
    # Set at creation time (= vessel_id) and preserved through merges.
    # Used by scoring to count per-identity gap frequency, preventing
    # inflation when merged vessels accumulate gaps from multiple identities.
    original_vessel_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Feed outage: True if this gap was caused by a data feed failure,
    # not by the vessel going dark. Scored gaps with is_feed_outage=True are skipped.
    is_feed_outage: Mapped[bool] = mapped_column(Boolean, default=False)
    # Coverage quality tag from corridor metadata (GOOD/MODERATE/PARTIAL/POOR/NONE/UNKNOWN)
    coverage_quality: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    vessel: Mapped["Vessel"] = relationship("Vessel", back_populates="gap_events")
    corridor: Mapped[Optional["Corridor"]] = relationship("Corridor", back_populates="gap_events")
    start_point: Mapped[Optional["AISPoint"]] = relationship(
        "AISPoint", foreign_keys=[start_point_id], lazy="joined"
    )
    end_point: Mapped[Optional["AISPoint"]] = relationship(
        "AISPoint", foreign_keys=[end_point_id], lazy="joined"
    )
    satellite_checks: Mapped[list] = relationship("SatelliteCheck", back_populates="gap_event", cascade="all, delete-orphan")
    movement_envelopes: Mapped[list] = relationship("MovementEnvelope", back_populates="gap_event", cascade="all, delete-orphan")
    evidence_cards: Mapped[list] = relationship("EvidenceCard", back_populates="gap_event", cascade="all, delete-orphan")
    spoofing_anomalies: Mapped[list] = relationship("SpoofingAnomaly", back_populates="gap_event", cascade="all, delete-orphan")
