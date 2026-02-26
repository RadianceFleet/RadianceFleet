"""AISHub client — batch AIS position data via REST API.

Fetches latest vessel positions from AISHub for a given bounding box.
Rate limit: 1 request per minute.

API docs: https://www.aishub.net/api
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.normalize import is_non_vessel_mmsi, parse_timestamp_flexible

logger = logging.getLogger(__name__)

_BASE_URL = "https://data.aishub.net/ws.php"
_TIMEOUT = 60.0


def fetch_area_positions(
    bbox: tuple[float, float, float, float],
    username: str | None = None,
    output_format: int = 1,  # 1 = JSON
) -> list[dict]:
    """Fetch latest vessel positions from AISHub for a bounding box.

    Args:
        bbox: (lat_min, lon_min, lat_max, lon_max).
        username: AISHub username credential.
        output_format: 1=JSON (default), 2=CSV.

    Returns list of position dicts mapped to RadianceFleet AIS format.
    """
    uname = username or settings.AISHUB_USERNAME
    if not uname:
        raise ValueError(
            "AISHUB_USERNAME not configured. Join at https://www.aishub.net/"
        )

    lat_min, lon_min, lat_max, lon_max = bbox

    params: dict[str, Any] = {
        "username": uname,
        "format": output_format,
        "output": "json",
        "compress": 0,  # No compression for simplicity
        "latmin": lat_min,
        "latmax": lat_max,
        "lonmin": lon_min,
        "lonmax": lon_max,
    }

    from app.utils.http_retry import retry_request

    positions = []
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = retry_request(
                client.get, _BASE_URL, params=params,
                delays=[60, 120, 180],  # AISHub rate limit: 1 req/min
            )
            data = resp.json()

        # AISHub returns a list with metadata at index 0 and positions at index 1
        if isinstance(data, list) and len(data) >= 2:
            meta = data[0]
            if isinstance(meta, dict) and meta.get("ERROR", False):
                error_msg = meta.get("ERROR_MESSAGE", "Unknown AISHub error")
                logger.error("AISHub API error: %s", error_msg)
                raise RuntimeError(f"AISHub API error: {error_msg}")

            raw_positions = data[1] if isinstance(data[1], list) else []
        elif isinstance(data, list):
            raw_positions = data
        else:
            raw_positions = []

        for pos in raw_positions:
            mapped = _map_aishub_position(pos)
            if mapped:
                positions.append(mapped)

    except httpx.HTTPStatusError as exc:
        logger.error("AISHub request failed: HTTP %d", exc.response.status_code)
        raise
    except httpx.TimeoutException:
        logger.error("AISHub request timed out after %ss", _TIMEOUT)
        raise

    logger.info("AISHub: fetched %d positions for bbox %s", len(positions), bbox)
    return positions


def _map_aishub_position(pos: dict) -> dict | None:
    """Map an AISHub position record to RadianceFleet AIS point format."""
    try:
        mmsi = str(pos.get("MMSI", ""))
        # 1.5: Filter non-vessel MMSIs
        if not mmsi or mmsi == "0" or is_non_vessel_mmsi(mmsi):
            return None

        lat = pos.get("LATITUDE")
        lon = pos.get("LONGITUDE")
        if lat is None or lon is None:
            return None

        # AISHub coordinates are in 1/10000 minutes (divide by 600000)
        if isinstance(lat, int) and abs(lat) > 180:
            lat = lat / 600000.0
        if isinstance(lon, int) and abs(lon) > 360:
            lon = lon / 600000.0

        lat = float(lat)
        lon = float(lon)
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return None

        # 1.3/1.6: Timestamp — use flexible parser, no fallback to now()
        ts_raw = pos.get("TIME")
        ts = parse_timestamp_flexible(ts_raw)
        if ts is None:
            return None

        # 1.1: SOG sentinel — raw 1023 / 10 = 102.3 = "not available"
        sog = pos.get("SOG")
        if sog is not None:
            sog = float(sog) / 10.0  # AISHub SOG is in 1/10 knot
            if sog >= 102.2:
                sog = None

        # 1.1: COG sentinel — raw 3600 / 10 = 360.0 = "not available"
        cog = pos.get("COG")
        if cog is not None:
            cog = float(cog) / 10.0  # AISHub COG is in 1/10 degree
            if cog >= 360.0:
                cog = None

        heading = pos.get("HEADING")
        if heading is not None:
            heading = float(heading)
            if heading == 511:  # 511 = not available
                heading = None

        return {
            "mmsi": mmsi,
            "vessel_name": pos.get("NAME", "").strip() or None,
            "imo": str(pos.get("IMO", "")) if pos.get("IMO") else None,
            "timestamp": ts.isoformat(),
            "lat": lat,
            "lon": lon,
            "sog": sog,
            "cog": cog,
            "heading": heading,
            "nav_status": pos.get("NAVSTAT"),
            "vessel_type": str(pos.get("TYPE", "")) if pos.get("TYPE") else None,
            "source": "aishub",
        }
    except Exception as exc:
        logger.debug("Failed to map AISHub position: %s", exc)
        return None


def ingest_aishub_positions(
    positions: list[dict],
    db: Session,
) -> dict[str, int]:
    """Ingest AISHub positions into the database.

    Uses the same vessel upsert and AIS point creation pattern as ingest.py.
    Returns {"stored": int, "skipped": int, "vessels_created": int}.
    """
    from app.models.vessel import Vessel
    from app.models.ais_point import AISPoint
    from app.modules.ingest import _parse_timestamp

    stats = {"stored": 0, "skipped": 0, "vessels_created": 0}

    for pos in positions:
        mmsi = str(pos["mmsi"])
        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
        if not vessel:
            ts = _parse_timestamp(pos)
            if ts is None:
                stats["skipped"] += 1
                continue
            from app.utils.vessel_identity import mmsi_to_flag, flag_to_risk_category
            derived_flag = mmsi_to_flag(mmsi)
            vessel = Vessel(
                mmsi=mmsi,
                name=pos.get("vessel_name"),
                imo=pos.get("imo"),
                flag=derived_flag,
                flag_risk_category=flag_to_risk_category(derived_flag),
                ais_class="A",
                ais_source="aishub",
                mmsi_first_seen_utc=ts,
            )
            try:
                with db.begin_nested():
                    db.add(vessel)
                    db.flush()
            except IntegrityError:
                vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                if not vessel:
                    stats["skipped"] += 1
                    continue
            stats["vessels_created"] += 1

        ts = _parse_timestamp(pos)
        if ts is None:
            stats["skipped"] += 1
            continue

        # Skip duplicates
        existing = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id, AISPoint.timestamp_utc == ts)
            .first()
        )
        if existing:
            stats["skipped"] += 1
            continue

        point = AISPoint(
            vessel_id=vessel.vessel_id,
            timestamp_utc=ts,
            lat=float(pos["lat"]),
            lon=float(pos["lon"]),
            sog=float(pos["sog"]) if pos.get("sog") is not None else None,
            cog=float(pos["cog"]) if pos.get("cog") is not None else None,
            heading=float(pos["heading"]) if pos.get("heading") is not None else None,
            nav_status=pos.get("nav_status"),
            ais_class="A",
            source="aishub",
        )
        db.add(point)
        stats["stored"] += 1

    db.commit()
    logger.info("AISHub ingest: %s", stats)
    return stats
