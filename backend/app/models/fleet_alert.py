"""FleetAlert entity — fleet-level behavioural pattern alerts."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Integer, String, DateTime, JSON, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class FleetAlert(Base):
    __tablename__ = "fleet_alerts"
    __table_args__ = (
        UniqueConstraint("owner_cluster_id", "alert_type", name="uq_fleet_alert_cluster_type"),
    )

    alert_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_cluster_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("owner_clusters.cluster_id"), nullable=True
    )
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    vessel_ids_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    evidence_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    risk_score_component: Mapped[int] = mapped_column(Integer, default=0)
    created_utc: Mapped[datetime] = mapped_column(DateTime, default=func.now())
