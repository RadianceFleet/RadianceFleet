"""Port call detection from AIS data.

Detects port calls by finding periods where a vessel is:
  - Within 3nm of a known port
  - SOG < 1.0 kn
  - For > 2 consecutive hours

Creates PortCall records used by risk scoring's EU port call legitimacy signal.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.vessel import Vessel
from app.models.ais_point import AISPoint
from app.models.port import Port
from app.models.port_call import PortCall

logger = logging.getLogger(__name__)

# Detection thresholds
PORT_PROXIMITY_NM = 3.0
SOG_THRESHOLD_KN = 1.0
MIN_DURATION_HOURS = 2.0


def run_port_call_detection(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Detect port calls for all vessels in the given date range."""
    vessels = db.query(Vessel).all()
    total_calls = 0

    for vessel in vessels:
        calls = detect_port_calls_for_vessel(db, vessel, date_from, date_to)
        total_calls += calls

    logger.info("Port call detection complete: %d calls across %d vessels", total_calls, len(vessels))
    return {"port_calls_detected": total_calls, "vessels_processed": len(vessels)}


def detect_port_calls_for_vessel(
    db: Session,
    vessel: Vessel,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> int:
    """Detect port calls for a single vessel. Returns count of new PortCall records."""
    from app.utils.geo import haversine_nm

    query = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id == vessel.vessel_id)
        .order_by(AISPoint.timestamp_utc)
    )
    if date_from:
        query = query.filter(AISPoint.timestamp_utc >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        query = query.filter(AISPoint.timestamp_utc <= datetime.combine(date_to, datetime.max.time()))

    points = query.all()
    if len(points) < 2:
        return 0

    # Pre-fetch all ports for proximity checks
    ports = db.query(Port).filter(Port.major_port == True).all()
    if not ports:
        return 0

    # Build simple port lookup with lat/lon from WKT geometry
    from app.utils.geo import load_geometry
    port_coords = []
    for port in ports:
        pt = load_geometry(port.geometry)
        if pt is not None:
            port_coords.append((port, pt.y, pt.x))

    if not port_coords:
        logger.warning("Could not extract port coordinates — skipping port call detection")
        return 0

    call_count = 0
    # Track runs of low-SOG points near a port
    current_port = None
    run_start = None
    run_end = None

    for pt in points:
        if pt.sog is not None and pt.sog < SOG_THRESHOLD_KN:
            # Check proximity to any port
            nearest_port = None
            for port, p_lat, p_lon in port_coords:
                dist = haversine_nm(pt.lat, pt.lon, p_lat, p_lon)
                if dist <= PORT_PROXIMITY_NM:
                    nearest_port = port
                    break

            if nearest_port:
                if current_port is None or current_port.port_id != nearest_port.port_id:
                    # New port run — flush previous if valid
                    if current_port and run_start and run_end:
                        call_count += _maybe_create_port_call(
                            db, vessel, current_port, run_start, run_end
                        )
                    current_port = nearest_port
                    run_start = pt.timestamp_utc
                run_end = pt.timestamp_utc
                continue

        # Point is not near a port or moving too fast — flush run
        if current_port and run_start and run_end:
            call_count += _maybe_create_port_call(db, vessel, current_port, run_start, run_end)
        current_port = None
        run_start = None
        run_end = None

    # Flush final run
    if current_port and run_start and run_end:
        call_count += _maybe_create_port_call(db, vessel, current_port, run_start, run_end)

    if call_count:
        db.commit()
    return call_count


def _maybe_create_port_call(
    db: Session, vessel: Vessel, port: Port,
    arrival: datetime, departure: datetime,
) -> int:
    """Create a PortCall if duration >= threshold and not already recorded."""
    duration_h = (departure - arrival).total_seconds() / 3600
    if duration_h < MIN_DURATION_HOURS:
        return 0

    # Dedup: check for existing port call at same port within 24h
    existing = db.query(PortCall).filter(
        PortCall.vessel_id == vessel.vessel_id,
        PortCall.port_id == port.port_id,
        PortCall.arrival_utc >= arrival - timedelta(hours=24),
        PortCall.arrival_utc <= arrival + timedelta(hours=24),
    ).first()
    if existing:
        return 0

    db.add(PortCall(
        vessel_id=vessel.vessel_id,
        port_id=port.port_id,
        arrival_utc=arrival,
        departure_utc=departure,
    ))
    return 1
