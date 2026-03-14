"""IsolationForestAnomaly — multi-feature anomaly detection results."""

from __future__ import annotations

import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, JSON, String

from app.models.base import Base


class IsolationForestAnomaly(Base):
    __tablename__ = "isolation_forest_anomalies"

    anomaly_id = Column(Integer, primary_key=True, autoincrement=True)
    vessel_id = Column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    anomaly_score = Column(Float)  # 0-1, higher = more anomalous
    path_length_mean = Column(Float)  # average path length across ensemble
    risk_score_component = Column(Integer)  # points added to risk score
    tier = Column(String(10))  # "high", "medium", "low"
    feature_vector_json = Column(JSON)  # 13-feature dict
    top_features_json = Column(JSON)  # top 3 most anomalous features
    evidence_json = Column(JSON)  # additional context
    created_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
