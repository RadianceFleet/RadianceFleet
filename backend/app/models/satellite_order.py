"""SatelliteOrder entity -- commercial satellite imagery order tracking."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SatelliteOrder(Base):
    __tablename__ = "satellite_orders"

    satellite_order_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sat_check_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("satellite_checks.sat_check_id"), nullable=True, index=True
    )
    tasking_candidate_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("satellite_tasking_candidates.candidate_id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    order_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # archive_search, new_tasking
    external_order_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    # draft/submitted/accepted/processing/delivered/failed/cancelled
    aoi_wkt: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    time_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    product_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_confirmed: Mapped[bool] = mapped_column(default=False)
    scene_urls_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_utc: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=func.now(), onupdate=func.now()
    )

    logs: Mapped[list[SatelliteOrderLog]] = relationship(
        "SatelliteOrderLog", back_populates="order", cascade="all, delete-orphan"
    )
