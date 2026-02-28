"""Fake port call / position spoofing detector.

Detects vessels broadcasting physically impossible positions -- e.g., claiming to
be at Port A and Port B within a time frame that would require >25kn transit speed.
~460 fake voyages to Khor al Zubair were documented in H1 2025 alone.
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.ais_point import AISPoint
from app.models.base import SpoofingTypeEnum
from app.models.vessel import Vessel
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

_MAX_FEASIBLE_SPEED_KN = 25.0  # Max realistic speed for a tanker


def detect_fake_positions(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Detect kinematically impossible position sequences.

    For each vessel, checks consecutive AIS positions. If the implied speed
    exceeds 25kn, flags as potential fake position/port call.

    Returns:
        {"fake_positions_detected": N, "vessels_checked": M}
    """
    query = db.query(Vessel)
    vessels = query.all()

    fake_detected = 0
    vessels_checked = 0

    for vessel in vessels:
        pt_query = db.query(AISPoint).filter(
            AISPoint.vessel_id == vessel.vessel_id,
        ).order_by(AISPoint.timestamp_utc)

        if date_from:
            pt_query = pt_query.filter(
                AISPoint.timestamp_utc >= datetime.combine(date_from, datetime.min.time())
            )
        if date_to:
            pt_query = pt_query.filter(
                AISPoint.timestamp_utc <= datetime.combine(date_to, datetime.max.time())
            )

        points = pt_query.all()
        if len(points) < 2:
            continue

        vessels_checked += 1

        for i in range(len(points) - 1):
            p1, p2 = points[i], points[i + 1]

            time_diff_h = (p2.timestamp_utc - p1.timestamp_utc).total_seconds() / 3600
            if time_diff_h <= 0:
                continue

            dist_nm = haversine_nm(p1.lat, p1.lon, p2.lat, p2.lon)
            implied_speed = dist_nm / time_diff_h

            if implied_speed <= _MAX_FEASIBLE_SPEED_KN:
                continue

            # Skip very short distances (GPS jitter)
            if dist_nm < 1.0:
                continue

            # Skip very short time gaps (data race)
            if time_diff_h < 0.01:  # < 36 seconds
                continue

            # Check if already flagged
            existing = db.query(SpoofingAnomaly).filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FAKE_PORT_CALL,
                SpoofingAnomaly.start_time_utc == p1.timestamp_utc,
            ).first()
            if existing:
                continue

            anomaly = SpoofingAnomaly(
                vessel_id=vessel.vessel_id,
                anomaly_type=SpoofingTypeEnum.FAKE_PORT_CALL,
                start_time_utc=p1.timestamp_utc,
                end_time_utc=p2.timestamp_utc,
                risk_score_component=40,
                implied_speed_kn=round(implied_speed, 1),
                evidence_json={
                    "description": (
                        f"Kinematically impossible: {dist_nm:.1f}nm in {time_diff_h:.2f}h "
                        f"= {implied_speed:.1f}kn (max feasible: {_MAX_FEASIBLE_SPEED_KN}kn)"
                    ),
                    "distance_nm": round(dist_nm, 1),
                    "time_diff_h": round(time_diff_h, 2),
                    "implied_speed_kn": round(implied_speed, 1),
                },
            )
            db.add(anomaly)
            fake_detected += 1

    db.commit()
    logger.info(
        "Fake position detection: %d anomalies from %d vessels",
        fake_detected, vessels_checked,
    )
    return {"fake_positions_detected": fake_detected, "vessels_checked": vessels_checked}
