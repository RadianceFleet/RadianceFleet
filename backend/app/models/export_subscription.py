"""ExportSubscription entity — recurring bulk data export subscriptions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base


class ExportSubscription(Base):
    __tablename__ = "export_subscriptions"

    subscription_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysts.analyst_id"), nullable=False
    )
    schedule: Mapped[str] = mapped_column(String(20), nullable=False)  # daily/weekly/monthly
    schedule_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_hour_utc: Mapped[int] = mapped_column(Integer, default=6)
    export_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # alerts/vessels/ais_positions/evidence_cards
    filter_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    columns_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    format: Mapped[str] = mapped_column(String(10), nullable=False)  # csv/json/parquet
    delivery_method: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # email/s3/webhook
    delivery_config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_run_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=func.now())

    runs: Mapped[list[ExportRun]] = relationship("ExportRun", back_populates="subscription")
