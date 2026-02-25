"""MovementEnvelope entity — computed gap uncertainty polygon for map visualization."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, Float, String, JSON, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from geoalchemy2 import Geometry
from app.models.base import Base, EstimatedMethodEnum


class MovementEnvelope(Base):
    __tablename__ = "movement_envelopes"

    envelope_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gap_event_id: Mapped[int] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False, index=True)
    max_plausible_distance_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_gap_distance_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    velocity_plausibility_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Rotated ellipse parameters (Phase 2.2)
    envelope_semi_major_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    envelope_semi_minor_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    envelope_heading_degrees: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence_ellipse_geometry: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326), nullable=True
    )
    interpolated_positions_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    estimated_method: Mapped[Optional[str]] = mapped_column(
        SAEnum(EstimatedMethodEnum), nullable=True, default=EstimatedMethodEnum.LINEAR
    )

    gap_event: Mapped["AISGapEvent"] = relationship("AISGapEvent", back_populates="movement_envelopes")
