"""Capella Space API client for SAR satellite imagery ordering.

API docs: https://docs.capellaspace.com/
Auth: OAuth2 client credentials (API key exchange for bearer token).
STAC-compliant catalog search.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping as geojson_mapping

from app.config import settings
from app.modules.circuit_breakers import breakers
from app.modules.satellite_providers import register_provider
from app.modules.satellite_providers.base import (
    ArchiveSearchResult,
    OrderStatusResult,
    OrderSubmitResult,
    SatelliteProvider,
)
from app.utils.http_retry import retry_request

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.capellaspace.com"
_TOKEN_URL = f"{_BASE_URL}/token"
_CATALOG_URL = f"{_BASE_URL}/catalog/search"
_ORDERS_URL = f"{_BASE_URL}/orders"
_TIMEOUT = 30.0

# Module-level token cache
_token_cache: dict[str, Any] = {}


def _wkt_to_geojson(wkt_str: str) -> dict:
    """Convert WKT geometry to GeoJSON geometry dict."""
    geom = shapely_wkt.loads(wkt_str)
    return geojson_mapping(geom)


def _get_access_token(
    api_key: str | None = None,
    force_refresh: bool = False,
) -> str:
    """Obtain a bearer token from Capella via API key exchange.

    Capella uses POST /token with the API key in the Authorization header.
    Token is cached until expiry minus a 30s safety margin.
    """
    if not force_refresh and _token_cache.get("token"):
        if _time.monotonic() < _token_cache.get("expires_at", 0):
            return _token_cache["token"]

    key = api_key or settings.CAPELLA_API_KEY
    if not key:
        raise ValueError(
            "CAPELLA_API_KEY must be set. Register at https://www.capellaspace.com/"
        )

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = breakers["capella"].call(
            retry_request,
            client.post,
            _TOKEN_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["accessToken"]
        expires_in = data.get("expiresIn", 3600)
        _token_cache["token"] = token
        _token_cache["expires_at"] = _time.monotonic() + max(0, expires_in - 30)
        return token


class CapellaProvider(SatelliteProvider):
    """Capella Space SAR imagery provider."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.CAPELLA_API_KEY
        if not self._api_key:
            raise ValueError(
                "CAPELLA_API_KEY must be set. Register at https://www.capellaspace.com/"
            )

    @property
    def name(self) -> str:
        return "capella"

    def _headers(self, token: Optional[str] = None) -> dict[str, str]:
        if token is None:
            token = _get_access_token(api_key=self._api_key)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _request_with_retry_on_401(
        self,
        method: str,
        url: str,
        client: httpx.Client,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a request, refreshing the token on 401."""
        token = _get_access_token(api_key=self._api_key)
        kwargs["headers"] = self._headers(token)

        request_fn = getattr(client, method)
        try:
            resp = breakers["capella"].call(
                retry_request,
                request_fn,
                url,
                **kwargs,
            )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                logger.info("Capella 401 -- refreshing token and retrying")
                token = _get_access_token(api_key=self._api_key, force_refresh=True)
                kwargs["headers"] = self._headers(token)
                resp = breakers["capella"].call(
                    retry_request,
                    request_fn,
                    url,
                    **kwargs,
                )
                resp.raise_for_status()
                return resp
            raise

    def search_archive(
        self,
        aoi_wkt: str,
        start: datetime,
        end: datetime,
        cloud_cover_max: float = 30.0,
        limit: int = 10,
    ) -> list[ArchiveSearchResult]:
        """Search Capella STAC catalog for SAR scenes covering the AOI."""
        geojson_geom = _wkt_to_geojson(aoi_wkt)

        stac_body: dict[str, Any] = {
            "bbox": None,
            "intersects": geojson_geom,
            "datetime": f"{start.isoformat()}Z/{end.isoformat()}Z",
            "collections": ["capella-open"],
            "limit": limit,
        }

        results: list[ArchiveSearchResult] = []
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "post", _CATALOG_URL, client, json=stac_body
            )
            data = resp.json()

            for feature in data.get("features", []):
                props = feature.get("properties", {})
                acquired_str = props.get("datetime", "")
                try:
                    acquired_at = datetime.fromisoformat(
                        acquired_str.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    acquired_at = datetime.now(timezone.utc)

                # Convert feature geometry to WKT
                feat_geom = feature.get("geometry")
                geometry_wkt = None
                if feat_geom:
                    from shapely.geometry import shape

                    geometry_wkt = shape(feat_geom).wkt

                # Get thumbnail from assets
                assets = feature.get("assets", {})
                thumbnail_url = None
                if "thumbnail" in assets:
                    thumbnail_url = assets["thumbnail"].get("href")

                results.append(
                    ArchiveSearchResult(
                        scene_id=feature.get("id", ""),
                        provider="capella",
                        acquired_at=acquired_at,
                        resolution_m=props.get("sar:resolution_range"),
                        thumbnail_url=thumbnail_url,
                        geometry_wkt=geometry_wkt,
                        product_type=props.get("sar:product_type", "SLC"),
                        estimated_cost_usd=self.estimated_cost_per_scene(),
                        metadata={
                            "instrument_mode": props.get("sar:instrument_mode"),
                            "polarization": props.get("sar:polarizations"),
                            "orbit_state": props.get("sat:orbit_state"),
                        },
                    )
                )

        logger.info("Capella: found %d SAR scenes for AOI", len(results))
        return results

    def submit_order(
        self, scene_ids: list[str], product_type: str = "analytic"
    ) -> OrderSubmitResult:
        """Submit an order for the given STAC item IDs."""
        order_body: dict[str, Any] = {
            "items": [
                {"collectionId": "capella-open", "itemId": sid, "productType": product_type}
                for sid in scene_ids
            ],
        }

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "post", _ORDERS_URL, client, json=order_body
            )
            data = resp.json()

        return OrderSubmitResult(
            external_order_id=data.get("orderId", ""),
            status=data.get("status", "submitted"),
            estimated_cost_usd=len(scene_ids) * self.estimated_cost_per_scene(product_type),
            message=data.get("statusMessage"),
        )

    def check_order_status(self, external_order_id: str) -> OrderStatusResult:
        """Check status of an existing Capella order."""
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "get", f"{_ORDERS_URL}/{external_order_id}", client
            )
            data = resp.json()

        # Extract download URLs
        scene_urls: list[str] = []
        for item in data.get("items", []):
            for asset in item.get("assets", {}).values():
                if asset.get("href"):
                    scene_urls.append(asset["href"])

        # Map Capella statuses
        capella_status = data.get("status", "unknown")
        status_map = {
            "submitted": "accepted",
            "processing": "processing",
            "completed": "delivered",
            "failed": "failed",
            "cancelled": "cancelled",
        }
        status = status_map.get(capella_status, capella_status)

        return OrderStatusResult(
            external_order_id=external_order_id,
            status=status,
            scene_urls=scene_urls,
            message=data.get("statusMessage"),
            metadata={"capella_status": capella_status},
        )

    def cancel_order(self, external_order_id: str) -> bool:
        """Cancel a pending Capella order."""
        with httpx.Client(timeout=_TIMEOUT) as client:
            try:
                self._request_with_retry_on_401(
                    "post",
                    f"{_ORDERS_URL}/{external_order_id}/cancel",
                    client,
                )
                return True
            except httpx.HTTPStatusError:
                return False

    def estimated_cost_per_scene(self, product_type: str = "analytic") -> float:
        """Estimated cost per SAR scene in USD."""
        pricing = {
            "analytic": 250.0,
            "SLC": 300.0,
            "GEO": 200.0,
        }
        return pricing.get(product_type, 250.0)


register_provider("capella", CapellaProvider)
