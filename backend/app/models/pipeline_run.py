"""PipelineRun entity — tracks each discovery pipeline execution.

Records per-detector anomaly counts, data volume metrics, and drift
state so that consecutive runs can detect anomaly count drift and
auto-disable scoring for drifting detectors.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Per-detector anomaly counts: {"gap_detector": 45, "spoofing_detector": 12, ...}
    detector_anomaly_counts_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Data volume: {"ais_points_count": 5000, "vessels_count": 200}
    data_volume_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Detectors whose scoring was auto-disabled due to drift
    drift_disabled_detectors_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # "running", "completed", "failed"
    status: Mapped[str] = mapped_column(String(20), default="running")
