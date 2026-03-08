"""Umbra Space SAR satellite imagery provider client.

API docs: https://docs.canopy.umbra.space/
Auth: OAuth2 client credentials (client_id + client_secret exchange for bearer token).
STAC v2 catalog search. SAR imagery — cloud cover is not applicable.

IMPORTANT: Umbra enforces a 50 token requests/24h limit.
Token TTL is 24h; we cache aggressively with a 120s safety margin.
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

_BASE_URL = "https://api.canopy.umbra.space"
_TOKEN_URL = "https://auth.canopy.umbra.space/oauth/token"
_STAC_URL = f"{_BASE_URL}/v2/stac/search"
_TASKING_URL = f"{_BASE_URL}/tasking/tasks"
_TIMEOUT = 30.0

# Module-level token cache — aggressive caching due to 50 req/24h limit
_token_cache: dict[str, Any] = {}


def _wkt_to_geojson(wkt_str: str) -> dict:
    """Convert WKT geometry to GeoJSON geometry dict."""
    geom = shapely_wkt.loads(wkt_str)
    return geojson_mapping(geom)


def _get_access_token(
    client_id: str | None = None,
    client_secret: str | None = None,
    force_refresh: bool = False,
) -> str:
    """Obtain a bearer token from Umbra via OAuth2 client credentials.

    Token is cached until expiry minus a 120s safety margin.
    Umbra enforces a strict 50 token requests / 24h limit,
    so we cache aggressively.
    """
    if not force_refresh and _token_cache.get("token"):
        if _time.monotonic() < _token_cache.get("expires_at", 0):
            return _token_cache["token"]

    cid = client_id or settings.UMBRA_CLIENT_ID
    secret = client_secret or settings.UMBRA_API_KEY
    if not cid or not secret:
        raise ValueError(
            "UMBRA_CLIENT_ID and UMBRA_API_KEY must be set. "
            "Register at https://canopy.umbra.space/"
        )

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = breakers["umbra"].call(
            retry_request,
            client.post,
            _TOKEN_URL,
            json={
                "client_id": cid,
                "client_secret": secret,
                "audience": "https://api.canopy.umbra.space",
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        # Default 24h (86400s); cache with 120s margin
        expires_in = data.get("expires_in", 86400)
        _token_cache["token"] = token
        _token_cache["expires_at"] = _time.monotonic() + max(0, expires_in - 120)
        return token


# Umbra status -> normalized status mapping
_STATUS_MAP = {
    "ACTIVE": "accepted",
    "SUBMITTED": "accepted",
    "SCHEDULED": "processing",
    "COLLECTING": "processing",
    "PROCESSING": "processing",
    "DELIVERED": "delivered",
    "COMPLETED": "delivered",
    "FAILED": "failed",
    "REJECTED": "failed",
    "CANCELLED": "cancelled",
}


class UmbraProvider(SatelliteProvider):
    """Umbra Space SAR imagery provider."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._client_id = client_id or settings.UMBRA_CLIENT_ID
        self._client_secret = client_secret or settings.UMBRA_API_KEY
        if not self._client_id or not self._client_secret:
            raise ValueError(
                "UMBRA_CLIENT_ID and UMBRA_API_KEY must be set. "
                "Register at https://canopy.umbra.space/"
            )

    @property
    def name(self) -> str:
        return "umbra"

    def _headers(self, token: Optional[str] = None) -> dict[str, str]:
        if token is None:
            token = _get_access_token(
                client_id=self._client_id, client_secret=self._client_secret
            )
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _request_with_retry_on_401(
        self,
        method: str,
        url: str,
        client: httpx.Client,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a request, refreshing the token on 401."""
        token = _get_access_token(
            client_id=self._client_id, client_secret=self._client_secret
        )
        kwargs["headers"] = self._headers(token)

        request_fn = getattr(client, method)
        try:
            resp = breakers["umbra"].call(
                retry_request,
                request_fn,
                url,
                **kwargs,
            )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                logger.info("Umbra 401 -- refreshing token and retrying")
                token = _get_access_token(
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                    force_refresh=True,
                )
                kwargs["headers"] = self._headers(token)
                resp = breakers["umbra"].call(
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
        """Search Umbra STAC v2 catalog for SAR scenes covering the AOI.

        Note: cloud_cover_max is accepted for API compatibility but ignored
        because SAR is cloud-independent.
        """
        geojson_geom = _wkt_to_geojson(aoi_wkt)

        stac_body: dict[str, Any] = {
            "intersects": geojson_geom,
            "datetime": f"{start.isoformat()}Z/{end.isoformat()}Z",
            "collections": ["umbra-sar"],
            "limit": limit,
        }

        results: list[ArchiveSearchResult] = []
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "post", _STAC_URL, client, json=stac_body
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
                        provider="umbra",
                        acquired_at=acquired_at,
                        # SAR is cloud-independent — cloud_cover_pct is always None
                        cloud_cover_pct=None,
                        resolution_m=props.get("sar:resolution_range"),
                        thumbnail_url=thumbnail_url,
                        geometry_wkt=geometry_wkt,
                        product_type=props.get("sar:product_type", "GEC"),
                        estimated_cost_usd=self.estimated_cost_per_scene(),
                        metadata={
                            "instrument_mode": props.get("sar:instrument_mode"),
                            "polarization": props.get("sar:polarizations"),
                            "orbit_state": props.get("sat:orbit_state"),
                        },
                    )
                )

        logger.info("Umbra: found %d SAR scenes for AOI", len(results))
        return results

    def submit_order(
        self, scene_ids: list[str], product_type: str = "analytic"
    ) -> OrderSubmitResult:
        """Submit a tasking order to Umbra for the given scene IDs."""
        task_body: dict[str, Any] = {
            "type": "SPOTLIGHT",
            "sceneIds": scene_ids,
            "productType": product_type,
        }

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "post", _TASKING_URL, client, json=task_body
            )
            data = resp.json()

        umbra_status = data.get("status", "SUBMITTED")
        return OrderSubmitResult(
            external_order_id=data.get("taskId", ""),
            status=_STATUS_MAP.get(umbra_status, umbra_status.lower()),
            estimated_cost_usd=len(scene_ids) * self.estimated_cost_per_scene(product_type),
            message=data.get("statusMessage"),
        )

    def check_order_status(self, external_order_id: str) -> OrderStatusResult:
        """Check status of an existing Umbra tasking order."""
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "get", f"{_TASKING_URL}/{external_order_id}", client
            )
            data = resp.json()

        # Extract download URLs from deliveries
        scene_urls: list[str] = []
        for delivery in data.get("deliveries", []):
            url = delivery.get("url")
            if url:
                scene_urls.append(url)

        umbra_status = data.get("status", "UNKNOWN")
        status = _STATUS_MAP.get(umbra_status, umbra_status.lower())

        return OrderStatusResult(
            external_order_id=external_order_id,
            status=status,
            scene_urls=scene_urls,
            message=data.get("statusMessage"),
            metadata={"umbra_status": umbra_status},
        )

    def cancel_order(self, external_order_id: str) -> bool:
        """Cancel a pending Umbra tasking order."""
        with httpx.Client(timeout=_TIMEOUT) as client:
            try:
                self._request_with_retry_on_401(
                    "post",
                    f"{_TASKING_URL}/{external_order_id}/cancel",
                    client,
                )
                return True
            except httpx.HTTPStatusError:
                return False

    def estimated_cost_per_scene(self, product_type: str = "analytic") -> float:
        """Estimated cost per SAR spotlight collect in USD.

        Umbra SAR spotlight imagery is approximately $3,000/collect.
        """
        pricing = {
            "analytic": 3000.0,
            "GEC": 3000.0,
            "SICD": 3500.0,
        }
        return pricing.get(product_type, 3000.0)


register_provider("umbra", UmbraProvider)
