"""AIS CSV ingestion module.

Validates, normalizes, and persists AIS records from CSV input.
Rejects and logs invalid records (never silently drops).
See PRD §7.2 for validation rules.
"""
from __future__ import annotations

import logging
from datetime import datetime
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
    duplicates = 0
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
        ais_point = _create_ais_point(db, vessel, row)
        if ais_point is None:
            duplicates += 1
            continue
        accepted += 1

    db.commit()
    logger.info("Ingestion complete: %d accepted, %d rejected, %d duplicates", accepted, rejected, duplicates)
    return {
        "accepted": accepted,
        "rejected": rejected,
        "duplicates": duplicates,
        "errors": errors[:50],  # cap error list for response size
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
    return datetime.utcnow()


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

        db.add(VesselHistory(
            vessel_id=vessel.vessel_id,
            field_changed=field,
            old_value=old_str,
            new_value=new_str,
            observed_at=observed_at,
            source=source,
        ))


def _create_ais_point(db: Session, vessel: Vessel, row: dict) -> AISPoint | None:
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
