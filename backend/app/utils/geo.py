"""Shared geodesic distance and geometry utilities.

Canonical implementations of haversine distance used across gap_detector,
sts_detector, and other modules.  Also provides WKT ↔ Shapely helpers for
the SQLite-backed geometry columns (plain Text storing WKT).
"""
from __future__ import annotations

import math
import re
from typing import Optional

import shapely.wkt
from shapely.geometry.base import BaseGeometry

_EARTH_RADIUS_NM: float = 3440.065   # Earth mean radius in nautical miles
_EARTH_RADIUS_M: float = 6_371_000.0  # Earth mean radius in metres


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles between two WGS-84 coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return _EARTH_RADIUS_NM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return _EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── WKT ↔ Shapely helpers ────────────────────────────────────────────────────

def load_geometry(wkt_str: Optional[str]) -> Optional[BaseGeometry]:
    """Load a WKT text string (from DB) into a Shapely geometry object."""
    if not wkt_str:
        return None
    return shapely.wkt.loads(wkt_str)


def dump_geometry(shape: BaseGeometry) -> str:
    """Dump a Shapely geometry to WKT for DB storage."""
    return shape.wkt


# ── Coordinate extraction helpers ────────────────────────────────────────────

def parse_wkt_bbox(wkt: str) -> tuple[float, float, float, float] | None:
    """Return (min_lon, min_lat, max_lon, max_lat) from any WKT string.

    Extracts all numeric coordinate pairs via regex.  Returns None if no
    coordinate pairs are found.  Accepts raw WKT or stringified geometry
    objects (calls ``str()`` on the input first).
    """
    raw = str(wkt) if wkt is not None else ""
    pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", raw)
    if not pairs:
        return None
    lons = [float(p[0]) for p in pairs]
    lats = [float(p[1]) for p in pairs]
    return min(lons), min(lats), max(lons), max(lats)


def parse_wkt_point(wkt: str | None) -> tuple[float, float] | None:
    """Extract (lat, lon) from a WKT POINT string like ``POINT(lon lat)``.

    Returns (lat, lon) tuple matching the haversine_nm(lat, lon, …) convention,
    or None if the string cannot be parsed.
    """
    if not wkt:
        return None
    m = re.match(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)", wkt, re.IGNORECASE)
    if not m:
        return None
    lon, lat = float(m.group(1)), float(m.group(2))
    return (lat, lon)


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute initial (forward) bearing in degrees (0-360) from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_lambda) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda))
    return math.degrees(math.atan2(x, y)) % 360
