"""IMO fraud detector -- identifies vessels with duplicated or near-miss
IMO numbers, indicating identity cloning or document fraud.

Two detection modes:
  A. Simultaneous IMO use: same IMO on 2+ vessels both moving, >500nm apart
  B. Near-miss IMO: IMO differing by 1 digit on already-suspicious vessels
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import SpoofingTypeEnum
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.models.ais_point import AISPoint

logger = logging.getLogger(__name__)


def _validate_imo_checksum(imo: str) -> bool:
    """Validate IMO number checksum (7 digits, weighted sum mod 10)."""
    if not imo or len(imo) != 7 or not imo.isdigit():
        return False
    digits = [int(d) for d in imo]
    weighted_sum = sum(d * w for d, w in zip(digits[:6], [7, 6, 5, 4, 3, 2]))
    return weighted_sum % 10 == digits[6]


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points in nautical miles."""
    R_NM = 3440.065  # Earth radius in nautical miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R_NM * 2 * math.asin(math.sqrt(a))


def _has_recent_movement(db: Session, vessel_id: int, window_hours: int = 48) -> bool:
    """Check if vessel has any AIS point with SOG > 0.5kn within the window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    point = (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel_id,
            AISPoint.timestamp_utc >= cutoff,
            AISPoint.sog > 0.5,
        )
        .first()
    )
    return point is not None


def _get_last_position(db: Session, vessel_id: int) -> tuple[float, float] | None:
    """Get last known position for a vessel."""
    point = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id == vessel_id)
        .order_by(AISPoint.timestamp_utc.desc())
        .first()
    )
    if point:
        return (point.lat, point.lon)
    return None


def _detect_simultaneous_imo(db: Session) -> int:
    """Detect simultaneous use of the same IMO by different vessels.

    Returns number of anomalies created.
    """
    # Find IMOs used by multiple vessels
    vessels_with_imo = (
        db.query(Vessel)
        .filter(Vessel.imo.isnot(None), Vessel.imo != "")
        .all()
    )

    by_imo: dict[str, list[Vessel]] = defaultdict(list)
    for v in vessels_with_imo:
        # Normalize IMO -- strip "IMO" prefix if present
        imo = v.imo.strip()
        if imo.upper().startswith("IMO"):
            imo = imo[3:].strip()
        by_imo[imo].append(v)

    anomalies_created = 0

    for imo, vessels in by_imo.items():
        if len(vessels) < 2:
            continue

        # Validate IMO checksum on all vessels
        if not _validate_imo_checksum(imo):
            continue

        # Check each pair
        for i in range(len(vessels)):
            for j in range(i + 1, len(vessels)):
                v1, v2 = vessels[i], vessels[j]

                # Both must have recent movement
                if not _has_recent_movement(db, v1.vessel_id):
                    continue
                if not _has_recent_movement(db, v2.vessel_id):
                    continue

                # Must be >500nm apart
                pos1 = _get_last_position(db, v1.vessel_id)
                pos2 = _get_last_position(db, v2.vessel_id)
                if not pos1 or not pos2:
                    continue

                distance = _haversine_nm(pos1[0], pos1[1], pos2[0], pos2[1])
                if distance <= 500:
                    continue

                # Check for existing anomaly
                existing = db.query(SpoofingAnomaly).filter(
                    SpoofingAnomaly.vessel_id == v1.vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD,
                ).first()
                if existing:
                    continue

                now = datetime.now(timezone.utc)
                anomaly = SpoofingAnomaly(
                    vessel_id=v1.vessel_id,
                    anomaly_type=SpoofingTypeEnum.IMO_FRAUD,
                    start_time_utc=now,
                    risk_score_component=45,
                    evidence_json={
                        "imo": imo,
                        "vessel_ids": [v1.vessel_id, v2.vessel_id],
                        "distance_nm": round(distance, 1),
                        "detection_type": "simultaneous",
                    },
                )
                db.add(anomaly)
                anomalies_created += 1

    return anomalies_created


def _imo_differs_by_one(imo_a: str, imo_b: str) -> bool:
    """Check if two IMO strings differ by exactly one digit."""
    if len(imo_a) != len(imo_b):
        return False
    diffs = sum(1 for a, b in zip(imo_a, imo_b) if a != b)
    return diffs == 1


def _detect_near_miss_imo(db: Session) -> int:
    """Detect near-miss IMO numbers on already-suspicious vessels.

    Only flags when >=2 qualifying criteria are met:
      1. Same vessel_type
      2. Similar DWT (within 20%)
      3. Other risk indicators present (existing anomalies)

    Returns number of anomalies created.
    """
    # Get vessels that are already suspicious
    suspicious_vessel_ids = {
        row[0]
        for row in db.query(SpoofingAnomaly.vessel_id).distinct().all()
    }

    # Also include vessels with risk_score > 30 (if the column exists)
    # For safety, we just use the spoofing anomaly check

    # Get all vessels with IMOs
    all_vessels_with_imo = (
        db.query(Vessel)
        .filter(Vessel.imo.isnot(None), Vessel.imo != "")
        .all()
    )

    # Only check suspicious vessels
    suspicious_vessels = [
        v for v in all_vessels_with_imo
        if v.vessel_id in suspicious_vessel_ids
    ]

    anomalies_created = 0

    for sv in suspicious_vessels:
        sv_imo = sv.imo.strip()
        if sv_imo.upper().startswith("IMO"):
            sv_imo = sv_imo[3:].strip()

        if not sv_imo or len(sv_imo) != 7 or not sv_imo.isdigit():
            continue

        for other in all_vessels_with_imo:
            if other.vessel_id == sv.vessel_id:
                continue

            other_imo = other.imo.strip()
            if other_imo.upper().startswith("IMO"):
                other_imo = other_imo[3:].strip()

            if not other_imo or len(other_imo) != 7 or not other_imo.isdigit():
                continue

            if not _imo_differs_by_one(sv_imo, other_imo):
                continue

            # Count qualifying criteria
            qualifying = []

            # 1. Same vessel type
            if (
                sv.vessel_type
                and other.vessel_type
                and sv.vessel_type.lower() == other.vessel_type.lower()
            ):
                qualifying.append("same_vessel_type")

            # 2. Similar DWT (within 20%)
            if sv.deadweight and other.deadweight and sv.deadweight > 0:
                ratio = other.deadweight / sv.deadweight
                if 0.8 <= ratio <= 1.2:
                    qualifying.append("similar_dwt")

            # 3. Other risk indicators (existing anomalies on the other vessel)
            if other.vessel_id in suspicious_vessel_ids:
                qualifying.append("other_risk_indicators")

            if len(qualifying) < 2:
                continue

            # Check for existing anomaly
            existing = db.query(SpoofingAnomaly).filter(
                SpoofingAnomaly.vessel_id == sv.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD,
            ).first()
            if existing:
                continue

            now = datetime.now(timezone.utc)
            anomaly = SpoofingAnomaly(
                vessel_id=sv.vessel_id,
                anomaly_type=SpoofingTypeEnum.IMO_FRAUD,
                start_time_utc=now,
                risk_score_component=20,
                evidence_json={
                    "imo_a": sv_imo,
                    "imo_b": other_imo,
                    "qualifying_criteria": qualifying,
                    "detection_type": "near_miss",
                },
            )
            db.add(anomaly)
            anomalies_created += 1
            break  # Only one near-miss per suspicious vessel

    return anomalies_created


def run_imo_fraud_detection(db: Session) -> dict:
    """Run both IMO fraud detection modes.

    Returns:
        {"status": "ok", "simultaneous": N, "near_miss": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.IMO_FRAUD_DETECTION_ENABLED:
        return {"status": "disabled"}

    simultaneous = _detect_simultaneous_imo(db)
    near_miss = _detect_near_miss_imo(db)
    db.commit()

    logger.info(
        "IMO fraud: %d simultaneous, %d near-miss anomalies",
        simultaneous, near_miss,
    )
    return {
        "status": "ok",
        "simultaneous": simultaneous,
        "near_miss": near_miss,
    }
