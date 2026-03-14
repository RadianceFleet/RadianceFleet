"""OpenCorporates API client for beneficial ownership transparency.

Searches company registries worldwide for vessel ownership entities.
Gated by ``settings.OPENCORPORATES_ENABLED`` — returns empty results when disabled.

Rate-limited to respect free-tier API quotas (200 requests/month free).
Uses circuit breaker pattern from ``circuit_breakers.py``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import pybreaker

from app.config import settings
from app.modules.circuit_breakers import breakers

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Secrecy jurisdictions — used for SPV detection heuristics
SECRECY_JURISDICTIONS = frozenset({
    "MH", "LR", "PA", "MT", "CY", "VG", "KY", "BM", "GI", "BS",
    "VU", "WS", "SC", "MU", "HK", "SG", "AE", "BZ", "HN", "KM",
    "PW", "TG",
})

# Track last request time for rate limiting
_last_request_time: float = 0.0


def _rate_limit() -> None:
    """Sleep if needed to respect rate limit."""
    global _last_request_time
    now = time.monotonic()
    elapsed = now - _last_request_time
    delay = settings.OPENCORPORATES_RATE_LIMIT_S
    if elapsed < delay and _last_request_time > 0:
        time.sleep(delay - elapsed)
    _last_request_time = time.monotonic()


def _build_params(**extra: Any) -> dict[str, Any]:
    """Build query params with API key if configured."""
    params: dict[str, Any] = {}
    if settings.OPENCORPORATES_API_KEY:
        params["api_token"] = settings.OPENCORPORATES_API_KEY
    params.update({k: v for k, v in extra.items() if v is not None})
    return params


def search_companies(
    name: str,
    jurisdiction_code: str | None = None,
) -> list[dict[str, Any]]:
    """Search OpenCorporates for companies by name.

    Args:
        name: Company name to search for.
        jurisdiction_code: Optional 2-letter jurisdiction filter (e.g. "pa", "mh").

    Returns:
        List of company dicts with keys: ``name``, ``company_number``,
        ``jurisdiction_code``, ``opencorporates_url``, ``incorporation_date``,
        ``registered_address``, ``current_status``.
        Empty list when disabled or on error.
    """
    if not settings.OPENCORPORATES_ENABLED:
        return []

    url = f"{settings.OPENCORPORATES_API_URL}/companies/search"
    params = _build_params(q=name)
    if jurisdiction_code:
        params["jurisdiction_code"] = jurisdiction_code.lower()

    _rate_limit()

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = breakers["opencorporates"].call(
                client.get,
                url,
                params=params,
            )
        resp.raise_for_status()
        data = resp.json()
    except pybreaker.CircuitBreakerError:
        logger.warning("OpenCorporates circuit breaker is open — skipping search for %r", name)
        return []
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("OpenCorporates search failed for %r: %s", name, exc)
        return []

    companies_raw = data.get("results", {}).get("companies", [])
    results: list[dict[str, Any]] = []
    for item in companies_raw:
        company = item.get("company", {})
        results.append({
            "name": company.get("name", ""),
            "company_number": company.get("company_number", ""),
            "jurisdiction_code": company.get("jurisdiction_code", ""),
            "opencorporates_url": company.get("opencorporates_url", ""),
            "incorporation_date": company.get("incorporation_date"),
            "registered_address": company.get("registered_address_in_full", ""),
            "current_status": company.get("current_status", ""),
        })
    return results


def fetch_company(
    jurisdiction_code: str,
    company_number: str,
) -> dict[str, Any] | None:
    """Fetch full company details from OpenCorporates.

    Args:
        jurisdiction_code: 2-letter jurisdiction code (e.g. "pa").
        company_number: Company registration number.

    Returns:
        Company detail dict, or None if not found / disabled / error.
    """
    if not settings.OPENCORPORATES_ENABLED:
        return None

    url = (
        f"{settings.OPENCORPORATES_API_URL}/companies"
        f"/{jurisdiction_code.lower()}/{company_number}"
    )
    params = _build_params()

    _rate_limit()

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = breakers["opencorporates"].call(
                client.get,
                url,
                params=params,
            )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    except pybreaker.CircuitBreakerError:
        logger.warning(
            "OpenCorporates circuit breaker is open — skipping fetch for %s/%s",
            jurisdiction_code,
            company_number,
        )
        return None
    except (httpx.HTTPError, ValueError) as exc:
        logger.error(
            "OpenCorporates fetch failed for %s/%s: %s",
            jurisdiction_code,
            company_number,
            exc,
        )
        return None

    company = data.get("results", {}).get("company", {})
    return {
        "name": company.get("name", ""),
        "company_number": company.get("company_number", ""),
        "jurisdiction_code": company.get("jurisdiction_code", ""),
        "opencorporates_url": company.get("opencorporates_url", ""),
        "incorporation_date": company.get("incorporation_date"),
        "registered_address": company.get("registered_address_in_full", ""),
        "current_status": company.get("current_status", ""),
        "officers": [],
    }


def fetch_officers(
    jurisdiction_code: str,
    company_number: str,
) -> list[dict[str, Any]]:
    """Fetch officers (directors) for a company.

    Args:
        jurisdiction_code: 2-letter jurisdiction code.
        company_number: Company registration number.

    Returns:
        List of officer dicts with ``name``, ``position``, ``start_date``.
        Empty list when disabled or on error.
    """
    if not settings.OPENCORPORATES_ENABLED:
        return []

    url = (
        f"{settings.OPENCORPORATES_API_URL}/companies"
        f"/{jurisdiction_code.lower()}/{company_number}/officers"
    )
    params = _build_params()

    _rate_limit()

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = breakers["opencorporates"].call(
                client.get,
                url,
                params=params,
            )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
    except pybreaker.CircuitBreakerError:
        logger.warning(
            "OpenCorporates circuit breaker is open — skipping officers for %s/%s",
            jurisdiction_code,
            company_number,
        )
        return []
    except (httpx.HTTPError, ValueError) as exc:
        logger.error(
            "OpenCorporates officers fetch failed for %s/%s: %s",
            jurisdiction_code,
            company_number,
            exc,
        )
        return []

    officers_raw = data.get("results", {}).get("officers", [])
    results: list[dict[str, Any]] = []
    for item in officers_raw:
        officer = item.get("officer", {})
        results.append({
            "name": officer.get("name", ""),
            "position": officer.get("position", ""),
            "start_date": officer.get("start_date"),
        })
    return results
