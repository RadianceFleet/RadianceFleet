"""Draught intelligence detector — corroborating signal for shadow fleet detection.

Detects significant draught changes that correlate with cargo operations
(loading/unloading) occurring far from legitimate ports, during AIS gaps,
or near STS transfer events.  Draught is a corroborating signal only:
AIS draught is manually entered and unreliable on its own, but extreme
swings far from port strengthen other detectors.
"""
import math
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.vessel import Vessel
from app.models.port import Port
from app.models.gap_event import AISGapEvent
from app.models.sts_transfer import StsTransferEvent
from app.models.draught_event import DraughtChangeEvent

logger = logging.getLogger(__name__)

# ── Physical bounds ──────────────────────────────────────────────────────────
_DRAUGHT_MIN_M = 0.0
_DRAUGHT_MAX_M = 25.0

# ── Batch size for vessel processing ─────────────────────────────────────────
_VESSEL_BATCH_SIZE = 1000

# ── Sliding window for draught change detection (hours) ──────────────────────
_SLIDING_WINDOW_HOURS = 24

# ── Port proximity thresholds (nautical miles) ───────────────────────────────
_OFFSHORE_TERMINAL_SKIP_NM = 25.0
_REGULAR_PORT_SKIP_NM = 10.0

# ── STS linkage window (hours) ───────────────────────────────────────────────
_STS_LINKAGE_HOURS = 12


def _get_class_threshold(deadweight: float | None) -> float:
    """Return minimum draught change threshold (metres) based on vessel DWT class.

    Thresholds based on tonnes-per-centimetre (TPC) research:
    - VLCC (>200k DWT): 3.0m minimum change
    - Suezmax (120-200k): 2.0m
    - Aframax (80-120k): 1.5m
    - Panamax (<80k): 1.0m
    - Unknown: 1.0m (default to strictest / most sensitive)
    """
    if deadweight is None:
        return 1.0
    if deadweight > 200_000:
        return 3.0
    if deadweight > 120_000:
        return 2.0
    if deadweight > 80_000:
        return 1.5
    return 1.0


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles between two WGS-84 coordinates."""
    _EARTH_RADIUS_NM = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return _EARTH_RADIUS_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _parse_port_coords(geometry_wkt: str | None) -> tuple[float, float] | None:
    """Extract (lat, lon) from WKT POINT geometry string.

    Port.geometry stores WKT like "POINT(lon lat)".
    Returns (lat, lon) tuple or None if unparseable.
    """
    if not geometry_wkt:
        return None
    m = re.match(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)", geometry_wkt, re.IGNORECASE)
    if not m:
        return None
    lon, lat = float(m.group(1)), float(m.group(2))
    return (lat, lon)


def _find_nearest_port(lat: float, lon: float, ports: list) -> tuple[Optional[int], Optional[float], bool]:
    """Find nearest port to given coordinates.

    Returns (port_id, distance_nm, is_offshore_terminal).
    """
    nearest_id = None
    nearest_dist = None
    nearest_is_offshore = False

    for port in ports:
        coords = _parse_port_coords(port.geometry)
        if coords is None:
            continue
        plat, plon = coords
        dist = _haversine_nm(lat, lon, plat, plon)
        if nearest_dist is None or dist < nearest_dist:
            nearest_dist = dist
            nearest_id = port.port_id
            nearest_is_offshore = getattr(port, "is_offshore_terminal", False)

    return nearest_id, nearest_dist, nearest_is_offshore


def _is_valid_draught(draught: float | None) -> bool:
    """Check if draught value is within physical bounds."""
    if draught is None:
        return False
    return _DRAUGHT_MIN_M < draught <= _DRAUGHT_MAX_M


def _find_nearby_sts(db: Session, vessel_id: int, timestamp: datetime) -> Optional[int]:
    """Find STS event near the given timestamp for this vessel."""
    window = timedelta(hours=_STS_LINKAGE_HOURS)
    t_start = timestamp - window
    t_end = timestamp + window

    sts = db.query(StsTransferEvent).filter(
        ((StsTransferEvent.vessel_1_id == vessel_id) | (StsTransferEvent.vessel_2_id == vessel_id)),
        StsTransferEvent.start_time_utc <= t_end,
        StsTransferEvent.end_time_utc >= t_start,
    ).first()
    return sts.sts_id if sts else None


def run_draught_detection(db: Session) -> dict:
    """Run draught change detection across all vessels.

    Returns:
        dict with keys: events_created, vessels_processed, vessels_skipped
    """
    if not settings.DRAUGHT_DETECTION_ENABLED:
        return {"status": "disabled"}

    vessels = db.query(Vessel).all()
    ports = db.query(Port).all()

    events_created = 0
    vessels_processed = 0
    vessels_skipped = 0

    for batch_start in range(0, len(vessels), _VESSEL_BATCH_SIZE):
        batch = vessels[batch_start:batch_start + _VESSEL_BATCH_SIZE]

        for vessel in batch:
            threshold = _get_class_threshold(vessel.deadweight)

            # Load AIS points with non-null draught, ordered by timestamp
            points = (
                db.query(AISPoint)
                .filter(
                    AISPoint.vessel_id == vessel.vessel_id,
                    AISPoint.draught.isnot(None),
                )
                .order_by(AISPoint.timestamp_utc)
                .all()
            )

            # Skip vessels with <2 draught-populated points for sliding window
            if len(points) < 2:
                vessels_skipped += 1
            else:
                vessels_processed += 1

                # ── Sliding window draught change detection ──────────────
                # Track confirmed draught levels (need >=2 consecutive readings)
                i = 0
                while i < len(points) - 1:
                    # Validate current reading
                    if not _is_valid_draught(points[i].draught):
                        i += 1
                        continue

                    # Look ahead within the sliding window for a significant change
                    current_draught = points[i].draught
                    j = i + 1

                    while j < len(points):
                        if not _is_valid_draught(points[j].draught):
                            j += 1
                            continue

                        # Check if within 24h sliding window
                        time_diff = (points[j].timestamp_utc - points[i].timestamp_utc).total_seconds() / 3600.0
                        if time_diff > _SLIDING_WINDOW_HOURS:
                            break

                        new_draught = points[j].draught
                        delta = new_draught - current_draught

                        if abs(delta) >= threshold:
                            # Require >=2 consecutive readings confirming new draught
                            confirmed = False
                            for k in range(j + 1, len(points)):
                                if not _is_valid_draught(points[k].draught):
                                    continue
                                # Confirming reading should be close to the new draught
                                if abs(points[k].draught - new_draught) < threshold * 0.5:
                                    confirmed = True
                                    break
                                else:
                                    # Different value — not confirmed
                                    break

                            if not confirmed:
                                j += 1
                                continue

                            # Check port proximity
                            port_id, port_dist, is_offshore_terminal = _find_nearest_port(
                                points[j].lat, points[j].lon, ports
                            )

                            # Skip if near offshore terminal (legitimate loading)
                            if is_offshore_terminal and port_dist is not None and port_dist < _OFFSHORE_TERMINAL_SKIP_NM:
                                j += 1
                                continue

                            # Skip if near regular port (port operation)
                            if port_dist is not None and port_dist < _REGULAR_PORT_SKIP_NM:
                                j += 1
                                continue

                            # Determine risk score component
                            is_offshore = port_dist is None or port_dist >= _REGULAR_PORT_SKIP_NM
                            risk_score = 0

                            # Check for nearby STS event
                            sts_id = _find_nearby_sts(db, vessel.vessel_id, points[j].timestamp_utc)

                            if abs(delta) >= threshold * 2:
                                risk_score = 25  # draught_swing_extreme
                            elif is_offshore:
                                risk_score = 20  # offshore_draught_change_corroboration
                            if sts_id is not None:
                                risk_score = max(risk_score, 15)  # draught_sts_confirmation

                            event = DraughtChangeEvent(
                                vessel_id=vessel.vessel_id,
                                timestamp_utc=points[j].timestamp_utc,
                                old_draught_m=current_draught,
                                new_draught_m=new_draught,
                                delta_m=delta,
                                nearest_port_id=port_id,
                                distance_to_port_nm=round(port_dist, 1) if port_dist is not None else None,
                                is_offshore=is_offshore,
                                linked_sts_id=sts_id,
                                risk_score_component=risk_score,
                            )
                            db.add(event)
                            events_created += 1

                            # Move past this change to avoid duplicate detections
                            i = j
                            break

                        j += 1
                    else:
                        i += 1
                        continue
                    i += 1

            # ── Draught-across-gaps: compare draught at gap_off vs gap_on ────
            # This runs for ALL vessels (including those with <2 draught points
            # in the filtered set), since gap analysis queries its own pre/post
            # gap draught readings independently.
            gap_events = (
                db.query(AISGapEvent)
                .filter(AISGapEvent.vessel_id == vessel.vessel_id)
                .all()
            )

            for gap in gap_events:
                # Find draught reading closest to gap start
                pre_gap_point = (
                    db.query(AISPoint)
                    .filter(
                        AISPoint.vessel_id == vessel.vessel_id,
                        AISPoint.draught.isnot(None),
                        AISPoint.timestamp_utc <= gap.gap_start_utc,
                    )
                    .order_by(AISPoint.timestamp_utc.desc())
                    .first()
                )

                # Find draught reading closest to gap end
                post_gap_point = (
                    db.query(AISPoint)
                    .filter(
                        AISPoint.vessel_id == vessel.vessel_id,
                        AISPoint.draught.isnot(None),
                        AISPoint.timestamp_utc >= gap.gap_end_utc,
                    )
                    .order_by(AISPoint.timestamp_utc)
                    .first()
                )

                if not pre_gap_point or not post_gap_point:
                    continue

                if not _is_valid_draught(pre_gap_point.draught) or not _is_valid_draught(post_gap_point.draught):
                    continue

                gap_delta = post_gap_point.draught - pre_gap_point.draught
                if abs(gap_delta) < threshold:
                    continue

                # Check port proximity at gap_on location
                port_id, port_dist, is_offshore_terminal = _find_nearest_port(
                    post_gap_point.lat, post_gap_point.lon, ports
                )

                is_offshore = port_dist is None or port_dist >= _REGULAR_PORT_SKIP_NM

                event = DraughtChangeEvent(
                    vessel_id=vessel.vessel_id,
                    timestamp_utc=post_gap_point.timestamp_utc,
                    old_draught_m=pre_gap_point.draught,
                    new_draught_m=post_gap_point.draught,
                    delta_m=gap_delta,
                    nearest_port_id=port_id,
                    distance_to_port_nm=round(port_dist, 1) if port_dist is not None else None,
                    is_offshore=is_offshore,
                    linked_gap_id=gap.gap_event_id,
                    risk_score_component=20,  # draught_delta_across_gap
                )
                db.add(event)
                events_created += 1

    db.commit()
    logger.info(
        "Draught detection: %d events from %d vessels (%d skipped)",
        events_created, vessels_processed, vessels_skipped,
    )
    return {
        "events_created": events_created,
        "vessels_processed": vessels_processed,
        "vessels_skipped": vessels_skipped,
    }
