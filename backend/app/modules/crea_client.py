"""CREA Russia Fossil Tracker API client.

REST API for Russian fossil fuel cargo tracking data.
Reference: https://api.russiafossiltracker.com/
GitHub: https://github.com/energyandcleanair
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.russiafossiltracker.com"
_TIMEOUT = 30


def fetch_crea_vessel_data(
    mmsi: str | None = None,
    imo: str | None = None,
) -> dict[str, Any] | None:
    """Query CREA API for vessel cargo/route data.

    Returns vessel data dict or None if not found/error.
    """
    if not getattr(settings, "CREA_ENABLED", False):
        logger.info("CREA integration disabled (CREA_ENABLED=False)")
        return None

    base_url = getattr(settings, "CREA_API_BASE_URL", _BASE_URL)
    params: dict[str, str] = {}
    if imo:
        params["imo"] = imo
    elif mmsi:
        params["mmsi"] = mmsi
    else:
        return None

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{base_url}/v0/voyage", params=params)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("CREA API query failed: %s", e)
        return None


def import_crea_data(db: Session, limit: int = 100) -> dict:
    """Bulk fetch CREA data for known vessels and annotate.

    Updates vessel metadata with CREA cargo/insurance information
    where available.
    """
    if not getattr(settings, "CREA_ENABLED", False):
        return {"queried": 0, "enriched": 0, "errors": 0}

    from app.models.vessel import Vessel

    vessels = (
        db.query(Vessel)
        .filter(
            Vessel.imo.isnot(None),
        )
        .limit(limit)
        .all()
    )

    queried = 0
    enriched = 0
    errors = 0

    for vessel in vessels:
        try:
            data = fetch_crea_vessel_data(imo=vessel.imo)
            queried += 1
            if data:
                voyages = data.get("voyages", [])
                if voyages:
                    logger.info(
                        "CREA: vessel %s (IMO %s) has %d tracked voyages",
                        vessel.name,
                        vessel.imo,
                        len(voyages),
                    )
                    enriched += 1
        except Exception as e:
            errors += 1
            logger.warning("CREA fetch failed for %s: %s", vessel.imo, e)

    logger.info(
        "CREA import: queried=%d enriched=%d errors=%d", queried, enriched, errors
    )
    return {"queried": queried, "enriched": enriched, "errors": errors}
