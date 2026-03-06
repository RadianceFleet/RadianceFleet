"""Kystverket (Norwegian Coastal Administration) AIS feed client.

Consumes raw NMEA AIS sentences from the public TCP stream at 153.44.253.27:5631.
Uses pyais for decoding. Covers Barents Sea + Norwegian Sea -- the Murmansk export corridor.

Reference: https://www.kystverket.no/en/navigation-and-monitoring/ais/access-to-ais-data/
"""
from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.modules.circuit_breakers import breakers

logger = logging.getLogger(__name__)


def stream_kystverket(
    db: Session,
    duration_seconds: int = 300,
    host: str | None = None,
    port: int | None = None,
) -> dict:
    """Connect to Kystverket TCP AIS stream and ingest positions.

    Args:
        db: Active SQLAlchemy session.
        duration_seconds: How long to stream (default 5 minutes).
        host: TCP host (default from settings or 153.44.253.27).
        port: TCP port (default from settings or 5631).

    Returns:
        {"points_ingested": N, "vessels_seen": M, "errors": E}
    """
    if not getattr(settings, "KYSTVERKET_ENABLED", False):
        logger.info("Kystverket streaming disabled (KYSTVERKET_ENABLED=False)")
        return {"points_ingested": 0, "vessels_seen": 0, "errors": 0}

    _host = host or getattr(settings, "KYSTVERKET_HOST", "153.44.253.27")
    _port = port or getattr(settings, "KYSTVERKET_PORT", 5631)

    try:
        from pyais import decode as pyais_decode
    except ImportError:
        logger.error("pyais not installed. Run: uv pip install pyais>=2.5.0")
        return {"points_ingested": 0, "vessels_seen": 0, "errors": 1}

    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel
    from app.modules.normalize import is_non_vessel_mmsi
    from app.utils.vessel_identity import flag_to_risk_category, mmsi_to_flag
    from sqlalchemy.exc import IntegrityError

    points_ingested = 0
    vessels_seen: set[str] = set()
    errors = 0
    deadline = datetime.now(timezone.utc).timestamp() + duration_seconds

    try:
        logger.info(
            "Connecting to Kystverket AIS stream at %s:%d for %ds",
            _host,
            _port,
            duration_seconds,
        )

        def _connect():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(30)
            s.connect((_host, _port))
            return s

        sock = breakers["kystverket"].call(_connect)

        buffer = b""
        batch: list[dict] = []
        batch_size = 50
        static_cache: dict[str, dict] = {}  # mmsi -> {destination, draught} from Type 5

        while datetime.now(timezone.utc).timestamp() < deadline:
            try:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data
                lines = buffer.split(b"\n")
                buffer = lines[-1]  # incomplete line stays in buffer

                for line in lines[:-1]:
                    line_str = line.decode("ascii", errors="ignore").strip()
                    if not line_str or not line_str.startswith("!"):
                        continue
                    try:
                        msgs = pyais_decode(line_str)
                        for msg in msgs:
                            decoded = msg.asdict()
                            mmsi = str(decoded.get("mmsi", ""))
                            if not mmsi or is_non_vessel_mmsi(mmsi):
                                continue

                            # Type 5 messages carry static/voyage data but no position
                            msg_type = decoded.get("msg_type")
                            if msg_type == 5:
                                dest = (decoded.get("destination") or "").strip()[:20] or None
                                dr = decoded.get("draught")
                                raw_imo = decoded.get("imo")
                                static_cache[mmsi] = {
                                    "destination": dest,
                                    "draught": float(dr) / 10.0 if dr is not None else None,
                                    "imo": str(raw_imo) if raw_imo and int(raw_imo) > 0 else None,
                                    "callsign": (decoded.get("callsign") or "").strip() or None,
                                    "vessel_name": (decoded.get("shipname") or "").strip() or None,
                                    "vessel_type": _ais_ship_type_to_string(decoded.get("ship_type", 0)),
                                }
                                continue

                            lat = decoded.get("lat")
                            lon = decoded.get("lon")
                            sog = decoded.get("speed")
                            cog = decoded.get("course")
                            heading = decoded.get("heading")

                            if lat is None or lon is None:
                                continue
                            if lat == 91.0 or lon == 181.0:  # default/unavailable
                                continue

                            # Merge static data (destination/draught) if available
                            sd = static_cache.get(mmsi, {})

                            vessels_seen.add(mmsi)
                            batch.append(
                                {
                                    "mmsi": mmsi,
                                    "lat": float(lat),
                                    "lon": float(lon),
                                    "sog": float(sog) if sog is not None else None,
                                    "cog": float(cog) if cog is not None else None,
                                    "heading": float(heading)
                                    if heading is not None and heading != 511
                                    else None,
                                    "timestamp_utc": datetime.utcnow(),
                                    "source": "kystverket",
                                    "destination": sd.get("destination"),
                                    "draught": sd.get("draught"),
                                    "static_data": sd if sd else None,
                                }
                            )

                            if len(batch) >= batch_size:
                                for pt in batch:
                                    try:
                                        _ingest_point(db, pt)
                                        points_ingested += 1
                                    except Exception:
                                        errors += 1
                                db.commit()
                                batch = []
                    except Exception:
                        errors += 1

            except socket.timeout:
                continue
            except Exception as e:
                logger.warning("Kystverket stream error: %s", e)
                errors += 1
                break

        # Flush remaining batch
        for pt in batch:
            try:
                _ingest_point(db, pt)
                points_ingested += 1
            except Exception:
                errors += 1
        if batch:
            db.commit()

        sock.close()

    except Exception as e:
        logger.error("Kystverket connection failed: %s", e)
        errors += 1

    logger.info(
        "Kystverket stream complete: %d points ingested, %d vessels, %d errors",
        points_ingested,
        len(vessels_seen),
        errors,
    )
    return {
        "points_ingested": points_ingested,
        "vessels_seen": len(vessels_seen),
        "errors": errors,
    }


def _ingest_point(db: Session, pt: dict) -> None:
    """Ingest a single AIS point, creating the vessel if needed."""
    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel
    from app.utils.vessel_identity import flag_to_risk_category, mmsi_to_flag
    from sqlalchemy.exc import IntegrityError

    mmsi = pt["mmsi"]
    vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
    if not vessel:
        derived_flag = mmsi_to_flag(mmsi)
        vessel = Vessel(
            mmsi=mmsi,
            flag=derived_flag,
            flag_risk_category=flag_to_risk_category(derived_flag),
            ais_class="A",
            ais_source="kystverket",
            mmsi_first_seen_utc=pt["timestamp_utc"],
        )
        try:
            with db.begin_nested():
                db.add(vessel)
                db.flush()
        except IntegrityError:
            vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
            if not vessel:
                raise

    # Update data freshness tracking
    current_ais = getattr(vessel, "last_ais_received_utc", None)
    pt_ts = pt["timestamp_utc"]
    if current_ais is None or not isinstance(current_ais, datetime) or pt_ts > current_ais:
        vessel.last_ais_received_utc = pt_ts

    # Dedup
    existing = (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel.vessel_id,
            AISPoint.timestamp_utc == pt["timestamp_utc"],
        )
        .first()
    )
    if existing:
        return

    # Apply Type 5 static data to vessel (fill-if-empty)
    static = pt.get("static_data") or {}
    if static.get("imo") and not vessel.imo:
        vessel.imo = static["imo"]
    if static.get("callsign") and not vessel.callsign:
        vessel.callsign = static["callsign"]
    if static.get("vessel_name") and not vessel.name:
        vessel.name = static["vessel_name"]
    if static.get("vessel_type") and not vessel.vessel_type:
        vessel.vessel_type = static["vessel_type"]

    point = AISPoint(
        vessel_id=vessel.vessel_id,
        timestamp_utc=pt["timestamp_utc"],
        lat=pt["lat"],
        lon=pt["lon"],
        sog=pt["sog"],
        cog=pt["cog"],
        heading=pt["heading"],
        ais_class="A",
        source="kystverket",
        destination=pt.get("destination"),
        draught=pt.get("draught"),
    )
    db.add(point)

    # Dual-write to AIS observations for cross-receiver detection
    try:
        from app.models.ais_observation import AISObservation
        obs = AISObservation(
            mmsi=pt["mmsi"],
            timestamp_utc=pt["timestamp_utc"],
            lat=pt["lat"],
            lon=pt["lon"],
            sog=pt["sog"],
            cog=pt["cog"],
            heading=pt["heading"],
            draught=pt.get("draught"),
            source="kystverket",
        )
        db.add(obs)
    except Exception:
        pass  # Non-blocking


def _ais_ship_type_to_string(ship_type: int) -> str | None:
    """Convert AIS ship type code (ITU-R M.1371) to human-readable string.

    Returns None for unknown/unavailable codes (0, 99, or out of range).
    """
    if not ship_type or ship_type == 0:
        return None
    # Major categories (first digit = X0-X9 range)
    _CATEGORIES = {
        2: "WIG",           # Wing-In-Ground
        3: "Vessel",        # Fishing, towing, dredging, diving, military, sailing, pleasure
        4: "HSC",           # High Speed Craft
        5: "Special",       # Pilot, SAR, tug, port tender, anti-pollution, law enforcement
        6: "Passenger",
        7: "Cargo",
        8: "Tanker",
        9: "Other",
    }
    # Specific subtypes of interest for shadow fleet detection
    _SUBTYPES = {
        30: "Fishing",
        31: "Towing",
        32: "Towing (large)",
        33: "Dredging",
        34: "Diving",
        35: "Military",
        36: "Sailing",
        37: "Pleasure craft",
        60: "Passenger",
        70: "Cargo",
        71: "Cargo (DG Cat A)",
        72: "Cargo (DG Cat B)",
        73: "Cargo (DG Cat C)",
        74: "Cargo (DG Cat D)",
        80: "Tanker",
        81: "Tanker (DG Cat A)",
        82: "Tanker (DG Cat B)",
        83: "Tanker (DG Cat C)",
        84: "Tanker (DG Cat D)",
        89: "Tanker (other)",
    }
    if ship_type in _SUBTYPES:
        return _SUBTYPES[ship_type]
    category = ship_type // 10
    return _CATEGORIES.get(category)
