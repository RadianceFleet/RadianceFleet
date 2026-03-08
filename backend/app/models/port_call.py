"""PortCall entity — records vessel arrivals/departures at ports."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PortCall(Base):
    __tablename__ = "port_calls"

    port_call_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    port_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ports.port_id"), nullable=True, index=True
    )
    arrival_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    departure_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_port_name: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, default="manual", nullable=False)
