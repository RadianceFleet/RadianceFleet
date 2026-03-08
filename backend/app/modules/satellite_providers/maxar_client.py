"""Maxar satellite imagery provider client.

API docs: https://docs.maxar.com/
Auth: OAuth2 ROPC flow with API key fallback.
Discovery API (STAC-compliant) + Ordering API v1.
"""
from __future__ import annotations

import logging
import re
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

_BASE_URL = "https://api.maxar.com"
_TOKEN_URL = "https://account.maxar.com/auth/realms/mds/protocol/openid-connect/token"
_DISCOVERY_URL = f"{_BASE_URL}/discovery/v2/search"
_ORDERS_URL = f"{_BASE_URL}/ordering/v1"
_TIMEOUT = 30.0

# Module-level token cache
_token_cache: dict[str, Any] = {}

# Default pricing (USD per km²)
_ARCHIVE_PRICE_PER_KM2 = 15.0
_FRESH_PRICE_PER_KM2 = 18.0
_ESTIMATED_SCENE_KM2 = 25.0


def _wkt_to_geojson(wkt_str: str) -> dict:
    """Convert WKT geometry to GeoJSON geometry dict."""
    geom = shapely_wkt.loads(wkt_str)
    return geojson_mapping(geom)


def _is_api_key(value: str) -> bool:
    """Check if the value looks like a long hex/alphanum API key (>50 chars)."""
    return len(value) > 50 and bool(re.match(r"^[a-zA-Z0-9_\-]+$", value))


def _get_access_token(
    api_key: str | None = None,
    username: str | None = None,
    force_refresh: bool = False,
) -> str:
    """Obtain a bearer token from Maxar via OAuth2 ROPC flow.

    If the API key looks like a long alphanum string (>50 chars), it is used
    directly as an API key (X-API-Key header) — in that case we return the
    key itself and the caller will use X-API-Key instead of Bearer.

    Otherwise we treat it as a password and perform an OAuth2 ROPC flow.
    Token is cached until expiry minus a 60s safety margin.
    """
    key = api_key or settings.MAXAR_API_KEY
    if not key:
        raise ValueError(
            "MAXAR_API_KEY must be set. Register at https://www.maxar.com/"
        )

    # API key mode — no token exchange needed
    if _is_api_key(key):
        return key

    # Check cache
    if not force_refresh and _token_cache.get("token"):
        if _time.monotonic() < _token_cache.get("expires_at", 0):
            return _token_cache["token"]

    uname = username or settings.MAXAR_USERNAME
    if not uname:
        raise ValueError(
            "MAXAR_USERNAME must be set for OAuth2 ROPC authentication."
        )

    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = breakers["maxar"].call(
            retry_request,
            client.post,
            _TOKEN_URL,
            data={
                "grant_type": "password",
                "username": uname,
                "password": key,
                "client_id": "maxar-sdk",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        # Default 2 hours (7200s), safety margin 60s
        expires_in = data.get("expires_in", 7200)
        _token_cache["token"] = token
        _token_cache["expires_at"] = _time.monotonic() + max(0, expires_in - 60)
        return token


class MaxarProvider(SatelliteProvider):
    """Maxar satellite imagery provider (WorldView, GeoEye)."""

    def __init__(
        self,
        api_key: str | None = None,
        username: str | None = None,
    ) -> None:
        self._api_key = api_key or settings.MAXAR_API_KEY
        if not self._api_key:
            raise ValueError(
                "MAXAR_API_KEY must be set. Register at https://www.maxar.com/"
            )
        self._username = username or settings.MAXAR_USERNAME

    @property
    def name(self) -> str:
        return "maxar"

    def _headers(self, token: Optional[str] = None) -> dict[str, str]:
        """Build auth headers — Bearer token or X-API-Key depending on key format."""
        if token is None:
            token = _get_access_token(
                api_key=self._api_key, username=self._username
            )
        if _is_api_key(self._api_key):
            return {
                "X-API-Key": self._api_key,
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _request_with_retry_on_401(
        self,
        method: str,
        url: str,
        client: httpx.Client,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make a request, refreshing the token on 401."""
        token = _get_access_token(
            api_key=self._api_key, username=self._username
        )
        kwargs["headers"] = self._headers(token)

        request_fn = getattr(client, method)
        try:
            resp = breakers["maxar"].call(
                retry_request,
                request_fn,
                url,
                **kwargs,
            )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                logger.info("Maxar 401 -- refreshing token and retrying")
                token = _get_access_token(
                    api_key=self._api_key,
                    username=self._username,
                    force_refresh=True,
                )
                kwargs["headers"] = self._headers(token)
                resp = breakers["maxar"].call(
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
        """Search Maxar Discovery catalog (STAC) for optical scenes covering the AOI."""
        geojson_geom = _wkt_to_geojson(aoi_wkt)

        stac_body: dict[str, Any] = {
            "intersects": geojson_geom,
            "datetime": f"{start.isoformat()}Z/{end.isoformat()}Z",
            "limit": limit,
            "filter": {
                "op": "and",
                "args": [
                    {
                        "op": "<=",
                        "args": [
                            {"property": "eo:cloud_cover"},
                            cloud_cover_max,
                        ],
                    }
                ],
            },
            "filter-lang": "cql2-json",
        }

        results: list[ArchiveSearchResult] = []
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "post", _DISCOVERY_URL, client, json=stac_body
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

                # Cloud cover: Maxar uses 0-100 scale
                cloud_cover_raw = props.get("eo:cloud_cover")
                cloud_cover_pct = float(cloud_cover_raw) if cloud_cover_raw is not None else None

                results.append(
                    ArchiveSearchResult(
                        scene_id=feature.get("id", ""),
                        provider="maxar",
                        acquired_at=acquired_at,
                        cloud_cover_pct=cloud_cover_pct,
                        resolution_m=props.get("gsd"),
                        thumbnail_url=thumbnail_url,
                        geometry_wkt=geometry_wkt,
                        product_type=props.get("platform", "WorldView"),
                        estimated_cost_usd=self.estimated_cost_per_scene(),
                        metadata={
                            "platform": props.get("platform"),
                            "off_nadir": props.get("view:off_nadir"),
                            "sun_elevation": props.get("view:sun_elevation"),
                            "constellation": props.get("constellation"),
                        },
                    )
                )

        logger.info("Maxar: found %d scenes for AOI", len(results))
        return results

    def submit_order(
        self, scene_ids: list[str], product_type: str = "analytic"
    ) -> OrderSubmitResult:
        """Submit an order via Maxar Ordering API v1 pipeline."""
        order_body: dict[str, Any] = {
            "output_configs": [
                {
                    "image_format": "geotiff",
                    "product_type": product_type,
                }
            ],
            "items": scene_ids,
            "metadata": {
                "source": "radiancefleet",
            },
        }

        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "post",
                f"{_ORDERS_URL}/pipelines/imagery/analytic/order",
                client,
                json=order_body,
            )
            data = resp.json()

        return OrderSubmitResult(
            external_order_id=data.get("order_id", ""),
            status=data.get("status", "SUBMITTED"),
            estimated_cost_usd=len(scene_ids) * self.estimated_cost_per_scene(product_type),
            message=data.get("message"),
        )

    def check_order_status(self, external_order_id: str) -> OrderStatusResult:
        """Check status of an existing Maxar order."""
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = self._request_with_retry_on_401(
                "get", f"{_ORDERS_URL}/orders/{external_order_id}", client
            )
            data = resp.json()

        # Extract download URLs from output files
        scene_urls: list[str] = []
        for output in data.get("output_files", []):
            url = output.get("url") or output.get("href")
            if url:
                scene_urls.append(url)

        # Map Maxar statuses to our standard statuses
        maxar_status = data.get("status", "unknown")
        status_map = {
            "SUBMITTED": "accepted",
            "RUNNING": "processing",
            "SUCCEEDED": "delivered",
            "FAILED": "failed",
            "ERROR": "failed",
            "CANCELLED": "cancelled",
        }
        status = status_map.get(maxar_status, maxar_status)

        return OrderStatusResult(
            external_order_id=external_order_id,
            status=status,
            scene_urls=scene_urls,
            cost_usd=data.get("total_cost_usd"),
            message=data.get("message"),
            metadata={"maxar_status": maxar_status},
        )

    def cancel_order(self, external_order_id: str) -> bool:
        """Cancel a pending Maxar order."""
        with httpx.Client(timeout=_TIMEOUT) as client:
            try:
                self._request_with_retry_on_401(
                    "post",
                    f"{_ORDERS_URL}/orders/{external_order_id}/cancel",
                    client,
                )
                return True
            except httpx.HTTPStatusError:
                return False

    def estimated_cost_per_scene(self, product_type: str = "analytic") -> float:
        """Estimated cost per scene in USD (based on ~25 km² per scene)."""
        if product_type in ("fresh", "tasking"):
            return _FRESH_PRICE_PER_KM2 * _ESTIMATED_SCENE_KM2  # $450
        return _ARCHIVE_PRICE_PER_KM2 * _ESTIMATED_SCENE_KM2  # $375


register_provider("maxar", MaxarProvider)
