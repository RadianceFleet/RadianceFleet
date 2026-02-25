"""SatelliteCheck entity — Sentinel-1 scene query package."""
from __future__ import annotations

from typing import Optional
from sqlalchemy import Integer, String, Float, JSON, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, SatelliteReviewStatusEnum


class SatelliteCheck(Base):
    __tablename__ = "satellite_checks"

    sat_check_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gap_event_id: Mapped[int] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False, index=True)
    provider: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, default="Sentinel-1")
    query_time_window: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    query_geometry: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    scene_refs_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str] = mapped_column(
        SAEnum(SatelliteReviewStatusEnum), nullable=False, default=SatelliteReviewStatusEnum.NOT_CHECKED
    )
    review_notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    imagery_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sentinel_scene_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    image_quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cloud_cover_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    gap_event: Mapped["AISGapEvent"] = relationship("AISGapEvent", back_populates="satellite_checks")
