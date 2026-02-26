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


def _is_likely_tanker(vessel) -> bool:
    """Check if vessel is likely a tanker based on vessel_type."""
    vtype = (getattr(vessel, "vessel_type", None) or "").lower()
    return "tanker" in vtype or "oil" in vtype or "chemical" in vtype or "lng" in vtype or "lpg" in vtype


def enrich_vessels_from_gfw(
    db: Session,
    token: str | None = None,
    limit: int = 50,
) -> dict:
    """Batch-enrich vessels missing critical metadata via GFW vessel search.

    Queries vessels where deadweight IS NULL and mmsi IS NOT NULL, then looks
    up each via GFW's vessel search API to populate imo, deadweight, year_built,
    and flag (if still missing).

    Args:
        db: SQLAlchemy session.
        token: GFW API bearer token (falls back to settings).
        limit: Max vessels to enrich per run.

    Returns:
        {"enriched": int, "failed": int, "skipped": int, "no_exact_match": int,
         "enriched_vessel_ids": list[int]}
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

    stats: dict = {"enriched": 0, "failed": 0, "skipped": 0, "no_exact_match": 0}
    enriched_ids: set[int] = set()

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

        # Pick best match: require exact MMSI match
        match = None
        for r in results:
            if str(r.get("mmsi")) == vessel.mmsi:
                match = r
                break
        if match is None:
            stats["no_exact_match"] += 1
            stats["skipped"] += 1
            time.sleep(_REQUEST_DELAY_S)
            continue  # Skip — wrong enrichment is worse than none

        changed = False

        if match.get("imo") and not vessel.imo:
            vessel.imo = str(match["imo"])
            changed = True

        # GFW returns tonnage_gt (Gross Tonnage), not DWT.
        # DWT ≈ 1.5× GT for tankers. For non-tankers, GT is a reasonable proxy.
        if match.get("tonnage_gt") and vessel.deadweight is None:
            gt = float(match["tonnage_gt"])
            if _is_likely_tanker(vessel):
                vessel.deadweight = gt * 1.5
            else:
                vessel.deadweight = gt
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
            enriched_ids.add(vessel.vessel_id)
        else:
            stats["skipped"] += 1

        time.sleep(_REQUEST_DELAY_S)

    db.commit()
    stats["enriched_vessel_ids"] = list(enriched_ids)
    logger.info("GFW vessel enrichment: %s", stats)
    return stats


def infer_ais_class(db: Session, vessel) -> str | None:
    """Infer AIS class from transmission intervals.

    Class A: 2-10s intervals (median ≤10s).
    Class B: 30s+ intervals (median >25s).
    Returns 'A', 'B', or None if insufficient data.
    """
    from app.models.ais_point import AISPoint

    points = (
        db.query(AISPoint)
        .filter(AISPoint.vessel_id == vessel.vessel_id)
        .order_by(AISPoint.timestamp_utc.desc())
        .limit(20)
        .all()
    )
    if len(points) < 5:
        return None

    intervals = [
        (points[i].timestamp_utc - points[i + 1].timestamp_utc).total_seconds()
        for i in range(len(points) - 1)
    ]
    # Filter out outliers (negative or very large gaps that represent actual AIS gaps)
    intervals = [iv for iv in intervals if 0 < iv < 600]
    if len(intervals) < 3:
        return None

    median = sorted(intervals)[len(intervals) // 2]
    if median > 25:
        return "B"
    if median <= 10:
        return "A"
    return None


def infer_ais_class_batch(db: Session) -> dict[str, int]:
    """Infer AIS class for all vessels with UNKNOWN class.

    Returns {"updated": int, "skipped": int}.
    """
    from app.models.vessel import Vessel
    from app.models.base import AISClassEnum

    vessels = (
        db.query(Vessel)
        .filter(Vessel.ais_class.in_([AISClassEnum.UNKNOWN, None]))
        .all()
    )

    stats = {"updated": 0, "skipped": 0}
    for vessel in vessels:
        inferred = infer_ais_class(db, vessel)
        if inferred:
            vessel.ais_class = inferred
            stats["updated"] += 1
        else:
            stats["skipped"] += 1

    db.commit()
    logger.info("AIS class inference: %s", stats)
    return stats


def infer_pi_coverage(db: Session) -> dict[str, int]:
    """P&I coverage inference disabled — circular double-counting with sanctions.

    The previous implementation inferred P&I lapsed from sanctions hits, but this
    created circular double-counting: sanctions hit -> infer P&I lapsed -> +20 pts,
    PLUS the vessel also fires watchlist_ofac -> +50 pts = +70 for a single OFAC listing.

    Until an external P&I API is available, this produces no-op results.
    Vessels keep their current pi_coverage_status (defaults to UNKNOWN).

    Returns {"lapsed": int, "unchanged": int}.
    """
    return {"lapsed": 0, "unchanged": 0}
