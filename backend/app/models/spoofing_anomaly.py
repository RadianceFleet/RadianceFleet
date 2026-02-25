"""SpoofingAnomaly entity — AIS spoofing detection events."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, Float, DateTime, JSON, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, SpoofingTypeEnum


class SpoofingAnomaly(Base):
    __tablename__ = "spoofing_anomalies"

    anomaly_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    # FK to the gap event this anomaly is most directly linked to (prevents double-counting)
    gap_event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=True, index=True
    )
    anomaly_type: Mapped[str] = mapped_column(SAEnum(SpoofingTypeEnum), nullable=False)
    start_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    evidence_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    implied_speed_kn: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    plausibility_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_score_component: Mapped[int] = mapped_column(Integer, default=0)

    vessel: Mapped["Vessel"] = relationship("Vessel")
