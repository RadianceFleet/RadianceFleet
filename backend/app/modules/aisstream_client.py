"""aisstream.io WebSocket client — real-time AIS data streaming.

Connects to wss://stream.aisstream.io/v0/stream and ingests PositionReport
and ShipStaticData messages into the RadianceFleet database.

Usage:
    from app.modules.aisstream_client import stream_ais, get_corridor_bounding_boxes
    boxes = get_corridor_bounding_boxes(db)
    result = asyncio.run(stream_ais(api_key, boxes, duration_seconds=300))
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def get_corridor_bounding_boxes(db: Session) -> list[list[list[float]]]:
    """Extract bounding boxes from corridor geometries for aisstream subscription.

    Returns list of [[lon_min, lat_min], [lon_max, lat_max]] pairs.
    """
    from app.models.corridor import Corridor

    corridors = db.query(Corridor).all()
    boxes = []
    for c in corridors:
        if c.geometry is None:
            continue
        try:
            from geoalchemy2.shape import to_shape
            shape = to_shape(c.geometry)
            bounds = shape.bounds  # (minx, miny, maxx, maxy) = (lon_min, lat_min, lon_max, lat_max)
            boxes.append([
                [bounds[1], bounds[0]],  # [lat_min, lon_min]
                [bounds[3], bounds[2]],  # [lat_max, lon_max]
            ])
        except Exception as exc:
            logger.warning("Could not extract bbox from corridor %s: %s", c.name, exc)
    return boxes


def _map_position_report(msg: dict) -> dict | None:
    """Map an aisstream PositionReport message to an AIS point dict."""
    try:
        meta = msg.get("MetaData", {})
        report = msg.get("Message", {}).get("PositionReport", {})
        if not report:
            return None

        mmsi = str(meta.get("MMSI", ""))
        if not mmsi or mmsi == "0":
            return None

        lat = meta.get("latitude") or report.get("Latitude")
        lon = meta.get("longitude") or report.get("Longitude")
        if lat is None or lon is None:
            return None
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return None

        ts_raw = meta.get("time_utc", "")
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        return {
            "mmsi": mmsi,
            "vessel_name": meta.get("ShipName", "").strip() or None,
            "timestamp": ts.isoformat(),
            "lat": lat,
            "lon": lon,
            "sog": report.get("Sog"),
            "cog": report.get("Cog"),
            "heading": report.get("TrueHeading"),
            "nav_status": report.get("NavigationalStatus"),
            "source": "aisstream",
        }
    except Exception as exc:
        logger.debug("Failed to map position report: %s", exc)
        return None


def _map_static_data(msg: dict) -> dict | None:
    """Map an aisstream ShipStaticData message to a vessel metadata dict."""
    try:
        meta = msg.get("MetaData", {})
        static = msg.get("Message", {}).get("ShipStaticData", {})
        if not static:
            return None

        mmsi = str(meta.get("MMSI", ""))
        if not mmsi or mmsi == "0":
            return None

        dim = static.get("Dimension", {})
        length = None
        width = None
        if dim:
            a = dim.get("A", 0) or 0
            b = dim.get("B", 0) or 0
            c = dim.get("C", 0) or 0
            d = dim.get("D", 0) or 0
            length = (a + b) if (a + b) > 0 else None
            width = (c + d) if (c + d) > 0 else None

        return {
            "mmsi": mmsi,
            "imo": str(static.get("ImoNumber", "")) if static.get("ImoNumber") else None,
            "vessel_name": meta.get("ShipName", "").strip() or None,
            "vessel_type": _ais_type_to_string(static.get("Type", 0)),
            "length": length,
            "width": width,
            "callsign": static.get("CallSign", "").strip() or None,
        }
    except Exception as exc:
        logger.debug("Failed to map static data: %s", exc)
        return None


def _ais_type_to_string(type_code: int) -> str | None:
    """Convert AIS ship type code to human-readable string."""
    if not type_code:
        return None
    if 80 <= type_code <= 89:
        return "Tanker"
    if 70 <= type_code <= 79:
        return "Cargo"
    if 60 <= type_code <= 69:
        return "Passenger"
    if 40 <= type_code <= 49:
        return "High Speed Craft"
    if 30 <= type_code <= 39:
        return "Fishing"
    return f"Type {type_code}"


def _ingest_batch(db: Session, points: list[dict], static_updates: dict[str, dict]) -> dict:
    """Ingest a batch of AIS points and static data updates into the DB.

    Returns {"points_stored": int, "vessels_updated": int}.
    """
    from app.models.vessel import Vessel
    from app.models.ais_point import AISPoint
    from app.modules.ingest import _parse_timestamp

    stored = 0
    vessels_updated = 0

    # Apply static data updates (vessel metadata from ShipStaticData messages)
    for mmsi, sdata in static_updates.items():
        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
        if vessel:
            changed = False
            if sdata.get("imo") and not vessel.imo:
                vessel.imo = sdata["imo"]
                changed = True
            if sdata.get("vessel_type") and not vessel.vessel_type:
                vessel.vessel_type = sdata["vessel_type"]
                changed = True
            if sdata.get("callsign") and not vessel.callsign:
                vessel.callsign = sdata["callsign"]
                changed = True
            if changed:
                vessels_updated += 1

    # Ingest position reports
    for pt in points:
        mmsi = str(pt["mmsi"])
        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
        if not vessel:
            ts = _parse_timestamp(pt)
            from app.utils.vessel_identity import mmsi_to_flag, flag_to_risk_category
            derived_flag = mmsi_to_flag(mmsi)
            vessel = Vessel(
                mmsi=mmsi,
                name=pt.get("vessel_name"),
                flag=derived_flag,
                flag_risk_category=flag_to_risk_category(derived_flag),
                ais_class="A",
                ais_source="aisstream",
                mmsi_first_seen_utc=ts,
            )
            db.add(vessel)
            db.flush()

        ts = _parse_timestamp(pt)

        # Skip duplicates (same vessel + timestamp)
        existing = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id, AISPoint.timestamp_utc == ts)
            .first()
        )
        if existing:
            continue

        point = AISPoint(
            vessel_id=vessel.vessel_id,
            timestamp_utc=ts,
            lat=float(pt["lat"]),
            lon=float(pt["lon"]),
            sog=float(pt["sog"]) if pt.get("sog") is not None else None,
            cog=float(pt["cog"]) if pt.get("cog") is not None else None,
            heading=float(pt["heading"]) if pt.get("heading") is not None and pt["heading"] != 511 else None,
            nav_status=pt.get("nav_status"),
            ais_class="A",
            source="aisstream",
        )
        db.add(point)
        stored += 1

    db.commit()
    return {"points_stored": stored, "vessels_updated": vessels_updated}


async def stream_ais(
    api_key: str,
    bounding_boxes: list[list[list[float]]],
    duration_seconds: int = 300,
    batch_interval: int = 30,
    db_factory: Callable[[], Session] | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    """Stream AIS data from aisstream.io and ingest into the database.

    Args:
        api_key: aisstream.io API key.
        bounding_boxes: List of [[lat_min, lon_min], [lat_max, lon_max]] pairs.
        duration_seconds: How long to stream (0 = unlimited). Default 300s (5 min).
        batch_interval: Seconds between batch DB writes. Default 30.
        db_factory: Callable returning a new SQLAlchemy Session. Defaults to SessionLocal.
        progress_callback: Called with stats dict after each batch.

    Returns:
        Summary dict with total messages, points stored, vessels seen.
    """
    import websockets

    if db_factory is None:
        from app.database import SessionLocal
        db_factory = SessionLocal

    ws_url = settings.AISSTREAM_WS_URL

    # Use all corridor bounding boxes, or a default Baltic/Black Sea box
    if not bounding_boxes:
        bounding_boxes = [
            [[54.0, 10.0], [66.0, 30.0]],  # Baltic Sea
            [[40.0, 27.0], [47.0, 42.0]],  # Black Sea
        ]

    subscription = {
        "APIKey": api_key,
        "BoundingBoxes": bounding_boxes,
        "FiltersShipMMSI": [],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }

    stats: dict[str, Any] = {
        "messages_received": 0,
        "position_reports": 0,
        "static_data_msgs": 0,
        "points_stored": 0,
        "vessels_seen": set(),
        "vessels_updated": 0,
        "batches": 0,
        "errors": 0,
        "duration_seconds": duration_seconds,
    }

    point_buffer: list[dict] = []
    static_buffer: dict[str, dict] = {}  # mmsi -> latest static data
    last_batch_time = time.monotonic()
    start_time = time.monotonic()

    try:
        async with websockets.connect(ws_url) as ws:
            # Send subscription within 3 seconds of connecting
            await ws.send(json.dumps(subscription))
            logger.info(
                "Connected to aisstream.io — streaming %d bounding boxes for %ss",
                len(bounding_boxes),
                duration_seconds or "unlimited",
            )

            async for raw_msg in ws:
                elapsed = time.monotonic() - start_time
                if duration_seconds > 0 and elapsed >= duration_seconds:
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    stats["errors"] += 1
                    continue

                stats["messages_received"] += 1
                msg_type = msg.get("MessageType", "")

                if msg_type == "PositionReport":
                    pt = _map_position_report(msg)
                    if pt:
                        stats["position_reports"] += 1
                        stats["vessels_seen"].add(pt["mmsi"])
                        point_buffer.append(pt)
                elif msg_type == "ShipStaticData":
                    sd = _map_static_data(msg)
                    if sd:
                        stats["static_data_msgs"] += 1
                        stats["vessels_seen"].add(sd["mmsi"])
                        static_buffer[sd["mmsi"]] = sd

                # Batch insert at interval
                now = time.monotonic()
                if now - last_batch_time >= batch_interval and point_buffer:
                    db = db_factory()
                    try:
                        result = _ingest_batch(db, point_buffer, static_buffer)
                        stats["points_stored"] += result["points_stored"]
                        stats["vessels_updated"] += result["vessels_updated"]
                        stats["batches"] += 1
                    finally:
                        db.close()

                    if progress_callback:
                        progress_callback({
                            "elapsed_s": int(elapsed),
                            "messages": stats["messages_received"],
                            "points_stored": stats["points_stored"],
                            "vessels_seen": len(stats["vessels_seen"]),
                            "msg_per_s": round(stats["messages_received"] / max(elapsed, 1), 1),
                        })

                    point_buffer.clear()
                    static_buffer.clear()
                    last_batch_time = now

            # Final batch
            if point_buffer:
                db = db_factory()
                try:
                    result = _ingest_batch(db, point_buffer, static_buffer)
                    stats["points_stored"] += result["points_stored"]
                    stats["vessels_updated"] += result["vessels_updated"]
                    stats["batches"] += 1
                finally:
                    db.close()

    except Exception as exc:
        logger.error("aisstream.io connection error: %s", exc)
        stats["error"] = str(exc)

    # Convert set to count for JSON serialization
    stats["vessels_seen"] = len(stats["vessels_seen"])
    stats["actual_duration_s"] = round(time.monotonic() - start_time, 1)

    logger.info(
        "aisstream.io session complete: %d msgs, %d points stored, %d vessels",
        stats["messages_received"],
        stats["points_stored"],
        stats["vessels_seen"],
    )
    return stats
