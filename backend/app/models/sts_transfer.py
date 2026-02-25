"""StsTransferEvent entity — detected ship-to-ship transfer events."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import Integer, Float, String, DateTime, ForeignKey, Enum as SAEnum, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, STSDetectionTypeEnum

if TYPE_CHECKING:
    from app.models.vessel import Vessel
    from app.models.corridor import Corridor


class StsTransferEvent(Base):
    __tablename__ = "sts_transfer_events"
    __table_args__ = (
        UniqueConstraint("vessel_1_id", "vessel_2_id", "start_time_utc", name="uq_sts_vessel_pair_time"),
    )

    sts_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_1_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    vessel_2_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    detection_type: Mapped[str] = mapped_column(
        SAEnum(STSDetectionTypeEnum), nullable=False, default=STSDetectionTypeEnum.VISIBLE_VISIBLE
    )
    start_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mean_proximity_meters: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mean_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mean_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    corridor_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("corridors.corridor_id"), nullable=True)
    satellite_confirmation_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # ETA in minutes for approaching-vector detections (Phase 5.1 Phase B)
    eta_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    risk_score_component: Mapped[int] = mapped_column(Integer, default=0)

    vessel_1: Mapped["Vessel"] = relationship(
        "Vessel", foreign_keys=[vessel_1_id], back_populates="sts_events_as_vessel_1"
    )
    vessel_2: Mapped["Vessel"] = relationship(
        "Vessel", foreign_keys=[vessel_2_id], back_populates="sts_events_as_vessel_2"
    )
    corridor: Mapped[Optional["Corridor"]] = relationship("Corridor")
