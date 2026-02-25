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
    corridor_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("corridors.corridor_id"), nullable=True)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    risk_breakdown_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum(AlertStatusEnum), nullable=False, default=AlertStatusEnum.NEW
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

    vessel: Mapped["Vessel"] = relationship("Vessel", back_populates="gap_events")
    corridor: Mapped[Optional["Corridor"]] = relationship("Corridor", back_populates="gap_events")
    satellite_checks: Mapped[list] = relationship("SatelliteCheck", back_populates="gap_event")
    movement_envelopes: Mapped[list] = relationship("MovementEnvelope", back_populates="gap_event")
    evidence_cards: Mapped[list] = relationship("EvidenceCard", back_populates="gap_event")
