"""SatelliteBulkOrder entity -- batch satellite imagery order management."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class SatelliteBulkOrder(Base):
    __tablename__ = "satellite_bulk_orders"

    bulk_order_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", index=True)
    # draft / queued / processing / completed / cancelled
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    total_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    submitted_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_total_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, onupdate=func.now()
    )

    items: Mapped[list["SatelliteBulkOrderItem"]] = relationship(
        "SatelliteBulkOrderItem", back_populates="bulk_order", cascade="all, delete-orphan"
    )
