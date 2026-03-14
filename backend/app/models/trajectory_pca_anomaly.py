"""TrajectoryPcaAnomaly — PCA-based trajectory reconstruction error anomalies."""

from __future__ import annotations

import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.models.base import Base


class TrajectoryPcaAnomaly(Base):
    __tablename__ = "trajectory_pca_anomalies"

    anomaly_id = Column(Integer, primary_key=True, autoincrement=True)
    vessel_id = Column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    segment_start = Column(DateTime)
    segment_end = Column(DateTime)
    reconstruction_error = Column(Float)  # SPE (Squared Prediction Error)
    anomaly_score = Column(Float)  # percentile-ranked 0-1
    risk_score_component = Column(Float)
    tier = Column(String(10))  # "high", "medium", "low"
    feature_vector_json = Column(Text)  # JSON string of the 8 features
    principal_components_json = Column(Text)  # JSON string of PC matrix
    top_error_features_json = Column(Text)  # JSON string of top contributing features
    evidence_json = Column(Text)  # JSON string with detection metadata
    created_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
