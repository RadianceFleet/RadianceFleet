"""TrajectoryAutoencoderAnomaly — autoencoder-based trajectory anomaly detection results."""

from __future__ import annotations

import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.models.base import Base


class TrajectoryAutoencoderAnomaly(Base):
    __tablename__ = "trajectory_autoencoder_anomalies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vessel_id = Column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    segment_start = Column(DateTime)
    segment_end = Column(DateTime)
    reconstruction_error = Column(Float)
    anomaly_score = Column(Float)
    tier = Column(String(10))  # HIGH / MEDIUM / LOW
    feature_vector_json = Column(Text)  # JSON string of input features
    reconstructed_vector_json = Column(Text)  # JSON string of reconstructed features
    bottleneck_json = Column(Text)  # JSON string of bottleneck layer values
    evidence_json = Column(Text)  # JSON string with details
    created_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
