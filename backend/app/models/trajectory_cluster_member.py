"""TrajectoryClusterMember entity — links vessel segments to trajectory clusters."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TrajectoryClusterMember(Base):
    __tablename__ = "trajectory_cluster_members"

    member_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cluster_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("trajectory_clusters.cluster_id"), nullable=False, index=True
    )
    vessel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True
    )
    segment_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    segment_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    start_lat: Mapped[float] = mapped_column(Float, nullable=False)
    start_lon: Mapped[float] = mapped_column(Float, nullable=False)
    end_lat: Mapped[float] = mapped_column(Float, nullable=False)
    end_lon: Mapped[float] = mapped_column(Float, nullable=False)
    bearing: Mapped[float] = mapped_column(Float, nullable=False)
    distance_nm: Mapped[float] = mapped_column(Float, nullable=False)
    mean_sog: Mapped[float | None] = mapped_column(Float, nullable=True)
    straightness_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_noise: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_score_component: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
