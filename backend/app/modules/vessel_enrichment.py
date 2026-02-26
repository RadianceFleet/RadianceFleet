"""Vessel metadata enrichment via GFW vessel search API.

AIS broadcasts only provide MMSI, name, lat/lon, SOG/COG. Critical scoring
fields (DWT, year_built, IMO) must be looked up from external registries.
This module batch-enriches vessels that are missing metadata using GFW's
vessel search endpoint.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Rate limit: ~1 request/sec to respect GFW API limits
_REQUEST_DELAY_S = 1.0


def enrich_vessels_from_gfw(
    db: Session,
    token: str | None = None,
    limit: int = 50,
) -> dict[str, int]:
    """Batch-enrich vessels missing critical metadata via GFW vessel search.

    Queries vessels where deadweight IS NULL and mmsi IS NOT NULL, then looks
    up each via GFW's vessel search API to populate imo, deadweight, year_built,
    and flag (if still missing).

    Args:
        db: SQLAlchemy session.
        token: GFW API bearer token (falls back to settings).
        limit: Max vessels to enrich per run.

    Returns:
        {"enriched": int, "failed": int, "skipped": int}
    """
    from app.models.vessel import Vessel
    from app.modules.gfw_client import search_vessel
    from app.utils.vessel_identity import flag_to_risk_category

    token = token or settings.GFW_API_TOKEN
    if not token:
        raise ValueError("GFW_API_TOKEN not configured")

    vessels = (
        db.query(Vessel)
        .filter(Vessel.deadweight == None, Vessel.mmsi != None)  # noqa: E711
        .limit(limit)
        .all()
    )

    stats = {"enriched": 0, "failed": 0, "skipped": 0}

    for vessel in vessels:
        try:
            results = search_vessel(vessel.mmsi, token=token)
        except Exception as exc:
            logger.warning("GFW search failed for MMSI %s: %s", vessel.mmsi, exc)
            stats["failed"] += 1
            time.sleep(_REQUEST_DELAY_S)
            continue

        if not results:
            stats["skipped"] += 1
            time.sleep(_REQUEST_DELAY_S)
            continue

        # Pick best match: prefer exact MMSI match
        match = None
        for r in results:
            if str(r.get("mmsi")) == vessel.mmsi:
                match = r
                break
        if match is None:
            match = results[0]

        changed = False

        if match.get("imo") and not vessel.imo:
            vessel.imo = str(match["imo"])
            changed = True

        # GFW returns tonnage_gt (Gross Tonnage), not DWT. For triage purposes
        # GT correlates well enough with DWT — tanker DWT ≈ 1.5-1.8× GT.
        # The scoring engine's DWT thresholds (>1000, ≥60K) still work because
        # GT > 1000 implies DWT > 1000, and large tanker GT maps to even larger DWT.
        if match.get("tonnage_gt") and vessel.deadweight is None:
            vessel.deadweight = float(match["tonnage_gt"])
            changed = True

        if match.get("flag") and not vessel.flag:
            vessel.flag = match["flag"]
            vessel.flag_risk_category = flag_to_risk_category(match["flag"])
            changed = True

        # year_built: GFW sometimes returns this in nested shipsData
        year = match.get("year_built")
        if year and vessel.year_built is None:
            vessel.year_built = int(year)
            changed = True

        if changed:
            stats["enriched"] += 1
        else:
            stats["skipped"] += 1

        time.sleep(_REQUEST_DELAY_S)

    db.commit()
    logger.info("GFW vessel enrichment: %s", stats)
    return stats
