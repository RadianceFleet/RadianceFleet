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

    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, params=params, headers=_headers(token))
        resp.raise_for_status()

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

    all_events = []
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
        while True:
            resp = client.get(url, params=params, headers=_headers(token))
            resp.raise_for_status()
            data = resp.json()
            entries = data.get("entries", [])
            for ev in entries:
                pos = ev.get("position", {})
                all_events.append({
                    "event_id": ev.get("id"),
                    "type": ev.get("type"),
                    "start": ev.get("start"),
                    "end": ev.get("end"),
                    "lat": pos.get("lat"),
                    "lon": pos.get("lon"),
                    "vessel_id": vessel_id,
                    "regions": ev.get("regions", {}),
                    "distances": ev.get("distances", {}),
                })
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

    detections: list[dict] = []
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(
                url,
                headers=_headers(token),
                json=geometry,
                params=params,
            )
            resp.raise_for_status()
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

        db.add(DarkVesselDetection(
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
        ))

        if matched_vessel_id:
            stats["matched"] += 1
        else:
            stats["dark"] += 1

    db.commit()
    logger.info("GFW SAR import: %s", stats)
    return stats
