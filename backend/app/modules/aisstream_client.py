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
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.normalize import is_non_vessel_mmsi, parse_timestamp_flexible

logger = logging.getLogger(__name__)


def get_corridor_bounding_boxes(db: Session, max_boxes: int = 10) -> list[list[list[float]]]:
    """Extract bounding boxes from corridor geometries for aisstream subscription.

    Returns list of [[lat_min, lon_min], [lat_max, lon_max]] pairs, merged
    into at most *max_boxes* regional super-boxes to avoid API throttling.
    The aisstream.io free tier silently throttles PositionReport delivery
    when too many bounding boxes are subscribed (observed: 39 boxes → ~0.02
    positions/s vs 3 boxes → ~26 positions/s).
    """
    from app.models.corridor import Corridor

    from app.utils.geo import load_geometry

    corridors = db.query(Corridor).all()
    raw_boxes: list[list[list[float]]] = []
    for c in corridors:
        if c.geometry is None:
            continue
        try:
            shape = load_geometry(c.geometry)
            if shape is None:
                continue
            bounds = shape.bounds  # (minx, miny, maxx, maxy) = (lon_min, lat_min, lon_max, lat_max)
            raw_boxes.append([
                [bounds[1], bounds[0]],  # [lat_min, lon_min]
                [bounds[3], bounds[2]],  # [lat_max, lon_max]
            ])
        except Exception as exc:
            logger.warning("Could not extract bbox from corridor %s: %s", c.name, exc)

    if len(raw_boxes) <= max_boxes:
        return raw_boxes

    return _merge_bounding_boxes(raw_boxes, max_boxes)


def _box_area(box: list[list[float]]) -> float:
    """Return the approximate area of a bounding box in square degrees."""
    return abs(box[1][0] - box[0][0]) * abs(box[1][1] - box[0][1])


def _merge_bounding_boxes(
    boxes: list[list[list[float]]],
    max_boxes: int = 10,
    max_box_area: float = 400.0,
) -> list[list[list[float]]]:
    """Merge bounding boxes into at most *max_boxes* by iteratively combining
    the closest pair, with an area cap to prevent API throttling.

    The aisstream.io API silently throttles PositionReport delivery when
    bounding boxes are too large or too numerous.  Boxes exceeding
    *max_box_area* square degrees are never merged further.
    """
    if len(boxes) <= max_boxes:
        return boxes

    # Work with mutable copies: each box is [[lat_min, lon_min], [lat_max, lon_max]]
    merged = [list(b) for b in boxes]

    while len(merged) > max_boxes:
        # Find the closest pair by center distance, skipping merges that would
        # exceed the area cap.
        best_dist = float("inf")
        best_i, best_j = -1, -1
        for i in range(len(merged)):
            ci_lat = (merged[i][0][0] + merged[i][1][0]) / 2
            ci_lon = (merged[i][0][1] + merged[i][1][1]) / 2
            for j in range(i + 1, len(merged)):
                # Preview the merged box area
                candidate = [
                    [min(merged[i][0][0], merged[j][0][0]), min(merged[i][0][1], merged[j][0][1])],
                    [max(merged[i][1][0], merged[j][1][0]), max(merged[i][1][1], merged[j][1][1])],
                ]
                if _box_area(candidate) > max_box_area:
                    continue  # Would be too large — skip

                cj_lat = (merged[j][0][0] + merged[j][1][0]) / 2
                cj_lon = (merged[j][0][1] + merged[j][1][1]) / 2
                d = (ci_lat - cj_lat) ** 2 + (ci_lon - cj_lon) ** 2
                if d < best_dist:
                    best_dist = d
                    best_i, best_j = i, j

        if best_i < 0:
            # No mergeable pair found (all remaining merges exceed area cap).
            # Drop the largest-area box that has the least corridor coverage
            # to stay within max_boxes.
            if len(merged) > max_boxes:
                areas = [(i, _box_area(b)) for i, b in enumerate(merged)]
                areas.sort(key=lambda x: -x[1])  # Largest first
                logger.warning(
                    "Bounding box merge: dropping largest box (%.1f sq deg) to stay within %d-box limit. "
                    "Some corridor coverage may be lost.",
                    areas[0][1], max_boxes,
                )
                merged.pop(areas[0][0])
            break

        # Merge best_j into best_i (union of bounding boxes)
        bi, bj = merged[best_i], merged[best_j]
        merged[best_i] = [
            [min(bi[0][0], bj[0][0]), min(bi[0][1], bj[0][1])],
            [max(bi[1][0], bj[1][0]), max(bi[1][1], bj[1][1])],
        ]
        merged.pop(best_j)

    logger.info(
        "Merged %d corridor boxes into %d regional boxes for AIS streaming",
        len(boxes), len(merged),
    )
    return merged


def _map_position_report(msg: dict, msg_type: str = "PositionReport") -> dict | None:
    """Map an aisstream position report message to an AIS point dict.

    Handles both Class A (PositionReport) and Class B
    (StandardClassBPositionReport) message types.
    """
    try:
        meta = msg.get("MetaData", {})
        report = msg.get("Message", {}).get(msg_type, {})
        if not report:
            return None

        mmsi = str(meta.get("MMSI", ""))
        # 1.5: Filter non-vessel MMSIs
        if not mmsi or mmsi == "0" or is_non_vessel_mmsi(mmsi):
            return None

        lat = meta.get("latitude") or report.get("Latitude")
        lon = meta.get("longitude") or report.get("Longitude")
        if lat is None or lon is None:
            return None
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return None

        # 1.3: No fallback to datetime.now() — return None if unparseable
        ts_raw = meta.get("time_utc", "")
        ts = parse_timestamp_flexible(ts_raw)
        if ts is None:
            return None

        # P1.2: Reject future timestamps (5-min tolerance for clock skew)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        ts_naive = ts.replace(tzinfo=None) if ts.tzinfo is not None else ts
        if ts_naive > now_utc + timedelta(minutes=5):
            return None

        # 1.1: SOG sentinel 102.3 (raw 1023 = "not available")
        sog = report.get("Sog")
        if sog is not None and sog >= 102.2:
            sog = None

        # 1.1: COG sentinel 360.0 (raw 3600 = "not available")
        cog = report.get("Cog")
        if cog is not None and cog >= 360.0:
            cog = None

        # Heading 511 already filtered in original code
        heading = report.get("TrueHeading")
        if heading is not None and heading == 511:
            heading = None

        return {
            "mmsi": mmsi,
            "vessel_name": meta.get("ShipName", "").strip() or None,
            "timestamp": ts.isoformat(),
            "lat": lat,
            "lon": lon,
            "sog": sog,
            "cog": cog,
            "heading": heading,
            "nav_status": report.get("NavigationalStatus"),
            "source": "aisstream",
            "ais_class": "B" if msg_type == "StandardClassBPositionReport" else "A",
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
        # 1.5: Filter non-vessel MMSIs
        if not mmsi or mmsi == "0" or is_non_vessel_mmsi(mmsi):
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
    from app.modules.ingest import _parse_timestamp, _track_field_change

    stored = 0
    vessels_updated = 0

    # Apply static data updates (vessel metadata from ShipStaticData messages)
    for mmsi, sdata in static_updates.items():
        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
        if not vessel:
            # Create vessel from static data even without a position report —
            # enables watchlist matching for vessels seen in the streaming window.
            from app.utils.vessel_identity import mmsi_to_flag, flag_to_risk_category
            derived_flag = mmsi_to_flag(mmsi)
            vessel = Vessel(
                mmsi=mmsi,
                name=sdata.get("vessel_name"),
                imo=sdata.get("imo"),
                vessel_type=sdata.get("vessel_type"),
                callsign=sdata.get("callsign"),
                flag=derived_flag,
                flag_risk_category=flag_to_risk_category(derived_flag),
                ais_class="A",
                ais_source="aisstream",
                mmsi_first_seen_utc=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            try:
                with db.begin_nested():
                    db.add(vessel)
                    db.flush()
            except IntegrityError:
                vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                if not vessel:
                    continue
            vessels_updated += 1
            continue
        if vessel:
            changed = False
            ts = datetime.now(timezone.utc).replace(tzinfo=None)

            # IMO: only fill if empty (IMO is permanent — changes indicate data error)
            if sdata.get("imo") and not vessel.imo:
                vessel.imo = sdata["imo"]
                changed = True

            # vessel_type: compare-and-track
            if sdata.get("vessel_type") and sdata["vessel_type"] != vessel.vessel_type:
                if vessel.vessel_type:
                    _track_field_change(db, vessel, "vessel_type", vessel.vessel_type, sdata["vessel_type"], ts, "aisstream")
                vessel.vessel_type = sdata["vessel_type"]
                changed = True

            # callsign: compare-and-track
            if sdata.get("callsign") and sdata["callsign"] != vessel.callsign:
                if vessel.callsign:
                    _track_field_change(db, vessel, "callsign", vessel.callsign, sdata["callsign"], ts, "aisstream")
                vessel.callsign = sdata["callsign"]
                changed = True

            # vessel name: compare-and-track
            if sdata.get("vessel_name") and sdata["vessel_name"] != vessel.name:
                if vessel.name:
                    _track_field_change(db, vessel, "name", vessel.name, sdata["vessel_name"], ts, "aisstream")
                vessel.name = sdata["vessel_name"]
                changed = True

            if changed:
                vessels_updated += 1

    # Ingest position reports
    for pt in points:
        mmsi = str(pt["mmsi"])
        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
        if not vessel:
            ts = _parse_timestamp(pt)
            if ts is None:
                continue
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
            try:
                with db.begin_nested():
                    db.add(vessel)
                    db.flush()
            except IntegrityError:
                vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                if not vessel:
                    continue  # Should not happen, but skip if it does

        ts = _parse_timestamp(pt)
        if ts is None:
            continue

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
            ais_class=pt.get("ais_class", "A"),
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
        "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport", "ShipStaticData"],
    }

    stats: dict[str, Any] = {
        "messages_received": 0,
        "position_reports": 0,
        "static_data_msgs": 0,
        "points_stored": 0,
        "vessels_seen": set(),
        "static_vessels": set(),
        "vessels_updated": 0,
        "batches": 0,
        "batch_errors": 0,
        "errors": 0,
        "duration_seconds": duration_seconds,
    }

    point_buffer: list[dict] = []
    static_buffer: dict[str, dict] = {}  # mmsi -> latest static data
    last_batch_time = time.monotonic()
    start_time = time.monotonic()

    # P1.1: Retry loop with exponential backoff for WebSocket reconnection
    retry_delays = [5, 15, 30]  # seconds
    retry_count = 0
    connection_broken = False

    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                # Reset retry count on successful connection
                retry_count = 0

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

                    if msg_type in ("PositionReport", "StandardClassBPositionReport"):
                        pt = _map_position_report(msg, msg_type=msg_type)
                        if pt:
                            stats["position_reports"] += 1
                            stats["vessels_seen"].add(pt["mmsi"])
                            point_buffer.append(pt)
                    elif msg_type == "ShipStaticData":
                        sd = _map_static_data(msg)
                        if sd:
                            stats["static_data_msgs"] += 1
                            stats["static_vessels"].add(sd["mmsi"])
                            static_buffer[sd["mmsi"]] = sd

                    # Batch insert at interval
                    now = time.monotonic()
                    if now - last_batch_time >= batch_interval and (point_buffer or static_buffer):
                        db = db_factory()
                        try:
                            result = _ingest_batch(db, point_buffer, static_buffer)
                            stats["points_stored"] += result["points_stored"]
                            stats["vessels_updated"] += result["vessels_updated"]
                            stats["batches"] += 1
                            point_buffer.clear()
                            static_buffer.clear()
                        except Exception as exc:
                            logger.error("Batch ingestion error: %s", exc)
                            db.rollback()
                            stats["batch_errors"] = stats.get("batch_errors", 0) + 1
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

                        last_batch_time = now

                # Normal exit (duration timeout or stream ended) — final batch
                if point_buffer or static_buffer:
                    db = db_factory()
                    try:
                        result = _ingest_batch(db, point_buffer, static_buffer)
                        stats["points_stored"] += result["points_stored"]
                        stats["vessels_updated"] += result["vessels_updated"]
                        stats["batches"] += 1
                        point_buffer.clear()
                        static_buffer.clear()
                    except Exception as exc:
                        logger.error("Final batch ingestion error: %s", exc)
                        db.rollback()
                        stats["batch_errors"] = stats.get("batch_errors", 0) + 1
                    finally:
                        db.close()

                # Normal completion — exit retry loop
                break

        except (websockets.ConnectionClosed, websockets.WebSocketException, OSError) as exc:
            # Check if time budget is exhausted
            elapsed = time.monotonic() - start_time
            if duration_seconds > 0 and elapsed >= duration_seconds:
                logger.warning("Connection lost after duration expired: %s", exc)
                stats["incomplete"] = True
                break

            if retry_count < len(retry_delays):
                delay = retry_delays[retry_count]
                retry_count += 1
                logger.warning(
                    "aisstream.io connection lost (%s), reconnecting in %ds (attempt %d/%d)",
                    exc, delay, retry_count, len(retry_delays),
                )
                await asyncio.sleep(delay)
                # Continue to retry — buffers are preserved across reconnections
            else:
                logger.error(
                    "aisstream.io connection lost after %d retries: %s", len(retry_delays), exc
                )
                stats["incomplete"] = True
                stats["error"] = str(exc)
                # Attempt to flush remaining buffer before giving up
                if point_buffer or static_buffer:
                    db = db_factory()
                    try:
                        result = _ingest_batch(db, point_buffer, static_buffer)
                        stats["points_stored"] += result["points_stored"]
                        stats["vessels_updated"] += result["vessels_updated"]
                        stats["batches"] += 1
                        point_buffer.clear()
                        static_buffer.clear()
                    except Exception as flush_exc:
                        logger.error("Buffer flush after retries exhausted failed: %s", flush_exc)
                        db.rollback()
                        stats["batch_errors"] = stats.get("batch_errors", 0) + 1
                    finally:
                        db.close()
                break

        except Exception as exc:
            logger.error("aisstream.io unexpected error: %s", exc)
            stats["error"] = str(exc)
            stats["incomplete"] = True
            break

    # Convert sets to counts for JSON serialization
    stats["vessels_seen"] = len(stats["vessels_seen"])
    stats["static_vessels"] = len(stats["static_vessels"])
    stats["actual_duration_s"] = round(time.monotonic() - start_time, 1)

    logger.info(
        "aisstream.io session complete: %d msgs, %d points stored, %d vessels",
        stats["messages_received"],
        stats["points_stored"],
        stats["vessels_seen"],
    )
    return stats
