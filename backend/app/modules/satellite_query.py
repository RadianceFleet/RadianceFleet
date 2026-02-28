"""Sentinel-1 satellite check package generation.

Generates Copernicus Browser query packages for analyst review.
See PRD §7.6 for the satellite workflow specification.
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.models.satellite_check import SatelliteCheck

logger = logging.getLogger(__name__)

COPERNICUS_BROWSER_BASE = "https://browser.dataspace.copernicus.eu/"

# AIS corridors in Baltic region (for data source note in evidence cards)
BALTIC_CORRIDOR_KEYWORDS = {"baltic", "kaliningrad", "primorsk", "ust-luga", "novorossiysk"}


def compute_bounding_box(
    center_lat: float,
    center_lon: float,
    radius_nm: float,
) -> dict[str, float]:
    """
    Convert center + radius (nautical miles) to WGS84 bounding box.

    lat_delta = radius_nm / 60
    lon_delta = radius_nm / (60 * cos(radians(center_lat)))

    Returns dict with min_lon, min_lat, max_lon, max_lat.
    """
    lat_delta = radius_nm / 60.0
    lon_delta = radius_nm / (60.0 * math.cos(math.radians(center_lat)))
    return {
        "min_lon": center_lon - lon_delta,
        "min_lat": center_lat - lat_delta,
        "max_lon": center_lon + lon_delta,
        "max_lat": center_lat + lat_delta,
    }


def build_copernicus_url(
    center_lat: float,
    center_lon: float,
    date_from: str,
    date_to: str,
) -> str:
    """Build a Copernicus Browser URL pre-centered on the gap position."""
    return (
        f"{COPERNICUS_BROWSER_BASE}"
        f"?zoom=7"
        f"&lat={center_lat:.4f}"
        f"&lng={center_lon:.4f}"
        f"&dateFrom={date_from}"
        f"&dateTo={date_to}"
        f"&themeId=OCEAN"
    )


def prepare_satellite_check(alert_id: int, db: Session) -> dict[str, Any]:
    """
    Generate a Sentinel-1 query package for an AIS gap event.

    Creates a SatelliteCheck record and returns the pre-filled Copernicus Browser URL.
    """
    gap = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not gap:
        return {"error": "Alert not found"}

    # Check for existing satellite check
    existing = db.query(SatelliteCheck).filter(SatelliteCheck.gap_event_id == alert_id).first()
    if existing:
        return {"message": "Satellite check already exists", "sat_check_id": existing.sat_check_id}

    # Time window: gap_start - 1h to gap_end + 1h
    time_from = gap.gap_start_utc - timedelta(hours=1)
    time_to = gap.gap_end_utc + timedelta(hours=1)

    # Determine center position from start/end points via movement envelope
    center_lat, center_lon = _get_gap_center(gap, db)

    # Bounding box from movement envelope radius
    radius_nm = gap.max_plausible_distance_nm or 50.0
    bbox = compute_bounding_box(center_lat, center_lon, radius_nm)

    copernicus_url = build_copernicus_url(
        center_lat,
        center_lon,
        time_from.strftime("%Y-%m-%d"),
        time_to.strftime("%Y-%m-%d"),
    )

    # Data source coverage metadata (NFR7 — blind spots disclosed to analysts)
    corridor_name = gap.corridor.name.lower() if gap.corridor else ""
    is_baltic = any(kw in corridor_name for kw in BALTIC_CORRIDOR_KEYWORDS)

    sat_check = SatelliteCheck(
        gap_event_id=alert_id,
        provider="Sentinel-1",
        query_time_window=f"{time_from.isoformat()}/{time_to.isoformat()}",
        review_status="not_checked",
    )
    db.add(sat_check)
    db.commit()

    logger.info("Satellite check prepared for alert %d (center: %.4f, %.4f)", alert_id, center_lat, center_lon)
    return {
        "sat_check_id": sat_check.sat_check_id,
        "copernicus_url": copernicus_url,
        "bounding_box": bbox,
        "time_window": {
            "from": time_from.isoformat(),
            "to": time_to.isoformat(),
        },
        "sensor_preference": "Sentinel-1 SAR (all-weather, day/night)",
        "cloud_cover_max": 100,  # SAR is cloud-penetrating; irrelevant but PRD mandates field
        "expected_vessel_count": 1,  # single vessel tracking; analyst may adjust
        "data_source_coverage": {
            "ais_provider": (gap.vessel.ais_source if gap.vessel else None) or "unknown",
            "ais_region": "Baltic Sea (DMA historical)" if is_baltic else "Unknown",
            "coverage_limitations": (
                "AIS coverage varies by region. "
                "Commercial AIS providers required for full Persian Gulf / Black Sea coverage."
                if not is_baltic else
                "Baltic Sea has good AIS coverage from Danish Maritime Authority."
            ),
            "disclaimer": (
                "This is investigative triage, not a legal determination. "
                "Satellite review requires analyst judgment."
            ),
        },
    }


def _get_gap_center(gap: AISGapEvent, db: Session) -> tuple[float, float]:
    """Return (center_lat, center_lon) for the gap — midpoint of start/end AIS points."""
    from app.models.ais_point import AISPoint

    start_lat = start_lon = end_lat = end_lon = None

    if gap.start_point_id:
        p = db.get(AISPoint, gap.start_point_id)
        if p:
            start_lat, start_lon = p.lat, p.lon

    if gap.end_point_id:
        p = db.get(AISPoint, gap.end_point_id)
        if p:
            end_lat, end_lon = p.lat, p.lon

    if start_lat and end_lat:
        return (start_lat + end_lat) / 2, (start_lon + end_lon) / 2
    if start_lat:
        return start_lat, start_lon
    if end_lat:
        return end_lat, end_lon

    # Try vessel's last known AIS position
    last = db.query(AISPoint).filter(
        AISPoint.vessel_id == gap.vessel_id
    ).order_by(AISPoint.timestamp_utc.desc()).first()
    if last:
        return last.lat, last.lon

    # Fallback — North Sea center
    return 55.0, 15.0
