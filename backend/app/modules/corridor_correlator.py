"""Corridor correlator — links AIS gap events to maritime corridors and dark zones.

For each gap event the correlator constructs the straight-line trajectory between
the gap's start and end AIS points and tests that line against every corridor (and
dark zone) polygon stored in the database.

Spatial query strategy:
  Bounding-box check: parses the corridor geometry WKT and checks whether either
  endpoint falls within the derived min/max bounds.  A small tolerance
  (BBOX_TOLERANCE_DEG) is added to avoid edge-case misses.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.ais_point import AISPoint
from app.models.corridor import Corridor
from app.models.dark_zone import DarkZone
from app.models.gap_event import AISGapEvent

logger = logging.getLogger(__name__)

# Bounding-box tolerance in degrees.
BBOX_TOLERANCE_DEG: float = 0.1


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _parse_wkt_bbox(wkt: str) -> Optional[tuple[float, float, float, float]]:
    """Return (min_lon, min_lat, max_lon, max_lat) from a POLYGON WKT string.

    Extracts all numeric coordinate pairs and derives an axis-aligned bounding
    box.  Returns None if the WKT cannot be parsed.

    The WKT coordinate order is ``lon lat`` (OGC convention), which
    matches the POLYGON stored as WKT text in the geometry column.
    """
    pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", wkt)
    if not pairs:
        return None
    lons = [float(p[0]) for p in pairs]
    lats = [float(p[1]) for p in pairs]
    return min(lons), min(lats), max(lons), max(lats)


def _point_in_bbox(
    lat: float,
    lon: float,
    bbox: tuple[float, float, float, float],
    tolerance: float = BBOX_TOLERANCE_DEG,
) -> bool:
    """Return True if (lat, lon) lies within bbox expanded by tolerance degrees."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        (min_lon - tolerance) <= lon <= (max_lon + tolerance)
        and (min_lat - tolerance) <= lat <= (max_lat + tolerance)
    )


def _geometry_wkt(geometry_value: object) -> Optional[str]:
    """Extract a plain WKT string from a geometry column value.

    The column is now plain Text storing WKT directly, so this is a simple
    pass-through with validation.
    """
    if geometry_value is None:
        return None
    raw = str(geometry_value)
    if "POLYGON" in raw.upper() or "MULTIPOLYGON" in raw.upper():
        match = re.search(r"((?:MULTI)?POLYGON\s*\(.*)", raw, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    return None


# ── Spatial query ─────────────────────────────────────────────────────────────

def _intersecting_rows(
    db: Session,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    model: type,
) -> list:
    """Return model rows whose geometry bbox contains either gap endpoint."""
    candidates = db.query(model).filter(model.geometry.isnot(None)).all()
    matches: list = []
    for row in candidates:
        wkt = _geometry_wkt(row.geometry)
        if wkt is None:
            continue
        bbox = _parse_wkt_bbox(wkt)
        if bbox is None:
            continue
        if _point_in_bbox(start_lat, start_lon, bbox) or _point_in_bbox(end_lat, end_lon, bbox):
            matches.append(row)
    return matches


# ── AIS point loader ──────────────────────────────────────────────────────────

def _load_gap_endpoints(
    db: Session, gap: AISGapEvent
) -> Optional[tuple[AISPoint, AISPoint]]:
    """Return (start_point, end_point) for a gap, or None if either is missing."""
    if gap.start_point_id is None or gap.end_point_id is None:
        return None

    start_pt = db.get(AISPoint, gap.start_point_id)
    end_pt = db.get(AISPoint, gap.end_point_id)

    if start_pt is None or end_pt is None:
        logger.debug(
            "Gap %d: AIS point(s) not found (start_point_id=%s, end_point_id=%s) — skipping.",
            gap.gap_event_id,
            gap.start_point_id,
            gap.end_point_id,
        )
        return None

    return start_pt, end_pt


# ── Public API ────────────────────────────────────────────────────────────────

def find_corridor_for_gap(db: Session, gap: AISGapEvent) -> Optional[Corridor]:
    """Find the highest-risk corridor that the gap trajectory intersects.

    Tests the gap's start/end AIS point coordinates against all corridor
    polygon bounding boxes.  If multiple corridors match, the one with the
    highest ``risk_weight`` is returned.  Returns None when no match is found
    or when the gap has no associated AIS points.
    """
    endpoints = _load_gap_endpoints(db, gap)
    if endpoints is None:
        return None

    start_pt, end_pt = endpoints
    matches = _intersecting_rows(
        db,
        start_lat=start_pt.lat,
        start_lon=start_pt.lon,
        end_lat=end_pt.lat,
        end_lon=end_pt.lon,
        model=Corridor,
    )

    if not matches:
        return None

    return max(matches, key=lambda c: c.risk_weight)


def find_dark_zone_for_gap(db: Session, gap: AISGapEvent) -> Optional[DarkZone]:
    """Find the dark zone whose polygon bbox the gap trajectory intersects.

    Uses the same bounding-box approach as ``find_corridor_for_gap`` but
    queries the ``dark_zones`` table.

    If multiple dark zones match, the one with the lowest primary key
    (zone_id) is returned as a stable tie-breaker.
    """
    endpoints = _load_gap_endpoints(db, gap)
    if endpoints is None:
        return None

    start_pt, end_pt = endpoints
    matches = _intersecting_rows(
        db,
        start_lat=start_pt.lat,
        start_lon=start_pt.lon,
        end_lat=end_pt.lat,
        end_lon=end_pt.lon,
        model=DarkZone,
    )

    if not matches:
        return None

    return min(matches, key=lambda z: z.zone_id)


def correlate_all_uncorrelated_gaps(db: Session) -> dict:
    """Batch-correlate all gap events that have not yet been assigned a corridor.

    For every AISGapEvent with ``corridor_id=None``:
      - Resolves the gap's trajectory from its start/end AIS points.
      - Calls ``find_corridor_for_gap`` to assign ``corridor_id``.
      - Checks whether the matched corridor is a jamming zone
        (``is_jamming_zone=True``) and, if so, sets ``in_dark_zone=True``.
      - Independently calls ``find_dark_zone_for_gap`` to populate
        ``dark_zone_id`` and set ``in_dark_zone=True``.

    Persists all updates in a single ``db.commit()`` at the end.

    Returns:
        A summary dict::

            {
                "correlated": <int>,   # gaps assigned a corridor_id
                "dark_zone": <int>,    # gaps marked in_dark_zone=True
            }
    """
    uncorrelated_gaps = (
        db.query(AISGapEvent).filter(AISGapEvent.corridor_id.is_(None)).all()
    )

    correlated_count = 0
    dark_zone_count = 0

    for gap in uncorrelated_gaps:
        corridor = find_corridor_for_gap(db, gap)
        if corridor is not None:
            gap.corridor_id = corridor.corridor_id
            correlated_count += 1
            if corridor.is_jamming_zone:
                gap.in_dark_zone = True

        dark_zone = find_dark_zone_for_gap(db, gap)
        if dark_zone is not None:
            gap.dark_zone_id = dark_zone.zone_id
            gap.in_dark_zone = True

        if gap.in_dark_zone:
            dark_zone_count += 1

    db.commit()

    logger.info(
        "Corridor correlation complete: %d/%d gaps correlated, %d in dark zone.",
        correlated_count,
        len(uncorrelated_gaps),
        dark_zone_count,
    )

    return {"correlated": correlated_count, "dark_zone": dark_zone_count}
