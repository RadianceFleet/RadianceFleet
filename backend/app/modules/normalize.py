"""AIS data normalization and validation.

Implements validation rules from PRD §7.2.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import polars as pl


# --- Shared helpers ---

_COMMON_TIMESTAMP_FORMATS = [
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
]

# Test/invalid MMSIs that should always be rejected
_TEST_MMSIS = frozenset({"111111111", "123456789", "000000000"})


def is_non_vessel_mmsi(mmsi: str) -> str | None:
    """Check if an MMSI belongs to a non-vessel station (ITU-R M.585).

    Returns an error string if the MMSI is non-vessel, None if it is a valid vessel MMSI.
    """
    if not mmsi or not re.fullmatch(r"\d{9}", mmsi):
        return None  # Let the digit-validation check handle this
    # SAR aircraft: 970-979xxxxxx
    if mmsi.startswith(("970", "971", "972", "973", "974", "975", "976", "977", "978", "979")):
        return f"Non-vessel MMSI (SAR aircraft): {mmsi}"
    # AtoN (Aid to Navigation): 99xxxxxxx
    if mmsi.startswith("99"):
        return f"Non-vessel MMSI (Aid to Navigation): {mmsi}"
    # Coast stations: 00xxxxxxx
    if mmsi.startswith("00"):
        return f"Non-vessel MMSI (coast station): {mmsi}"
    # Common test MMSIs
    if mmsi in _TEST_MMSIS:
        return f"Test MMSI rejected: {mmsi}"
    return None


def parse_timestamp_flexible(ts: Any) -> datetime | None:
    """Parse a timestamp from various formats.

    Returns a datetime object or None if parsing fails.
    Supports: ISO 8601, Unix epoch, and common strftime formats.
    """
    if isinstance(ts, datetime):
        return ts

    # Unix epoch (int or float)
    if isinstance(ts, (int, float)) and ts > 1_000_000_000:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            pass

    if isinstance(ts, str):
        ts_str = ts.strip()
        if not ts_str:
            return None

        # Try ISO format first
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            pass

        # Try common strftime formats
        for fmt in _COMMON_TIMESTAMP_FORMATS:
            try:
                return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        # Go-style: "2024-12-29 18:22:32.318353 +0000 UTC"
        if ts_str.endswith(" UTC"):
            cleaned = ts_str[:-4].strip()  # remove trailing " UTC"
            for go_fmt in (
                "%Y-%m-%d %H:%M:%S.%f %z",
                "%Y-%m-%d %H:%M:%S %z",
            ):
                try:
                    return datetime.strptime(cleaned, go_fmt)
                except ValueError:
                    continue

    return None


def normalize_ais_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    """Rename and coerce columns to canonical field names."""
    rename_map = {
        "shipname": "vessel_name",
        "ship_name": "vessel_name",
        "vessel_name": "vessel_name",
        "ship_type": "vessel_type",
        "latitude": "lat",
        "longitude": "lon",
        "speed": "sog",
        "course": "cog",
        "status": "nav_status",
        "navigational_status": "nav_status",
        "time": "timestamp",
        "datetime": "timestamp",
        "basedatetime": "timestamp",
        # MarineTraffic / VesselFinder uppercase aliases (4.3)
        "SPEED": "sog",
        "LAT": "lat",
        "LON": "lon",
        "HEADING": "heading",
        "TIMESTAMP": "timestamp",
        "COURSE": "cog",
        "NAME": "vessel_name",
        "MMSI": "mmsi",
        "IMO": "imo",
        "SHIPNAME": "vessel_name",
    }
    # Only rename columns that exist (and avoid identity renames)
    actual_renames = {k: v for k, v in rename_map.items() if k in df.columns and k != v}
    if actual_renames:
        df = df.rename(actual_renames)

    # Cast timestamp column if it exists and is string
    if "timestamp" in df.columns and df["timestamp"].dtype == pl.Utf8:
        df = df.with_columns(
            pl.col("timestamp").alias("timestamp_utc")
        )
    elif "timestamp" in df.columns:
        df = df.rename({"timestamp": "timestamp_utc"})

    return df


def validate_ais_row(row: dict[str, Any]) -> str | None:
    """
    Validate a single normalized AIS row.
    Returns an error string if invalid, None if valid.
    Validation rules from PRD §7.2.
    """
    # --- MMSI validation ---
    mmsi_raw = row.get("mmsi", "")
    mmsi = str(mmsi_raw).strip()

    # 4.5: Detect scientific notation MMSI (e.g., "2.41e+08" from Excel)
    if "e" in mmsi.lower():
        return f"MMSI in scientific notation: {mmsi!r} (export CSV column as text format)"

    # P1.3: Pad to 9 digits — some CSV exporters drop leading zeros
    mmsi = mmsi.zfill(9)
    row["mmsi"] = mmsi  # Update row with padded value

    if not re.fullmatch(r"\d{9}", mmsi):
        return f"Invalid MMSI: {mmsi!r} (must be 9 digits)"

    # 1.5: Non-vessel MMSI types
    mmsi_type_err = is_non_vessel_mmsi(mmsi)
    if mmsi_type_err:
        return mmsi_type_err

    imo = row.get("imo")
    if imo:
        imo_str = str(imo).strip().removeprefix("IMO ")
        if not re.fullmatch(r"\d{7}", imo_str):
            return f"Invalid IMO: {imo!r} (must be 7 digits)"

    try:
        lat = float(row["lat"])
        lon = float(row["lon"])
    except (TypeError, ValueError):
        return f"Invalid coordinates: lat={row.get('lat')}, lon={row.get('lon')}"

    if not (-90 <= lat <= 90):
        return f"Latitude out of range: {lat}"
    if not (-180 <= lon <= 180):
        return f"Longitude out of range: {lon}"

    # --- SOG validation with AIS sentinel handling ---
    sog = row.get("sog")
    if sog is not None:
        try:
            sog = float(sog)
        except (TypeError, ValueError):
            return f"Invalid SOG: {sog}"
        if sog < 0:
            return f"Negative SOG: {sog}"
        # 1.1: SOG sentinel 102.3 (raw 1023 = "not available")
        if sog >= 102.2:
            row["sog"] = None  # Sentinel → not available
        elif sog > 35:
            return f"SOG exceeds physical limit: {sog} knots"

    # --- COG validation with AIS sentinel handling ---
    cog = row.get("cog")
    if cog is not None:
        try:
            cog = float(cog)
        except (TypeError, ValueError):
            return f"Invalid COG: {cog}"
        # 1.1: COG sentinel 360.0 (raw 3600 = "not available")
        if cog >= 360.0:
            row["cog"] = None  # Sentinel → not available

    # --- Heading validation with AIS sentinel handling ---
    heading = row.get("heading")
    if heading is not None:
        try:
            heading_val = float(heading)
        except (TypeError, ValueError):
            pass  # Non-numeric heading — let downstream handle
        else:
            if heading_val == 511:
                row["heading"] = None  # Sentinel → not available
            elif heading_val < 0 or heading_val > 360:
                return f"Heading out of range: {heading_val}"

    # --- Timestamp validation ---
    ts = row.get("timestamp_utc") or row.get("timestamp")
    if ts is None:
        return "Missing timestamp"

    ts_dt = parse_timestamp_flexible(ts)
    if ts_dt is None:
        return f"Unparseable timestamp {ts!r}"

    now = datetime.now(timezone.utc)
    if ts_dt > now:
        return f"Future timestamp rejected: {ts_dt}"
    if ts_dt.year < 2010:
        return f"Timestamp too old (pre-2010): {ts_dt}"

    return None
