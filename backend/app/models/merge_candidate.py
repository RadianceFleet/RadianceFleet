"""MergeCandidate — tracks potential same-vessel pairs across MMSI changes."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Integer, Float, String, DateTime, ForeignKey, JSON,
    Enum as SAEnum, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, MergeCandidateStatusEnum


class MergeCandidate(Base):
    __tablename__ = "merge_candidates"
    __table_args__ = (
        UniqueConstraint("vessel_a_id", "vessel_b_id", name="uq_merge_candidate_pair"),
    )

    candidate_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_a_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    vessel_b_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    # Positions at match time
    vessel_a_last_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vessel_a_last_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vessel_a_last_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    vessel_b_first_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vessel_b_first_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vessel_b_first_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Scoring
    distance_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    time_delta_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    match_reasons_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    satellite_corroboration_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum(MergeCandidateStatusEnum),
        nullable=False,
        default=MergeCandidateStatusEnum.PENDING,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Relationships
    vessel_a: Mapped["Vessel"] = relationship(
        "Vessel", foreign_keys=[vessel_a_id],
    )
    vessel_b: Mapped["Vessel"] = relationship(
        "Vessel", foreign_keys=[vessel_b_id],
    )
