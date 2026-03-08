"""MovementEnvelope entity — computed gap uncertainty polygon for map visualization."""

from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, EstimatedMethodEnum


class MovementEnvelope(Base):
    __tablename__ = "movement_envelopes"

    envelope_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gap_event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=False, index=True
    )
    max_plausible_distance_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_gap_distance_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    velocity_plausibility_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Rotated ellipse parameters (Phase 2.2)
    envelope_semi_major_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    envelope_semi_minor_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    envelope_heading_degrees: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_ellipse_geometry: Mapped[str | None] = mapped_column(Text, nullable=True)
    interpolated_positions_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    estimated_method: Mapped[str | None] = mapped_column(
        SAEnum(EstimatedMethodEnum), nullable=True, default=EstimatedMethodEnum.LINEAR
    )

    gap_event: Mapped[AISGapEvent] = relationship(
        "AISGapEvent", back_populates="movement_envelopes"
    )
