"""Vessel metadata enrichment via GFW vessel search API.

AIS broadcasts only provide MMSI, name, lat/lon, SOG/COG. Critical scoring
fields (DWT, year_built, IMO) must be looked up from external registries.
This module batch-enriches vessels that are missing metadata using GFW's
vessel search endpoint.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.vessel_history import VesselHistory

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
    limit: int = 200,
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

    from sqlalchemy import or_

    vessels = (
        db.query(Vessel)
        .filter(
            or_(
                Vessel.deadweight == None,  # noqa: E711
                Vessel.is_heuristic_dwt == True,  # noqa: E712  # Re-enrich if only heuristic DWT
                Vessel.imo == None,  # noqa: E711
                Vessel.callsign == None,  # noqa: E711
            ),
            Vessel.mmsi != None,  # noqa: E711
        )
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
            # Provenance for rollback
            if not db.query(VesselHistory).filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.field_changed == "imo",
                VesselHistory.source == "gfw_enrichment_fill",
            ).first():
                db.add(VesselHistory(
                    vessel_id=vessel.vessel_id, field_changed="imo",
                    old_value="", new_value=str(match["imo"]),
                    observed_at=datetime.utcnow(), source="gfw_enrichment_fill",
                ))

        # Store vessel_type BEFORE DWT computation so _is_likely_tanker() sees
        # the correct type when GFW is the first source for vessel_type.
        if match.get("vessel_type") and not vessel.vessel_type:
            vessel.vessel_type = match["vessel_type"]
            changed = True

        # GFW returns tonnage_gt (Gross Tonnage), not DWT.
        # DWT ≈ 1.5× GT for tankers. For non-tankers, GT is a reasonable proxy.
        if match.get("tonnage_gt") and (vessel.deadweight is None or getattr(vessel, "is_heuristic_dwt", False)):
            gt = float(match["tonnage_gt"])
            vessel.deadweight = gt * 1.5 if _is_likely_tanker(vessel) else gt
            vessel.is_heuristic_dwt = True  # Mark as derived from GT heuristic
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

        if match.get("callsign") and not vessel.callsign:
            vessel.callsign = match["callsign"]
            changed = True
            if not db.query(VesselHistory).filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.field_changed == "callsign",
                VesselHistory.source == "gfw_enrichment_fill",
            ).first():
                db.add(VesselHistory(
                    vessel_id=vessel.vessel_id, field_changed="callsign",
                    old_value="", new_value=match["callsign"],
                    observed_at=datetime.utcnow(), source="gfw_enrichment_fill",
                ))

        # Populate VesselHistory from GFW identity_history
        from sqlalchemy.exc import IntegrityError
        for hist in match.get("identity_history", []):
            try:
                _obs_at = datetime.fromisoformat(hist["date_from"].replace("Z", "+00:00"))
                _obs_at = _obs_at.replace(tzinfo=None)  # naive UTC
            except (ValueError, AttributeError):
                continue

            for field, hist_key in [("imo", "imo"), ("name", "name"), ("callsign", "callsign"), ("flag", "flag")]:
                hist_val = hist.get(hist_key)
                if not hist_val or not str(hist_val).strip():
                    continue
                sp = db.begin_nested()
                try:
                    db.add(VesselHistory(
                        vessel_id=vessel.vessel_id, field_changed=field,
                        old_value="", new_value=str(hist_val).strip(),
                        observed_at=_obs_at, source="gfw_enrichment_history",
                    ))
                    sp.commit()
                except IntegrityError:
                    sp.rollback()

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


def populate_gfw_identity_history(
    db: Session,
    limit: int = 200,
    token: str | None = None,
) -> dict:
    """Populate VesselHistory from GFW identity_history for vessels that have no prior history rows.

    Selects vessels with MMSI that have NO existing VesselHistory row with
    source='gfw_enrichment_history' (NOT EXISTS subquery avoids re-fetching).
    Only writes identity_history entries — no metadata field updates whatsoever.

    Args:
        db: SQLAlchemy session.
        limit: Max vessels to process per run.
        token: GFW API bearer token (falls back to settings).

    Returns:
        {"processed": int, "written": int, "skipped": int}
          processed — number of vessels API was called for
          written   — total VesselHistory rows inserted
          skipped   — vessels skipped because NOT EXISTS check found existing history rows
    """
    from app.models.vessel import Vessel
    from app.modules.gfw_client import search_vessel
    from sqlalchemy import not_, exists
    from sqlalchemy.exc import IntegrityError

    history_exists = exists().where(
        VesselHistory.vessel_id == Vessel.vessel_id,
        VesselHistory.source == "gfw_enrichment_history",
    )
    candidates = (
        db.query(Vessel)
        .filter(
            Vessel.mmsi.isnot(None),
            Vessel.merged_into_vessel_id.is_(None),
            not_(history_exists),
        )
        .order_by(Vessel.vessel_id)
        .limit(limit)
        .all()
    )

    total_vessels = db.query(Vessel).filter(Vessel.mmsi.isnot(None), Vessel.merged_into_vessel_id.is_(None)).count()
    skipped = total_vessels - db.query(Vessel).filter(
        Vessel.mmsi.isnot(None),
        Vessel.merged_into_vessel_id.is_(None),
        not_(history_exists),
    ).count()
    # Cap skipped to what actually falls outside the limit window
    # More precisely: skipped = vessels that had history (not in candidates list)
    # We calculate it as (all eligible - candidates before limit) but since we apply limit,
    # just track actual skipped as those not included due to existing history.
    # Recalculate: skipped is vessels with mmsi+not merged that DO have history
    skipped_count = (
        db.query(Vessel)
        .filter(
            Vessel.mmsi.isnot(None),
            Vessel.merged_into_vessel_id.is_(None),
            history_exists,
        )
        .count()
    )

    stats: dict = {"processed": 0, "written": 0, "skipped": skipped_count}

    for vessel in candidates:
        try:
            results = search_vessel(vessel.mmsi, token=token)
        except Exception as exc:
            logger.warning("GFW identity history fetch failed for MMSI %s: %s", vessel.mmsi, exc)
            stats["processed"] += 1
            time.sleep(_REQUEST_DELAY_S)
            continue

        stats["processed"] += 1

        # Find exact MMSI match
        match = None
        if results:
            for r in results:
                if str(r.get("mmsi")) == vessel.mmsi:
                    match = r
                    break

        if match is None:
            time.sleep(_REQUEST_DELAY_S)
            continue

        # Write identity_history only — no metadata field updates
        for hist in match.get("identity_history", []):
            try:
                _obs_at = datetime.fromisoformat(hist["date_from"].replace("Z", "+00:00"))
                _obs_at = _obs_at.replace(tzinfo=None)
            except (ValueError, AttributeError):
                continue

            for field, hist_key in [("imo", "imo"), ("name", "name"), ("callsign", "callsign"), ("flag", "flag")]:
                hist_val = hist.get(hist_key)
                if not hist_val or not str(hist_val).strip():
                    continue
                sp = db.begin_nested()
                try:
                    db.add(VesselHistory(
                        vessel_id=vessel.vessel_id, field_changed=field,
                        old_value="", new_value=str(hist_val).strip(),
                        observed_at=_obs_at, source="gfw_enrichment_history",
                    ))
                    sp.commit()
                    stats["written"] += 1
                except IntegrityError:
                    sp.rollback()

        time.sleep(_REQUEST_DELAY_S)

    db.commit()
    logger.info("GFW identity history population: %s", stats)
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


def enrich_vessels_from_equasis(
    db: Session,
    limit: int = 100,
    watchlist_only: bool = False,
) -> dict:
    """Enrich vessel metadata from Equasis (opt-in — requires EQUASIS_SCRAPING_ENABLED=true).

    Prioritizes watchlisted vessels first (they benefit most from enrichment).
    Selects vessels where DWT is NULL or is_heuristic_dwt=True, AND IMO is known.

    WARNING: Equasis ToS prohibits automated access. This function is disabled by
    default (EQUASIS_SCRAPING_ENABLED=false). For production use, Datalastic API
    (https://datalastic.com) is the recommended ToS-compliant alternative.

    Args:
        db: SQLAlchemy session.
        limit: Max vessels to enrich per run.
        watchlist_only: If True, restrict to actively watchlisted vessels only.

    Returns:
        {"enriched": int, "failed": int, "skipped": int}
        or {"enriched": 0, "failed": 0, "skipped": 0, "disabled": True} when opt-in flag is off.
    """
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist
    from app.utils.vessel_identity import flag_to_risk_category
    from app.modules.equasis_client import EquasisClient
    from sqlalchemy import or_

    try:
        client = EquasisClient()
    except RuntimeError as exc:
        logger.warning("Equasis enrichment skipped: %s", exc)
        return {"enriched": 0, "failed": 0, "skipped": 0, "disabled": True}

    # Watchlisted vessel IDs (always loaded for sorting; also used as filter when watchlist_only)
    watchlisted_ids = {
        row.vessel_id for row in db.query(VesselWatchlist.vessel_id)
        .filter(VesselWatchlist.is_active == True).all()  # noqa: E712
    }

    # Select vessels that need enrichment
    q = (
        db.query(Vessel)
        .filter(
            or_(
                Vessel.deadweight == None,  # noqa: E711
                Vessel.is_heuristic_dwt == True,  # noqa: E712
            ),
            Vessel.imo != None,  # noqa: E711  — Equasis requires IMO
        )
    )
    if watchlist_only:
        q = q.filter(Vessel.vessel_id.in_(watchlisted_ids))
    vessels = q.all()

    # Sort: watchlisted first, then by vessel_id
    vessels.sort(key=lambda v: (0 if v.vessel_id in watchlisted_ids else 1, v.vessel_id))
    vessels = vessels[:limit]

    stats = {"enriched": 0, "failed": 0, "skipped": 0}
    _SOURCE = "equasis_enrichment"

    for vessel in vessels:
        # Try IMO search first; fall back to MMSI
        data = client.search_by_imo(vessel.imo)
        if data is None and vessel.mmsi:
            data = client.search_by_mmsi(vessel.mmsi)
        if data is None:
            stats["skipped"] += 1
            continue

        changed = False

        # DWT — prefer authoritative Equasis value over heuristic
        if "dwt" in data and data["dwt"]:
            try:
                new_dwt = float(data["dwt"].replace(",", ""))
                old_dwt = vessel.deadweight
                if old_dwt != new_dwt:
                    vessel.deadweight = new_dwt
                    vessel.is_heuristic_dwt = False  # Authoritative source
                    db.add(VesselHistory(
                        vessel_id=vessel.vessel_id, field_changed="deadweight",
                        old_value=str(old_dwt) if old_dwt is not None else "",
                        new_value=str(new_dwt),
                        observed_at=datetime.utcnow(), source=_SOURCE,
                    ))
                    changed = True
            except (ValueError, AttributeError):
                pass

        # vessel_type
        if "vessel_type" in data and data["vessel_type"] and not vessel.vessel_type:
            vessel.vessel_type = data["vessel_type"]
            db.add(VesselHistory(
                vessel_id=vessel.vessel_id, field_changed="vessel_type",
                old_value="", new_value=data["vessel_type"],
                observed_at=datetime.utcnow(), source=_SOURCE,
            ))
            changed = True

        # year_built
        if "year_built" in data and data["year_built"] and vessel.year_built is None:
            try:
                vessel.year_built = int(data["year_built"])
                db.add(VesselHistory(
                    vessel_id=vessel.vessel_id, field_changed="year_built",
                    old_value="", new_value=data["year_built"],
                    observed_at=datetime.utcnow(), source=_SOURCE,
                ))
                changed = True
            except ValueError:
                pass

        # flag — only update if missing
        if "flag" in data and data["flag"] and not vessel.flag:
            vessel.flag = data["flag"]
            vessel.flag_risk_category = flag_to_risk_category(data["flag"])
            changed = True

        if changed:
            stats["enriched"] += 1
        else:
            stats["skipped"] += 1

    if stats["enriched"] > 0:
        db.commit()

    logger.info(
        "Equasis enrichment complete: enriched=%d failed=%d skipped=%d",
        stats["enriched"], stats["failed"], stats["skipped"],
    )
    return stats
