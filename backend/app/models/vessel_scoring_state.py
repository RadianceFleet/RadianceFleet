"""VesselScoringState entity — tracks incremental scoring state per vessel."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class VesselScoringState(Base):
    __tablename__ = "vessel_scoring_states"

    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), primary_key=True
    )
    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_ais_point_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_gap_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scoring_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    vessel: Mapped["Vessel"] = relationship("Vessel")
