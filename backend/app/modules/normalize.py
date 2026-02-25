"""AIS data normalization and validation.

Implements validation rules from PRD §7.2.
"""
from __future__ import annotations

import re
from typing import Any

import polars as pl


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
    }
    # Only rename columns that exist
    actual_renames = {k: v for k, v in rename_map.items() if k in df.columns}
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
    mmsi = str(row.get("mmsi", ""))
    if not re.fullmatch(r"\d{9}", mmsi):
        return f"Invalid MMSI: {mmsi!r} (must be 9 digits)"

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

    sog = row.get("sog")
    if sog is not None:
        try:
            sog = float(sog)
        except (TypeError, ValueError):
            return f"Invalid SOG: {sog}"
        if sog < 0:
            return f"Negative SOG: {sog}"
        if sog > 35:
            return f"SOG exceeds physical limit: {sog} knots"

    ts = row.get("timestamp_utc") or row.get("timestamp")
    if ts is None:
        return "Missing timestamp"

    from datetime import datetime, timezone
    try:
        if isinstance(ts, str):
            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            ts_dt = ts
        now = datetime.now(timezone.utc)
        if ts_dt > now:
            return f"Future timestamp rejected: {ts_dt}"
        if ts_dt.year < 2010:
            return f"Timestamp too old (pre-2010): {ts_dt}"
    except Exception as e:
        return f"Unparseable timestamp {ts!r}: {e}"

    return None
