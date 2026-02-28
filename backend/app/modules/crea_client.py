"""CREA Russia Fossil Tracker API client.

REST API for Russian fossil fuel cargo tracking data.
Reference: https://api.russiafossiltracker.com/
GitHub: https://github.com/energyandcleanair

Includes write-through persistence of voyage data to CreaVoyage table.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.crea_voyage import CreaVoyage

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


def _parse_date(val: str | None) -> datetime | None:
    """Parse an ISO-8601 date string to datetime, returning None on failure."""
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def import_crea_data(db: Session, limit: int = 100) -> dict:
    """Bulk fetch CREA data for known vessels and persist voyage records.

    Fetches voyage data from the CREA Russia Fossil Tracker API and
    writes through to the crea_voyages table with dedup via savepoints.
    """
    if not getattr(settings, "CREA_ENABLED", False):
        return {"queried": 0, "enriched": 0, "errors": 0, "voyages_stored": 0, "duplicates_skipped": 0}

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
    voyages_stored = 0
    duplicates_skipped = 0
    import_run_id = str(uuid.uuid4())

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

                    for v in voyages:
                        voyage = CreaVoyage(
                            vessel_id=vessel.vessel_id,
                            departure_port=v.get("departure_port"),
                            arrival_port=v.get("arrival_port"),
                            commodity=v.get("commodity"),
                            cargo_volume_tonnes=v.get("cargo_volume_tonnes"),
                            departure_date=_parse_date(v.get("departure_date")),
                            arrival_date=_parse_date(v.get("arrival_date")),
                            source_url=v.get("source_url"),
                            import_run_id=import_run_id,
                        )
                        savepoint = db.begin_nested()
                        try:
                            db.add(voyage)
                            savepoint.commit()
                            voyages_stored += 1
                        except IntegrityError:
                            savepoint.rollback()
                            duplicates_skipped += 1
        except Exception as e:
            errors += 1
            logger.warning("CREA fetch failed for %s: %s", vessel.imo, e)

    db.commit()
    logger.info(
        "CREA import: queried=%d enriched=%d errors=%d voyages_stored=%d duplicates_skipped=%d",
        queried, enriched, errors, voyages_stored, duplicates_skipped,
    )
    return {
        "queried": queried,
        "enriched": enriched,
        "errors": errors,
        "voyages_stored": voyages_stored,
        "duplicates_skipped": duplicates_skipped,
        "import_run_id": import_run_id,
    }
