"""SatelliteOrderLog entity -- audit trail for satellite order API interactions."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, Float, String, DateTime, ForeignKey, Text, JSON, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SatelliteOrderLog(Base):
    __tablename__ = "satellite_order_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    satellite_order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("satellite_orders.satellite_order_id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    request_time_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    request_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    order: Mapped["SatelliteOrder"] = relationship("SatelliteOrder", back_populates="logs")
