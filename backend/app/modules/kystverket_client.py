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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect((_host, _port))

        buffer = b""
        batch: list[dict] = []
        batch_size = 50

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
                            lat = decoded.get("lat")
                            lon = decoded.get("lon")
                            sog = decoded.get("speed")
                            cog = decoded.get("course")
                            heading = decoded.get("heading")

                            if not mmsi or lat is None or lon is None:
                                continue
                            if lat == 91.0 or lon == 181.0:  # default/unavailable
                                continue
                            if is_non_vessel_mmsi(mmsi):
                                continue

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
                                    "timestamp_utc": datetime.now(timezone.utc),
                                    "source": "kystverket",
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
    )
    db.add(point)
