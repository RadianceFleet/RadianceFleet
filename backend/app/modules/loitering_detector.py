"""Loitering detection engine.

Detects vessels exhibiting sustained low-speed movement (loitering) from AIS data.
Loitering is a pre-STS indicator: vessels slow to near-zero SOG while waiting for
a ship-to-ship transfer partner, often in open water outside formal anchorages.

Two detection modes:
  1. detect_loitering_for_vessel / run_loitering_detection
       — AIS-based rolling-median SOG analysis; creates LoiteringEvent records.
  2. detect_laid_up_vessels
       — Multi-day positional stability analysis; sets laid-up flags on Vessel.

Scoring (risk_score_component):
  ≥12 hours in a corridor  → 20 pts
  ≥ 4 hours (baseline)     →  8 pts
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import polars as pl
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.ais_point import AISPoint
from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent
from app.models.loitering_event import LoiteringEvent
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# SOG threshold below which a vessel is considered to be loitering (knots)
_SOG_LOITER_THRESHOLD_KN: float = 0.5

# Minimum consecutive hours of low SOG to constitute a loitering event
_MIN_LOITER_HOURS: int = 4

# Hours threshold for "sustained" loitering (higher risk score)
_SUSTAINED_LOITER_HOURS: int = 12

# Risk score components
_RISK_BASELINE: int = 8       # ≥4 h loitering
_RISK_SUSTAINED: int = 20     # ≥12 h loitering in a corridor

# Gap linkage window: scan for gaps within this many hours of loitering boundaries
from app.config import settings as _settings
_GAP_LINK_WINDOW_HOURS: int = _settings.LOITER_GAP_LINKAGE_HOURS

# Minimum AIS points required to analyse a vessel
_MIN_POINTS: int = 4

# Bounding-box tolerance for corridor overlap check (degrees; ~2 nm at mid-latitudes)
_BBOX_TOLERANCE_DEG: float = 0.033

# Laid-up detection constants
_LAID_UP_30D_DAYS: int = 30
_LAID_UP_60D_DAYS: int = 60
_LAID_UP_BBOX_DEG: float = 0.033   # ±2 nm positional stability threshold


# ── Internal helpers ───────────────────────────────────────────────────────────

def _parse_corridor_bbox(corridor: Corridor) -> Optional[tuple[float, float, float, float]]:
    """Extract (min_lat, max_lat, min_lon, max_lon) from a corridor's WKB geometry.

    Returns None if the geometry is unavailable or cannot be parsed.
    Uses geoalchemy2 / shapely if installed; otherwise returns None so that the
    caller degrades gracefully to skipping the corridor.
    """
    try:
        from geoalchemy2.shape import to_shape  # type: ignore
        shape = to_shape(corridor.geometry)
        min_lon, min_lat, max_lon, max_lat = shape.bounds
        return (min_lat, max_lat, min_lon, max_lon)
    except (ImportError, ValueError, TypeError, AttributeError):
        return None


def _point_in_corridor(lat: float, lon: float, corridor: Corridor) -> bool:
    """Return True if (lat, lon) lies within the corridor's bounding box."""
    bbox = _parse_corridor_bbox(corridor)
    if bbox is None:
        return False
    min_lat, max_lat, min_lon, max_lon = bbox
    # Expand by tolerance to account for vessels just outside the polygon edge
    return (
        (min_lat - _BBOX_TOLERANCE_DEG) <= lat <= (max_lat + _BBOX_TOLERANCE_DEG)
        and (min_lon - _BBOX_TOLERANCE_DEG) <= lon <= (max_lon + _BBOX_TOLERANCE_DEG)
    )


def _find_corridor_for_position(
    lat: float,
    lon: float,
    corridors: list[Corridor],
) -> Optional[Corridor]:
    """Return the first corridor whose bounding box contains (lat, lon), or None."""
    for corridor in corridors:
        if _point_in_corridor(lat, lon, corridor):
            return corridor
    return None


def _find_preceding_gap(
    db: Session,
    vessel_id: int,
    loiter_start: datetime,
) -> Optional[AISGapEvent]:
    """Return the most recent gap event ending within _GAP_LINK_WINDOW_HOURS before loiter_start."""
    window_start = loiter_start - timedelta(hours=_GAP_LINK_WINDOW_HOURS)
    return (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.vessel_id == vessel_id,
            AISGapEvent.gap_end_utc >= window_start,
            AISGapEvent.gap_end_utc <= loiter_start,
        )
        .order_by(AISGapEvent.gap_end_utc.desc())
        .first()
    )


def _find_following_gap(
    db: Session,
    vessel_id: int,
    loiter_end: datetime,
) -> Optional[AISGapEvent]:
    """Return the earliest gap event starting within _GAP_LINK_WINDOW_HOURS after loiter_end."""
    window_end = loiter_end + timedelta(hours=_GAP_LINK_WINDOW_HOURS)
    return (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.vessel_id == vessel_id,
            AISGapEvent.gap_start_utc >= loiter_end,
            AISGapEvent.gap_start_utc <= window_end,
        )
        .order_by(AISGapEvent.gap_start_utc.asc())
        .first()
    )


# ── Run detection per vessel ───────────────────────────────────────────────────

def detect_loitering_for_vessel(
    db: Session,
    vessel: Vessel,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> int:
    """Detect loitering events for a single vessel.

    Algorithm:
      1. Load AIS points into a Polars DataFrame.
      2. Bucket into 1-hour windows; compute per-bucket median SOG.
      3. Find consecutive hour-buckets where median SOG < 0.5 kn for ≥ 4 h.
      4. For each qualifying run, create a LoiteringEvent (if not duplicate).
      5. Attempt corridor linkage; link preceding/following gap events.

    Args:
        db: SQLAlchemy sync session.
        vessel: Vessel ORM instance to analyse.
        date_from: Inclusive start date filter (UTC).
        date_to: Inclusive end date filter (UTC).

    Returns:
        Number of new LoiteringEvent rows created.
    """
    # ── 1. Query AIS points ────────────────────────────────────────────────────
    query = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id == vessel.vessel_id)
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

    points = query.all()
    if len(points) < _MIN_POINTS:
        logger.debug(
            "Vessel %d (%s): only %d AIS points — skipping loitering detection",
            vessel.vessel_id,
            vessel.mmsi,
            len(points),
        )
        return 0

    # ── 2. Build Polars DataFrame and compute 1-hour rolling median SOG ────────
    df = pl.DataFrame(
        {
            "timestamp_utc": [p.timestamp_utc for p in points],
            "lat": [p.lat for p in points],
            "lon": [p.lon for p in points],
            "sog": [p.sog if p.sog is not None else float("nan") for p in points],
        }
    ).with_columns(pl.col("timestamp_utc").cast(pl.Datetime("us")))

    # Group into 1-hour buckets and compute median SOG per bucket
    hourly = (
        df.sort("timestamp_utc")
        .group_by_dynamic("timestamp_utc", every="1h")
        .agg(
            pl.col("sog").median().alias("median_sog"),
            pl.col("lat").mean().alias("bucket_mean_lat"),
            pl.col("lon").mean().alias("bucket_mean_lon"),
        )
        .sort("timestamp_utc")
    )

    # ── 3. Identify consecutive low-SOG runs ───────────────────────────────────
    # A "low-SOG" bucket: median_sog < threshold OR NaN (treated as stationary)
    hourly = hourly.with_columns(
        pl.col("median_sog")
        .map_elements(
            lambda v: (v is None or (v == v and v < _SOG_LOITER_THRESHOLD_KN)),
            return_dtype=pl.Boolean,
        )
        .alias("is_low_sog")
    )

    bucket_rows = hourly.to_dicts()
    n_buckets = len(bucket_rows)

    # ── 4. Load corridors once for corridor linkage ────────────────────────────
    try:
        corridors: list[Corridor] = db.query(Corridor).all()
    except OperationalError as exc:
        logger.warning("Could not load corridors for loitering correlation: %s", exc)
        corridors = []

    # ── 5. Walk through buckets to extract runs ────────────────────────────────
    events_created = 0
    i = 0

    while i < n_buckets:
        row = bucket_rows[i]
        if not row["is_low_sog"]:
            i += 1
            continue

        # Start of a low-SOG run
        run_start = i
        while i < n_buckets and bucket_rows[i]["is_low_sog"]:
            i += 1
        run_end = i  # exclusive

        run_length_hours = run_end - run_start
        if run_length_hours < _MIN_LOITER_HOURS:
            continue

        # Aggregate run-level statistics
        run_rows = bucket_rows[run_start:run_end]
        start_time: datetime = run_rows[0]["timestamp_utc"]
        end_time: datetime = run_rows[-1]["timestamp_utc"]
        # End time is the start of the last bucket; add 1 h to close the window
        end_time = end_time + timedelta(hours=1)

        # Guard: ensure start_time is a plain datetime (Polars may return datetime)
        if not isinstance(start_time, datetime):
            start_time = datetime.fromisoformat(str(start_time))
        if not isinstance(end_time, datetime):
            end_time = datetime.fromisoformat(str(end_time))

        duration_hours = (end_time - start_time).total_seconds() / 3600

        sog_values = [r["median_sog"] for r in run_rows if r["median_sog"] is not None and r["median_sog"] == r["median_sog"]]
        median_sog = float(pl.Series(sog_values).median()) if sog_values else None

        mean_lat = sum(r["bucket_mean_lat"] for r in run_rows) / len(run_rows)
        mean_lon = sum(r["bucket_mean_lon"] for r in run_rows) / len(run_rows)

        # ── 5a. Deduplication check ────────────────────────────────────────────
        existing = (
            db.query(LoiteringEvent)
            .filter(
                LoiteringEvent.vessel_id == vessel.vessel_id,
                LoiteringEvent.start_time_utc == start_time,
            )
            .first()
        )
        if existing:
            logger.debug(
                "Vessel %d: loitering event at %s already recorded — skipping",
                vessel.vessel_id,
                start_time.isoformat(),
            )
            continue

        # ── 5b. Corridor linkage (bounding-box point-in-corridor) ──────────────
        matched_corridor: Optional[Corridor] = None
        try:
            matched_corridor = _find_corridor_for_position(mean_lat, mean_lon, corridors)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("Corridor lookup failed for vessel %d: %s", vessel.vessel_id, exc)

        # ── 5c. Risk score component ───────────────────────────────────────────
        if run_length_hours >= _SUSTAINED_LOITER_HOURS and matched_corridor is not None:
            risk_score_component = _RISK_SUSTAINED
        else:
            risk_score_component = _RISK_BASELINE

        # ── 5d. Gap linkage ────────────────────────────────────────────────────
        preceding_gap = _find_preceding_gap(db, vessel.vessel_id, start_time)
        following_gap = _find_following_gap(db, vessel.vessel_id, end_time)

        # ── 5e. Persist LoiteringEvent ─────────────────────────────────────────
        event = LoiteringEvent(
            vessel_id=vessel.vessel_id,
            start_time_utc=start_time,
            end_time_utc=end_time,
            duration_hours=round(duration_hours, 2),
            median_sog_kn=median_sog,
            mean_lat=round(mean_lat, 6),
            mean_lon=round(mean_lon, 6),
            corridor_id=matched_corridor.corridor_id if matched_corridor else None,
            preceding_gap_id=preceding_gap.gap_event_id if preceding_gap else None,
            following_gap_id=following_gap.gap_event_id if following_gap else None,
            risk_score_component=risk_score_component,
        )
        db.add(event)
        events_created += 1

        logger.info(
            "Vessel %d (%s): loitering %.1f h starting %s — corridor=%s risk=%d",
            vessel.vessel_id,
            vessel.mmsi,
            duration_hours,
            start_time.isoformat(),
            matched_corridor.name if matched_corridor else "none",
            risk_score_component,
        )

    if events_created:
        db.commit()

    return events_created


# ── Batch runner ───────────────────────────────────────────────────────────────

def run_loitering_detection(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """Run loitering detection across all vessels.

    Args:
        db: SQLAlchemy sync session.
        date_from: Inclusive start date filter.
        date_to: Inclusive end date filter.

    Returns:
        {"loitering_events_created": N, "vessels_processed": M}
    """
    vessels = db.query(Vessel).all()
    total_events = 0

    for vessel in vessels:
        try:
            n = detect_loitering_for_vessel(db, vessel, date_from=date_from, date_to=date_to)
            total_events += n
        except Exception as exc:
            logger.exception(
                "Loitering detection failed for vessel %d (%s): %s",
                vessel.vessel_id,
                vessel.mmsi,
                exc,
            )

    logger.info(
        "Loitering detection complete: %d events created across %d vessels",
        total_events,
        len(vessels),
    )
    return {
        "loitering_events_created": total_events,
        "vessels_processed": len(vessels),
    }


# ── Laid-up vessel detection ───────────────────────────────────────────────────

def detect_laid_up_vessels(db: Session) -> dict:
    """Detect vessels that have been stationary for 30 or 60 consecutive days.

    A vessel is considered laid up when its median daily position varies by less
    than ±_LAID_UP_BBOX_DEG (~2 nm) in both latitude and longitude for a
    sustained period.

    Side effects:
      - Sets Vessel.vessel_laid_up_30d = True for vessels ≥30 consecutive days.
      - Sets Vessel.vessel_laid_up_60d = True for vessels ≥60 consecutive days.
      - Sets Vessel.vessel_laid_up_in_sts_zone = True if position overlaps an
        STS zone corridor.

    Returns:
        {"laid_up_updated": N}  where N is the count of Vessel rows modified.
    """
    # Load corridors once; filter to STS zones for the in-zone flag
    try:
        all_corridors: list[Corridor] = db.query(Corridor).all()
        from app.models.base import CorridorTypeEnum
        sts_corridors = [c for c in all_corridors if c.corridor_type == CorridorTypeEnum.STS_ZONE]
    except (OperationalError, ImportError) as exc:
        logger.warning("Could not load corridors for laid-up STS check: %s", exc)
        all_corridors = []
        sts_corridors = []

    vessels = db.query(Vessel).all()
    updated_count = 0

    for vessel in vessels:
        # ── 1. Load all AIS points for this vessel ─────────────────────────────
        points = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id)
            .order_by(AISPoint.timestamp_utc)
            .all()
        )
        if not points:
            continue

        # ── 2. Build a daily summary using Polars ──────────────────────────────
        df = pl.DataFrame(
            {
                "timestamp_utc": [p.timestamp_utc for p in points],
                "lat": [p.lat for p in points],
                "lon": [p.lon for p in points],
            }
        ).with_columns(pl.col("timestamp_utc").cast(pl.Datetime("us")))

        daily = (
            df.sort("timestamp_utc")
            .group_by_dynamic("timestamp_utc", every="1d")
            .agg(
                pl.col("lat").median().alias("day_lat"),
                pl.col("lon").median().alias("day_lon"),
            )
            .sort("timestamp_utc")
        )

        day_rows = daily.to_dicts()
        n_days = len(day_rows)
        if n_days < _LAID_UP_30D_DAYS:
            continue  # not enough daily observations to qualify

        # ── 3. Sliding window: find longest consecutive stationary run ──────────
        max_run_days = 0
        run_lat: Optional[float] = None
        run_lon: Optional[float] = None

        run_start_idx = 0
        run_length = 1
        anchor_lat = day_rows[0]["day_lat"]
        anchor_lon = day_rows[0]["day_lon"]

        for j in range(1, n_days):
            lat_j = day_rows[j]["day_lat"]
            lon_j = day_rows[j]["day_lon"]

            # Check if today's median position is within bbox of the run's anchor
            within_bbox = (
                lat_j is not None
                and lon_j is not None
                and anchor_lat is not None
                and anchor_lon is not None
                and abs(lat_j - anchor_lat) <= _LAID_UP_BBOX_DEG
                and abs(lon_j - anchor_lon) <= _LAID_UP_BBOX_DEG
            )

            if within_bbox:
                run_length += 1
            else:
                # End of current run; record if it is the longest
                if run_length > max_run_days:
                    max_run_days = run_length
                    # Mean position over this run
                    run_lat = sum(
                        r["day_lat"]
                        for r in day_rows[run_start_idx : run_start_idx + run_length]
                        if r["day_lat"] is not None
                    ) / run_length
                    run_lon = sum(
                        r["day_lon"]
                        for r in day_rows[run_start_idx : run_start_idx + run_length]
                        if r["day_lon"] is not None
                    ) / run_length
                # Start a new run from this day
                run_start_idx = j
                run_length = 1
                anchor_lat = lat_j
                anchor_lon = lon_j

        # Final run check
        if run_length > max_run_days:
            max_run_days = run_length
            run_lat = sum(
                r["day_lat"]
                for r in day_rows[run_start_idx : run_start_idx + run_length]
                if r["day_lat"] is not None
            ) / run_length
            run_lon = sum(
                r["day_lon"]
                for r in day_rows[run_start_idx : run_start_idx + run_length]
                if r["day_lon"] is not None
            ) / run_length

        if max_run_days < _LAID_UP_30D_DAYS:
            continue

        # ── 4. Determine flags to set ──────────────────────────────────────────
        is_30d = max_run_days >= _LAID_UP_30D_DAYS
        is_60d = max_run_days >= _LAID_UP_60D_DAYS

        # STS zone check using the run's mean position
        in_sts = False
        if run_lat is not None and run_lon is not None and sts_corridors:
            try:
                in_sts = _find_corridor_for_position(run_lat, run_lon, sts_corridors) is not None
            except (ValueError, TypeError, AttributeError) as exc:
                logger.warning("STS zone check failed for vessel %d: %s", vessel.vessel_id, exc)

        # Only update DB row if a flag value actually changes
        changed = False
        if is_30d and not vessel.vessel_laid_up_30d:
            vessel.vessel_laid_up_30d = True
            changed = True
        if is_60d and not vessel.vessel_laid_up_60d:
            vessel.vessel_laid_up_60d = True
            changed = True
        if in_sts and not vessel.vessel_laid_up_in_sts_zone:
            vessel.vessel_laid_up_in_sts_zone = True
            changed = True

        if changed:
            updated_count += 1
            logger.info(
                "Vessel %d (%s): laid-up flags updated — 30d=%s 60d=%s sts=%s (run=%d days)",
                vessel.vessel_id,
                vessel.mmsi,
                vessel.vessel_laid_up_30d,
                vessel.vessel_laid_up_60d,
                vessel.vessel_laid_up_in_sts_zone,
                max_run_days,
            )

    if updated_count:
        db.commit()

    logger.info("Laid-up detection complete: %d vessels updated", updated_count)
    return {"laid_up_updated": updated_count}
