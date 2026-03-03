# IMPORTANT: Equasis Terms of Service explicitly prohibit automated access.
# This client is opt-in only (EQUASIS_SCRAPING_ENABLED=false by default).
# For production use, Datalastic API (https://datalastic.com) is the recommended
# ToS-compliant alternative. This client is provided for development and research
# use only.
"""Equasis HTML session scraper (opt-in metadata enrichment).

WARNING: Equasis ToS prohibits automated access. This module is DISABLED by
default (EQUASIS_SCRAPING_ENABLED=false). Enable only with explicit consent.
See https://www.equasis.org for ToS.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

_REQUEST_DELAY_S = 2.0  # 1 req/2s — ToS compliance


class EquasisClient:
    """Equasis vessel registry scraper. Opt-in only via EQUASIS_SCRAPING_ENABLED."""

    BASE_URL = "https://www.equasis.org/EquasisWeb"

    def __init__(self) -> None:
        if not settings.EQUASIS_SCRAPING_ENABLED:
            raise RuntimeError(
                "Equasis scraping is disabled (EQUASIS_SCRAPING_ENABLED=false). "
                "Set EQUASIS_SCRAPING_ENABLED=true to opt in. "
                "WARNING: Equasis ToS prohibits automated access. "
                "For production, use Datalastic API instead."
            )
        if not settings.EQUASIS_USERNAME or not settings.EQUASIS_PASSWORD:
            raise RuntimeError(
                "EQUASIS_USERNAME and EQUASIS_PASSWORD must be set when scraping is enabled."
            )
        self._session: Optional[requests.Session] = None

    def _login(self) -> None:
        """POST /public/Signin and persist session cookie."""
        session = requests.Session()
        resp = session.post(
            f"{self.BASE_URL}/public/Signin",
            data={
                "j_email": settings.EQUASIS_USERNAME,
                "j_password": settings.EQUASIS_PASSWORD,
                "submit": "Login",
            },
            timeout=15,
            allow_redirects=True,
        )
        resp.raise_for_status()
        # Success redirects to /restricted/HomePage; /public/ means bad credentials
        if "/public/" in resp.url:
            raise RuntimeError("Equasis login failed — check EQUASIS_USERNAME and EQUASIS_PASSWORD.")
        self._session = session
        logger.debug("Equasis login successful")

    def _get(self, path: str, params: dict) -> requests.Response:
        """Lazy login + automatic re-login on session expiry."""
        if self._session is None:
            self._login()
        time.sleep(_REQUEST_DELAY_S)
        resp = self._session.get(f"{self.BASE_URL}{path}", params=params, timeout=15)
        # Re-login if session expired (redirect to /public/ or 401)
        if resp.status_code == 401 or "/public/" in resp.url:
            logger.debug("Equasis session expired, re-logging in")
            self._session = None
            self._login()
            resp = self._session.get(f"{self.BASE_URL}{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp

    def search_by_imo(self, imo: str) -> Optional[dict]:
        """Search Equasis by IMO number. Returns enrichment dict or None."""
        try:
            resp = self._get("/restricted/ShipInfo", {"P_IMO": imo})
            return _parse_vessel_page(resp.text)
        except Exception as exc:
            logger.warning("Equasis search_by_imo(%s) failed: %s", imo, exc)
            return None

    def search_by_mmsi(self, mmsi: str) -> Optional[dict]:
        """Fallback search by MMSI when IMO is unavailable."""
        try:
            resp = self._get("/restricted/Search", {"P_MMSI": mmsi})
            return _parse_vessel_page(resp.text)
        except Exception as exc:
            logger.warning("Equasis search_by_mmsi(%s) failed: %s", mmsi, exc)
            return None


def _parse_vessel_page(html: str) -> Optional[dict]:
    """Parse Equasis vessel info page HTML. Returns enrichment dict or None.

    Returns dict with any subset of:
    {dwt, vessel_type, year_built, flag, ism_company, gross_tonnage}
    All values are strings. Returns None if page indicates no data.
    """
    soup = BeautifulSoup(html, "html.parser")
    data: dict = {}

    # Check for "no vessel found" indicators
    if "No ship found" in html or "no ship" in html.lower():
        return None

    # Equasis uses labelled table rows. Common patterns:
    # <td>Deadweight:</td><td>65000</td>
    # <td>Type:</td><td>Crude Oil Tanker</td>
    # <td>Year of Build:</td><td>2003</td>
    # <td>Flag:</td><td>Panama</td>
    # <td>ISM Company:</td><td>ABC Ship Management</td>
    # <td>Gross Tonnage:</td><td>45000</td>
    #
    # The actual selectors depend on the current Equasis page structure.
    # Parse all label-value pairs from the main info tables.
    label_map = {
        "deadweight": "dwt",
        "dead weight": "dwt",
        "type": "vessel_type",
        "ship type": "vessel_type",
        "year of build": "year_built",
        "built": "year_built",
        "flag": "flag",
        "flag state": "flag",
        "ism company": "ism_company",
        "gross tonnage": "gross_tonnage",
        "gt": "gross_tonnage",
    }

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).rstrip(":").lower()
            value = cells[1].get_text(strip=True)
            if label in label_map and value:
                data[label_map[label]] = value

    return data if data else None
