"""Cross-receiver AIS position validation.

Detects when the same MMSI reports inconsistent positions across different
AIS data sources within a short time window. This is a statistical signal
for AIS handshake (identity swap) and fake port call spoofing.

Requires multi-source AIS data in the ais_observations table.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.ais_observation import AISObservation
from app.models.base import SpoofingTypeEnum
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)


def detect_cross_receiver_anomalies(
    db: Session,
    time_window_minutes: int = 10,
    distance_threshold_nm: float = 5.0,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Detect position disagreements across AIS sources.

    For each MMSI, finds observations from different sources within
    time_window_minutes. If the positions disagree by more than
    distance_threshold_nm, creates a SpoofingAnomaly.

    Returns:
        {"anomalies_created": N, "mmsis_checked": M}
    """
    # Load observations within date range
    query = db.query(AISObservation).order_by(
        AISObservation.mmsi, AISObservation.timestamp_utc
    )
    if date_from:
        query = query.filter(
            AISObservation.timestamp_utc >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to:
        query = query.filter(
            AISObservation.timestamp_utc <= datetime.combine(date_to, datetime.max.time())
        )

    observations = query.all()
    if not observations:
        logger.info("Cross-receiver: no observations found.")
        return {"anomalies_created": 0, "mmsis_checked": 0}

    # Group by MMSI
    by_mmsi: dict[str, list[AISObservation]] = defaultdict(list)
    for obs in observations:
        by_mmsi[obs.mmsi].append(obs)

    anomalies_created = 0
    window = timedelta(minutes=time_window_minutes)

    for mmsi, obs_list in by_mmsi.items():
        # Need at least 2 sources
        sources = {o.source for o in obs_list}
        if len(sources) < 2:
            continue

        # Resolve vessel (required: vessel_id is NOT NULL on SpoofingAnomaly)
        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
        if not vessel:
            continue

        # Compare observations from different sources within time window
        for i in range(len(obs_list)):
            for j in range(i + 1, len(obs_list)):
                o1, o2 = obs_list[i], obs_list[j]
                if o1.source == o2.source:
                    continue

                time_diff = abs((o1.timestamp_utc - o2.timestamp_utc).total_seconds())
                if time_diff > window.total_seconds():
                    continue

                dist_nm = haversine_nm(o1.lat, o1.lon, o2.lat, o2.lon)
                if dist_nm <= distance_threshold_nm:
                    continue

                # Position disagreement detected!
                # Check if already reported
                existing = db.query(SpoofingAnomaly).filter(
                    SpoofingAnomaly.vessel_id == vessel.vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.CROSS_RECEIVER_DISAGREEMENT,
                    SpoofingAnomaly.start_time_utc == min(o1.timestamp_utc, o2.timestamp_utc),
                ).first()
                if existing:
                    continue

                anomaly = SpoofingAnomaly(
                    vessel_id=vessel.vessel_id,
                    anomaly_type=SpoofingTypeEnum.CROSS_RECEIVER_DISAGREEMENT,
                    start_time_utc=min(o1.timestamp_utc, o2.timestamp_utc),
                    end_time_utc=max(o1.timestamp_utc, o2.timestamp_utc),
                    risk_score_component=30,
                    evidence_json={
                        "description": (
                            f"MMSI {mmsi}: position disagreement {dist_nm:.1f}nm between "
                            f"{o1.source} and {o2.source} within {time_diff:.0f}s"
                        ),
                        "distance_nm": round(dist_nm, 1),
                        "source_a": o1.source,
                        "source_b": o2.source,
                        "time_diff_s": round(time_diff),
                    },
                )
                db.add(anomaly)
                anomalies_created += 1

    db.commit()
    logger.info(
        "Cross-receiver: %d anomalies from %d MMSIs checked",
        anomalies_created, len(by_mmsi),
    )
    return {"anomalies_created": anomalies_created, "mmsis_checked": len(by_mmsi)}
