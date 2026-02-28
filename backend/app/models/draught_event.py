"""Draught change event - records significant draught changes for corroborating detection."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, Float, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DraughtChangeEvent(Base):
    __tablename__ = "draught_change_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    old_draught_m: Mapped[float] = mapped_column(Float, nullable=False)
    new_draught_m: Mapped[float] = mapped_column(Float, nullable=False)
    delta_m: Mapped[float] = mapped_column(Float, nullable=False)  # signed
    nearest_port_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("ports.port_id"), nullable=True)
    distance_to_port_nm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_offshore: Mapped[bool] = mapped_column(Boolean, default=False)
    linked_gap_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=True)
    linked_sts_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("sts_transfer_events.sts_id"), nullable=True)
    risk_score_component: Mapped[int] = mapped_column(Integer, default=0)
