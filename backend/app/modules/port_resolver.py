"""Port resolution — maps lat/lon + optional port name to internal Port records.

Strategy (in order):
1. Geo-nearest port within 10nm of provided coordinates
2. Name normalization match (if port_name provided)
3. Return None if no match (caller creates PortCall with port_id=NULL)
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.port import Port
from app.utils.geo import haversine_nm, load_geometry

logger = logging.getLogger(__name__)

_PORT_MATCH_RADIUS_NM = 10.0


def resolve_port(
    db: Session,
    lat: float,
    lon: float,
    port_name: Optional[str] = None,
) -> Optional[Port]:
    """Resolve coordinates + optional name to an internal Port record.

    Args:
        db: Active SQLAlchemy session.
        lat: Latitude of the port position.
        lon: Longitude of the port position.
        port_name: Optional port name from external source (e.g., GFW).

    Returns:
        Matching Port or None.
    """
    ports = db.query(Port).all()
    if not ports:
        return None

    # 1. Geo-nearest within radius
    best_port = None
    best_dist = _PORT_MATCH_RADIUS_NM
    for port in ports:
        port_shape = load_geometry(port.geometry)
        if port_shape is None:
            continue
        port_lat, port_lon = port_shape.y, port_shape.x
        dist = haversine_nm(lat, lon, port_lat, port_lon)
        if dist < best_dist:
            best_dist = dist
            best_port = port

    if best_port is not None:
        return best_port

    # 2. Name match (if provided)
    if port_name:
        normalized = port_name.strip().upper()
        for port in ports:
            if port.name and port.name.strip().upper() == normalized:
                return port

    return None
