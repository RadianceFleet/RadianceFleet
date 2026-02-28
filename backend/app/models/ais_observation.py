"""Raw per-source AIS observation storage.

Separate from ais_points (which is deduplicated). This table keeps ALL observations
from ALL sources, enabling cross-receiver comparison for handshake detection and
fake port call identification.

Retention: 72h rolling window (cross-receiver comparison only needs recent data).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Index
from sqlalchemy.orm import Session

from app.models.base import Base


class AISObservation(Base):
    __tablename__ = "ais_observations"

    observation_id = Column(Integer, primary_key=True, autoincrement=True)
    mmsi = Column(String(9), nullable=False, index=True)
    source = Column(String(50), nullable=False)  # "aisstream", "kystverket", "digitraffic", "aishub"
    received_utc = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    timestamp_utc = Column(DateTime, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    sog = Column(Float, nullable=True)
    cog = Column(Float, nullable=True)
    heading = Column(Float, nullable=True)
    raw_data = Column(Text, nullable=True)
    draught = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_ais_obs_mmsi_ts", "mmsi", "timestamp_utc"),
        Index("ix_ais_obs_received_utc", "received_utc"),
    )

    @staticmethod
    def purge_old(db: Session, hours: int = 72) -> int:
        """Delete observations older than `hours`. Returns count deleted.

        Note: Does NOT commit the transaction. The caller is responsible
        for calling db.commit() when ready.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        count = db.query(AISObservation).filter(
            AISObservation.received_utc < cutoff
        ).delete()
        return count
