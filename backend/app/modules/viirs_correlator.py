"""VIIRS-AIS gap correlator.

For each VIIRS nighttime boat detection, checks for AIS gaps within a
configurable time and distance window. Tags unmatched detections and
cross-references corridors.

Follows the same pattern as sar_correlator.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

# Default correlation parameters
DEFAULT_RADIUS_NM: float = 30.0
DEFAULT_TIME_WINDOW_H: float = 6.0


def find_nearby_gaps(
    db: Session,
    lat: float,
    lon: float,
    timestamp: datetime,
    radius_nm: float = DEFAULT_RADIUS_NM,
    time_window_h: float = DEFAULT_TIME_WINDOW_H,
) -> list[Any]:
    """Find AIS gap events near a given position and time.

    Args:
        db: SQLAlchemy session.
        lat: Detection latitude.
        lon: Detection longitude.
        timestamp: Detection timestamp.
        radius_nm: Search radius in nautical miles.
        time_window_h: Time window in hours (±).

    Returns:
        List of AISGapEvent records that fall within the spatial+temporal window.
    """
    from app.models.gap_event import AISGapEvent

    time_start = timestamp - timedelta(hours=time_window_h)
    time_end = timestamp + timedelta(hours=time_window_h)

    # Query gaps overlapping the time window
    candidates = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.gap_start_utc <= time_end,
            AISGapEvent.gap_end_utc >= time_start,
        )
        .all()
    )

    nearby: list[Any] = []
    for gap in candidates:
        # Use gap_off position if available, otherwise skip
        gap_lat = getattr(gap, "gap_off_lat", None)
        gap_lon = getattr(gap, "gap_off_lon", None)
        if gap_lat is None or gap_lon is None:
            continue

        dist = haversine_nm(lat, lon, gap_lat, gap_lon)
        if dist <= radius_nm:
            nearby.append(gap)

    return nearby


def correlate_viirs_with_gaps(
    db: Session,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    radius_nm: float = DEFAULT_RADIUS_NM,
    time_window_h: float = DEFAULT_TIME_WINDOW_H,
) -> dict[str, Any]:
    """Correlate VIIRS detections with AIS gap events.

    For each unmatched VIIRS detection (scene_id starts with 'viirs-'),
    searches for AIS gaps within the time+distance window. Updates
    detections that match gaps with the gap event ID.

    Args:
        db: SQLAlchemy session.
        date_from: Optional start date filter.
        date_to: Optional end date filter.
        radius_nm: Search radius in nautical miles.
        time_window_h: Time window in hours.

    Returns:
        Stats dict with counts.
    """
    from app.models.stubs import DarkVesselDetection

    stats: dict[str, Any] = {
        "detections_processed": 0,
        "gaps_matched": 0,
        "unmatched": 0,
        "skipped_no_position": 0,
        "corridor_matches": 0,
        "errors": 0,
    }

    # Query unmatched VIIRS detections
    query = db.query(DarkVesselDetection).filter(
        DarkVesselDetection.ais_match_result == "unmatched",
        DarkVesselDetection.scene_id.like("viirs-%"),
    )
    if date_from is not None:
        query = query.filter(DarkVesselDetection.detection_time_utc >= date_from)
    if date_to is not None:
        query = query.filter(DarkVesselDetection.detection_time_utc <= date_to)

    detections = query.all()

    if not detections:
        logger.info("VIIRS correlator: no unmatched VIIRS detections to process")
        return stats

    for det in detections:
        if det.detection_lat is None or det.detection_lon is None:
            stats["skipped_no_position"] += 1
            continue

        if det.detection_time_utc is None:
            stats["skipped_no_position"] += 1
            continue

        stats["detections_processed"] += 1

        try:
            nearby_gaps = find_nearby_gaps(
                db,
                det.detection_lat,
                det.detection_lon,
                det.detection_time_utc,
                radius_nm=radius_nm,
                time_window_h=time_window_h,
            )
        except Exception:
            logger.warning("Error finding gaps for VIIRS detection %s", det.detection_id, exc_info=True)
            stats["errors"] += 1
            continue

        if nearby_gaps:
            # Link to the closest gap
            best_gap = min(
                nearby_gaps,
                key=lambda g: haversine_nm(
                    det.detection_lat,
                    det.detection_lon,
                    getattr(g, "gap_off_lat", 0),
                    getattr(g, "gap_off_lon", 0),
                ),
            )
            det.created_gap_event_id = best_gap.gap_event_id
            det.ais_match_result = "gap_correlated"
            det.ais_match_attempted = True

            # Check corridor match
            if (
                det.corridor_id is not None
                and best_gap.corridor_id is not None
                and det.corridor_id == best_gap.corridor_id
            ):
                stats["corridor_matches"] += 1

            stats["gaps_matched"] += 1
        else:
            stats["unmatched"] += 1

    db.commit()
    logger.info(
        "VIIRS correlator complete: %d processed, %d gap-matched, %d unmatched",
        stats["detections_processed"],
        stats["gaps_matched"],
        stats["unmatched"],
    )
    return stats
