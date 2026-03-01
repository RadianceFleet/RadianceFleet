"""Weather correlation — false positive reduction for speed anomalies.

Integrates with Open-Meteo Archive API (free, no API key needed) to fetch
historical wind data and apply scoring deductions for weather-explained
speed variations.

Provides:
  - get_weather()             — fetch weather from Open-Meteo Archive API
  - get_weather_stub()        — stub weather data provider (legacy, returns empty)
  - compute_weather_deduction — scoring deduction logic
  - correlate_weather()       — check if speed anomaly coincides with weather conditions
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen, Request

import json as _json

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Open-Meteo Archive API base URL (free, no key needed)
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _round_coord(val: float) -> float:
    """Round to 1-degree grid for cache efficiency."""
    return math.floor(val * 1) / 1  # 1-degree grid


def _round_hour(dt: datetime) -> str:
    """Round datetime to nearest hour and return ISO date string."""
    return dt.strftime("%Y-%m-%d")


@lru_cache(maxsize=1024)
def _fetch_open_meteo(lat_grid: float, lon_grid: float, date_str: str) -> dict[str, Any]:
    """Fetch weather data from Open-Meteo Archive API with 1-degree/1-day caching.

    Args:
        lat_grid: Latitude rounded to 1-degree grid.
        lon_grid: Longitude rounded to 1-degree grid.
        date_str: Date string in YYYY-MM-DD format.

    Returns:
        dict with wind_speed_kn and wind_gust_kn, or empty dict on error.
    """
    url = (
        f"{OPEN_METEO_ARCHIVE_URL}"
        f"?latitude={lat_grid}&longitude={lon_grid}"
        f"&start_date={date_str}&end_date={date_str}"
        f"&hourly=wind_speed_10m,wind_gusts_10m"
    )
    try:
        req = Request(url, headers={"User-Agent": "RadianceFleet/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8"))

        hourly = data.get("hourly", {})
        wind_speeds = hourly.get("wind_speed_10m", [])
        wind_gusts = hourly.get("wind_gusts_10m", [])

        if not wind_speeds:
            return {}

        # Filter out None values
        valid_speeds = [s for s in wind_speeds if s is not None]
        valid_gusts = [g for g in wind_gusts if g is not None]

        if not valid_speeds:
            return {}

        # Open-Meteo returns km/h; convert to knots (1 km/h = 0.539957 kn)
        max_speed_kmh = max(valid_speeds)
        max_gust_kmh = max(valid_gusts) if valid_gusts else max_speed_kmh
        avg_speed_kmh = sum(valid_speeds) / len(valid_speeds)

        return {
            "wind_speed_kn": round(max_speed_kmh * 0.539957, 1),
            "wind_gust_kn": round(max_gust_kmh * 0.539957, 1),
            "avg_wind_speed_kn": round(avg_speed_kmh * 0.539957, 1),
            "source": "open-meteo",
        }
    except (URLError, OSError, ValueError, KeyError, _json.JSONDecodeError) as exc:
        logger.debug("Open-Meteo fetch failed for %s/%s/%s: %s", lat_grid, lon_grid, date_str, exc)
        return {}


def get_weather(
    lat: float,
    lon: float,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Fetch weather data from Open-Meteo Archive API.

    Uses 1-degree spatial grid and 1-day temporal resolution for LRU cache
    efficiency. Graceful degradation: returns empty dict on any error.

    Args:
        lat: Latitude of position.
        lon: Longitude of position.
        timestamp: Time of observation (defaults to now).

    Returns:
        dict with wind_speed_kn, wind_gust_kn, source, etc. or empty dict.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)

    lat_grid = _round_coord(lat)
    lon_grid = _round_coord(lon)
    date_str = _round_hour(timestamp)

    return _fetch_open_meteo(lat_grid, lon_grid, date_str)


def get_weather_stub(
    lat: float,
    lon: float,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Legacy stub weather data provider.

    Retained for backward compatibility. Delegates to get_weather() which
    uses the Open-Meteo Archive API.

    Args:
        lat: Latitude of position.
        lon: Longitude of position.
        timestamp: Time of observation.

    Returns:
        dict with wind_speed_kn, conditions, etc. or empty dict if unavailable.
    """
    return get_weather(lat, lon, timestamp)


def compute_weather_deduction(weather_data: dict[str, Any]) -> tuple[int, str]:
    """Compute scoring deduction based on weather conditions.

    IMPORTANT: This deduction applies to speed anomaly scoring ONLY,
    NOT to gap scores. Wind and storms explain speed variations but
    do not explain AIS transmission gaps.

    Thresholds (using max wind speed or gust, whichever is higher):
      - Wind > 40kn (storm): -15
      - Wind > 25kn (high wind): -8

    Args:
        weather_data: Dict with wind_speed_kn and/or wind_gust_kn fields.

    Returns:
        (deduction_points, reason) where deduction_points is negative.
        Returns (0, "") if no deduction applies.
    """
    if not weather_data:
        return 0, ""

    wind_speed = weather_data.get("wind_speed_kn")
    wind_gust = weather_data.get("wind_gust_kn")

    # Use the higher of wind speed or gust
    effective_wind = None
    if isinstance(wind_speed, (int, float)):
        effective_wind = wind_speed
    if isinstance(wind_gust, (int, float)):
        if effective_wind is None or wind_gust > effective_wind:
            effective_wind = wind_gust

    if effective_wind is None:
        return 0, ""

    # Storm conditions: wind > 40kn -> -15
    if effective_wind > 40:
        return -15, "storm_conditions"

    # High wind: wind > 25kn -> -8
    if effective_wind > 25:
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
    2. Check weather conditions at those positions via Open-Meteo Archive API
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
        weather = get_weather(pt.lat, pt.lon, pt.timestamp_utc)
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
                "wind_gust_kn": weather.get("wind_gust_kn"),
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
