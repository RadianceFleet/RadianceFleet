"""Sparse AIS transmission detector -- identifies vessels transmitting at
minimum legal rate without creating formal AIS gaps.

Shadow fleet vessels sometimes transmit at the minimum legal rate (roughly
1-2 transmissions per hour) while underway to technically comply with
IMO regulations while making position tracking difficult. This evasion
technique avoids creating formal AIS gaps while still degrading tracking
quality significantly.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.base import SpoofingTypeEnum
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────
_UNDERWAY_SOG_THRESHOLD_KN = 3.0   # vessel considered "underway" above this SOG
_WINDOW_HOURS = 24                  # rolling window for density calculation
_MODERATE_THRESHOLD_PTS_PER_HOUR = 2.0  # <=2 pts/hour = moderate sparsity
_SEVERE_THRESHOLD_PTS_PER_HOUR = 1.0    # <1 pt/hour = severe sparsity
_MIN_UNDERWAY_HOURS = 4.0               # minimum underway duration to flag


def run_sparse_transmission_detection(db: Session) -> dict:
    """Detect vessels transmitting at suspiciously low rates while underway.

    Computes per-vessel AIS point density over 24h rolling windows for
    periods when the vessel is underway (SOG > 3kn). Flags vessels with
    density below threshold.

    Scoring:
      - Moderate sparsity (<=2 pts/hour underway): +15
      - Severe sparsity (<1 pt/hour underway): +25

    Returns:
        {"status": "ok", "anomalies_created": N, "vessels_checked": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.SPARSE_TRANSMISSION_DETECTION_ENABLED:
        return {"status": "disabled"}

    vessels = db.query(Vessel).all()
    anomalies_created = 0
    vessels_checked = 0

    for vessel in vessels:
        # Get AIS points ordered by time
        points = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id)
            .order_by(AISPoint.timestamp_utc)
            .all()
        )

        if len(points) < 2:
            continue

        vessels_checked += 1

        # Check for existing anomaly
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == vessel.vessel_id,
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.SPARSE_TRANSMISSION,
        ).first()
        if existing:
            continue

        # Find sparse windows using sliding window approach
        sparse_result = _find_sparse_windows(points)
        if sparse_result is None:
            continue

        severity, avg_density, window_start, window_end, underway_hours = sparse_result

        if severity == "severe":
            score = 25
        else:
            score = 15

        anomaly = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.SPARSE_TRANSMISSION,
            start_time_utc=window_start,
            end_time_utc=window_end,
            risk_score_component=score,
            evidence_json={
                "severity": severity,
                "avg_points_per_hour": round(avg_density, 2),
                "underway_hours": round(underway_hours, 1),
                "window_hours": _WINDOW_HOURS,
            },
        )
        db.add(anomaly)
        anomalies_created += 1

    db.commit()
    logger.info(
        "Sparse transmission: %d anomalies from %d vessels checked",
        anomalies_created, vessels_checked,
    )
    return {
        "status": "ok",
        "anomalies_created": anomalies_created,
        "vessels_checked": vessels_checked,
    }


def _find_sparse_windows(
    points: list,
) -> tuple[str, float, datetime, datetime, float] | None:
    """Find the worst sparse transmission window in the AIS point sequence.

    Returns (severity, avg_density, window_start, window_end, underway_hours)
    or None if no sparse window found.
    """
    if len(points) < 2:
        return None

    best_result = None
    best_density = float("inf")

    # Sliding window: iterate through points as window starts
    for i in range(len(points)):
        window_start = points[i].timestamp_utc
        window_end = window_start + timedelta(hours=_WINDOW_HOURS)

        # Collect points in window
        window_points = []
        for j in range(i, len(points)):
            if points[j].timestamp_utc > window_end:
                break
            window_points.append(points[j])

        if len(window_points) < 2:
            continue

        # Filter for underway points
        underway_points = [
            p for p in window_points
            if p.sog is not None and p.sog > _UNDERWAY_SOG_THRESHOLD_KN
        ]

        if len(underway_points) < 2:
            continue

        # Calculate underway duration
        actual_end = min(window_end, window_points[-1].timestamp_utc)
        underway_start = underway_points[0].timestamp_utc
        underway_end = underway_points[-1].timestamp_utc
        underway_hours = (underway_end - underway_start).total_seconds() / 3600.0

        if underway_hours < _MIN_UNDERWAY_HOURS:
            continue

        # Calculate point density (points per hour while underway)
        density = len(underway_points) / underway_hours

        # Check if this is the sparsest window so far
        if density < best_density and density <= _MODERATE_THRESHOLD_PTS_PER_HOUR:
            if density < _SEVERE_THRESHOLD_PTS_PER_HOUR:
                severity = "severe"
            else:
                severity = "moderate"

            best_density = density
            best_result = (severity, density, underway_start, underway_end, underway_hours)

    return best_result
