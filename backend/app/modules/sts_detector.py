"""STS (ship-to-ship) transfer event detector.

Implements two-phase proximity analysis over AIS data to surface suspected
oil-transfer events between tankers:

  Phase A — Confirmed transfers (detection_type='visible_visible')
    Uses haversine proximity, speed, and heading filters over 15-minute AIS
    buckets.  Pairs that remain within 200 m of each other for at least
    8 consecutive windows (2 hours) are persisted as StsTransferEvents.

  Phase B — Approaching vectors (detection_type='approaching')
    Identifies stationary tankers inside known STS-zone corridors and finds
    other tankers on an intercept course.  An event is created when the
    computed ETA is under 4 hours.

Performance note: AIS points are first indexed into a 1-degree lat/lon grid
so that only vessels sharing a grid cell are compared, avoiding an O(n²)
full cross-product.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.ais_point import AISPoint
from app.models.corridor import Corridor
from app.models.sts_transfer import StsTransferEvent
from app.models.vessel import Vessel
from app.models.base import STSDetectionTypeEnum, CorridorTypeEnum

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_NM_TO_METERS: float = 1852.0
_BUCKET_MINUTES: int = 15          # width of each time bucket
from app.config import settings as _settings
_MIN_CONSECUTIVE_WINDOWS: int = _settings.STS_MIN_WINDOWS
_PROXIMITY_METERS: float = _settings.STS_PROXIMITY_METERS
_SOG_STATIONARY: float = 1.0       # knots — Phase A "not moving"
_SOG_STATIONARY_B: float = 0.5     # knots — Phase B "anchor-like"
_SOG_APPROACHING_MIN: float = 0.5  # knots
_SOG_APPROACHING_MAX: float = 3.0  # knots
_COG_PARALLEL_DEG: float = 30.0    # tolerance for parallel / anti-parallel heading check
_ETA_MAX_MINUTES: int = 240        # 4-hour horizon for Phase B
_TANKER_MIN_DWT: float = 20_000.0  # DWT threshold when vessel_type not available

# risk_score_component values
_RISK_STS_ZONE: int = 35
_RISK_NO_ZONE: int = 25
_RISK_APPROACHING: int = 20


# ── Public entry point ────────────────────────────────────────────────────────

def detect_sts_events(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Run both detection phases and persist new StsTransferEvents.

    Args:
        db: Active SQLAlchemy session.
        date_from: Inclusive start date filter on AIS timestamps (UTC).
        date_to: Inclusive end date filter on AIS timestamps (UTC).

    Returns:
        ``{"sts_events_created": N}`` where N is the total number of new rows
        inserted across both phases.
    """
    from app.modules.risk_scoring import load_scoring_config
    config = load_scoring_config()

    corridors = db.query(Corridor).all()
    sts_zone_bboxes = _build_sts_zone_bboxes(corridors)

    tanker_ids = _tanker_vessel_ids(db)
    if not tanker_ids:
        logger.info("STS detector: no tanker vessels found — skipping.")
        return {"sts_events_created": 0}

    points = _load_ais_points(db, tanker_ids, date_from, date_to)
    logger.info(
        "STS detector: loaded %d AIS points for %d tanker vessels.",
        len(points),
        len(tanker_ids),
    )

    created_a = _phase_a(db, points, sts_zone_bboxes, corridors, config)
    created_b = _phase_b(db, points, sts_zone_bboxes, corridors, config)

    total = created_a + created_b
    logger.info(
        "STS detector complete: %d events created (Phase A: %d, Phase B: %d).",
        total, created_a, created_b,
    )
    return {"sts_events_created": total}


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS-84 coordinates.

    Thin wrapper around app.utils.geo.haversine_meters for backward compatibility.
    """
    from app.utils.geo import haversine_meters
    return haversine_meters(lat1, lon1, lat2, lon2)


def _heading_to_point(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float
) -> float:
    """Initial bearing (degrees 0-360) from one point to another."""
    lat1, lon1 = math.radians(from_lat), math.radians(from_lon)
    lat2, lon2 = math.radians(to_lat), math.radians(to_lon)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360.0) % 360.0


def _heading_diff(h1: float, h2: float) -> float:
    """Minimum angular difference between two headings, result in [0, 180]."""
    diff = abs(h1 - h2) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


# ── Data loading helpers ──────────────────────────────────────────────────────

def _tanker_vessel_ids(db: Session) -> list[int]:
    """Return vessel_ids for tankers (configurable via vessel_filter.yaml)."""
    from app.utils.vessel_filter import is_tanker_type
    vessels = db.query(Vessel).all()
    return [v.vessel_id for v in vessels if is_tanker_type(v)]


def _load_ais_points(
    db: Session,
    vessel_ids: list[int],
    date_from: Optional[date],
    date_to: Optional[date],
) -> list[AISPoint]:
    """Load AIS points for given vessel IDs within the optional date window."""
    query = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id.in_(vessel_ids))
        .order_by(AISPoint.timestamp_utc)
    )
    if date_from:
        query = query.filter(
            AISPoint.timestamp_utc >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to:
        query = query.filter(
            AISPoint.timestamp_utc <= datetime.combine(date_to, datetime.max.time())
        )
    return query.all()


# ── Corridor / bounding-box helpers ──────────────────────────────────────────

def _parse_wkt_bbox(
    geometry_value: object,
) -> Optional[tuple[float, float, float, float]]:
    """Extract (min_lon, min_lat, max_lon, max_lat) from a GeoAlchemy2 geometry value."""
    import re

    if geometry_value is None:
        return None
    raw = str(geometry_value)
    pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", raw)
    if not pairs:
        return None
    lons = [float(p[0]) for p in pairs]
    lats = [float(p[1]) for p in pairs]
    return min(lons), min(lats), max(lons), max(lats)


def _build_sts_zone_bboxes(
    corridors: list[Corridor],
) -> list[tuple[Corridor, tuple[float, float, float, float]]]:
    """Return (corridor, bbox) pairs for every STS-zone corridor with geometry."""
    result = []
    for c in corridors:
        ct = str(c.corridor_type.value if hasattr(c.corridor_type, "value") else c.corridor_type)
        if ct != CorridorTypeEnum.STS_ZONE.value:
            continue
        bbox = _parse_wkt_bbox(c.geometry)
        if bbox is not None:
            result.append((c, bbox))
    return result


def _in_bbox(
    lat: float,
    lon: float,
    bbox: tuple[float, float, float, float],
    tolerance: float = 0.05,
) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        (min_lon - tolerance) <= lon <= (max_lon + tolerance)
        and (min_lat - tolerance) <= lat <= (max_lat + tolerance)
    )


def _corridor_for_position(
    lat: float,
    lon: float,
    sts_zone_bboxes: list[tuple[Corridor, tuple]],
) -> Optional[Corridor]:
    """Return the first STS-zone corridor whose bounding box contains the point."""
    for corridor, bbox in sts_zone_bboxes:
        if _in_bbox(lat, lon, bbox):
            return corridor
    return None


def _any_sts_zone_corridor(
    corridors: list[Corridor],
) -> Optional[Corridor]:
    """Return the first STS-zone corridor (used as a fallback reference)."""
    for c in corridors:
        ct = str(c.corridor_type.value if hasattr(c.corridor_type, "value") else c.corridor_type)
        if ct == CorridorTypeEnum.STS_ZONE.value:
            return c
    return None


# ── Bucketing helpers ─────────────────────────────────────────────────────────

def _bucket_key(ts: datetime) -> int:
    """Map a timestamp to an integer 15-minute bucket index (minutes since epoch)."""
    epoch_minutes = int(ts.timestamp() // 60)
    return (epoch_minutes // _BUCKET_MINUTES) * _BUCKET_MINUTES


def _grid_cell(lat: float, lon: float) -> tuple[int, int]:
    """Map coordinates to a 1-degree integer grid cell."""
    return int(math.floor(lat)), int(math.floor(lon))


# ── Dark vessel gap overlap helper ───────────────────────────────────────────


def _apply_dark_vessel_bonus(
    db: Session,
    event: "StsTransferEvent",
    vessel_1_id: int,
    vessel_2_id: int,
    config: dict,
) -> None:
    """Add +15 to event.risk_score_component if either vessel has a gap overlapping the STS event.

    Temporal join: gap_start < sts_end + 2h  AND  gap_end > sts_start - 2h.

    NOTE: This check requires run_gap_detection() to have been run first.
    If detect-sts is run before detect-gaps, no gap records will exist and this check
    silently returns 0 (correct — no false fire, but signal will be missing).
    """
    from app.models.gap_event import AISGapEvent
    from sqlalchemy import or_

    two_hours = timedelta(hours=2)
    dark_gap = db.query(AISGapEvent).filter(
        or_(
            AISGapEvent.vessel_id == vessel_1_id,
            AISGapEvent.vessel_id == vessel_2_id,
        ),
        AISGapEvent.gap_start_utc < event.end_time_utc + two_hours,
        AISGapEvent.gap_end_utc > event.start_time_utc - two_hours,
    ).first()

    if dark_gap:
        bonus = config.get("sts", {}).get("one_vessel_dark_during_proximity", 15)
        event.risk_score_component += bonus


# ── Deduplication helper ──────────────────────────────────────────────────────

def _overlap_exists(
    db: Session,
    vessel_1_id: int,
    vessel_2_id: int,
    start_time: datetime,
    end_time: datetime,
) -> bool:
    """Return True if a StsTransferEvent already covers this vessel pair / time window."""
    # Check both orderings of the vessel pair.
    for v1, v2 in [(vessel_1_id, vessel_2_id), (vessel_2_id, vessel_1_id)]:
        existing = (
            db.query(StsTransferEvent)
            .filter(
                StsTransferEvent.vessel_1_id == v1,
                StsTransferEvent.vessel_2_id == v2,
                StsTransferEvent.start_time_utc <= end_time,
                StsTransferEvent.end_time_utc >= start_time,
            )
            .first()
        )
        if existing:
            return True
    return False


# ── Phase A — confirmed visible-visible transfers ─────────────────────────────

def _phase_a(
    db: Session,
    points: list[AISPoint],
    sts_zone_bboxes: list[tuple[Corridor, tuple]],
    corridors: list[Corridor],
    config: dict = None,
) -> int:
    """Detect confirmed STS transfers from AIS proximity patterns.

    Algorithm:
      1. Index all points into (vessel_id, bucket_key) pairs.
      2. For each 15-minute bucket, build a 1-degree lat/lon grid.
      3. For every pair of distinct tankers sharing a grid cell, apply the
         proximity + SOG + heading filter.
      4. Accumulate consecutive passing windows; create an event after
         MIN_CONSECUTIVE_WINDOWS (8) windows.

    Returns the count of new StsTransferEvents inserted.
    """
    # vessel_id -> sorted list of AISPoints
    vessel_points: dict[int, list[AISPoint]] = defaultdict(list)
    for pt in points:
        vessel_points[pt.vessel_id].append(pt)

    # (vessel_id, bucket) -> representative AIS point (latest in bucket)
    bucket_index: dict[tuple[int, int], AISPoint] = {}
    for vessel_id, pts in vessel_points.items():
        for pt in pts:
            bk = _bucket_key(pt.timestamp_utc)
            key = (vessel_id, bk)
            # Use the last point in the bucket as the representative sample.
            if key not in bucket_index or pt.timestamp_utc > bucket_index[key].timestamp_utc:
                bucket_index[key] = pt

    # Group representative points by bucket -> grid cell -> list of vessel_ids
    # Structure: bucket_key -> grid_cell -> [(vessel_id, AISPoint), ...]
    bucket_grid: dict[int, dict[tuple[int, int], list[tuple[int, AISPoint]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (vessel_id, bk), pt in bucket_index.items():
        cell = _grid_cell(pt.lat, pt.lon)
        bucket_grid[bk][cell].append((vessel_id, pt))

    # pair -> list of consecutive window passing timestamps
    # pair = (min_vessel_id, max_vessel_id) for canonical ordering
    # Value: list of (bucket_key, dist_m, mean_lat, mean_lon)
    pair_windows: dict[
        tuple[int, int], list[tuple[int, float, float, float]]
    ] = defaultdict(list)

    for bk in sorted(bucket_grid.keys()):
        grid = bucket_grid[bk]
        for cell, vessel_list in grid.items():
            if len(vessel_list) < 2:
                continue
            # Check all pairs in this grid cell.
            for i in range(len(vessel_list)):
                for j in range(i + 1, len(vessel_list)):
                    vid_a, pt_a = vessel_list[i]
                    vid_b, pt_b = vessel_list[j]

                    dist_m = _haversine_meters(pt_a.lat, pt_a.lon, pt_b.lat, pt_b.lon)
                    if dist_m >= _PROXIMITY_METERS:
                        continue

                    sog_a = pt_a.sog if pt_a.sog is not None else 999.0
                    sog_b = pt_b.sog if pt_b.sog is not None else 999.0
                    if sog_a >= _SOG_STATIONARY or sog_b >= _SOG_STATIONARY:
                        continue

                    # Heading filter: parallel (|diff| < 30°) or anti-parallel (|diff - 180°| < 30°)
                    cog_a = pt_a.cog
                    cog_b = pt_b.cog
                    if cog_a is not None and cog_b is not None:
                        diff = _heading_diff(cog_a, cog_b)
                        parallel = diff < _COG_PARALLEL_DEG
                        anti_parallel = abs(diff - 180.0) < _COG_PARALLEL_DEG
                        if not (parallel or anti_parallel):
                            continue

                    pair_key = (min(vid_a, vid_b), max(vid_a, vid_b))
                    mean_lat = (pt_a.lat + pt_b.lat) / 2.0
                    mean_lon = (pt_a.lon + pt_b.lon) / 2.0
                    pair_windows[pair_key].append((bk, dist_m, mean_lat, mean_lon))

    # Evaluate each pair's window list for consecutive runs.
    created = 0
    for (vid1, vid2), windows in pair_windows.items():
        if len(windows) < _MIN_CONSECUTIVE_WINDOWS:
            continue

        # Windows are ordered by bucket_key (bk).  A "consecutive run" means
        # successive buckets differ by exactly _BUCKET_MINUTES.
        windows.sort(key=lambda w: w[0])
        run_start = 0

        for idx in range(1, len(windows) + 1):
            is_last = idx == len(windows)
            consecutive = (
                not is_last
                and windows[idx][0] - windows[idx - 1][0] == _BUCKET_MINUTES
            )

            if not consecutive:
                run_len = idx - run_start
                if run_len >= _MIN_CONSECUTIVE_WINDOWS:
                    run = windows[run_start:idx]
                    start_bk = run[0][0]
                    end_bk = run[-1][0]
                    start_dt = datetime.fromtimestamp(start_bk * 60, tz=timezone.utc)
                    end_dt = datetime.fromtimestamp((end_bk + _BUCKET_MINUTES) * 60, tz=timezone.utc)

                    if _overlap_exists(db, vid1, vid2, start_dt, end_dt):
                        run_start = idx
                        continue

                    mean_dist = sum(w[1] for w in run) / len(run)
                    mean_lat = sum(w[2] for w in run) / len(run)
                    mean_lon = sum(w[3] for w in run) / len(run)
                    duration = int((end_dt - start_dt).total_seconds() / 60)

                    # Port proximity filter: skip if both vessels are within 3nm of a major port
                    from app.models.port import Port
                    from app.utils.geo import haversine_nm
                    try:
                        ports = db.query(Port).filter(Port.major_port == True).all()
                        in_port = False
                        for port in ports:
                            try:
                                from geoalchemy2.shape import to_shape
                                port_shape = to_shape(port.geometry)
                                port_lat, port_lon = port_shape.y, port_shape.x
                            except Exception:
                                continue
                            d1 = haversine_nm(mean_lat, mean_lon, port_lat, port_lon)
                            if d1 < 3.0:
                                in_port = True
                                break
                        if in_port:
                            run_start = idx
                            continue
                    except Exception:
                        pass  # If port check fails, proceed with STS detection

                    corridor = _corridor_for_position(mean_lat, mean_lon, sts_zone_bboxes)
                    if corridor is not None:
                        risk = _RISK_STS_ZONE
                    else:
                        risk = _RISK_NO_ZONE
                        corridor = None

                    event = StsTransferEvent(
                        vessel_1_id=vid1,
                        vessel_2_id=vid2,
                        detection_type=STSDetectionTypeEnum.VISIBLE_VISIBLE,
                        start_time_utc=start_dt,
                        end_time_utc=end_dt,
                        duration_minutes=duration,
                        mean_proximity_meters=round(mean_dist, 1),
                        mean_lat=round(mean_lat, 6),
                        mean_lon=round(mean_lon, 6),
                        corridor_id=corridor.corridor_id if corridor else None,
                        risk_score_component=risk,
                    )
                    db.add(event)
                    if config is not None:
                        _apply_dark_vessel_bonus(db, event, vid1, vid2, config)
                    created += 1

                run_start = idx

    db.commit()
    logger.info("Phase A: %d visible-visible STS events created.", created)
    return created


# ── Phase B — approaching vectors ─────────────────────────────────────────────

def _phase_b(
    db: Session,
    points: list[AISPoint],
    sts_zone_bboxes: list[tuple[Corridor, tuple]],
    corridors: list[Corridor],
    config: dict = None,
) -> int:
    """Detect approaching-vector STS precursors inside known STS zones.

    For each stationary tanker (SOG < 0.5 kn) whose latest AIS point falls
    within an STS-zone corridor bounding box, find other tankers approaching
    it (SOG 0.5–3 kn, bearing toward stationary).  Create an event when the
    estimated time of arrival is under 4 hours.

    Returns the count of new StsTransferEvents inserted.
    """
    if not sts_zone_bboxes:
        logger.debug("Phase B: no STS-zone corridors with geometry — skipping.")
        return 0

    # Build a map of vessel_id -> latest AISPoint.
    latest: dict[int, AISPoint] = {}
    for pt in points:
        if pt.vessel_id not in latest or pt.timestamp_utc > latest[pt.vessel_id].timestamp_utc:
            latest[pt.vessel_id] = pt

    # Separate stationary vessels inside STS zones from all others.
    stationary_in_zone: list[tuple[AISPoint, Corridor]] = []
    all_moving: list[AISPoint] = []

    for pt in latest.values():
        sog = pt.sog if pt.sog is not None else 999.0
        corridor = _corridor_for_position(pt.lat, pt.lon, sts_zone_bboxes)
        if sog < _SOG_STATIONARY_B and corridor is not None:
            stationary_in_zone.append((pt, corridor))
        elif _SOG_APPROACHING_MIN <= sog <= _SOG_APPROACHING_MAX:
            all_moving.append(pt)

    if not stationary_in_zone:
        logger.debug("Phase B: no stationary tankers found in STS zones.")
        return 0

    created = 0
    for stat_pt, corridor in stationary_in_zone:
        for mov_pt in all_moving:
            if mov_pt.vessel_id == stat_pt.vessel_id:
                continue

            dist_m = _haversine_meters(stat_pt.lat, stat_pt.lon, mov_pt.lat, mov_pt.lon)

            # Compute the bearing from the moving vessel toward the stationary one.
            bearing_to_stat = _heading_to_point(
                mov_pt.lat, mov_pt.lon, stat_pt.lat, stat_pt.lon
            )
            mov_cog = mov_pt.cog if mov_pt.cog is not None else mov_pt.heading
            if mov_cog is None:
                continue

            # Accept if the vessel is heading roughly toward the stationary one.
            if _heading_diff(mov_cog, bearing_to_stat) > _COG_PARALLEL_DEG:
                continue

            dist_nm = dist_m / _NM_TO_METERS
            sog = mov_pt.sog  # already confirmed >= 0.5 kn above
            eta_minutes = int((dist_nm / sog) * 60)

            if eta_minutes >= _ETA_MAX_MINUTES:
                continue

            # Use the moving vessel's timestamp as the event reference time.
            event_time = mov_pt.timestamp_utc

            # Canonical pair ordering.
            vid1 = min(stat_pt.vessel_id, mov_pt.vessel_id)
            vid2 = max(stat_pt.vessel_id, mov_pt.vessel_id)
            eta_end = datetime.fromtimestamp(
                event_time.timestamp() + eta_minutes * 60, tz=timezone.utc
            )

            if _overlap_exists(db, vid1, vid2, event_time, eta_end):
                continue

            mean_lat = (stat_pt.lat + mov_pt.lat) / 2.0
            mean_lon = (stat_pt.lon + mov_pt.lon) / 2.0

            event = StsTransferEvent(
                vessel_1_id=vid1,
                vessel_2_id=vid2,
                detection_type=STSDetectionTypeEnum.APPROACHING,
                start_time_utc=event_time,
                end_time_utc=eta_end,
                duration_minutes=eta_minutes,
                mean_proximity_meters=round(dist_m, 1),
                mean_lat=round(mean_lat, 6),
                mean_lon=round(mean_lon, 6),
                corridor_id=corridor.corridor_id,
                eta_minutes=eta_minutes,
                risk_score_component=_RISK_APPROACHING,
            )
            db.add(event)
            if config is not None:
                _apply_dark_vessel_bonus(db, event, vid1, vid2, config)
            created += 1

    db.commit()
    logger.info("Phase B: %d approaching-vector STS events created.", created)
    return created
