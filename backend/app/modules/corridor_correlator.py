"""Corridor correlator — links AIS gap events to maritime corridors and dark zones.

For each gap event the correlator constructs the straight-line trajectory between
the gap's start and end AIS points and tests that line against every corridor (and
dark zone) polygon stored in the database.

Spatial query strategy (in order of preference):
  1. PostGIS / SpatiaLite: ST_Intersects(ST_MakeLine(...), corridor.geometry)
     Catches transits *through* a corridor even when neither endpoint lands inside it.
  2. Bounding-box fallback (no spatial extension): parses the corridor geometry WKT
     and checks whether either endpoint falls within the derived min/max bounds.
     A small tolerance (BBOX_TOLERANCE_DEG) is added to avoid edge-case misses.

The module logs a single warning the first time the spatial path fails so that
operators can confirm whether SpatiaLite is loaded.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import func
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.ais_point import AISPoint
from app.models.corridor import Corridor
from app.models.dark_zone import DarkZone
from app.models.gap_event import AISGapEvent

logger = logging.getLogger(__name__)

# Bounding-box tolerance in degrees when the spatial fallback is used.
BBOX_TOLERANCE_DEG: float = 0.1

# Module-level flag: set to True after the first failed ST_Intersects attempt
# so that the warning is emitted only once per process lifetime.
_spatial_unavailable: bool = False


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _parse_wkt_bbox(wkt: str) -> Optional[tuple[float, float, float, float]]:
    """Return (min_lon, min_lat, max_lon, max_lat) from a POLYGON WKT string.

    Extracts all numeric coordinate pairs and derives an axis-aligned bounding
    box.  Returns None if the WKT cannot be parsed.

    The WKT coordinate order is ``lon lat`` (OGC / GeoJSON convention), which
    matches the srid=4326 POLYGON stored by GeoAlchemy2.
    """
    # Match sequences of "lon lat" pairs (two floats separated by a space,
    # optionally preceded by a comma+space).
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
    """Extract a plain WKT string from a GeoAlchemy2 or raw geometry value.

    GeoAlchemy2 column values may be WKBElement objects that expose .desc
    (hex-encoded WKB) or can be coerced to str.  For the bounding-box fallback
    we only need the WKT, so we try a few representations and return the first
    that looks like a POLYGON/MULTIPOLYGON string.
    """
    if geometry_value is None:
        return None
    raw = str(geometry_value)
    # GeoAlchemy2 WKBElement str() returns hex or "SRID=...; POLYGON (...)".
    # If it already contains coordinate data we can use it directly.
    if "POLYGON" in raw.upper() or "MULTIPOLYGON" in raw.upper():
        # Strip optional SRID prefix
        match = re.search(r"((?:MULTI)?POLYGON\s*\(.*)", raw, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    # Unable to derive WKT from this value.
    return None


# ── Spatial query builders ────────────────────────────────────────────────────

def _st_intersects_trajectory(
    db: Session,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    model: type,
) -> list:
    """Query *model* rows whose geometry intersects the trajectory line.

    Raises OperationalError (or any DB exception) if the spatial functions are
    not available so the caller can fall back gracefully.

    Args:
        db: Active SQLAlchemy session.
        start_lat / start_lon: Gap-start coordinates.
        end_lat / end_lon: Gap-end coordinates.
        model: Either Corridor or DarkZone — must have a .geometry column.

    Returns:
        List of model instances whose geometry intersects the trajectory.
    """
    trajectory = func.ST_MakeLine(
        func.ST_MakePoint(start_lon, start_lat),
        func.ST_MakePoint(end_lon, end_lat),
    )
    return (
        db.query(model)
        .filter(model.geometry.isnot(None))
        .filter(func.ST_Intersects(trajectory, model.geometry))
        .all()
    )


def _bbox_fallback(
    db: Session,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    model: type,
) -> list:
    """Bounding-box fallback when ST_Intersects is unavailable.

    Loads all rows with non-null geometry, derives bounding boxes from WKT, and
    returns those where at least one gap endpoint is inside the bbox.

    Args:
        db: Active SQLAlchemy session.
        start_lat / start_lon: Gap-start coordinates.
        end_lat / end_lon: Gap-end coordinates.
        model: Either Corridor or DarkZone.

    Returns:
        List of matching model instances.
    """
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


def _intersecting_rows(
    db: Session,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    model: type,
) -> list:
    """Return model rows whose geometry intersects the gap trajectory.

    Attempts the ST_Intersects path first; if that raises an OperationalError
    (no spatial extension) it logs a one-time warning and delegates to the
    bounding-box fallback.
    """
    global _spatial_unavailable

    if not _spatial_unavailable:
        try:
            return _st_intersects_trajectory(db, start_lat, start_lon, end_lat, end_lon, model)
        except OperationalError:
            _spatial_unavailable = True
            logger.warning(
                "ST_Intersects / ST_MakeLine not available — spatial extension (SpatiaLite or "
                "PostGIS) is not loaded.  Falling back to bounding-box corridor correlation.  "
                "Results will be less precise for gaps that transit a corridor without an "
                "endpoint inside it."
            )
        except Exception as exc:  # noqa: BLE001
            # Unexpected spatial error — log and fall back rather than crash.
            _spatial_unavailable = True
            logger.warning(
                "Unexpected error during ST_Intersects query (%s).  Falling back to "
                "bounding-box corridor correlation.",
                exc,
            )

    return _bbox_fallback(db, start_lat, start_lon, end_lat, end_lon, model)


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

    Constructs the straight-line trajectory from the gap's start AIS point to
    its end AIS point and tests it against all corridor polygons using
    ST_Intersects.  When the spatial extension is unavailable the function falls
    back to a bounding-box overlap check.

    If multiple corridors match, the one with the highest ``risk_weight`` is
    returned.  Returns None when no match is found or when the gap has no
    associated AIS points.

    Args:
        db: Active SQLAlchemy session.
        gap: The AISGapEvent to correlate.

    Returns:
        The best-matching Corridor, or None.
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

    # Return the highest-risk corridor among all intersecting ones.
    return max(matches, key=lambda c: c.risk_weight)


def find_dark_zone_for_gap(db: Session, gap: AISGapEvent) -> Optional[DarkZone]:
    """Find the dark zone whose polygon the gap trajectory intersects.

    Uses the same ST_Intersects / bounding-box approach as
    ``find_corridor_for_gap`` but queries the ``dark_zones`` table.

    If multiple dark zones match, the one with the lowest primary key
    (zone_id) is returned as a stable tie-breaker.

    Args:
        db: Active SQLAlchemy session.
        gap: The AISGapEvent to correlate.

    Returns:
        The matching DarkZone, or None.
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

    Args:
        db: Active SQLAlchemy session.

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
        # ── Corridor correlation ──────────────────────────────────────────────
        corridor = find_corridor_for_gap(db, gap)
        if corridor is not None:
            gap.corridor_id = corridor.corridor_id
            correlated_count += 1

            # A corridor flagged as a jamming zone also constitutes a dark zone.
            if corridor.is_jamming_zone:
                gap.in_dark_zone = True

        # ── Dark zone correlation ─────────────────────────────────────────────
        # Run independently: a gap can intersect a dedicated DarkZone polygon
        # even when no corridor is matched (or when the corridor is not a
        # jamming zone).
        dark_zone = find_dark_zone_for_gap(db, gap)
        if dark_zone is not None:
            gap.dark_zone_id = dark_zone.zone_id
            gap.in_dark_zone = True

        # Count this gap if it ended up marked as in_dark_zone (either source).
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
