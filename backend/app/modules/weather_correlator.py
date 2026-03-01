"""Weather correlation — false positive reduction for speed anomalies.

Stub implementation: no actual weather API integration yet.
The scoring deduction logic is fully implemented and testable with mock data.

Provides:
  - correlate_weather()       — check if speed anomaly coincides with weather conditions
  - get_weather_stub()        — stub weather data provider (returns mock data)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def get_weather_stub(
    lat: float,
    lon: float,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Stub weather data provider.

    In production this would call an external weather API (e.g. OpenWeather, ECMWF).
    Currently returns an empty dict to indicate no weather data is available.

    Args:
        lat: Latitude of position.
        lon: Longitude of position.
        timestamp: Time of observation.

    Returns:
        dict with wind_speed_kn, conditions, etc. or empty dict if unavailable.
    """
    # Stub: no weather data available
    # When a real weather API is integrated, this will return actual data:
    # {"wind_speed_kn": 15.0, "conditions": "moderate", "wave_height_m": 2.5}
    return {}


def compute_weather_deduction(weather_data: dict[str, Any]) -> tuple[int, str]:
    """Compute scoring deduction based on weather conditions.

    IMPORTANT: This deduction applies to speed anomaly scoring ONLY,
    NOT to gap scores. Wind and storms explain speed variations but
    do not explain AIS transmission gaps.

    Args:
        weather_data: Dict with wind_speed_kn and/or conditions fields.

    Returns:
        (deduction_points, reason) where deduction_points is negative.
        Returns (0, "") if no deduction applies.
    """
    if not weather_data:
        return 0, ""

    wind_speed = weather_data.get("wind_speed_kn")
    if wind_speed is None or not isinstance(wind_speed, (int, float)):
        return 0, ""

    # Storm conditions: wind > 35kn -> -15
    if wind_speed > 35:
        return -15, "storm_conditions"

    # High wind: wind > 25kn -> -8
    if wind_speed > 25:
        return -8, "high_wind"

    return 0, ""


def correlate_weather(
    db: Session,
    vessel_id: int,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict:
    """Correlate vessel speed anomalies with weather conditions.

    1. Get vessel's AIS points with speed anomalies
    2. Check weather conditions at those positions
    3. Apply deductions for speed anomaly scores where weather explains the variation

    IMPORTANT: Weather deduction applies to speed anomaly ONLY, NOT to gap scores.

    Args:
        db: SQLAlchemy session.
        vessel_id: Vessel to analyze.
        date_from: Start of analysis window.
        date_to: End of analysis window.

    Returns:
        dict with correlations, total_deduction, and details.
        Returns empty dict if no weather data available (graceful fallback).
    """
    from app.models.ais_point import AISPoint

    result: dict[str, Any] = {}

    if date_from is None:
        date_from = datetime.now(timezone.utc) - timedelta(days=7)
    if date_to is None:
        date_to = datetime.now(timezone.utc)

    # Strip timezone info for SQLite compatibility
    if date_from.tzinfo:
        date_from = date_from.replace(tzinfo=None)
    if date_to.tzinfo:
        date_to = date_to.replace(tzinfo=None)

    # Get AIS points in the window
    points = (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel_id,
            AISPoint.timestamp_utc >= date_from,
            AISPoint.timestamp_utc <= date_to,
        )
        .order_by(AISPoint.timestamp_utc.asc())
        .all()
    )

    if not points:
        return result

    correlations: list[dict[str, Any]] = []
    total_deduction = 0

    for pt in points:
        weather = get_weather_stub(pt.lat, pt.lon, pt.timestamp_utc)
        if not weather:
            continue

        deduction, reason = compute_weather_deduction(weather)
        if deduction != 0:
            correlations.append({
                "ais_point_id": pt.ais_point_id,
                "lat": pt.lat,
                "lon": pt.lon,
                "timestamp": pt.timestamp_utc.isoformat() if pt.timestamp_utc else None,
                "wind_speed_kn": weather.get("wind_speed_kn"),
                "deduction": deduction,
                "reason": reason,
            })
            total_deduction += deduction

    if not correlations:
        return result

    result = {
        "vessel_id": vessel_id,
        "correlations": correlations,
        "total_deduction": total_deduction,
        "applies_to": "speed_anomaly_only",
    }

    return result
