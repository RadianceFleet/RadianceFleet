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

import requests
from bs4 import BeautifulSoup

from app.config import settings
from app.modules.circuit_breakers import breakers

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
        self._session: requests.Session | None = None

    def _login(self) -> None:
        """POST /authen/HomePage and persist session cookie.

        allow_redirects=False is intentional: Equasis POST returns the logged-in
        page directly with a 200 + Set-Cookie. Following redirects (allow_redirects=True)
        causes the final GET to return an empty body, losing the session.
        """
        session = requests.Session()
        resp = session.post(
            f"{self.BASE_URL}/authen/HomePage",
            params={"fs": "HomePage"},
            data={
                "j_email": settings.EQUASIS_USERNAME,
                "j_password": settings.EQUASIS_PASSWORD,
            },
            timeout=15,
            allow_redirects=False,
        )
        resp.raise_for_status()
        # Success: response body contains "Logout" (authenticated state)
        # Failure: no Logout link — still showing the login form
        if "Logout" not in resp.text:
            raise RuntimeError(
                "Equasis login failed — check EQUASIS_USERNAME and EQUASIS_PASSWORD."
            )
        self._session = session
        logger.debug("Equasis login successful")

    def _get(self, path: str, params: dict) -> requests.Response:
        """Lazy login + automatic re-login on session expiry."""
        if self._session is None:
            self._login()
        time.sleep(_REQUEST_DELAY_S)
        resp = breakers["equasis"].call(
            self._session.get, f"{self.BASE_URL}{path}", params=params, timeout=15
        )
        # Re-login if session expired (redirect to /authen/ or /public/ or 401)
        if resp.status_code == 401 or "/authen/" in resp.url or "/public/" in resp.url:
            logger.debug("Equasis session expired, re-logging in")
            self._session = None
            self._login()
            resp = self._session.get(f"{self.BASE_URL}{path}", params=params, timeout=15)
        resp.raise_for_status()
        return resp

    def search_by_imo(self, imo: str) -> dict | None:
        """Search Equasis by IMO number. Returns enrichment dict or None."""
        try:
            resp = self._get("/restricted/ShipInfo", {"P_IMO": imo})
            return _parse_vessel_page(resp.text)
        except Exception as exc:
            logger.warning("Equasis search_by_imo(%s) failed: %s", imo, exc)
            return None

    def search_by_mmsi(self, mmsi: str) -> dict | None:
        """Fallback search by MMSI when IMO is unavailable."""
        try:
            resp = self._get("/restricted/Search", {"P_MMSI": mmsi})
            return _parse_vessel_page(resp.text)
        except Exception as exc:
            logger.warning("Equasis search_by_mmsi(%s) failed: %s", mmsi, exc)
            return None


def _parse_vessel_page(html: str) -> dict | None:
    """Parse Equasis vessel info page HTML. Returns enrichment dict or None.

    Equasis uses a Bootstrap grid layout: each field is a <div class="row"> where
    the first child div contains a <b>Label</b> and the second child div holds the value.
    Company/ISM data lives in a separate <tbody> table with columns:
      [company_id, role, company_name, flag, since_date]

    Returns dict with any subset of:
      {dwt, vessel_type, year_built, flag, ism_company, gross_tonnage, callsign}
    All values are strings. Returns None if page indicates no data.
    """
    soup = BeautifulSoup(html, "html.parser")
    data: dict = {}

    if "No ship found" in html or "no ship" in html.lower():
        return None

    # ── Main vessel fields from div.row grid ────────────────────────────────
    label_map = {
        "dwt": "dwt",
        "gross tonnage": "gross_tonnage",
        "type of ship": "vessel_type",
        "ship type": "vessel_type",
        "year of build": "year_built",
        "call sign": "callsign",
    }
    for row in soup.find_all("div", class_="row"):
        divs = row.find_all("div", recursive=False)
        if len(divs) < 2:
            continue
        b_tag = divs[0].find("b")
        if not b_tag:
            continue
        label = b_tag.get_text(strip=True).lower()
        if label in label_map:
            value = divs[1].get_text(strip=True)
            if value:
                data[label_map[label]] = value
        # Flag: 4-div row — label=div[0], value=div[3] "(CountryName)"
        elif label == "flag" and len(divs) >= 4:
            flag_text = divs[3].get_text(strip=True).strip("()")
            if flag_text:
                data["flag"] = flag_text

    # ── ISM Manager from company table ──────────────────────────────────────
    # Table rows: [company_id, role, company_name, flag, since_date]
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 3:
            role = tds[1].get_text(strip=True)
            if "ISM Manager" in role:
                name = tds[2].get_text(strip=True)
                if name and name.upper() not in ("UNKNOWN", "N/A", ""):
                    data["ism_company"] = name
                break

    return data if data else None
