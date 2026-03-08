"""Planet Labs Orders API v2 client for satellite imagery ordering.

API docs: https://developers.planet.com/docs/orders/
Auth: Basic auth with API key as username, empty password.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

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

_DATA_API = "https://api.planet.com/data/v1"
_ORDERS_API = "https://api.planet.com/compute/ops/orders/v2"
_TIMEOUT = 30.0


def _wkt_to_geojson(wkt_str: str) -> dict:
    """Convert WKT geometry to GeoJSON geometry dict."""
    geom = shapely_wkt.loads(wkt_str)
    return geojson_mapping(geom)


class PlanetProvider(SatelliteProvider):
    """Planet Labs satellite imagery provider."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.PLANET_API_KEY
        if not self._api_key:
            raise ValueError("PLANET_API_KEY must be set. Register at https://www.planet.com/")

    @property
    def name(self) -> str:
        return "planet"

    def _auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(username=self._api_key, password="")

    def search_archive(
        self,
        aoi_wkt: str,
        start: datetime,
        end: datetime,
        cloud_cover_max: float = 30.0,
        limit: int = 10,
    ) -> list[ArchiveSearchResult]:
        """Search Planet archive for scenes covering the AOI."""
        geojson_geom = _wkt_to_geojson(aoi_wkt)

        search_filter: dict[str, Any] = {
            "type": "AndFilter",
            "config": [
                {
                    "type": "GeometryFilter",
                    "field_name": "geometry",
                    "config": geojson_geom,
                },
                {
                    "type": "DateRangeFilter",
                    "field_name": "acquired",
                    "config": {
                        "gte": start.isoformat() + "Z",
                        "lte": end.isoformat() + "Z",
                    },
                },
                {
                    "type": "RangeFilter",
                    "field_name": "cloud_cover",
                    "config": {"lte": cloud_cover_max / 100.0},
                },
            ],
        }

        body: dict[str, Any] = {
            "item_types": ["PSScene"],
            "filter": search_filter,
        }

        results: list[ArchiveSearchResult] = []
        with httpx.Client(timeout=_TIMEOUT, auth=self._auth()) as client:
            resp = breakers["planet"].call(
                retry_request,
                client.post,
                f"{_DATA_API}/quick-search",
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

            for feature in data.get("features", [])[:limit]:
                props = feature.get("properties", {})
                acquired_str = props.get("acquired", "")
                try:
                    acquired_at = datetime.fromisoformat(acquired_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    acquired_at = datetime.now(UTC)

                # Convert feature geometry back to WKT
                feat_geom = feature.get("geometry")
                geometry_wkt = None
                if feat_geom:
                    from shapely.geometry import shape

                    geometry_wkt = shape(feat_geom).wkt

                results.append(
                    ArchiveSearchResult(
                        scene_id=feature.get("id", ""),
                        provider="planet",
                        acquired_at=acquired_at,
                        cloud_cover_pct=(props.get("cloud_cover", 0) or 0) * 100.0,
                        resolution_m=props.get("pixel_resolution"),
                        thumbnail_url=feature.get("_links", {}).get("thumbnail"),
                        geometry_wkt=geometry_wkt,
                        product_type="PSScene",
                        estimated_cost_usd=self.estimated_cost_per_scene(),
                        metadata={
                            "sun_elevation": props.get("sun_elevation"),
                            "view_angle": props.get("view_angle"),
                        },
                    )
                )

        logger.info("Planet: found %d scenes for AOI", len(results))
        return results

    def submit_order(
        self, scene_ids: list[str], product_type: str = "analytic"
    ) -> OrderSubmitResult:
        """Submit an order for the given scene IDs."""
        order_body: dict[str, Any] = {
            "name": f"radiancefleet-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}",
            "products": [
                {
                    "item_ids": scene_ids,
                    "item_type": "PSScene",
                    "product_bundle": product_type,
                }
            ],
        }

        with httpx.Client(timeout=_TIMEOUT, auth=self._auth()) as client:
            resp = breakers["planet"].call(
                retry_request,
                client.post,
                _ORDERS_API,
                json=order_body,
            )
            resp.raise_for_status()
            data = resp.json()

        return OrderSubmitResult(
            external_order_id=data.get("id", ""),
            status=data.get("state", "submitted"),
            estimated_cost_usd=len(scene_ids) * self.estimated_cost_per_scene(product_type),
            message=data.get("name"),
        )

    def check_order_status(self, external_order_id: str) -> OrderStatusResult:
        """Check the status of an existing order."""
        with httpx.Client(timeout=_TIMEOUT, auth=self._auth()) as client:
            resp = breakers["planet"].call(
                retry_request,
                client.get,
                f"{_ORDERS_API}/{external_order_id}",
            )
            resp.raise_for_status()
            data = resp.json()

        # Extract download URLs from results
        scene_urls: list[str] = []
        for result in data.get("_links", {}).get("results", []):
            if result.get("location"):
                scene_urls.append(result["location"])

        # Map Planet states to our states
        planet_state = data.get("state", "unknown")
        status_map = {
            "queued": "accepted",
            "running": "processing",
            "success": "delivered",
            "failed": "failed",
            "partial": "processing",
            "cancelled": "cancelled",
        }
        status = status_map.get(planet_state, planet_state)

        return OrderStatusResult(
            external_order_id=external_order_id,
            status=status,
            scene_urls=scene_urls,
            message=data.get("name"),
            metadata={"planet_state": planet_state},
        )

    def cancel_order(self, external_order_id: str) -> bool:
        """Cancel a pending order."""
        with httpx.Client(timeout=_TIMEOUT, auth=self._auth()) as client:
            resp = breakers["planet"].call(
                retry_request,
                client.put,
                f"{_ORDERS_API}/{external_order_id}",
                json={"state": "cancelled"},
            )
            return resp.status_code in (200, 204)

    def estimated_cost_per_scene(self, product_type: str = "analytic") -> float:
        """Estimated cost per scene in USD (Planet SkySat/PSScene pricing)."""
        pricing = {
            "analytic": 10.0,
            "analytic_udm2": 12.0,
            "visual": 8.0,
        }
        return pricing.get(product_type, 10.0)


register_provider("planet", PlanetProvider)
