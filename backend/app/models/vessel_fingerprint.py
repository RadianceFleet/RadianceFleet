"""VesselFingerprint — behavioral fingerprint for vessel identity corroboration."""

from __future__ import annotations

import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import Base


class VesselFingerprint(Base):
    __tablename__ = "vessel_fingerprints"

    fingerprint_id = Column(Integer, primary_key=True, autoincrement=True)
    vessel_id = Column(Integer, ForeignKey("vessels.vessel_id"), nullable=False, index=True)
    operational_state = Column(String(20))  # "ballast" or "laden" or "unknown"
    feature_vector_json = Column(JSON)  # 10-feature dict
    covariance_json = Column(JSON)  # covariance matrix (10x10 list of lists)
    sample_count = Column(Integer)  # number of 6h windows used
    point_count = Column(Integer)  # total AIS points used
    is_diagonal_only = Column(Boolean, default=False)  # True if <10 windows
    created_at = Column(
        DateTime,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    updated_at = Column(DateTime, nullable=True)

    vessel = relationship("Vessel", back_populates="fingerprints")
