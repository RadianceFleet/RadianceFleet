"""Global Fishing Watch API client — vessel events and SAR detections.

Provides access to:
  - Vessel search (by name or MMSI)
  - Vessel events (encounters, loitering, port visits)
  - SAR vessel detections (dark vessel candidates)

API docs: https://globalfishingwatch.org/our-apis/documentation
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.utils.geo import haversine_nm

logger = logging.getLogger(__name__)

_TIMEOUT = 60.0
_VESSEL_SEARCH_DATASET = "public-global-vessel-identity:latest"
_FISHING_EVENTS_DATASET = "public-global-fishing-events:latest"
_ENCOUNTER_EVENTS_DATASET = "public-global-encounters-events:latest"
_LOITERING_EVENTS_DATASET = "public-global-loitering-events-carriers:latest"
_PORT_VISITS_DATASET = "public-global-port-visits-c2-events:latest"
_GAP_EVENTS_DATASET = "public-global-gaps-events:latest"

AIS_MATCH_RADIUS_NM = 2.0
AIS_MATCH_WINDOW_H = 3


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def search_vessel(
    query: str,
    token: str | None = None,
) -> list[dict]:
    """Search GFW vessel registry by name or MMSI.

    Returns list of vessel dicts with id, name, mmsi, imo, flag, etc.
    """
    token = token or settings.GFW_API_TOKEN
    if not token:
        raise ValueError("GFW_API_TOKEN not configured")

    base = settings.GFW_API_BASE_URL.rstrip("/")
    url = f"{base}/v3/vessels/search"
    params = {
        "query": query,
        "datasets[0]": _VESSEL_SEARCH_DATASET,
        "limit": 20,
    }

    from app.utils.http_retry import retry_request

    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = retry_request(client.get, url, params=params, headers=_headers(token))

    data = resp.json()
    entries = data.get("entries", [])

    results = []
    for entry in entries:
        # GFW returns nested identity info under registryInfo/combinedSourcesInfo
        combined = entry.get("combinedSourcesInfo", [{}])
        info = combined[0] if combined else {}
        ship_name_list = info.get("shipsData", [{}])
        ship = ship_name_list[0] if ship_name_list else {}

        results.append({
            "gfw_id": entry.get("id"),
            "name": ship.get("shipname"),
            "mmsi": entry.get("ssvid"),
            "imo": ship.get("imo"),
            "flag": ship.get("flag"),
            "vessel_type": ship.get("vesselType") or ship.get("geartype"),
            "length_m": ship.get("lengthM"),
            "tonnage_gt": ship.get("tonnageGt"),
            "year_built": ship.get("builtYear"),
        })
    return results


def get_vessel_events(
    vessel_id: str,
    token: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    event_types: list[str] | None = None,
) -> list[dict]:
    """Fetch events for a GFW vessel ID.

    Args:
        vessel_id: GFW vessel identifier (from search_vessel).
        token: GFW API bearer token.
        start_date: ISO date string (YYYY-MM-DD).
        end_date: ISO date string (YYYY-MM-DD).
        event_types: List of event types to fetch. Default: encounters, loitering, port_visits.

    Returns list of event dicts.
    """
    token = token or settings.GFW_API_TOKEN
    if not token:
        raise ValueError("GFW_API_TOKEN not configured")

    if event_types is None:
        event_types = ["encounter", "loitering", "port_visit"]

    datasets = []
    for et in event_types:
        if et == "encounter":
            datasets.append(_ENCOUNTER_EVENTS_DATASET)
        elif et == "loitering":
            datasets.append(_LOITERING_EVENTS_DATASET)
        elif et == "port_visit":
            datasets.append(_PORT_VISITS_DATASET)
        elif et == "fishing":
            datasets.append(_FISHING_EVENTS_DATASET)
        elif et == "gap":
            datasets.append(_GAP_EVENTS_DATASET)

    base = settings.GFW_API_BASE_URL.rstrip("/")
    url = f"{base}/v3/events"
    params: dict[str, Any] = {
        "vessels[0]": vessel_id,
        "limit": 100,
        "offset": 0,
    }
    for i, ds in enumerate(datasets):
        params[f"datasets[{i}]"] = ds
    if start_date:
        params["start-date"] = start_date
    if end_date:
        params["end-date"] = end_date

    from app.utils.http_retry import retry_request

    all_events = []
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        while True:
            resp = retry_request(client.get, url, params=params, headers=_headers(token))
            data = resp.json()
            entries = data.get("entries", [])
            for ev in entries:
                pos = ev.get("position", {})
                event_dict: dict[str, Any] = {
                    "event_id": ev.get("id"),
                    "type": ev.get("type"),
                    "start": ev.get("start"),
                    "end": ev.get("end"),
                    "lat": pos.get("lat"),
                    "lon": pos.get("lon"),
                    "vessel_id": vessel_id,
                    "regions": ev.get("regions", {}),
                    "distances": ev.get("distances", {}),
                }
                # Enrich gap events with off/on positions + metrics
                gap_info = ev.get("gap") or ev.get("gapInfo") or {}
                if gap_info:
                    off_pos = gap_info.get("offPosition") or {}
                    on_pos = gap_info.get("onPosition") or {}
                    event_dict["gap_off_lat"] = off_pos.get("lat")
                    event_dict["gap_off_lon"] = off_pos.get("lon")
                    event_dict["gap_on_lat"] = on_pos.get("lat")
                    event_dict["gap_on_lon"] = on_pos.get("lon")
                    event_dict["duration_hours"] = gap_info.get("durationHours")
                    event_dict["distance_km"] = gap_info.get("distanceKm")
                    event_dict["implied_speed_knots"] = gap_info.get("impliedSpeedKnots")
                # Also extract vessel MMSI (ssvid) from the response
                vessel_info = ev.get("vessel") or {}
                event_dict["ssvid"] = vessel_info.get("ssvid")
                all_events.append(event_dict)
            # Paginate
            if len(entries) < params.get("limit", 100):
                break
            params["offset"] = params.get("offset", 0) + len(entries)

    logger.info("GFW: fetched %d events for vessel %s", len(all_events), vessel_id)
    return all_events


def get_sar_detections(
    bbox: tuple[float, float, float, float],
    token: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Fetch SAR vessel detections from GFW 4Wings API for a bounding box.

    Uses the 4Wings API (``/v3/4wings/report``) with the SAR vessel presence
    dataset.  The 4Wings endpoint returns gridded spatiotemporal data — each
    record represents a radar detection in a lat/lon cell at a given time.

    Args:
        bbox: (lat_min, lon_min, lat_max, lon_max).
        token: GFW API bearer token.
        start_date: ISO date string (YYYY-MM-DD).
        end_date: ISO date string (YYYY-MM-DD).

    Returns list of detection dicts compatible with import_sar_detections_to_db().
    """
    token = token or settings.GFW_API_TOKEN
    if not token:
        raise ValueError("GFW_API_TOKEN not configured")

    if not start_date:
        start_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.utcnow().strftime("%Y-%m-%d")

    base = settings.GFW_API_BASE_URL.rstrip("/")
    lat_min, lon_min, lat_max, lon_max = bbox

    url = f"{base}/v3/4wings/report"
    params: dict[str, Any] = {
        "datasets[0]": "public-global-sar-presence:latest",
        "spatial-resolution": "LOW",  # 0.1° grid cells
        "temporal-resolution": "DAILY",
        "date-range": f"{start_date},{end_date}",
        "format": "JSON",
    }

    # POST with GeoJSON body for area filtering
    geometry = {
        "geojson": {
            "type": "Polygon",
            "coordinates": [[
                [lon_min, lat_min],
                [lon_max, lat_min],
                [lon_max, lat_max],
                [lon_min, lat_max],
                [lon_min, lat_min],
            ]],
        }
    }

    from app.utils.http_retry import retry_request

    detections: list[dict] = []
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = retry_request(
                client.post,
                url,
                headers=_headers(token),
                json=geometry,
                params=params,
            )
            data = resp.json()

        # 4Wings response: list of temporal groups, each containing grid cells.
        # Multiple response shapes observed — handle both flat and nested.
        entries = data if isinstance(data, list) else data.get("entries", [])
        for group in entries:
            if isinstance(group, dict):
                _parse_4wings_group(group, detections)
            elif isinstance(group, list):
                for item in group:
                    if isinstance(item, dict):
                        _parse_4wings_group(item, detections)

    except httpx.HTTPStatusError as exc:
        logger.error("GFW 4Wings SAR request failed: HTTP %d", exc.response.status_code)
        raise
    except Exception as exc:
        logger.error("GFW 4Wings SAR error: %s", exc)
        raise

    logger.info("GFW: fetched %d SAR detections for bbox %s", len(detections), bbox)
    return detections


def _parse_4wings_group(group: dict, detections: list[dict]) -> None:
    """Parse a single 4Wings response group/entry into detection dicts."""
    lat = group.get("lat") or group.get("latitude")
    lon = group.get("lon") or group.get("longitude")
    date_str = group.get("date") or group.get("timestamp") or group.get("startDate")
    count = group.get("detections") or group.get("hours") or group.get("value") or 1

    if lat is not None and lon is not None and date_str:
        scene_id = f"gfw-sar-{date_str}-{float(lat):.4f}-{float(lon):.4f}"
        detections.append({
            "scene_id": scene_id,
            "detection_lat": float(lat),
            "detection_lon": float(lon),
            "detection_time_utc": str(date_str),
            "length_estimate_m": group.get("lengthM"),
            "model_confidence": None,
            "vessel_type_inferred": group.get("vesselType") or "unknown",
        })
        # If the cell reports multiple detections, the count is informational —
        # we create one record per cell/day since we can't disambiguate.
        if isinstance(count, (int, float)) and count > 1:
            logger.debug("4Wings cell %s/%s has %d detections", lat, lon, count)

    # Some responses nest sub-entries under "timeseries" or "data"
    for sub_key in ("timeseries", "data", "rows"):
        sub = group.get(sub_key)
        if isinstance(sub, list):
            for item in sub:
                if isinstance(item, dict):
                    _parse_4wings_group(item, detections)


def query_sar_detections(
    lat: float, lon: float, radius_nm: float,
    date_from: str, date_to: str,
    token: str | None = None,
) -> list[dict]:
    """Query GFW 4Wings API for SAR vessel detections near a point.

    Convenience wrapper around get_sar_detections() that builds a bbox from
    lat/lon and radius. Returns detections with lat, lon, timestamp,
    estimated_length_m.

    Used for merge candidate satellite corroboration — confirms whether a
    dark vessel was present at a location during a gap.
    """
    # Convert radius_nm to approximate degrees
    deg = radius_nm / 60.0
    bbox = (lat - deg, lon - deg, lat + deg, lon + deg)
    return get_sar_detections(bbox, token=token, start_date=date_from, end_date=date_to)


def corroborate_merge_candidate(
    candidate: "MergeCandidate",
    token: str | None = None,
) -> dict:
    """Check GFW SAR for dark vessel presence near both ends of a merge gap.

    Returns corroboration result dict suitable for MergeCandidate.satellite_corroboration_json.
    """
    from app.models.merge_candidate import MergeCandidate  # noqa: F811

    result: dict[str, Any] = {"vessel_a_location": {}, "vessel_b_location": {}, "corroboration_score": 0}
    score = 0

    for label, lat, lon, ts in [
        ("vessel_a_location", candidate.vessel_a_last_lat, candidate.vessel_a_last_lon, candidate.vessel_a_last_time),
        ("vessel_b_location", candidate.vessel_b_first_lat, candidate.vessel_b_first_lon, candidate.vessel_b_first_time),
    ]:
        if lat is None or lon is None or ts is None:
            result[label] = {"sar_detections": 0, "dark_detections": 0, "length_match": False}
            continue

        date_from = (ts - timedelta(hours=24)).strftime("%Y-%m-%d")
        date_to = (ts + timedelta(hours=24)).strftime("%Y-%m-%d")

        try:
            detections = query_sar_detections(lat, lon, 10.0, date_from, date_to, token=token)
        except Exception as exc:
            logger.warning("SAR query failed for %s: %s", label, exc)
            result[label] = {"sar_detections": 0, "dark_detections": 0, "error": str(exc)}
            continue

        # Filter unmatched (dark) detections near the position
        dark_count = 0
        total_count = len(detections)
        for det in detections:
            d_lat = det.get("detection_lat")
            d_lon = det.get("detection_lon")
            if d_lat is not None and d_lon is not None:
                dist = haversine_nm(lat, lon, d_lat, d_lon)
                if dist <= 10.0:
                    dark_count += 1

        result[label] = {
            "sar_detections": total_count,
            "dark_detections": dark_count,
            "length_match": dark_count > 0,
        }
        if dark_count > 0:
            score += 8  # each location contributes up to 8 points

    result["corroboration_score"] = min(15, score)
    return result


def import_sar_detections_to_db(
    detections: list[dict],
    db: Session,
) -> dict[str, int]:
    """Import SAR detections into DarkVesselDetection table, matching against AIS.

    Reuses AIS matching logic from gfw_import.py pattern.
    Returns {"total", "matched", "dark", "rejected"}.
    """
    from app.models.stubs import DarkVesselDetection
    from app.models.ais_point import AISPoint

    stats: dict[str, int] = {"total": 0, "matched": 0, "dark": 0, "rejected": 0}

    for det in detections:
        stats["total"] += 1

        lat = det.get("detection_lat")
        lon = det.get("detection_lon")
        if lat is None or lon is None:
            stats["rejected"] += 1
            continue

        ts_raw = det.get("detection_time_utc")
        if isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                stats["rejected"] += 1
                continue
        elif isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            stats["rejected"] += 1
            continue

        # Make tz-naive for DB comparison
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)

        # AIS match: find nearby vessel within time window
        window_start = ts - timedelta(hours=AIS_MATCH_WINDOW_H)
        window_end = ts + timedelta(hours=AIS_MATCH_WINDOW_H)

        candidates = (
            db.query(AISPoint)
            .filter(
                AISPoint.timestamp_utc >= window_start,
                AISPoint.timestamp_utc <= window_end,
            )
            .all()
        )

        matched_vessel_id = None
        for pt in candidates:
            if haversine_nm(lat, lon, pt.lat, pt.lon) <= AIS_MATCH_RADIUS_NM:
                matched_vessel_id = pt.vessel_id
                break

        # Dedup: skip if we already have a detection with the same ID
        # (but only when scene_id is non-empty — blank IDs are not reliable for dedup)
        scene_id = det.get("scene_id", "")
        if scene_id:
            existing = db.query(DarkVesselDetection).filter(
                DarkVesselDetection.scene_id == scene_id,
            ).first()
            if existing:
                continue

        detection = DarkVesselDetection(
            scene_id=det.get("scene_id", ""),
            detection_lat=lat,
            detection_lon=lon,
            detection_time_utc=ts,
            length_estimate_m=det.get("length_estimate_m"),
            vessel_type_inferred=det.get("vessel_type_inferred", "unknown"),
            ais_match_attempted=True,
            ais_match_result="matched" if matched_vessel_id else "unmatched",
            matched_vessel_id=matched_vessel_id,
            model_confidence=det.get("model_confidence"),
        )
        if det.get("corridor_id"):
            detection.corridor_id = det["corridor_id"]
        db.add(detection)

        if matched_vessel_id:
            stats["matched"] += 1
        else:
            stats["dark"] += 1

    db.commit()
    logger.info("GFW SAR import: %s", stats)
    return stats


def import_gfw_gap_events(
    db: Session,
    start_date: str,
    end_date: str,
    limit: int | None = None,
    resume_from_vessel_id: int | None = None,
    token: str | None = None,
) -> dict:
    """Import GFW intentional AIS-disabling gap events for vessels in the DB.

    Iterates vessels with MMSI, resolves to GFW vessel ID, pulls gap events,
    and creates AISGapEvent records with dedup.

    Args:
        db: SQLAlchemy session.
        start_date: ISO date (YYYY-MM-DD).
        end_date: ISO date (YYYY-MM-DD).
        limit: Max vessels to query (None = all).
        resume_from_vessel_id: Skip vessels with id <= this (checkpoint resume).
        token: GFW API token override.

    Returns dict with import statistics.
    """
    import time
    from app.models.vessel import Vessel
    from app.models.gap_event import AISGapEvent

    token = token or settings.GFW_API_TOKEN
    if not token:
        raise ValueError("GFW_API_TOKEN not configured")

    stats: dict[str, Any] = {
        "vessels_queried": 0,
        "vessels_skipped_no_mmsi": 0,
        "vessels_skipped_no_gfw_id": 0,
        "total_events": 0,
        "imported": 0,
        "skipped_dup": 0,
        "in_corridor": 0,
        "partial": False,
        "last_vessel_id": None,
        "errors": [],
    }

    q = db.query(Vessel).filter(
        Vessel.mmsi.isnot(None),
        Vessel.merged_into_vessel_id.is_(None),
    ).order_by(Vessel.vessel_id)

    if resume_from_vessel_id:
        q = q.filter(Vessel.vessel_id > resume_from_vessel_id)

    vessels = q.all()
    if limit:
        vessels = vessels[:limit]

    consecutive_failures = 0
    batch_count = 0

    for vessel in vessels:
        if not vessel.mmsi or len(vessel.mmsi) != 9:
            stats["vessels_skipped_no_mmsi"] += 1
            continue

        # Resolve MMSI to GFW vessel ID
        try:
            results = search_vessel(vessel.mmsi, token=token)
            time.sleep(0.5)  # Rate-limit between API calls
        except Exception as exc:
            consecutive_failures += 1
            logger.warning("GFW search failed for MMSI %s: %s", vessel.mmsi, exc)
            stats["errors"].append(f"search {vessel.mmsi}: {exc}")
            if consecutive_failures >= 3:
                logger.warning("3 consecutive GFW failures — stopping with partial results")
                stats["partial"] = True
                break
            continue

        consecutive_failures = 0

        if not results:
            stats["vessels_skipped_no_gfw_id"] += 1
            continue

        gfw_id = results[0].get("gfw_id")
        if not gfw_id:
            stats["vessels_skipped_no_gfw_id"] += 1
            continue

        # Fetch gap events for this vessel
        try:
            events = get_vessel_events(
                gfw_id, token=token,
                start_date=start_date, end_date=end_date,
                event_types=["gap"],
            )
            time.sleep(0.5)
        except Exception as exc:
            consecutive_failures += 1
            logger.warning("GFW gap events failed for %s: %s", gfw_id, exc)
            stats["errors"].append(f"events {gfw_id}: {exc}")
            if consecutive_failures >= 3:
                stats["partial"] = True
                break
            continue

        consecutive_failures = 0
        stats["vessels_queried"] += 1
        stats["total_events"] += len(events)

        for ev in events:
            gap_start_str = ev.get("start")
            gap_end_str = ev.get("end")
            if not gap_start_str or not gap_end_str:
                continue

            try:
                gap_start = datetime.fromisoformat(gap_start_str.replace("Z", "+00:00"))
                gap_end = datetime.fromisoformat(gap_end_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue

            # Make tz-naive for DB
            if gap_start.tzinfo:
                gap_start = gap_start.replace(tzinfo=None)
            if gap_end.tzinfo:
                gap_end = gap_end.replace(tzinfo=None)

            # Dedup: same vessel + gap_start within ±10 min
            dedup_window = timedelta(minutes=10)
            existing = db.query(AISGapEvent).filter(
                AISGapEvent.vessel_id == vessel.vessel_id,
                AISGapEvent.gap_start_utc >= gap_start - dedup_window,
                AISGapEvent.gap_start_utc <= gap_start + dedup_window,
            ).first()
            if existing:
                stats["skipped_dup"] += 1
                continue

            duration_min = int((gap_end - gap_start).total_seconds() / 60)
            off_lat = ev.get("gap_off_lat")
            off_lon = ev.get("gap_off_lon")
            on_lat = ev.get("gap_on_lat")
            on_lon = ev.get("gap_on_lon")

            # Compute actual distance if both positions available
            actual_dist_nm = None
            if off_lat is not None and off_lon is not None and on_lat is not None and on_lon is not None:
                actual_dist_nm = haversine_nm(off_lat, off_lon, on_lat, on_lon)

            # Implied speed from GFW or computed
            implied_speed = ev.get("implied_speed_knots")
            duration_h = duration_min / 60
            max_dist = None
            ratio = None
            if duration_h > 0:
                max_dist = 22.0 * duration_h  # Conservative max speed
                if actual_dist_nm is not None and max_dist > 0:
                    ratio = actual_dist_nm / max_dist

            gap_event = AISGapEvent(
                vessel_id=vessel.vessel_id,
                gap_start_utc=gap_start,
                gap_end_utc=gap_end,
                duration_minutes=duration_min,
                risk_score=0,
                status="new",
                impossible_speed_flag=(ratio is not None and ratio > 1.1),
                velocity_plausibility_ratio=ratio,
                max_plausible_distance_nm=max_dist,
                actual_gap_distance_nm=actual_dist_nm,
                gap_off_lat=off_lat,
                gap_off_lon=off_lon,
                gap_on_lat=on_lat,
                gap_on_lon=on_lon,
                source="gfw",
            )
            db.add(gap_event)
            db.flush()

            # Corridor correlation using off-position
            if off_lat is not None and off_lon is not None:
                try:
                    from app.modules.corridor_correlator import find_corridor_for_point
                    corridor = find_corridor_for_point(db, off_lat, off_lon)
                    if corridor:
                        gap_event.corridor_id = corridor.corridor_id
                        stats["in_corridor"] += 1
                        if corridor.is_jamming_zone:
                            gap_event.in_dark_zone = True
                except (ImportError, Exception) as exc:
                    logger.debug("Corridor correlation skipped: %s", exc)

            stats["imported"] += 1

        # Batch commit every 50 vessels
        batch_count += 1
        if batch_count % 50 == 0:
            db.commit()

        stats["last_vessel_id"] = vessel.vessel_id

    db.commit()
    logger.info("GFW gap import: %s", {k: v for k, v in stats.items() if k != "errors"})
    return stats


def sweep_corridors_sar(
    db: Session,
    start_date: str,
    end_date: str,
    corridor_types: list[str] | None = None,
    token: str | None = None,
) -> dict:
    """Sweep all corridors for SAR detections over a date range.

    Args:
        db: SQLAlchemy session.
        start_date: ISO date (YYYY-MM-DD).
        end_date: ISO date (YYYY-MM-DD).
        corridor_types: Filter corridors by type (e.g. ["export_route", "sts_zone"]).
        token: GFW API token override.

    Returns dict with sweep statistics.
    """
    import time
    from app.models.corridor import Corridor

    token = token or settings.GFW_API_TOKEN
    if not token:
        raise ValueError("GFW_API_TOKEN not configured")

    stats: dict[str, Any] = {
        "corridors_queried": 0,
        "total_detections": 0,
        "dark_vessels": 0,
        "matched": 0,
        "partial": False,
        "errors": [],
    }

    q = db.query(Corridor).filter(Corridor.geometry.isnot(None))
    if corridor_types:
        q = q.filter(Corridor.corridor_type.in_(corridor_types))
    corridors = q.all()

    consecutive_failures = 0

    for corridor in corridors:
        # Extract bbox from corridor geometry WKT
        bbox = _extract_bbox_from_wkt(corridor.geometry)
        if bbox is None:
            continue

        try:
            detections = get_sar_detections(bbox, token=token, start_date=start_date, end_date=end_date)
            time.sleep(1)  # Rate-limit
        except Exception as exc:
            consecutive_failures += 1
            logger.warning("SAR sweep failed for corridor %s: %s", corridor.name, exc)
            stats["errors"].append(f"{corridor.name}: {exc}")
            if consecutive_failures >= 3:
                stats["partial"] = True
                break
            continue

        consecutive_failures = 0
        stats["corridors_queried"] += 1

        if detections:
            # Tag detections with corridor_id before import
            for det in detections:
                det["corridor_id"] = corridor.corridor_id

            result = import_sar_detections_to_db(detections, db)
            stats["total_detections"] += result.get("total", 0)
            stats["dark_vessels"] += result.get("dark", 0)
            stats["matched"] += result.get("matched", 0)

    logger.info("SAR corridor sweep: %s", {k: v for k, v in stats.items() if k != "errors"})
    return stats


def import_gfw_encounters(
    db: Session,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    token: str | None = None,
) -> dict:
    """Import GFW encounter events as StsTransferEvents.

    Fetches encounter events from GFW for all vessels in the database,
    creates StsTransferEvent records with detection_type='gfw_encounter'.

    Uses the same search_vessel → get_vessel_events pattern as import_gfw_gap_events.
    """
    import time
    from app.models.vessel import Vessel
    from app.models.sts_transfer import StsTransferEvent
    from app.models.base import STSDetectionTypeEnum

    token = token or settings.GFW_API_TOKEN
    if not token:
        raise ValueError("GFW_API_TOKEN not configured")

    vessels = db.query(Vessel).filter(
        Vessel.mmsi.isnot(None),
        Vessel.merged_into_vessel_id.is_(None),
    ).limit(limit).all()

    created = 0
    errors = 0

    for vessel in vessels:
        if not vessel.mmsi or len(vessel.mmsi) != 9:
            continue

        try:
            # Resolve MMSI to GFW vessel ID
            results = search_vessel(vessel.mmsi, token=token)
            time.sleep(0.5)

            if not results:
                continue
            gfw_id = results[0].get("gfw_id")
            if not gfw_id:
                continue

            events = get_vessel_events(
                gfw_id,
                token=token,
                event_types=["encounter"],
                start_date=date_from,
                end_date=date_to,
            )
            time.sleep(0.5)

            for event in (events or []):
                # Extract encounter data
                lat = event.get("lat")
                lon = event.get("lon")
                start = event.get("start")
                end = event.get("end")

                if not (lat and lon and start and end):
                    continue

                start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))

                # Make tz-naive for DB
                if start_dt.tzinfo:
                    start_dt = start_dt.replace(tzinfo=None)
                if end_dt.tzinfo:
                    end_dt = end_dt.replace(tzinfo=None)

                duration = int((end_dt - start_dt).total_seconds() / 60)

                # Try to match partner vessel via encounter info
                encounter_info = event.get("encounter", {})
                partner_mmsi = encounter_info.get("vessel", {}).get("ssvid")
                partner_vessel = None
                if partner_mmsi:
                    partner_vessel = db.query(Vessel).filter(
                        Vessel.mmsi == str(partner_mmsi)
                    ).first()

                # Skip if partner not found — we need both vessel IDs
                if partner_vessel is None:
                    continue

                vid1 = min(vessel.vessel_id, partner_vessel.vessel_id)
                vid2 = max(vessel.vessel_id, partner_vessel.vessel_id)

                # Dedup check
                existing = db.query(StsTransferEvent).filter(
                    StsTransferEvent.vessel_1_id == vid1,
                    StsTransferEvent.vessel_2_id == vid2,
                    StsTransferEvent.start_time_utc == start_dt,
                ).first()
                if existing:
                    continue

                sts_event = StsTransferEvent(
                    vessel_1_id=vid1,
                    vessel_2_id=vid2,
                    detection_type=STSDetectionTypeEnum.GFW_ENCOUNTER,
                    start_time_utc=start_dt,
                    end_time_utc=end_dt,
                    duration_minutes=duration,
                    mean_lat=round(float(lat), 6),
                    mean_lon=round(float(lon), 6),
                    risk_score_component=25,
                )
                db.add(sts_event)
                created += 1

        except Exception as e:
            logger.warning("GFW encounter import failed for vessel %s: %s", vessel.mmsi, e)
            errors += 1

    db.commit()
    logger.info("GFW encounter import: created=%d errors=%d", created, errors)
    return {"created": created, "errors": errors}


def import_gfw_port_visits(
    db: Session,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
    token: str | None = None,
) -> dict:
    """Import GFW port visit events as PortCall records.

    Uses the same search_vessel → get_vessel_events pattern as import_gfw_gap_events.
    Port resolution maps GFW coordinates to internal Port records where possible.
    """
    import time
    from app.models.vessel import Vessel
    from app.models.port_call import PortCall
    from app.modules.port_resolver import resolve_port

    token = token or settings.GFW_API_TOKEN
    if not token:
        raise ValueError("GFW_API_TOKEN not configured")

    vessels = db.query(Vessel).filter(
        Vessel.mmsi.isnot(None),
        Vessel.merged_into_vessel_id.is_(None),
    ).limit(limit).all()

    created = 0
    errors = 0

    for vessel in vessels:
        if not vessel.mmsi or len(vessel.mmsi) != 9:
            continue

        try:
            # Resolve MMSI to GFW vessel ID
            results = search_vessel(vessel.mmsi, token=token)
            time.sleep(0.5)

            if not results:
                continue
            gfw_id = results[0].get("gfw_id")
            if not gfw_id:
                continue

            events = get_vessel_events(
                gfw_id,
                token=token,
                event_types=["port_visit"],
                start_date=date_from,
                end_date=date_to,
            )
            time.sleep(0.5)

            for event in (events or []):
                lat = event.get("lat")
                lon = event.get("lon")
                start = event.get("start")
                end = event.get("end")

                if not (lat and lon and start):
                    continue

                start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None

                # Make tz-naive for DB
                if start_dt.tzinfo:
                    start_dt = start_dt.replace(tzinfo=None)
                if end_dt and end_dt.tzinfo:
                    end_dt = end_dt.replace(tzinfo=None)

                # Resolve port
                port_visit_info = event.get("port_visit", {}) or event.get("port", {})
                raw_port_name = port_visit_info.get("name") or event.get("port_name")
                port = resolve_port(db, float(lat), float(lon), port_name=raw_port_name)

                # Dedup
                existing = db.query(PortCall).filter(
                    PortCall.vessel_id == vessel.vessel_id,
                    PortCall.arrival_utc == start_dt,
                ).first()
                if existing:
                    continue

                port_call = PortCall(
                    vessel_id=vessel.vessel_id,
                    port_id=port.port_id if port else None,
                    arrival_utc=start_dt,
                    departure_utc=end_dt,
                    raw_port_name=raw_port_name,
                    source="gfw",
                )
                db.add(port_call)
                created += 1

        except Exception as e:
            logger.warning("GFW port visit import failed for vessel %s: %s", vessel.mmsi, e)
            errors += 1

    db.commit()
    logger.info("GFW port visit import: created=%d errors=%d", created, errors)
    return {"created": created, "errors": errors}


def _extract_bbox_from_wkt(wkt: str | None) -> tuple[float, float, float, float] | None:
    """Extract (lat_min, lon_min, lat_max, lon_max) bounding box from WKT POLYGON."""
    if not wkt:
        return None
    import re
    # Extract all coordinate pairs from WKT
    nums = re.findall(r"(-?[\d.]+)\s+(-?[\d.]+)", wkt)
    if not nums:
        return None
    lons = [float(n[0]) for n in nums]
    lats = [float(n[1]) for n in nums]
    return (min(lats), min(lons), max(lats), max(lons))
