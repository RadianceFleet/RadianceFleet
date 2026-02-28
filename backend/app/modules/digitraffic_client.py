"""Digitraffic (Fintraffic) Marine API client.

REST API for Finnish AIS data + port calls covering the Baltic Sea.
Covers Primorsk, Ust-Luga -- the #1 Russian oil export corridor (47.7% of seaborne crude).

Reference: https://www.digitraffic.fi/en/marine-traffic/
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://meri.digitraffic.fi"
_AIS_ENDPOINT = f"{_BASE_URL}/api/ais/v1/locations"
_PORT_CALLS_ENDPOINT = f"{_BASE_URL}/api/port-call/v1/port-calls"
_TIMEOUT = 30


def fetch_digitraffic_ais(
    db: Session,
    bbox: tuple[float, float, float, float] | None = None,
) -> dict:
    """Fetch latest AIS positions from Digitraffic and ingest.

    Args:
        db: Active SQLAlchemy session.
        bbox: Optional (min_lat, min_lon, max_lat, max_lon) filter.
              Defaults to Baltic region if None.

    Returns:
        {"points_ingested": N, "vessels_seen": M, "errors": E}
    """
    if not getattr(settings, "DIGITRAFFIC_ENABLED", False):
        logger.info("Digitraffic disabled (DIGITRAFFIC_ENABLED=False)")
        return {"points_ingested": 0, "vessels_seen": 0, "errors": 0}

    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel
    from app.modules.normalize import is_non_vessel_mmsi
    from app.utils.vessel_identity import flag_to_risk_category, mmsi_to_flag

    points = 0
    vessels: set[str] = set()
    errors = 0

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(_AIS_ENDPOINT)
            resp.raise_for_status()
            data = resp.json()

        features = data.get("features", [])
        logger.info("Digitraffic: received %d vessel positions", len(features))

        for feat in features:
            try:
                props = feat.get("properties", {})
                geom = feat.get("geometry", {})
                coords = geom.get("coordinates", [])

                mmsi = str(props.get("mmsi", ""))
                if not mmsi or len(mmsi) != 9:
                    continue
                if is_non_vessel_mmsi(mmsi):
                    continue

                lon, lat = float(coords[0]), float(coords[1])

                # Filter by bbox if provided
                if bbox:
                    min_lat, min_lon, max_lat, max_lon = bbox
                    if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                        continue

                sog = props.get("sog")
                cog = props.get("cog")
                heading = props.get("heading")
                ts = props.get("timestampExternal") or props.get("timestamp")

                timestamp = datetime.now(timezone.utc)
                if ts:
                    try:
                        if isinstance(ts, (int, float)):
                            timestamp = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                        else:
                            timestamp = datetime.fromisoformat(
                                str(ts).replace("Z", "+00:00")
                            )
                    except Exception:
                        pass

                vessels.add(mmsi)

                # Upsert vessel
                vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                if not vessel:
                    derived_flag = mmsi_to_flag(mmsi)
                    vessel = Vessel(
                        mmsi=mmsi,
                        flag=derived_flag,
                        flag_risk_category=flag_to_risk_category(derived_flag),
                        ais_class="A",
                        ais_source="digitraffic",
                        mmsi_first_seen_utc=timestamp,
                    )
                    try:
                        with db.begin_nested():
                            db.add(vessel)
                            db.flush()
                    except IntegrityError:
                        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                        if not vessel:
                            errors += 1
                            continue

                # Dedup
                existing = (
                    db.query(AISPoint)
                    .filter(
                        AISPoint.vessel_id == vessel.vessel_id,
                        AISPoint.timestamp_utc == timestamp,
                    )
                    .first()
                )
                if existing:
                    continue

                point = AISPoint(
                    vessel_id=vessel.vessel_id,
                    timestamp_utc=timestamp,
                    lat=lat,
                    lon=lon,
                    sog=float(sog) / 10.0 if sog is not None else None,
                    cog=float(cog) / 10.0 if cog is not None else None,
                    heading=float(heading)
                    if heading is not None and heading != 511
                    else None,
                    ais_class="A",
                    source="digitraffic",
                )
                db.add(point)
                points += 1
            except Exception:
                errors += 1

        db.commit()

    except Exception as e:
        logger.error("Digitraffic fetch failed: %s", e)
        errors += 1

    logger.info("Digitraffic: %d points, %d vessels, %d errors", points, len(vessels), errors)
    return {"points_ingested": points, "vessels_seen": len(vessels), "errors": errors}


def fetch_digitraffic_port_calls(db: Session, mmsi: str | None = None) -> dict:
    """Fetch port call data from Digitraffic.

    Args:
        db: Active SQLAlchemy session.
        mmsi: Optional MMSI filter. If None, fetches recent port calls.

    Returns:
        {"port_calls_created": N, "errors": E}
    """
    if not getattr(settings, "DIGITRAFFIC_ENABLED", False):
        return {"port_calls_created": 0, "errors": 0}

    from app.models.port import Port
    from app.models.port_call import PortCall
    from app.models.vessel import Vessel

    created = 0
    errors = 0

    try:
        params: dict[str, str] = {}
        if mmsi:
            params["vesselMmsi"] = mmsi

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(_PORT_CALLS_ENDPOINT, params=params)
            resp.raise_for_status()
            data = resp.json()

        port_calls_data = data if isinstance(data, list) else data.get("portCalls", [])

        for pc in port_calls_data:
            try:
                pc_mmsi = str(pc.get("mmsi") or "")
                vessel = (
                    db.query(Vessel).filter(Vessel.mmsi == pc_mmsi).first()
                    if pc_mmsi
                    else None
                )
                if not vessel:
                    continue

                arrival = pc.get("portCallTimestamp") or pc.get("ata")
                departure = pc.get("departure") or pc.get("atd")

                if not arrival:
                    continue

                arr_dt = datetime.fromisoformat(str(arrival).replace("Z", "+00:00"))
                dep_dt = (
                    datetime.fromisoformat(str(departure).replace("Z", "+00:00"))
                    if departure
                    else None
                )

                # Try to find a matching port by name
                port_name = pc.get("portName") or pc.get("portAreaName") or ""
                port = None
                if port_name:
                    port = (
                        db.query(Port)
                        .filter(Port.name.ilike(f"%{port_name}%"))
                        .first()
                    )

                if not port:
                    # Cannot create PortCall without a port_id (non-nullable FK)
                    continue

                # Dedup
                existing = (
                    db.query(PortCall)
                    .filter(
                        PortCall.vessel_id == vessel.vessel_id,
                        PortCall.arrival_utc == arr_dt,
                    )
                    .first()
                )
                if existing:
                    continue

                port_call = PortCall(
                    vessel_id=vessel.vessel_id,
                    port_id=port.port_id,
                    arrival_utc=arr_dt,
                    departure_utc=dep_dt,
                )
                db.add(port_call)
                created += 1

            except Exception:
                errors += 1

        db.commit()

    except Exception as e:
        logger.error("Digitraffic port calls failed: %s", e)
        errors += 1

    logger.info("Digitraffic port calls: %d created, %d errors", created, errors)
    return {"port_calls_created": created, "errors": errors}
