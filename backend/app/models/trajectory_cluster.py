"""TrajectoryCluster entity — DBSCAN trajectory cluster metadata."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TrajectoryCluster(Base):
    __tablename__ = "trajectory_clusters"

    cluster_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[int] = mapped_column(Integer, nullable=False)
    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lon: Mapped[float] = mapped_column(Float, nullable=False)
    radius_nm: Mapped[float] = mapped_column(Float, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    is_anomalous: Mapped[bool] = mapped_column(Boolean, default=False)
    anomaly_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
