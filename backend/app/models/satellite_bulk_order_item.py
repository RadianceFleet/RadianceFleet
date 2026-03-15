"""SatelliteBulkOrderItem entity -- individual item within a bulk satellite order."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SatelliteBulkOrderItem(Base):
    __tablename__ = "satellite_bulk_order_items"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bulk_order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("satellite_bulk_orders.bulk_order_id"), nullable=False, index=True
    )
    satellite_order_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("satellite_orders.satellite_order_id"), nullable=True, index=True
    )
    priority_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    alert_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ais_gap_events.gap_event_id"), nullable=True
    )
    provider_preference: Mapped[str | None] = mapped_column(String(50), nullable=True)
    aoi_wkt: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # pending / queued / submitted / delivered / failed / skipped
    skip_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())

    bulk_order: Mapped["SatelliteBulkOrder"] = relationship(
        "SatelliteBulkOrder", back_populates="items"
    )
