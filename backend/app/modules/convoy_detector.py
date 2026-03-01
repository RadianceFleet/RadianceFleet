"""Convoy detection — synchronized vessel movement analysis.

Detects pairs of vessels moving together at sustained proximity:
  - Distance < 5nm across multiple consecutive observations
  - Both SOG > 3kn (underway, not anchored together)
  - Heading delta < 15 degrees (same direction)
  - Duration >= 4 hours

Scoring tiers:
  4-8h  -> +15 (convoy_4_to_8h)
  8-24h -> +25 (convoy_8_to_24h)
  24h+  -> +35 (convoy_24h_plus)

Also includes floating storage detection and Arctic corridor no-ice-class scoring.

Performance: AIS points are indexed into a 1-degree lat/lon grid (same pattern
as sts_detector) to avoid O(n^2) comparisons.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.convoy_event import ConvoyEvent
from app.models.corridor import Corridor
from app.models.vessel import Vessel
from app.modules.risk_scoring import load_scoring_config
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_CONVOY_DISTANCE_NM: float = 5.0     # max distance for convoy pairing
_CONVOY_MIN_SOG_KN: float = 3.0      # both vessels must be underway
_CONVOY_HEADING_DELTA_DEG: float = 15.0  # max heading difference
_BUCKET_MINUTES: int = 15            # time bucket width (match sts_detector)
_MIN_CONSECUTIVE_WINDOWS: int = 16   # 16 * 15 min = 4 hours minimum
_MAX_PAIRS_PER_RUN: int = 5000       # safety cap on pair comparisons


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _grid_cell(lat: float, lon: float) -> tuple[int, int]:
    """Map coordinates to a 1-degree integer grid cell."""
    return int(math.floor(lat)), int(math.floor(lon))


def _heading_diff(h1: float, h2: float) -> float:
    """Minimum angular difference between two headings, result in [0, 180]."""
    diff = abs(h1 - h2) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


def _bucket_key(ts: datetime) -> int:
    """Map a timestamp to an integer 15-minute bucket index (minutes since epoch)."""
    epoch_minutes = int(ts.timestamp() // 60)
    return (epoch_minutes // _BUCKET_MINUTES) * _BUCKET_MINUTES


# ── Scoring helper ────────────────────────────────────────────────────────────

def _convoy_score(duration_hours: float, config: dict | None = None) -> int:
    """Return risk score component based on convoy duration tier."""
    convoy_cfg = (config or {}).get("convoy", {})
    if duration_hours >= 24:
        return convoy_cfg.get("convoy_24h_plus", 35)
    elif duration_hours >= 8:
        return convoy_cfg.get("convoy_8_to_24h", 25)
    elif duration_hours >= 4:
        return convoy_cfg.get("convoy_4_to_8h", 15)
    return 0


# ── Corridor matching ─────────────────────────────────────────────────────────

def _parse_wkt_bbox(geometry_value: object) -> Optional[tuple[float, float, float, float]]:
    """Extract (min_lon, min_lat, max_lon, max_lat) from WKT geometry."""
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


def _in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def _find_corridor(
    lat: float,
    lon: float,
    corridor_bboxes: list[tuple[Corridor, tuple[float, float, float, float]]],
) -> Optional[int]:
    """Return corridor_id of the first corridor whose bbox contains the point."""
    for corridor, bbox in corridor_bboxes:
        if _in_bbox(lat, lon, bbox):
            return corridor.corridor_id
    return None


# ── Deduplication ─────────────────────────────────────────────────────────────

def _convoy_overlap_exists(
    db: Session,
    vessel_a_id: int,
    vessel_b_id: int,
    start_time: datetime,
    end_time: datetime,
) -> bool:
    """Return True if a ConvoyEvent already covers this vessel pair / time window."""
    for va, vb in [(vessel_a_id, vessel_b_id), (vessel_b_id, vessel_a_id)]:
        existing = (
            db.query(ConvoyEvent)
            .filter(
                ConvoyEvent.vessel_a_id == va,
                ConvoyEvent.vessel_b_id == vb,
                ConvoyEvent.start_time_utc <= end_time,
                ConvoyEvent.end_time_utc >= start_time,
            )
            .first()
        )
        if existing:
            return True
    return False


# ── Main detection function ───────────────────────────────────────────────────

def detect_convoys(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Detect convoy events — pairs of vessels moving together in formation.

    Args:
        db: Active SQLAlchemy session.
        date_from: Inclusive start date filter on AIS timestamps (UTC).
        date_to: Inclusive end date filter on AIS timestamps (UTC).

    Returns:
        {"convoy_events_created": N}
    """
    if not settings.CONVOY_DETECTION_ENABLED:
        return {"convoy_events_created": 0, "status": "disabled"}

    config = load_scoring_config()

    # Build corridor bbox index
    corridors = db.query(Corridor).all()
    corridor_bboxes = []
    for c in corridors:
        bbox = _parse_wkt_bbox(c.geometry)
        if bbox is not None:
            corridor_bboxes.append((c, bbox))

    # Load AIS points
    query = db.query(AISPoint).order_by(AISPoint.timestamp_utc)
    if date_from:
        query = query.filter(
            AISPoint.timestamp_utc >= datetime.combine(date_from, datetime.min.time())
        )
    if date_to:
        query = query.filter(
            AISPoint.timestamp_utc <= datetime.combine(date_to, datetime.max.time())
        )
    points = query.all()

    if not points:
        return {"convoy_events_created": 0}

    # Step 1: Index points into (vessel_id, bucket) -> representative AISPoint
    bucket_index: dict[tuple[int, int], AISPoint] = {}
    for pt in points:
        bk = _bucket_key(pt.timestamp_utc)
        key = (pt.vessel_id, bk)
        if key not in bucket_index or pt.timestamp_utc > bucket_index[key].timestamp_utc:
            bucket_index[key] = pt

    # Step 2: Group by bucket -> grid cell -> vessel list
    bucket_grid: dict[int, dict[tuple[int, int], list[tuple[int, AISPoint]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (vessel_id, bk), pt in bucket_index.items():
        cell = _grid_cell(pt.lat, pt.lon)
        bucket_grid[bk][cell].append((vessel_id, pt))

    # Step 3: Find convoy pairs — vessels close, underway, same heading
    pair_windows: dict[
        tuple[int, int], list[tuple[int, float, float, float, float]]
    ] = defaultdict(list)

    for bk in sorted(bucket_grid.keys()):
        grid = bucket_grid[bk]
        for cell, vessel_list in grid.items():
            if len(vessel_list) < 2:
                continue
            for i in range(len(vessel_list)):
                for j in range(i + 1, len(vessel_list)):
                    vid_a, pt_a = vessel_list[i]
                    vid_b, pt_b = vessel_list[j]

                    # Distance check (nautical miles)
                    dist_nm = haversine_nm(pt_a.lat, pt_a.lon, pt_b.lat, pt_b.lon)
                    if dist_nm >= _CONVOY_DISTANCE_NM:
                        continue

                    # SOG check — both must be underway
                    sog_a = pt_a.sog if pt_a.sog is not None else 0.0
                    sog_b = pt_b.sog if pt_b.sog is not None else 0.0
                    if sog_a < _CONVOY_MIN_SOG_KN or sog_b < _CONVOY_MIN_SOG_KN:
                        continue

                    # Heading check — must be moving in same direction
                    heading_a = pt_a.cog if pt_a.cog is not None else pt_a.heading
                    heading_b = pt_b.cog if pt_b.cog is not None else pt_b.heading
                    if heading_a is None or heading_b is None:
                        continue
                    h_delta = _heading_diff(heading_a, heading_b)
                    if h_delta > _CONVOY_HEADING_DELTA_DEG:
                        continue

                    pair_key = (min(vid_a, vid_b), max(vid_a, vid_b))
                    mean_lat = (pt_a.lat + pt_b.lat) / 2.0
                    mean_lon = (pt_a.lon + pt_b.lon) / 2.0
                    pair_windows[pair_key].append((bk, dist_nm, mean_lat, mean_lon, h_delta))

    # Step 4: Evaluate consecutive runs and create ConvoyEvents
    created = 0
    for (vid_a, vid_b), windows in pair_windows.items():
        if len(windows) < _MIN_CONSECUTIVE_WINDOWS:
            continue

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

                    duration_hours = (end_dt - start_dt).total_seconds() / 3600.0

                    if _convoy_overlap_exists(db, vid_a, vid_b, start_dt, end_dt):
                        run_start = idx
                        continue

                    mean_dist = sum(w[1] for w in run) / len(run)
                    mean_lat = sum(w[2] for w in run) / len(run)
                    mean_lon = sum(w[3] for w in run) / len(run)
                    mean_h_delta = sum(w[4] for w in run) / len(run)

                    corridor_id = _find_corridor(mean_lat, mean_lon, corridor_bboxes)
                    score = _convoy_score(duration_hours, config)

                    event = ConvoyEvent(
                        vessel_a_id=vid_a,
                        vessel_b_id=vid_b,
                        start_time_utc=start_dt,
                        end_time_utc=end_dt,
                        duration_hours=round(duration_hours, 2),
                        mean_distance_nm=round(mean_dist, 2),
                        mean_heading_delta=round(mean_h_delta, 1),
                        corridor_id=corridor_id,
                        risk_score_component=score,
                        evidence_json={
                            "window_count": run_len,
                            "mean_distance_nm": round(mean_dist, 2),
                            "mean_heading_delta": round(mean_h_delta, 1),
                            "duration_hours": round(duration_hours, 2),
                        },
                    )
                    db.add(event)
                    created += 1

                run_start = idx

    db.commit()
    logger.info("Convoy detector: %d convoy events created.", created)
    return {"convoy_events_created": created}


# ── Floating storage detection ────────────────────────────────────────────────

def detect_floating_storage(db: Session) -> dict:
    """Detect floating storage intermediaries.

    A floating storage vessel is:
      - Stationary > 30 days (reuses loitering detection pattern)
      - Has >= 2 STS events → operating as an intermediary

    Returns dict with counts.
    """
    if not settings.CONVOY_DETECTION_ENABLED:
        return {"floating_storage_detected": 0, "status": "disabled"}

    from app.models.loitering_event import LoiteringEvent
    from app.models.sts_transfer import StsTransferEvent
    from sqlalchemy import or_, func

    config = load_scoring_config()
    convoy_cfg = config.get("convoy", {})
    fs_score = convoy_cfg.get("floating_storage_intermediary", 25)

    # Find vessels with extended loitering (>30 days = 720 hours)
    long_loiter = (
        db.query(LoiteringEvent)
        .filter(LoiteringEvent.duration_hours >= 720.0)
        .all()
    )

    if not long_loiter:
        return {"floating_storage_detected": 0}

    detected = 0
    for loiter in long_loiter:
        # Count STS events for this vessel
        sts_count = db.query(StsTransferEvent).filter(
            or_(
                StsTransferEvent.vessel_1_id == loiter.vessel_id,
                StsTransferEvent.vessel_2_id == loiter.vessel_id,
            )
        ).count()

        if sts_count >= 2:
            # Create a ConvoyEvent to record floating storage detection
            # (using ConvoyEvent table as the event store for Stage 5-B detections)
            existing = (
                db.query(ConvoyEvent)
                .filter(
                    ConvoyEvent.vessel_a_id == loiter.vessel_id,
                    ConvoyEvent.vessel_b_id == loiter.vessel_id,
                    ConvoyEvent.evidence_json.isnot(None),
                )
                .first()
            )
            # Simple dedup: check if already recorded
            if existing:
                continue

            event = ConvoyEvent(
                vessel_a_id=loiter.vessel_id,
                vessel_b_id=loiter.vessel_id,  # self-reference for floating storage
                start_time_utc=loiter.start_time_utc,
                end_time_utc=loiter.end_time_utc,
                duration_hours=loiter.duration_hours,
                mean_distance_nm=0.0,
                mean_heading_delta=0.0,
                corridor_id=loiter.corridor_id,
                risk_score_component=fs_score,
                evidence_json={
                    "type": "floating_storage",
                    "loiter_hours": loiter.duration_hours,
                    "sts_event_count": sts_count,
                },
            )
            db.add(event)
            detected += 1

    db.commit()
    logger.info("Floating storage detector: %d intermediaries detected.", detected)
    return {"floating_storage_detected": detected}


# ── Arctic corridor no-ice-class scoring ──────────────────────────────────────

_ARCTIC_CORRIDOR_TAGS = {"arctic", "nsr", "ice_class_required"}
_ARCTIC_MIN_LAT: float = 66.5  # Arctic Circle


def detect_arctic_no_ice_class(db: Session) -> dict:
    """Flag tankers transiting Arctic corridors without ice class.

    A vessel in an Arctic corridor (lat > 66.5N or tagged arctic/nsr)
    without ice class designation receives +25 risk score.

    Returns dict with counts.
    """
    if not settings.CONVOY_DETECTION_ENABLED:
        return {"arctic_flagged": 0, "status": "disabled"}

    config = load_scoring_config()
    convoy_cfg = config.get("convoy", {})
    arctic_score = convoy_cfg.get("arctic_no_ice_class", 25)

    # Find Arctic corridors (by tags or latitude)
    corridors = db.query(Corridor).all()
    arctic_corridor_ids: set[int] = set()
    arctic_bboxes: list[tuple[int, tuple[float, float, float, float]]] = []
    for c in corridors:
        tags = set()
        if hasattr(c, "tags") and c.tags:
            if isinstance(c.tags, list):
                tags = set(c.tags)
            elif isinstance(c.tags, str):
                tags = {t.strip() for t in c.tags.split(",")}
        bbox = _parse_wkt_bbox(c.geometry)
        if bbox is not None:
            min_lon, min_lat, max_lon, max_lat = bbox
            # Arctic if tagged or if bbox extends above Arctic Circle
            if tags & _ARCTIC_CORRIDOR_TAGS or max_lat >= _ARCTIC_MIN_LAT:
                arctic_corridor_ids.add(c.corridor_id)
                arctic_bboxes.append((c.corridor_id, bbox))

    if not arctic_corridor_ids:
        return {"arctic_flagged": 0}

    # Find vessels with AIS points in Arctic corridors
    vessels = db.query(Vessel).all()
    flagged = 0

    for vessel in vessels:
        # Check if vessel has ice class — look at vessel_type for ice indicators
        vessel_type = (vessel.vessel_type or "").lower()
        has_ice_class = any(
            kw in vessel_type
            for kw in ["ice", "arctic", "polar", "ice class", "ice-class", "1a", "1b", "1c"]
        )
        if has_ice_class:
            continue

        # Check if tanker type
        if "tanker" not in vessel_type:
            continue

        # Check if vessel has recent AIS points in Arctic corridors
        recent_points = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id)
            .order_by(AISPoint.timestamp_utc.desc())
            .limit(50)
            .all()
        )

        in_arctic = False
        for pt in recent_points:
            for cid, bbox in arctic_bboxes:
                if _in_bbox(pt.lat, pt.lon, bbox):
                    in_arctic = True
                    break
            if in_arctic:
                break

        if not in_arctic:
            continue

        # Check for existing convoy event for this vessel as arctic no-ice
        existing = (
            db.query(ConvoyEvent)
            .filter(
                ConvoyEvent.vessel_a_id == vessel.vessel_id,
                ConvoyEvent.vessel_b_id == vessel.vessel_id,
            )
            .all()
        )
        already_flagged = any(
            e.evidence_json and e.evidence_json.get("type") == "arctic_no_ice_class"
            for e in existing
        )
        if already_flagged:
            continue

        event = ConvoyEvent(
            vessel_a_id=vessel.vessel_id,
            vessel_b_id=vessel.vessel_id,
            start_time_utc=recent_points[-1].timestamp_utc if recent_points else None,
            end_time_utc=recent_points[0].timestamp_utc if recent_points else None,
            duration_hours=0.0,
            mean_distance_nm=0.0,
            mean_heading_delta=0.0,
            corridor_id=None,
            risk_score_component=arctic_score,
            evidence_json={
                "type": "arctic_no_ice_class",
                "vessel_type": vessel.vessel_type,
            },
        )
        db.add(event)
        flagged += 1

    db.commit()
    logger.info("Arctic no-ice-class detector: %d vessels flagged.", flagged)
    return {"arctic_flagged": flagged}
