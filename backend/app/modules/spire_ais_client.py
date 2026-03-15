"""Spire Maritime satellite AIS client (GraphQL API v2).

Provides satellite AIS coverage for the Persian Gulf region where terrestrial
AIS coverage is unavailable. Uses a separate API key (SPIRE_AIS_API_KEY) from
the verification lookup key (SPIRE_API_KEY) to avoid quota conflicts.

Reference: https://documentation.spire.com/maritime-2-0/
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.modules.circuit_breakers import breakers

logger = logging.getLogger(__name__)


class SpireAisClient:
    """Client for Spire Maritime GraphQL API (satellite AIS positions)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        request_delay: float = 1.0,
    ) -> None:
        self.api_key = api_key or settings.SPIRE_AIS_API_KEY
        self.base_url = base_url or settings.SPIRE_AIS_BASE_URL
        self.request_delay = request_delay
        self._last_request_time: float = 0.0

    def _rate_limit(self) -> None:
        """Enforce minimum delay between requests."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.monotonic()

    def _do_request(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        """Execute a GraphQL request against the Spire API."""
        self._rate_limit()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        with httpx.Client(timeout=60.0) as client:
            resp = client.post(self.base_url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def fetch_positions(
        self,
        bbox: list[list[list[float]]],
        since_utc: datetime,
        limit: int = 500,
    ) -> list[dict]:
        """Fetch vessel positions within a bounding box polygon since a given time.

        Args:
            bbox: GeoJSON-style polygon coordinates, e.g.
                  [[[47,23],[57,23],[57,30.5],[47,30.5],[47,23]]]
            since_utc: Only return positions updated after this time.
            limit: Maximum number of vessel nodes to return.

        Returns:
            List of normalized position dicts.
        """
        if not self.api_key:
            logger.warning("Spire AIS API key not configured")
            return []

        since_str = since_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        query = """
        query($area: GeoJSONPolygonInput!, $startDate: String!, $limit: Int!) {
          vessels(
            area: $area
            lastPositionUpdate: { startDate: $startDate }
            limit: $limit
          ) {
            nodes {
              staticData {
                mmsi
                imo
                name
                shipType
              }
              lastPositionUpdate {
                timestamp
                latitude
                longitude
                speed
                course
                heading
              }
            }
          }
        }
        """

        variables = {
            "area": {
                "type": "POLYGON",
                "coordinates": bbox,
            },
            "startDate": since_str,
            "limit": limit,
        }

        try:
            data = breakers["spire_ais"].call(self._do_request, query, variables)
        except Exception as exc:
            logger.error("Spire AIS GraphQL request failed: %s", exc)
            raise

        # Parse response
        nodes = []
        try:
            vessels_data = data.get("data", {}).get("vessels", {})
            raw_nodes = vessels_data.get("nodes", [])
            for raw in raw_nodes:
                normalized = self._normalize_position(raw)
                if normalized:
                    nodes.append(normalized)
        except Exception as exc:
            logger.error("Failed to parse Spire AIS response: %s", exc)

        logger.info("Spire AIS: fetched %d positions", len(nodes))
        return nodes

    @staticmethod
    def _normalize_position(raw: dict) -> dict | None:
        """Normalize a Spire vessel node to RadianceFleet's AIS point format.

        Args:
            raw: A vessel node from the GraphQL response.

        Returns:
            Normalized dict with keys: mmsi, imo, name, ship_type,
            timestamp_utc, lat, lon, sog, cog, heading.
            Returns None if essential fields are missing.
        """
        static = raw.get("staticData") or {}
        position = raw.get("lastPositionUpdate") or {}

        mmsi = static.get("mmsi")
        lat = position.get("latitude")
        lon = position.get("longitude")
        timestamp = position.get("timestamp")

        if not mmsi or lat is None or lon is None or not timestamp:
            return None

        mmsi_str = str(mmsi)
        if len(mmsi_str) != 9:
            return None

        # Parse timestamp
        try:
            if isinstance(timestamp, str):
                ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            elif isinstance(timestamp, (int, float)):
                ts = datetime.fromtimestamp(timestamp, tz=__import__("datetime").UTC).replace(
                    tzinfo=None
                )
            else:
                return None
        except Exception:
            return None

        return {
            "mmsi": mmsi_str,
            "imo": str(static.get("imo", "")) if static.get("imo") else None,
            "name": static.get("name"),
            "ship_type": static.get("shipType"),
            "timestamp_utc": ts,
            "lat": float(lat),
            "lon": float(lon),
            "sog": float(position["speed"]) if position.get("speed") is not None else None,
            "cog": float(position["course"]) if position.get("course") is not None else None,
            "heading": (
                float(position["heading"])
                if position.get("heading") is not None and position.get("heading") != 511
                else None
            ),
        }

    def test_connection(self) -> dict:
        """Test API connectivity with a minimal query.

        Returns:
            Dict with status and details.
        """
        if not self.api_key:
            return {"status": "error", "detail": "SPIRE_AIS_API_KEY not configured"}

        query = """
        query {
          vessels(limit: 1) {
            nodes {
              staticData { mmsi }
            }
          }
        }
        """
        try:
            data = self._do_request(query)
            nodes = data.get("data", {}).get("vessels", {}).get("nodes", [])
            return {
                "status": "ok",
                "detail": f"Connected, received {len(nodes)} vessel(s)",
            }
        except httpx.HTTPStatusError as exc:
            return {
                "status": "error",
                "detail": f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
            }
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}
