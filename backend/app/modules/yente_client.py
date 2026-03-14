"""Yente sanctions screening client.

Calls a self-hosted yente instance (OpenSanctions match API) for real-time
fuzzy-match sanctions screening against OFAC, EU, UN, and Tokyo MOU datasets.

Yente uses FollowTheMoney entity schemas; vessel screening uses the ``Vessel``
schema with properties ``name``, ``mmsi``, ``imoNumber``, and ``flag``.

All functions are gated by ``settings.YENTE_ENABLED`` — when disabled, they
return empty results without making HTTP calls.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pybreaker

from app.config import settings
from app.modules.circuit_breakers import breakers

logger = logging.getLogger(__name__)

# Reusable timeout for yente HTTP calls (connect, read).
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def _build_headers() -> dict[str, str]:
    """Build HTTP headers for yente API calls."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.YENTE_API_KEY:
        headers["Authorization"] = f"ApiKey {settings.YENTE_API_KEY}"
    return headers


def _build_vessel_entity(
    name: str,
    mmsi: str | None = None,
    imo: str | None = None,
    flag: str | None = None,
) -> dict[str, Any]:
    """Build a FollowTheMoney Vessel entity for the yente match API."""
    properties: dict[str, list[str]] = {"name": [name]}
    if mmsi:
        properties["mmsi"] = [mmsi]
    if imo:
        properties["imoNumber"] = [imo]
    if flag:
        properties["flag"] = [flag]
    return {"schema": "Vessel", "properties": properties}


def match_vessel(
    name: str,
    mmsi: str | None = None,
    imo: str | None = None,
    flag: str | None = None,
) -> list[dict[str, Any]]:
    """Screen a vessel against sanctions lists via yente match API.

    Calls ``POST /match/{dataset}?algorithm=logic-v2`` with a FollowTheMoney
    Vessel entity.  Only matches with ``score >= YENTE_MATCH_THRESHOLD`` are
    returned.

    Args:
        name: Vessel name (required).
        mmsi: Optional MMSI number.
        imo: Optional IMO number.
        flag: Optional flag state code.

    Returns:
        List of match dicts, each with keys: ``score``, ``name``, ``datasets``,
        ``id``, ``schema``, ``properties``.  Empty list when disabled or on error.
    """
    if not settings.YENTE_ENABLED:
        return []

    dataset = settings.YENTE_DATASETS
    url = f"{settings.YENTE_API_URL}/match/{dataset}"
    entity = _build_vessel_entity(name, mmsi=mmsi, imo=imo, flag=flag)
    params = {"algorithm": "logic-v2"}

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = breakers["yente"].call(
                client.post,
                url,
                json=entity,
                params=params,
                headers=_build_headers(),
            )
        resp.raise_for_status()
        data = resp.json()
    except pybreaker.CircuitBreakerError:
        logger.warning("Yente circuit breaker is open — skipping match for %r", name)
        return []
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Yente match request failed for %r: %s", name, exc)
        return []

    results = data.get("responses", data.get("results", []))
    threshold = settings.YENTE_MATCH_THRESHOLD

    filtered: list[dict[str, Any]] = []
    for item in results:
        score = item.get("score", 0.0)
        if score >= threshold:
            filtered.append({
                "id": item.get("id"),
                "schema": item.get("schema"),
                "score": score,
                "name": item.get("caption", item.get("name", "")),
                "datasets": item.get("datasets", []),
                "properties": item.get("properties", {}),
            })

    return filtered


def search_vessel(query: str) -> list[dict[str, Any]]:
    """Free-text search for vessels in yente.

    Calls ``GET /search/{dataset}?q={query}``.

    Args:
        query: Search query string.

    Returns:
        List of result dicts.  Empty list when disabled or on error.
    """
    if not settings.YENTE_ENABLED:
        return []

    dataset = settings.YENTE_DATASETS
    url = f"{settings.YENTE_API_URL}/search/{dataset}"
    params = {"q": query}

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = breakers["yente"].call(
                client.get,
                url,
                params=params,
                headers=_build_headers(),
            )
        resp.raise_for_status()
        data = resp.json()
    except pybreaker.CircuitBreakerError:
        logger.warning("Yente circuit breaker is open — skipping search for %r", query)
        return []
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Yente search request failed for %r: %s", query, exc)
        return []

    results = data.get("results", [])
    return [
        {
            "id": item.get("id"),
            "schema": item.get("schema"),
            "score": item.get("score", 0.0),
            "name": item.get("caption", item.get("name", "")),
            "datasets": item.get("datasets", []),
            "properties": item.get("properties", {}),
        }
        for item in results
    ]


def check_health() -> bool:
    """Check if the yente service is healthy.

    Calls ``GET /healthz``.

    Returns:
        ``True`` if yente is reachable and healthy, ``False`` otherwise.
        Always returns ``False`` when ``YENTE_ENABLED`` is ``False``.
    """
    if not settings.YENTE_ENABLED:
        return False

    url = f"{settings.YENTE_API_URL}/healthz"

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = breakers["yente"].call(
                client.get,
                url,
                headers=_build_headers(),
            )
        return resp.status_code == 200
    except pybreaker.CircuitBreakerError:
        logger.warning("Yente circuit breaker is open — health check failed")
        return False
    except httpx.HTTPError as exc:
        logger.error("Yente health check failed: %s", exc)
        return False
