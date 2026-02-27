"""MMSI cloning detection — find simultaneous transmissions from distant locations.

14,000+ cases/year of the same MMSI broadcast from two locations simultaneously.
This module detects impossible-speed pairs within time windows and creates
SpoofingAnomaly records of type MMSI_REUSE.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from itertools import groupby

from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.models.ais_point import AISPoint
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.models.base import SpoofingTypeEnum
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

# Impossible speed threshold (knots) — beyond any vessel capability
_IMPOSSIBLE_SPEED_KN = 50.0
# Time window for consecutive point comparison
_WINDOW_SECONDS = 3600  # 1 hour


def detect_mmsi_cloning(db: Session) -> list[dict]:
    """Scan AIS points for same MMSI at impossible distances within 1 hour.

    For each MMSI, finds consecutive AIS point pairs requiring >50kn speed
    and creates SpoofingAnomaly records.

    Returns list of dicts: {mmsi, vessel_id, point_a, point_b, distance_nm, implied_speed_kn}.
    """
    results: list[dict] = []
    stats = {"mmsi_scanned": 0, "cloning_events": 0, "anomalies_created": 0}

    # Get all canonical vessels with AIS points
    vessels = (
        db.query(Vessel)
        .filter(Vessel.merged_into_vessel_id == None)  # noqa: E711
        .all()
    )

    for vessel in vessels:
        stats["mmsi_scanned"] += 1
        points = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id)
            .order_by(asc(AISPoint.timestamp_utc))
            .all()
        )

        if len(points) < 2:
            continue

        clones = _find_impossible_jumps(points, vessel)
        for clone in clones:
            # Check if anomaly already exists for this MMSI near this time
            existing = (
                db.query(SpoofingAnomaly)
                .filter(
                    SpoofingAnomaly.vessel_id == vessel.vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.MMSI_REUSE,
                    SpoofingAnomaly.start_time_utc == clone["point_a_time"],
                )
                .first()
            )
            if existing:
                continue

            anomaly = SpoofingAnomaly(
                vessel_id=vessel.vessel_id,
                anomaly_type=SpoofingTypeEnum.MMSI_REUSE,
                start_time_utc=clone["point_a_time"],
                end_time_utc=clone["point_b_time"],
                implied_speed_kn=clone["implied_speed_kn"],
                risk_score_component=_score_cloning(clone["implied_speed_kn"]),
                evidence_json={
                    "point_a": {"lat": clone["point_a_lat"], "lon": clone["point_a_lon"]},
                    "point_b": {"lat": clone["point_b_lat"], "lon": clone["point_b_lon"]},
                    "distance_nm": clone["distance_nm"],
                    "time_delta_seconds": clone["time_delta_seconds"],
                    "detection_type": "mmsi_cloning",
                },
            )
            db.add(anomaly)
            stats["anomalies_created"] += 1

            results.append({
                "mmsi": vessel.mmsi,
                "vessel_id": vessel.vessel_id,
                "point_a": {"lat": clone["point_a_lat"], "lon": clone["point_a_lon"], "time": str(clone["point_a_time"])},
                "point_b": {"lat": clone["point_b_lat"], "lon": clone["point_b_lon"], "time": str(clone["point_b_time"])},
                "distance_nm": clone["distance_nm"],
                "implied_speed_kn": clone["implied_speed_kn"],
            })

        if clones:
            stats["cloning_events"] += 1

    db.commit()
    logger.info("MMSI cloning detection: %s", stats)
    return results


def _find_impossible_jumps(points: list[AISPoint], vessel: Vessel) -> list[dict]:
    """Find consecutive point pairs requiring impossible speed."""
    jumps: list[dict] = []

    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]

        time_delta = (p2.timestamp_utc - p1.timestamp_utc).total_seconds()
        if time_delta <= 0 or time_delta > _WINDOW_SECONDS:
            continue

        distance = haversine_nm(p1.lat, p1.lon, p2.lat, p2.lon)
        speed = distance / (time_delta / 3600)

        if speed > _IMPOSSIBLE_SPEED_KN:
            jumps.append({
                "point_a_lat": p1.lat,
                "point_a_lon": p1.lon,
                "point_a_time": p1.timestamp_utc,
                "point_b_lat": p2.lat,
                "point_b_lon": p2.lon,
                "point_b_time": p2.timestamp_utc,
                "distance_nm": round(distance, 2),
                "time_delta_seconds": time_delta,
                "implied_speed_kn": round(speed, 1),
            })

    return jumps


def _score_cloning(implied_speed_kn: float) -> int:
    """Score a cloning event based on implied speed."""
    if implied_speed_kn >= 100:
        return 55  # matches mmsi_reuse_implied_speed_100kn
    if implied_speed_kn >= 30:
        return 40  # matches mmsi_reuse_implied_speed_30kn
    return 25
