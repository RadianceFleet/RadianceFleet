"""Gap-SAR cross-correlation validator (v4.0).

Validates AIS gaps by checking whether satellite (SAR/VIIRS) detections
were observed near the predicted vessel position during the gap.

- Confirmed dark transit: SAR detection found near predicted position (+40)
- Possible outage: no detection but gap is in known coverage area (-10)
- Inconclusive: no coverage data available
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.gap_event import AISGapEvent
from app.models.stubs import DarkVesselDetection
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)


def _interpolate_position(
    gap: AISGapEvent,
    target_time: datetime,
) -> tuple[float, float] | None:
    """Linear interpolation between gap off/on positions at target_time.

    Uses gap_off_lat/lon (start) and gap_on_lat/lon (end).
    Returns (lat, lon) or None if positions are missing.
    """
    lat1 = gap.gap_off_lat
    lon1 = gap.gap_off_lon
    lat2 = gap.gap_on_lat
    lon2 = gap.gap_on_lon

    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None

    total_seconds = (gap.gap_end_utc - gap.gap_start_utc).total_seconds()
    if total_seconds <= 0:
        return (lat1, lon1)

    elapsed = (target_time - gap.gap_start_utc).total_seconds()
    fraction = max(0.0, min(1.0, elapsed / total_seconds))

    pred_lat = lat1 + fraction * (lat2 - lat1)
    pred_lon = lon1 + fraction * (lon2 - lon1)
    return (pred_lat, pred_lon)


def validate_gaps_with_sar(
    db: Session,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """Cross-correlate AIS gaps with SAR/VIIRS detections.

    For each gap in the date range:
    1. Compute predicted position via linear interpolation
    2. Query DarkVesselDetection for nearby detections
    3. Classify as confirmed_dark_transit, possible_outage, or inconclusive
    4. Update the gap's risk_breakdown_json with sar_validation results

    Returns summary counts.
    """
    if not settings.GAP_SAR_VALIDATION_ENABLED:
        return {
            "gaps_checked": 0,
            "confirmed_dark": 0,
            "possible_outage": 0,
            "inconclusive": 0,
            "disabled": True,
        }

    search_radius = settings.GAP_SAR_SEARCH_RADIUS_NM
    time_window_h = settings.GAP_SAR_TIME_WINDOW_H

    query = db.query(AISGapEvent)
    if date_from is not None:
        query = query.filter(AISGapEvent.gap_start_utc >= date_from)
    if date_to is not None:
        query = query.filter(AISGapEvent.gap_end_utc <= date_to)

    gaps = query.all()

    confirmed_dark = 0
    possible_outage = 0
    inconclusive = 0

    for gap in gaps:
        # Compute predicted midpoint position
        midpoint_time = gap.gap_start_utc + (gap.gap_end_utc - gap.gap_start_utc) / 2
        predicted = _interpolate_position(gap, midpoint_time)

        if predicted is None:
            # No position data — inconclusive
            _update_gap_sar_validation(gap, "inconclusive", [], None, None)
            inconclusive += 1
            continue

        pred_lat, pred_lon = predicted

        # Query detections in time window around the gap
        time_start = gap.gap_start_utc - timedelta(hours=time_window_h)
        time_end = gap.gap_end_utc + timedelta(hours=time_window_h)

        detections = (
            db.query(DarkVesselDetection)
            .filter(
                DarkVesselDetection.detection_time_utc >= time_start,
                DarkVesselDetection.detection_time_utc <= time_end,
                DarkVesselDetection.detection_lat.isnot(None),
                DarkVesselDetection.detection_lon.isnot(None),
            )
            .all()
        )

        # Filter by spatial proximity
        nearby: list[dict[str, Any]] = []
        for det in detections:
            dist = haversine_nm(pred_lat, pred_lon, det.detection_lat, det.detection_lon)
            if dist <= search_radius:
                nearby.append(
                    {
                        "detection_id": det.detection_id,
                        "scene_id": det.scene_id,
                        "distance_nm": round(dist, 2),
                        "detection_time": (
                            det.detection_time_utc.isoformat()
                            if det.detection_time_utc
                            else None
                        ),
                    }
                )

        if nearby:
            _update_gap_sar_validation(gap, "confirmed", nearby, pred_lat, pred_lon)
            confirmed_dark += 1
        elif gap.coverage_quality and gap.coverage_quality.upper() in (
            "GOOD",
            "MODERATE",
        ):
            _update_gap_sar_validation(gap, "outage", [], pred_lat, pred_lon)
            possible_outage += 1
        else:
            _update_gap_sar_validation(gap, "inconclusive", [], pred_lat, pred_lon)
            inconclusive += 1

    db.commit()

    return {
        "gaps_checked": len(gaps),
        "confirmed_dark": confirmed_dark,
        "possible_outage": possible_outage,
        "inconclusive": inconclusive,
    }


def _update_gap_sar_validation(
    gap: AISGapEvent,
    result: str,
    detections: list[dict[str, Any]],
    predicted_lat: float | None,
    predicted_lon: float | None,
) -> None:
    """Write sar_validation results into the gap's risk_breakdown_json."""
    breakdown = gap.risk_breakdown_json or {}
    breakdown["sar_validation"] = {
        "result": result,
        "detections": detections,
        "predicted_lat": predicted_lat,
        "predicted_lon": predicted_lon,
    }
    gap.risk_breakdown_json = breakdown
