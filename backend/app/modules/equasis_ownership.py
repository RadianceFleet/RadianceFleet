"""Equasis ownership chain extraction — multi-hop corporate ownership discovery.

Orchestrates EquasisClient.get_ownership_chain() and imports results into
VesselOwner records via import_equasis_ownership().

Gated by EQUASIS_SCRAPING_ENABLED (disabled by default, ToS-sensitive).
When disabled, all functions return empty results gracefully.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.vessel import Vessel
from app.modules.ownership_graph import import_equasis_ownership

logger = logging.getLogger(__name__)


def _create_client():
    """Create EquasisClient, raising RuntimeError when scraping is disabled."""
    from app.modules.equasis_client import EquasisClient

    return EquasisClient()


def extract_ownership_chain(db: Session, vessel_id: int) -> dict[str, Any]:
    """Extract multi-hop ownership chain for a vessel from Equasis.

    For a vessel with an IMO number, calls EquasisClient to get ownership data,
    creates VesselOwner entries, and returns chain metadata.

    Returns:
        dict with chain data (vessel_id, imo, records, count) or empty dict
        when EQUASIS_SCRAPING_ENABLED=false or vessel has no IMO.
    """
    try:
        client = _create_client()
    except RuntimeError:
        logger.info("Equasis scraping disabled — skipping ownership extraction")
        return {}

    # Look up vessel and its IMO
    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if vessel is None:
        logger.warning("Vessel %d not found", vessel_id)
        return {}
    if not vessel.imo:
        logger.info("Vessel %d has no IMO — skipping Equasis ownership lookup", vessel_id)
        return {}

    # Fetch ownership chain from Equasis
    chain_data = client.get_ownership_chain(vessel.imo)
    if not chain_data:
        logger.info("No ownership data returned for vessel %d (IMO %s)", vessel_id, vessel.imo)
        return {"vessel_id": vessel_id, "imo": vessel.imo, "records": [], "count": 0}

    # Enforce max hops limit
    max_hops = settings.EQUASIS_OWNERSHIP_MAX_HOPS
    if len(chain_data) > max_hops:
        logger.info(
            "Truncating ownership chain from %d to %d entries (max hops)",
            len(chain_data),
            max_hops,
        )
        chain_data = chain_data[:max_hops]

    # Import into VesselOwner records
    records = import_equasis_ownership(db, vessel_id, chain_data)

    return {
        "vessel_id": vessel_id,
        "imo": vessel.imo,
        "records": [
            {
                "owner_name": r.owner_name,
                "ownership_type": r.ownership_type,
                "country": r.country,
            }
            for r in records
        ],
        "count": len(records),
    }


def batch_extract_ownership(
    db: Session,
    vessel_ids: list[int] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Batch extract ownership chains for multiple vessels.

    Args:
        db: SQLAlchemy session.
        vessel_ids: Specific vessel IDs to process, or None for all with IMO.
        limit: Maximum number of vessels to process in this batch.

    Returns:
        dict with processed count, results list, and errors count.
        Returns empty results when EQUASIS_SCRAPING_ENABLED=false.
    """
    try:
        _create_client()
    except RuntimeError:
        logger.info("Equasis scraping disabled — skipping batch ownership extraction")
        return {"processed": 0, "results": [], "errors": 0}

    # Build query for vessels with IMO numbers
    query = db.query(Vessel).filter(Vessel.imo.isnot(None), Vessel.imo != "")
    if vessel_ids is not None:
        query = query.filter(Vessel.vessel_id.in_(vessel_ids))
    vessels = query.limit(limit).all()

    results: list[dict] = []
    errors = 0
    rate_limit_s = settings.EQUASIS_OWNERSHIP_RATE_LIMIT_S

    for i, vessel in enumerate(vessels):
        try:
            result = extract_ownership_chain(db, vessel.vessel_id)
            if result:
                results.append(result)
        except Exception as exc:
            logger.warning(
                "Error extracting ownership for vessel %d: %s",
                vessel.vessel_id,
                exc,
            )
            errors += 1

        # Rate limiting between requests (skip after last)
        if i < len(vessels) - 1:
            time.sleep(rate_limit_s)

    return {
        "processed": len(vessels),
        "results": results,
        "errors": errors,
    }
