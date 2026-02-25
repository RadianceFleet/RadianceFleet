"""AIS CSV ingestion module.

Validates, normalizes, and persists AIS records from CSV input.
Rejects and logs invalid records (never silently drops).
See PRD §7.2 for validation rules.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from io import IOBase
from typing import Any

import polars as pl
from sqlalchemy.orm import Session

from app.models.vessel import Vessel
from app.models.ais_point import AISPoint
from app.models.vessel_history import VesselHistory

logger = logging.getLogger(__name__)

# AIS vessel type codes that classify as tankers (ITU-R M.1371)
TANKER_TYPE_CODES = set(range(80, 90))  # 80–89 = tanker types

REQUIRED_COLUMNS = {"mmsi", "timestamp", "lat", "lon"}

# PRD §7.2: AIS source quality ranking (higher index = higher quality)
_SOURCE_QUALITY = {
    "csv_import": 0,
    "terrestrial": 1,
    "aisstream": 2,
    "satellite": 3,
    "exactearth": 4,
    "spire": 4,
}

# PRD §7.2: class-specific SOG thresholds for ingestion-time flagging
_CLASS_SOG_LIMITS: list[tuple[float, float | None, float, str]] = [
    # (min_dwt, max_dwt_or_None, max_sog_kn, label)
    (200_000, None,    18, "VLCC"),
    (120_000, 200_000, 19, "Suezmax"),
    (80_000,  120_000, 20, "Aframax"),
    (60_000,  80_000,  20, "Panamax"),
]


def _is_higher_quality_source(new_source: str, existing_source: str) -> bool:
    """Return True if new_source is higher quality than existing_source."""
    return _SOURCE_QUALITY.get(new_source, 0) > _SOURCE_QUALITY.get(existing_source, 0)


def _check_sog_class_limit(vessel: Vessel, sog: float | None) -> None:
    """PRD §7.2: log warning if SOG exceeds class-specific limit (never reject)."""
    if sog is None or vessel.deadweight is None:
        return
    dwt = vessel.deadweight
    for min_dwt, max_dwt, max_sog, label in _CLASS_SOG_LIMITS:
        if max_dwt is None and dwt >= min_dwt:
            if sog > max_sog:
                logger.warning(
                    "SOG %.1f kn exceeds %s limit (%d kn) for MMSI %s (DWT=%d)",
                    sog, label, max_sog, vessel.mmsi, dwt,
                )
            return
        if max_dwt is not None and min_dwt <= dwt < max_dwt:
            if sog > max_sog:
                logger.warning(
                    "SOG %.1f kn exceeds %s limit (%d kn) for MMSI %s (DWT=%d)",
                    sog, label, max_sog, vessel.mmsi, dwt,
                )
            return


def ingest_ais_csv(file: IOBase, db: Session) -> dict[str, Any]:
    """
    Ingest AIS records from a CSV file object.

    Returns a summary dict with counts of accepted, rejected, and duplicate records.
    """
    from app.modules.normalize import normalize_ais_dataframe, validate_ais_row

    df = pl.read_csv(file, infer_schema_length=1000)
    # Normalize column names to lowercase
    df = df.rename({col: col.lower().strip() for col in df.columns})

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")

    accepted = 0
    rejected = 0
    replaced_count = 0
    ignored_count = 0
    errors: list[str] = []

    df_normalized = normalize_ais_dataframe(df)

    for row in df_normalized.iter_rows(named=True):
        error = validate_ais_row(row)
        if error:
            logger.warning("Rejected AIS record: %s | row: %s", error, row)
            errors.append(error)
            rejected += 1
            continue

        vessel = _get_or_create_vessel(db, row)
        _check_sog_class_limit(vessel, row.get("sog"))
        result = _create_ais_point(db, vessel, row)
        if result is None:
            ignored_count += 1
            continue
        if result == "replaced":
            replaced_count += 1
            continue
        accepted += 1

    db.commit()
    duplicates = replaced_count + ignored_count
    logger.info(
        "Ingestion complete: %d accepted, %d rejected, %d duplicates (replaced=%d, ignored=%d)",
        accepted, rejected, duplicates, replaced_count, ignored_count,
    )
    return {
        "accepted": accepted,
        "rejected": rejected,
        "duplicates": duplicates,
        "replaced": replaced_count,
        "ignored": ignored_count,
        "errors": errors[:50],
        "errors_truncated": len(errors) > 50,
        "total_errors": len(errors),
    }


def _get_or_create_vessel(db: Session, row: dict) -> Vessel:
    mmsi = str(row["mmsi"])
    vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
    if not vessel:
        ts = _parse_timestamp(row)
        vessel = Vessel(
            mmsi=mmsi,
            imo=row.get("imo"),
            name=row.get("vessel_name") or row.get("shipname"),
            flag=row.get("flag") or row.get("country"),
            vessel_type=row.get("vessel_type") or row.get("ship_type"),
            deadweight=row.get("deadweight"),
            ais_class=row.get("ais_class", "unknown"),
            mmsi_first_seen_utc=ts,
        )
        db.add(vessel)
        db.flush()
        return vessel

    # Existing vessel — track identity changes before overwriting
    ts = _parse_timestamp(row)
    _track_field_change(db, vessel, "name",
                        vessel.name, row.get("vessel_name") or row.get("shipname"),
                        ts, "ais_csv")
    _track_field_change(db, vessel, "flag",
                        vessel.flag, row.get("flag") or row.get("country"),
                        ts, "ais_csv")
    _track_field_change(db, vessel, "ais_class",
                        vessel.ais_class, row.get("ais_class"),
                        ts, "ais_csv")

    # Update mutable fields
    new_name = row.get("vessel_name") or row.get("shipname")
    new_flag = row.get("flag") or row.get("country")
    new_ais_class = row.get("ais_class")
    if new_name:
        vessel.name = new_name
    if new_flag:
        vessel.flag = new_flag
    if new_ais_class:
        vessel.ais_class = new_ais_class

    return vessel


def _parse_timestamp(row: dict):
    ts = row.get("timestamp_utc") or row.get("timestamp")
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            pass
    if isinstance(ts, datetime):
        return ts
    return datetime.now(timezone.utc)


def _track_field_change(
    db: Session,
    vessel: Vessel,
    field: str,
    old_val,
    new_val,
    observed_at: datetime,
    source: str,
) -> None:
    """Record a VesselHistory entry when an identity field changes."""
    if old_val is None or new_val is None:
        return
    old_str = str(old_val).strip()
    new_str = str(new_val).strip()
    if old_str and new_str and old_str.lower() != new_str.lower():
        # Check how recent the last AIS point was to flag rapid changes
        last_point = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id)
            .order_by(AISPoint.timestamp_utc.desc())
            .first()
        )
        if last_point:
            change_window_h = (
                observed_at - last_point.timestamp_utc
            ).total_seconds() / 3600 if hasattr(observed_at, 'total_seconds') else 0
            if hasattr(observed_at, '__sub__') and hasattr(last_point.timestamp_utc, '__sub__'):
                try:
                    change_window_h = (observed_at - last_point.timestamp_utc).total_seconds() / 3600
                    if change_window_h < 24:
                        logger.warning(
                            "MMSI %s: %s changed within %.1fh (%s → %s)",
                            vessel.mmsi, field, change_window_h, old_str, new_str
                        )
                except TypeError:
                    pass

        # Dedup: skip if an identical record exists within 24h (prevents re-import inflation)
        existing = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == field,
            VesselHistory.old_value == old_str,
            VesselHistory.new_value == new_str,
            VesselHistory.observed_at >= observed_at - timedelta(hours=24),
            VesselHistory.observed_at <= observed_at + timedelta(hours=24),
        ).first()
        if existing:
            return

        db.add(VesselHistory(
            vessel_id=vessel.vessel_id,
            field_changed=field,
            old_value=old_str,
            new_value=new_str,
            observed_at=observed_at,
            source=source,
        ))


def _create_ais_point(db: Session, vessel: Vessel, row: dict) -> AISPoint | str | None:
    """Create or replace an AIS point.

    Returns:
        AISPoint if a new point was created,
        "replaced" if an existing point was updated with higher-quality data,
        None if the duplicate was ignored (existing source was equal or better).
    """
    ts = _parse_timestamp(row)

    # Duplicate check: same MMSI + timestamp
    existing = (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel.vessel_id,
            AISPoint.timestamp_utc == ts,
        )
        .first()
    )
    if existing:
        # PRD §7.2: keep highest-quality record; prefer satellite AIS over terrestrial
        new_source = row.get("source", "csv_import")
        if _is_higher_quality_source(new_source, existing.source):
            existing.lat = float(row["lat"])
            existing.lon = float(row["lon"])
            existing.sog = float(row.get("sog") or 0)
            existing.cog = float(row.get("cog") or 0)
            existing.heading = row.get("heading")
            existing.nav_status = row.get("nav_status")
            existing.ais_class = row.get("ais_class", existing.ais_class)
            old_source = existing.source
            existing.source = new_source
            logger.debug("Replaced AIS point (vessel=%s, ts=%s): %s > %s",
                         vessel.mmsi, ts, new_source, old_source)
            return "replaced"
        return None

    point = AISPoint(
        vessel_id=vessel.vessel_id,
        timestamp_utc=ts,
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        sog=float(row.get("sog") or 0),
        cog=float(row.get("cog") or 0),
        heading=row.get("heading"),
        nav_status=row.get("nav_status"),
        ais_class=row.get("ais_class", "A"),
        source=row.get("source", "csv_import"),
    )
    db.add(point)
    return point
