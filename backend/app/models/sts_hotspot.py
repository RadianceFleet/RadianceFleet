"""STS Transfer Hotspot — detected geographic clusters of ship-to-ship transfers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StsHotspot(Base):
    __tablename__ = "sts_hotspots"

    hotspot_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_nm: Mapped[float] = mapped_column(Float, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    trend: Mapped[str] = mapped_column(String(20), nullable=False, default="stable")
    trend_slope: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    corridor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("corridors.corridor_id"), nullable=True
    )
    risk_score_component: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
