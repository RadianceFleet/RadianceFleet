"""Danish Maritime Authority (DMA) historical AIS data importer.

Downloads daily CSV archives from web.ais.dk/aisdata/ and imports into RadianceFleet.
Every Russian shadow fleet tanker exiting the Baltic MUST transit the Danish Straits.
DMA data goes back to 2006.

DMA provides: MMSI, timestamp, lat, lon, SOG, COG, heading, IMO, ship_type, draught,
destination, cargo_type.

CAVEATS:
- Files are 1.5-2.6 GB/day -- MUST use streaming CSV reader
- IMO field is "Unknown" for most rows (AIS position reports only have it in Type 5 records)
- Timestamp format: DD/MM/YYYY HH:MM:SS (dayfirst=True)
- Column names differ from standard: "Name" not "vessel_name", "Ship type" not "vessel_type", etc.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://web.ais.dk/aisdata"

# DMA CSV column name -> canonical name mapping
_DMA_COLUMN_MAP = {
    "# Timestamp": "timestamp",
    "Type of mobile": "type_of_mobile",
    "MMSI": "mmsi",
    "Latitude": "lat",
    "Longitude": "lon",
    "Navigational status": "nav_status",
    "ROT": "rot",
    "SOG": "sog",
    "COG": "cog",
    "Heading": "heading",
    "IMO": "imo",
    "Callsign": "callsign",
    "Name": "vessel_name",
    "Ship type": "vessel_type",
    "Cargo type": "cargo_type",
    "Width": "beam_m",
    "Length": "length_m",
    "Type of position fixing device": "pos_device",
    "Draught": "draught",
    "Destination": "destination",
    "ETA": "eta",
    "Data source type": "data_source_type",
    "A": "dim_a",
    "B": "dim_b",
    "C": "dim_c",
    "D": "dim_d",
}

# Vessel type strings in DMA data that correspond to tankers
_TANKER_TYPES = {
    "Tanker",
    "Tanker - Hazardous category A",
    "Tanker - Hazardous category B",
    "Tanker - Hazardous category C",
    "Tanker - Hazardous category D",
    "Tanker - No additional information",
}


def _build_url(d: date, gzip: bool = True) -> str:
    """Build download URL for a given date."""
    datestr = d.strftime("%Y-%m-%d")
    if gzip:
        return f"{_BASE_URL}/aisdk-{datestr}.csv.gz"
    return f"{_BASE_URL}/aisdk-{datestr}.csv"


def _normalize_row(header: list[str], values: list[str]) -> dict | None:
    """Map a raw DMA CSV row to canonical field names. Returns None if row invalid."""
    if len(values) != len(header):
        return None
    raw = dict(zip(header, values))
    row: dict = {}
    for dma_col, canonical in _DMA_COLUMN_MAP.items():
        if dma_col in raw:
            row[canonical] = raw[dma_col].strip() if raw[dma_col] else ""
    return row


def _parse_dma_timestamp(ts_str: str) -> datetime | None:
    """Parse DMA timestamp (DD/MM/YYYY HH:MM:SS)."""
    if not ts_str:
        return None
    try:
        return datetime.strptime(ts_str, "%d/%m/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_and_import_dma(
    db: Session,
    start_date: date,
    end_date: date,
    vessel_types: list[str] | None = None,
) -> dict:
    """Download DMA daily CSV archives and import AIS points.

    Args:
        db: Active SQLAlchemy session.
        start_date: First date to import (inclusive).
        end_date: Last date to import (inclusive).
        vessel_types: Optional filter list, e.g. ["Tanker"]. If None, all types imported.

    Returns:
        Stats dict with points_imported, vessels_created, vessels_updated, days_processed, errors.
    """
    if not getattr(settings, "DMA_ENABLED", False):
        logger.info("DMA import disabled (DMA_ENABLED=False)")
        return {
            "points_imported": 0,
            "vessels_created": 0,
            "vessels_updated": 0,
            "days_processed": 0,
            "errors": 0,
        }

    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel
    from app.modules.normalize import is_non_vessel_mmsi
    from app.utils.vessel_identity import flag_to_risk_category, mmsi_to_flag

    stats = {
        "points_imported": 0,
        "vessels_created": 0,
        "vessels_updated": 0,
        "days_processed": 0,
        "errors": 0,
    }

    # Build type filter set (case-insensitive match)
    type_filter: set[str] | None = None
    if vessel_types:
        type_filter = {t.lower() for t in vessel_types}

    from datetime import datetime as _dt, timezone as _tz

    current = start_date
    while current <= end_date:
        url = _build_url(current, gzip=True)
        logger.info("DMA: fetching %s", url)
        day_started_at = _dt.now(_tz.utc)

        try:
            with httpx.Client(timeout=120) as client:
                resp = client.get(url)
            if resp.status_code == 404:
                # Try non-gzip fallback
                url = _build_url(current, gzip=False)
                with httpx.Client(timeout=120) as client:
                    resp = client.get(url)
            resp.raise_for_status()

            # Stream CSV line-by-line
            header: list[str] | None = None
            day_points = 0

            if url.endswith(".gz"):
                import gzip as gz_mod
                lines = gz_mod.open(io.BytesIO(resp.content), "rt", encoding="utf-8")
            else:
                lines = resp.text.splitlines()

            reader = csv.reader(lines)
            for row_values in reader:
                if header is None:
                    header = row_values
                    continue

                row = _normalize_row(header, row_values)
                if row is None:
                    stats["errors"] += 1
                    continue

                # Type filter
                if type_filter:
                    vtype = row.get("vessel_type", "").lower()
                    if not any(t in vtype for t in type_filter):
                        continue

                mmsi = str(row.get("mmsi", "")).strip()
                if not mmsi or len(mmsi) != 9 or not mmsi.isdigit():
                    continue
                if is_non_vessel_mmsi(mmsi):
                    continue

                # Skip "Unknown" or empty IMO values
                imo = row.get("imo", "").strip()
                if imo.lower() in ("unknown", ""):
                    imo = None
                elif not imo.isdigit() or len(imo) != 7:
                    imo = None

                ts = _parse_dma_timestamp(row.get("timestamp", ""))
                if ts is None:
                    stats["errors"] += 1
                    continue

                try:
                    lat = float(row.get("lat", ""))
                    lon = float(row.get("lon", ""))
                except (ValueError, TypeError):
                    stats["errors"] += 1
                    continue

                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    continue

                # Parse optional numeric fields
                sog = _safe_float(row.get("sog"))
                cog = _safe_float(row.get("cog"))
                heading_raw = _safe_float(row.get("heading"))
                heading = heading_raw if heading_raw is not None and heading_raw != 511 else None
                draught = _safe_float(row.get("draught"))

                # Upsert vessel
                vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                if not vessel:
                    derived_flag = mmsi_to_flag(mmsi)
                    vessel = Vessel(
                        mmsi=mmsi,
                        imo=imo,
                        name=row.get("vessel_name") or None,
                        flag=derived_flag,
                        flag_risk_category=flag_to_risk_category(derived_flag),
                        vessel_type=row.get("vessel_type") or None,
                        ais_class="A",
                        ais_source="dma",
                        callsign=row.get("callsign") or None,
                        mmsi_first_seen_utc=ts,
                    )
                    try:
                        with db.begin_nested():
                            db.add(vessel)
                            db.flush()
                        stats["vessels_created"] += 1
                    except IntegrityError:
                        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                        if not vessel:
                            stats["errors"] += 1
                            continue
                else:
                    # Update vessel metadata if we have better data
                    updated = False
                    if imo and not vessel.imo:
                        vessel.imo = imo
                        updated = True
                    if row.get("vessel_name") and not vessel.name:
                        vessel.name = row.get("vessel_name")
                        updated = True
                    if updated:
                        stats["vessels_updated"] += 1

                # Dedup check
                existing = (
                    db.query(AISPoint)
                    .filter(
                        AISPoint.vessel_id == vessel.vessel_id,
                        AISPoint.timestamp_utc == ts,
                    )
                    .first()
                )
                if existing:
                    continue

                point = AISPoint(
                    vessel_id=vessel.vessel_id,
                    timestamp_utc=ts,
                    lat=lat,
                    lon=lon,
                    sog=sog,
                    cog=cog,
                    heading=heading,
                    draught=draught,
                    destination=row.get("destination") or None,
                    ais_class="A",
                    source="dma",
                )
                db.add(point)
                day_points += 1

                # Batch commit every 5000 points
                if day_points % 5000 == 0:
                    db.commit()

            db.commit()
            stats["points_imported"] += day_points
            stats["days_processed"] += 1
            logger.info("DMA: %s — %d points imported", current, day_points)

            # Record coverage window — completed
            try:
                from app.modules.coverage_tracker import record_coverage_window
                record_coverage_window(
                    db, "dma", current, current,
                    status="completed",
                    points_imported=day_points,
                    vessels_queried=0,
                    started_at=day_started_at,
                    finished_at=_dt.now(_tz.utc),
                )
                db.commit()
            except Exception as cov_exc:
                logger.warning("DMA coverage recording failed for %s: %s", current, cov_exc)

        except Exception as e:
            logger.error("DMA: failed to process %s: %s", current, e)
            stats["errors"] += 1
            # Record coverage window — failed
            try:
                from app.modules.coverage_tracker import record_coverage_window
                record_coverage_window(
                    db, "dma", current, current,
                    status="failed",
                    points_imported=0,
                    errors=1,
                    started_at=day_started_at,
                    finished_at=_dt.now(_tz.utc),
                    notes=str(e)[:500],
                )
                db.commit()
            except Exception:
                pass

        current += timedelta(days=1)

    logger.info(
        "DMA import complete: %d points, %d days, %d errors",
        stats["points_imported"],
        stats["days_processed"],
        stats["errors"],
    )
    return stats


def _safe_float(val: Optional[str]) -> Optional[float]:
    """Parse a string to float, returning None on failure."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
