"""Copernicus CDSE client — Sentinel-1 SAR image catalog queries.

Queries the Copernicus Data Space Ecosystem (CDSE) catalog to find
Sentinel-1 SAR scenes covering a given area and time window. Enhances
existing satellite_query.py by checking actual scene availability.

Catalog API: https://catalogue.dataspace.copernicus.eu/odata/v1/Products
Auth: OAuth2 via https://identity.dataspace.copernicus.eu/
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

_CATALOG_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_TIMEOUT = 30.0


def _get_access_token(
    client_id: str | None = None,
    client_secret: str | None = None,
) -> str:
    """Obtain an OAuth2 access token from Copernicus CDSE.

    Uses client_credentials grant type.
    """
    cid = client_id or settings.COPERNICUS_CLIENT_ID
    csecret = client_secret or settings.COPERNICUS_CLIENT_SECRET
    if not cid or not csecret:
        raise ValueError(
            "COPERNICUS_CLIENT_ID and COPERNICUS_CLIENT_SECRET must be set. "
            "Register at https://dataspace.copernicus.eu/"
        )

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": cid,
                "client_secret": csecret,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


def find_sentinel1_scenes(
    bbox: tuple[float, float, float, float],
    date_from: str,
    date_to: str,
    token: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    """Query Copernicus catalog for Sentinel-1 SAR scenes in a bounding box.

    Args:
        bbox: (lat_min, lon_min, lat_max, lon_max).
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        token: OAuth2 access token. If None, will obtain one automatically.
        max_results: Maximum number of scenes to return.

    Returns list of scene dicts with id, name, acquisition_time, footprint, preview_url.
    """
    if token is None:
        token = _get_access_token()

    lat_min, lon_min, lat_max, lon_max = bbox

    # OData filter for Sentinel-1 IW mode, GRD product type
    wkt_polygon = (
        f"POLYGON(({lon_min} {lat_min},{lon_max} {lat_min},"
        f"{lon_max} {lat_max},{lon_min} {lat_max},{lon_min} {lat_min}))"
    )

    odata_filter = (
        f"Collection/Name eq 'SENTINEL-1' "
        f"and Attributes/OData.CSC.StringAttribute/any(att:att/Name eq 'productType' "
        f"and att/OData.CSC.StringAttribute/Value eq 'IW_GRDH_1S') "
        f"and ContentDate/Start ge {date_from}T00:00:00.000Z "
        f"and ContentDate/Start le {date_to}T23:59:59.999Z "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{wkt_polygon}')"
    )

    params: dict[str, Any] = {
        "$filter": odata_filter,
        "$top": max_results,
        "$orderby": "ContentDate/Start desc",
        "$expand": "Attributes",
    }

    scenes = []
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(
                _CATALOG_URL,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            for product in data.get("value", []):
                scene_id = product.get("Id", "")
                name = product.get("Name", "")
                content_date = product.get("ContentDate", {})
                start_time = content_date.get("Start", "")

                # Build quicklook preview URL
                preview_url = (
                    f"https://catalogue.dataspace.copernicus.eu"
                    f"/odata/v1/Products({scene_id})/Nodes({name})/Nodes(preview)"
                    f"/Nodes(quick-look.png)/$value"
                )

                scenes.append({
                    "scene_id": scene_id,
                    "name": name,
                    "acquisition_time": start_time,
                    "footprint": product.get("GeoFootprint", {}).get("coordinates"),
                    "preview_url": preview_url,
                    "download_url": f"https://zipper.dataspace.copernicus.eu/odata/v1/Products({scene_id})/$value",
                    "size_mb": round(product.get("ContentLength", 0) / 1_048_576, 1),
                })

    except httpx.HTTPStatusError as exc:
        logger.error("Copernicus catalog query failed: HTTP %d", exc.response.status_code)
        raise
    except Exception as exc:
        logger.error("Copernicus catalog error: %s", exc)
        raise

    logger.info("Copernicus: found %d Sentinel-1 scenes for bbox %s", len(scenes), bbox)
    return scenes


def enhance_satellite_check(
    alert_id: int,
    db: Session,
    token: str | None = None,
) -> dict[str, Any]:
    """Enhance an existing satellite check with actual Sentinel-1 scene availability.

    Queries the Copernicus catalog for scenes covering the gap event's bounding box
    and time window, then updates the SatelliteCheck record.

    Returns dict with scene count and scene details.
    """
    from app.models.gap_event import AISGapEvent
    from app.models.satellite_check import SatelliteCheck
    from app.modules.satellite_query import compute_bounding_box, _get_gap_center

    gap = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not gap:
        return {"error": "Alert not found"}

    # Time window: gap_start - 1h to gap_end + 1h
    time_from = gap.gap_start_utc - timedelta(hours=1)
    time_to = gap.gap_end_utc + timedelta(hours=1)

    center_lat, center_lon = _get_gap_center(gap, db)
    radius_nm = gap.max_plausible_distance_nm or 50.0
    bbox_dict = compute_bounding_box(center_lat, center_lon, radius_nm)
    bbox = (bbox_dict["min_lat"], bbox_dict["min_lon"], bbox_dict["max_lat"], bbox_dict["max_lon"])

    try:
        scenes = find_sentinel1_scenes(
            bbox,
            time_from.strftime("%Y-%m-%d"),
            time_to.strftime("%Y-%m-%d"),
            token=token,
        )
    except Exception as exc:
        return {"error": f"Copernicus query failed: {exc}", "scenes": []}

    # Update or create satellite check record
    sat_check = db.query(SatelliteCheck).filter(SatelliteCheck.gap_event_id == alert_id).first()
    if sat_check and scenes:
        sat_check.review_status = "candidate_scenes_found"
    elif not sat_check:
        sat_check = SatelliteCheck(
            gap_event_id=alert_id,
            provider="Sentinel-1",
            query_time_window=f"{time_from.isoformat()}/{time_to.isoformat()}",
            review_status="candidate_scenes_found" if scenes else "not_checked",
        )
        db.add(sat_check)

    db.commit()

    return {
        "alert_id": alert_id,
        "scenes_found": len(scenes),
        "scenes": scenes,
        "bounding_box": bbox_dict,
        "time_window": {
            "from": time_from.isoformat(),
            "to": time_to.isoformat(),
        },
    }
