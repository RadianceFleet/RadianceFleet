"""Datalastic API client — vessel metadata enrichment.

Provides authoritative DWT, vessel type, year built, callsign, and gross tonnage
via the Datalastic REST API. ToS-compliant alternative to Equasis scraping.

API docs: https://datalastic.com/api-reference/
"""

from __future__ import annotations

import logging
import time

import httpx

from app.config import settings
from app.modules.circuit_breakers import breakers

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.datalastic.com/api/v0"
_TIMEOUT = 10.0
_REQUEST_DELAY_S = 1.0  # Conservative 1 req/s (API allows 600/min)


def fetch_vessel_info(
    mmsi: str | None = None,
    imo: str | None = None,
) -> dict | None:
    """Fetch vessel metadata from Datalastic.

    Args:
        mmsi: 9-digit MMSI string.
        imo: 7-digit IMO string.

    Returns:
        Normalised dict with fields: deadweight, vessel_type, year_built,
        callsign, flag, gross_tonnage. Returns None if not found or API key
        not configured.
    """
    api_key = settings.DATALASTIC_API_KEY
    if not api_key:
        return None

    if not mmsi and not imo:
        return None

    params: dict[str, str] = {"api-key": api_key}
    if mmsi:
        params["mmsi"] = mmsi
    elif imo:
        params["imo"] = imo

    url = f"{_BASE_URL}/vessel_info"

    from app.utils.http_retry import retry_request

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = breakers["datalastic"].call(
                retry_request, client.get, url, params=params
            )
    except Exception as exc:
        logger.warning("Datalastic request failed: %s", exc)
        return None

    if resp.status_code == 404:
        return None

    if resp.status_code >= 400:
        logger.warning("Datalastic HTTP %d for %s", resp.status_code, mmsi or imo)
        return None

    try:
        data = resp.json()
    except Exception:
        logger.warning("Datalastic returned non-JSON response")
        return None

    # The API may wrap the vessel in a "data" key or return it directly
    vessel = data.get("data", data) if isinstance(data, dict) else data

    if not isinstance(vessel, dict):
        return None

    result: dict = {}

    try:
        dw = vessel.get("deadweight")
        if dw is not None:
            result["deadweight"] = float(dw)
    except (ValueError, TypeError):
        pass

    try:
        vtype = vessel.get("type_specific")
        if vtype:
            result["vessel_type"] = str(vtype)
    except (ValueError, TypeError):
        pass

    try:
        yb = vessel.get("year_built")
        if yb is not None:
            result["year_built"] = int(yb)
    except (ValueError, TypeError):
        pass

    try:
        cs = vessel.get("callsign")
        if cs:
            result["callsign"] = str(cs)
    except (ValueError, TypeError):
        pass

    try:
        flag = vessel.get("country_iso")
        if flag:
            result["flag"] = str(flag)
    except (ValueError, TypeError):
        pass

    try:
        gt = vessel.get("gross_tonnage")
        if gt is not None:
            result["gross_tonnage"] = float(gt)
    except (ValueError, TypeError):
        pass

    time.sleep(_REQUEST_DELAY_S)
    return result if result else None
