"""Vessel, watchlist, merge, and port-call endpoints."""

from __future__ import annotations

import logging
import urllib.parse
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api._helpers import (
    _audit_log,
    _check_upload_size,
    _compute_data_age_hours,
    _compute_freshness_warning,
    _validate_date_range,
    limiter,
)
from app.auth import require_auth
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Vessels
# ---------------------------------------------------------------------------


@router.get("/vessels", tags=["vessels"])
def search_vessels(
    search: str | None = Query(None, description="MMSI, IMO, or vessel name"),
    flag: str | None = None,
    vessel_type: str | None = None,
    min_dwt: float | None = Query(None, description="Minimum deadweight tonnage"),
    max_dwt: float | None = Query(None, description="Maximum deadweight tonnage"),
    min_year_built: int | None = Query(None, description="Minimum year built"),
    watchlist_only: bool = Query(False, description="Only vessels on active watchlists"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Search vessels by MMSI, IMO, or name. Returns summary with last risk score."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist

    limit = min(limit, settings.MAX_QUERY_LIMIT)
    q = db.query(Vessel).filter(Vessel.merged_into_vessel_id == None)  # noqa: E711 — exclude absorbed
    matched_via_alias = {}  # vessel_id → absorbed MMSI that matched
    if search:
        # First try direct match on canonical vessels
        direct = q.filter(
            or_(
                Vessel.mmsi == search,
                Vessel.imo == search,
                Vessel.name.ilike(f"%{search}%"),
            )
        )
        if direct.count() == 0:
            # Search absorbed vessels and redirect to canonical
            absorbed = (
                db.query(Vessel)
                .filter(
                    Vessel.merged_into_vessel_id != None,  # noqa: E711
                    or_(
                        Vessel.mmsi == search,
                        Vessel.imo == search,
                        Vessel.name.ilike(f"%{search}%"),
                    ),
                )
                .all()
            )
            if absorbed:
                from app.modules.identity_resolver import resolve_canonical

                canonical_ids = set()
                for a in absorbed:
                    cid = resolve_canonical(a.vessel_id, db)
                    canonical_ids.add(cid)
                    matched_via_alias[cid] = a.mmsi
                q = db.query(Vessel).filter(Vessel.vessel_id.in_(canonical_ids))
            else:
                q = direct
        else:
            q = direct
    if flag:
        q = q.filter(Vessel.flag == flag.upper())
    if vessel_type:
        q = q.filter(Vessel.vessel_type.ilike(f"%{vessel_type}%"))
    if min_dwt is not None:
        q = q.filter(Vessel.deadweight >= min_dwt)
    if max_dwt is not None:
        q = q.filter(Vessel.deadweight <= max_dwt)
    if min_year_built is not None:
        q = q.filter(Vessel.year_built >= min_year_built)
    if watchlist_only:
        q = q.filter(
            Vessel.vessel_id.in_(
                db.query(VesselWatchlist.vessel_id).filter(VesselWatchlist.is_active)
            )
        )

    total = q.count()
    vessels = q.offset(skip).limit(limit).all()
    results = []
    for v in vessels:
        last_gap = (
            db.query(AISGapEvent)
            .filter(AISGapEvent.vessel_id == v.vessel_id)
            .order_by(AISGapEvent.risk_score.desc())
            .first()
        )
        on_watchlist = (
            db.query(VesselWatchlist)
            .filter(VesselWatchlist.vessel_id == v.vessel_id, VesselWatchlist.is_active)
            .first()
        ) is not None
        last_risk = last_gap.risk_score if last_gap else None
        stub_score = getattr(v, "watchlist_stub_score", None)
        # Compute effective_score: prefer gap score (even 0), fall back to stub score
        # IMPORTANT: use is-not-None check, NOT truthiness (last_risk_score=0 is valid)
        effective = last_risk if last_risk is not None else stub_score
        entry = {
            "vessel_id": v.vessel_id,
            "mmsi": v.mmsi,
            "imo": v.imo,
            "name": v.name,
            "flag": v.flag,
            "vessel_type": v.vessel_type,
            "deadweight": v.deadweight,
            "last_risk_score": last_risk,
            "watchlist_status": on_watchlist,
            "watchlist_stub_score": stub_score,
            "effective_score": effective,
        }
        if v.vessel_id in matched_via_alias:
            entry["matched_via_absorbed_mmsi"] = matched_via_alias[v.vessel_id]
        results.append(entry)
    return {"items": results, "total": total}


@router.get("/vessels/{vessel_id}/track.geojson", tags=["vessels"])
def get_vessel_track_geojson(
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    """Export vessel track as GeoJSON FeatureCollection."""
    from app.models.vessel import Vessel
    from app.modules.track_export import export_track_geojson

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    return export_track_geojson(db, vessel_id, date_from, date_to)


@router.get("/vessels/{vessel_id}/track.kml", tags=["vessels"])
def get_vessel_track_kml(
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    """Export vessel track as KML."""
    from fastapi.responses import Response

    from app.models.vessel import Vessel
    from app.modules.track_export import export_track_kml

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    kml_str = export_track_kml(
        db, vessel_id, vessel.name or f"Vessel {vessel_id}", date_from, date_to
    )
    return Response(content=kml_str, media_type="application/vnd.google-earth.kml+xml")


@router.get("/vessels/{vessel_id}", tags=["vessels"])
def get_vessel_detail(vessel_id: int, db: Session = Depends(get_db)):
    """Full vessel profile including watchlist, spoofing, loitering, STS, gap counts."""
    from app.models.gap_event import AISGapEvent
    from app.models.loitering_event import LoiteringEvent
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.sts_transfer import StsTransferEvent
    from app.models.vessel import Vessel
    from app.models.vessel_owner import VesselOwner
    from app.models.vessel_watchlist import VesselWatchlist
    from app.schemas.psc_detention import PscDetentionRead

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    now = datetime.now(UTC)

    # For absorbed (merged) vessels, return minimal detail with merged_into_vessel_id set.
    if vessel.merged_into_vessel_id is not None:
        from app.modules.identity_resolver import resolve_canonical

        canonical_id = resolve_canonical(vessel_id, db)
        return {
            "vessel_id": vessel.vessel_id,
            "mmsi": vessel.mmsi,
            "imo": vessel.imo,
            "name": vessel.name,
            "flag": vessel.flag,
            "vessel_type": vessel.vessel_type,
            "deadweight": vessel.deadweight,
            "year_built": vessel.year_built,
            "ais_class": str(vessel.ais_class.value)
            if hasattr(vessel.ais_class, "value")
            else vessel.ais_class,
            "flag_risk_category": str(vessel.flag_risk_category.value)
            if hasattr(vessel.flag_risk_category, "value")
            else vessel.flag_risk_category,
            "pi_coverage_status": str(vessel.pi_coverage_status.value)
            if hasattr(vessel.pi_coverage_status, "value")
            else vessel.pi_coverage_status,
            "psc_detained_last_12m": vessel.psc_detained_last_12m,
            "psc_detention_count": 0,
            "psc_latest_detention_date": None,
            "psc_detentions": [],
            "mmsi_first_seen_utc": vessel.mmsi_first_seen_utc,
            "vessel_laid_up_30d": vessel.vessel_laid_up_30d,
            "vessel_laid_up_60d": vessel.vessel_laid_up_60d,
            "vessel_laid_up_in_sts_zone": vessel.vessel_laid_up_in_sts_zone,
            "merged_into_vessel_id": canonical_id,
            "watchlist_entries": [],
            "spoofing_anomalies_30d": [],
            "loitering_events_30d": [],
            "sts_events_60d": [],
            "total_gaps_7d": 0,
            "total_gaps_30d": 0,
            "equasis_url": f"https://www.equasis.org/EquasisWeb/restricted/Search?P_IMO={vessel.imo}"
            if vessel.imo
            else None,
            "opencorporates_url": None,
            "data_age_hours": _compute_data_age_hours(vessel, now),
            "data_freshness_warning": _compute_freshness_warning(vessel, now),
            "watchlist_stub_score": None,
            "watchlist_stub_breakdown": None,
        }

    gaps_7d = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.vessel_id == vessel_id,
            AISGapEvent.gap_start_utc >= now - timedelta(days=7),
        )
        .count()
    )
    gaps_30d = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.vessel_id == vessel_id,
            AISGapEvent.gap_start_utc >= now - timedelta(days=30),
        )
        .count()
    )
    watchlist_entries = (
        db.query(VesselWatchlist).filter(VesselWatchlist.vessel_id == vessel_id).all()
    )
    spoofing_30d = (
        db.query(SpoofingAnomaly)
        .filter(
            SpoofingAnomaly.vessel_id == vessel_id,
            SpoofingAnomaly.start_time_utc >= now - timedelta(days=30),
        )
        .all()
    )
    loitering_30d = (
        db.query(LoiteringEvent)
        .filter(
            LoiteringEvent.vessel_id == vessel_id,
            LoiteringEvent.start_time_utc >= now - timedelta(days=30),
        )
        .all()
    )
    sts_60d = (
        db.query(StsTransferEvent)
        .filter(
            or_(
                StsTransferEvent.vessel_1_id == vessel_id,
                StsTransferEvent.vessel_2_id == vessel_id,
            ),
            StsTransferEvent.start_time_utc >= now - timedelta(days=60),
        )
        .all()
    )

    # PSC detention records (safe for mock objects)
    try:
        _psc_dets = list(vessel.psc_detentions) if vessel.psc_detentions else []
    except (TypeError, AttributeError):
        _psc_dets = []

    # External verification deep-links (Phase C15)
    equasis_url = None
    opencorporates_url = None
    if vessel.imo:
        equasis_url = f"https://www.equasis.org/EquasisWeb/restricted/Search?P_IMO={vessel.imo}"
    owner = db.query(VesselOwner).filter(VesselOwner.vessel_id == vessel_id).first()
    if owner and owner.owner_name and isinstance(owner.owner_name, str):
        opencorporates_url = (
            f"https://opencorporates.com/companies?q={urllib.parse.quote(owner.owner_name)}"
        )

    return {
        "vessel_id": vessel.vessel_id,
        "mmsi": vessel.mmsi,
        "imo": vessel.imo,
        "name": vessel.name,
        "flag": vessel.flag,
        "vessel_type": vessel.vessel_type,
        "deadweight": vessel.deadweight,
        "year_built": vessel.year_built,
        "ais_class": str(vessel.ais_class.value)
        if hasattr(vessel.ais_class, "value")
        else vessel.ais_class,
        "flag_risk_category": str(vessel.flag_risk_category.value)
        if hasattr(vessel.flag_risk_category, "value")
        else vessel.flag_risk_category,
        "pi_coverage_status": str(vessel.pi_coverage_status.value)
        if hasattr(vessel.pi_coverage_status, "value")
        else vessel.pi_coverage_status,
        "psc_detained_last_12m": vessel.psc_detained_last_12m,
        "psc_detention_count": len(_psc_dets),
        "psc_latest_detention_date": max(d.detention_date for d in _psc_dets).isoformat()
        if _psc_dets
        else None,
        "psc_detentions": [PscDetentionRead.model_validate(d).model_dump() for d in _psc_dets[:10]],
        "mmsi_first_seen_utc": vessel.mmsi_first_seen_utc,
        "vessel_laid_up_30d": vessel.vessel_laid_up_30d,
        "vessel_laid_up_60d": vessel.vessel_laid_up_60d,
        "vessel_laid_up_in_sts_zone": vessel.vessel_laid_up_in_sts_zone,
        "merged_into_vessel_id": None,
        "watchlist_entries": [
            {
                "watchlist_entry_id": w.watchlist_entry_id,
                "watchlist_source": w.watchlist_source,
                "reason": w.reason,
                "date_listed": w.date_listed,
                "is_active": w.is_active,
            }
            for w in watchlist_entries
        ],
        "spoofing_anomalies_30d": [
            {
                "anomaly_id": s.anomaly_id,
                "anomaly_type": str(s.anomaly_type.value)
                if hasattr(s.anomaly_type, "value")
                else s.anomaly_type,
                "start_time_utc": s.start_time_utc,
                "risk_score_component": s.risk_score_component,
            }
            for s in spoofing_30d
        ],
        "loitering_events_30d": [
            {
                "loiter_id": le.loiter_id,
                "start_time_utc": le.start_time_utc,
                "duration_hours": le.duration_hours,
                "corridor_id": le.corridor_id,
            }
            for le in loitering_30d
        ],
        "sts_events_60d": [
            {
                "sts_id": s.sts_id,
                "vessel_1_id": s.vessel_1_id,
                "vessel_2_id": s.vessel_2_id,
                "start_time_utc": s.start_time_utc,
                "detection_type": str(s.detection_type.value)
                if hasattr(s.detection_type, "value")
                else s.detection_type,
            }
            for s in sts_60d
        ],
        "total_gaps_7d": gaps_7d,
        "total_gaps_30d": gaps_30d,
        "equasis_url": equasis_url,
        "opencorporates_url": opencorporates_url,
        "data_age_hours": _compute_data_age_hours(vessel, now),
        "data_freshness_warning": _compute_freshness_warning(vessel, now),
        "watchlist_stub_score": vessel.watchlist_stub_score,
        "watchlist_stub_breakdown": vessel.watchlist_stub_breakdown,
    }


@router.get("/vessels/{vessel_id}/psc-detentions", tags=["vessels"])
def get_vessel_psc_detentions(vessel_id: int, db: Session = Depends(get_db)):
    """List PSC detentions for a vessel, ordered by date DESC."""
    from app.models.psc_detention import PscDetention
    from app.schemas.psc_detention import PscDetentionRead

    detentions = (
        db.query(PscDetention)
        .filter(PscDetention.vessel_id == vessel_id)
        .order_by(PscDetention.detention_date.desc())
        .all()
    )

    return [PscDetentionRead.model_validate(d).model_dump() for d in detentions]


@router.get("/vessels/{vessel_id}/alerts", tags=["vessels"])
def get_vessel_alerts(
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    sort_by: str = Query("gap_start_utc", description="gap_start_utc or risk_score"),
    sort_order: str = Query("desc"),
    db: Session = Depends(get_db),
):
    """All gap events for a vessel, sorted."""
    from app.models.gap_event import AISGapEvent

    _validate_date_range(date_from, date_to)
    q = db.query(AISGapEvent).filter(AISGapEvent.vessel_id == vessel_id)
    if date_from:
        q = q.filter(
            AISGapEvent.gap_start_utc >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            AISGapEvent.gap_start_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    sort_col = AISGapEvent.gap_start_utc if sort_by == "gap_start_utc" else AISGapEvent.risk_score
    q = q.order_by(sort_col.desc() if sort_order == "desc" else sort_col.asc())
    results = q.all()
    return {"items": results, "total": len(results)}


@router.get("/vessels/{vessel_id}/history", tags=["vessels"])
def get_vessel_history(vessel_id: int, db: Session = Depends(get_db)):
    """Identity change history for a vessel (renames, flag changes, etc.)."""
    from app.models.vessel import Vessel
    from app.models.vessel_history import VesselHistory
    from app.schemas.vessel_detail import VesselHistoryRead

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    history = (
        db.query(VesselHistory)
        .filter(VesselHistory.vessel_id == vessel_id)
        .order_by(VesselHistory.observed_at.desc())
        .all()
    )
    return [VesselHistoryRead.model_validate(h) for h in history]


@router.get("/vessels/{vessel_id}/watchlist", tags=["vessels"])
def get_vessel_watchlist_entries(vessel_id: int, db: Session = Depends(get_db)):
    """Active watchlist entries for a vessel."""
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist
    from app.schemas.vessel_detail import WatchlistEntryRead

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")
    entries = (
        db.query(VesselWatchlist)
        .filter(VesselWatchlist.vessel_id == vessel_id, VesselWatchlist.is_active)
        .all()
    )
    return [WatchlistEntryRead.model_validate(e) for e in entries]


@router.get("/vessels/{vessel_id}/timeline", tags=["vessels"])
def get_vessel_timeline_endpoint(
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Unified chronological event timeline for a vessel."""
    from app.models.vessel import Vessel
    from app.modules.identity_resolver import get_vessel_timeline

    _validate_date_range(date_from, date_to)
    vessel = db.query(Vessel).get(vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    start_dt = datetime(date_from.year, date_from.month, date_from.day) if date_from else None
    end_dt = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59) if date_to else None
    events = get_vessel_timeline(
        db, vessel_id, limit=limit, offset=offset, start_dt=start_dt, end_dt=end_dt
    )
    return {"vessel_id": vessel_id, "events": events, "count": len(events)}


@router.get("/vessels/{vessel_id}/aliases", tags=["vessels"])
def get_vessel_aliases_endpoint(vessel_id: int, db: Session = Depends(get_db)):
    """All MMSIs this vessel has used (current + absorbed identities)."""
    from app.models.vessel import Vessel
    from app.modules.identity_resolver import get_vessel_aliases

    vessel = db.query(Vessel).get(vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    aliases = get_vessel_aliases(db, vessel_id)
    return {"vessel_id": vessel_id, "aliases": aliases}


# ── Ownership verification (Phase C15) ───────────────────────────────────────


class OwnerUpdateRequest(BaseModel):
    owner_name: str | None = None
    is_sanctioned: bool | None = None
    source_url: str | None = None
    notes: str | None = None
    verified_by: str | None = None


@router.patch("/vessels/{vessel_id}/owner", tags=["vessels"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def update_vessel_owner(
    request: Request, vessel_id: int, body: OwnerUpdateRequest, db: Session = Depends(get_db), _auth: dict = Depends(require_auth)
):
    """Update or create vessel ownership verification record."""
    from app.models.vessel import Vessel
    from app.models.vessel_owner import VesselOwner

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    owner = db.query(VesselOwner).filter(VesselOwner.vessel_id == vessel_id).first()
    if not owner:
        owner = VesselOwner(vessel_id=vessel_id, owner_name=body.owner_name or "Unknown")
        db.add(owner)

    if body.owner_name is not None:
        owner.owner_name = body.owner_name
    if body.is_sanctioned is not None:
        owner.is_sanctioned = body.is_sanctioned
    if body.source_url is not None:
        owner.source_url = body.source_url
    if body.notes is not None:
        owner.verification_notes = body.notes
    if body.verified_by is not None:
        owner.verified_by = body.verified_by
        owner.verified_at = datetime.now(UTC)

    db.commit()
    return {"status": "updated", "vessel_id": vessel_id}


# ── Paid verification (Phase D17-19) ─────────────────────────────────────────


@router.post("/vessels/{vessel_id}/verify", tags=["vessels"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def verify_vessel_endpoint(
    request: Request, vessel_id: int, provider: str = "skylight", db: Session = Depends(get_db), _auth: dict = Depends(require_auth)
):
    """Trigger pay-per-query verification for a vessel."""
    from app.modules.paid_verification import verify_vessel

    result = verify_vessel(db, vessel_id, provider_name=provider)
    db.commit()
    return {
        "provider": result.provider,
        "success": result.success,
        "data": result.data,
        "cost_usd": result.cost_usd,
        "error": result.error,
    }


@router.get("/verification/budget", tags=["verification"])
def verification_budget(db: Session = Depends(get_db)):
    """Show current verification budget status."""
    from app.modules.paid_verification import get_budget_status

    return get_budget_status(db)


# ---------------------------------------------------------------------------
# Watchlist Management
# ---------------------------------------------------------------------------

from app.schemas.alerts import WatchlistAddRequest


@router.post("/watchlist", tags=["watchlist"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def add_to_watchlist(body: WatchlistAddRequest, request: Request, db: Session = Depends(get_db), _auth: dict = Depends(require_auth)):
    """Add a vessel to the local watchlist."""
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist

    vessel_id = body.vessel_id
    if not vessel_id:
        raise HTTPException(status_code=400, detail="vessel_id required")

    VALID_WATCHLIST_SOURCES = {
        "OFAC_SDN",
        "EU_COUNCIL",
        "KSE_SHADOW",
        "OPENSANCTIONS",
        "FLEETLEAKS",
        "UKRAINE_GUR",
        "LOCAL_INVESTIGATION",
        "MANUAL",
    }
    source = (body.watchlist_source or body.source or "LOCAL_INVESTIGATION").upper()
    if source not in VALID_WATCHLIST_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source '{source}'. Must be one of: {sorted(VALID_WATCHLIST_SOURCES)}",
        )

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    entry = VesselWatchlist(
        vessel_id=vessel_id,
        watchlist_source=source,
        reason=body.reason,
        is_active=True,
    )
    db.add(entry)
    _audit_log(
        db,
        "add",
        "watchlist",
        vessel_id,
        details={
            "reason": body.reason,
            "source": body.source,
        },
        request=request,
    )
    db.commit()
    return {"watchlist_entry_id": entry.watchlist_entry_id, "status": "added"}


@router.get("/watchlist", tags=["watchlist"])
def list_watchlist(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List all watchlist entries (paginated)."""
    from app.models.vessel_watchlist import VesselWatchlist

    q = db.query(VesselWatchlist).filter(VesselWatchlist.is_active)
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.delete("/watchlist/{watchlist_entry_id}", tags=["watchlist"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def remove_from_watchlist(watchlist_entry_id: int, request: Request, db: Session = Depends(get_db), _auth: dict = Depends(require_auth)):
    """Remove a watchlist entry (soft delete)."""
    from app.models.vessel_watchlist import VesselWatchlist

    entry = (
        db.query(VesselWatchlist)
        .filter(VesselWatchlist.watchlist_entry_id == watchlist_entry_id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    entry.is_active = False
    _audit_log(db, "remove", "watchlist", watchlist_entry_id, request=request)
    db.commit()
    return {"status": "removed"}


@router.post("/watchlist/import", tags=["watchlist"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
async def import_watchlist_file(
    request: Request,
    source: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Batch-import watchlist from CSV file. source: ofac | kse | opensanctions"""
    _check_upload_size(file)
    import os
    import tempfile

    from app.modules.watchlist_loader import load_kse_list, load_ofac_sdn, load_opensanctions

    valid_sources = {"ofac", "kse", "opensanctions"}
    if source not in valid_sources:
        raise HTTPException(status_code=422, detail=f"source must be one of {valid_sources}")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        loaders = {"ofac": load_ofac_sdn, "kse": load_kse_list, "opensanctions": load_opensanctions}
        count = loaders[source](db, tmp_path)
        return {"imported": count, "source": source}
    finally:
        os.unlink(tmp_path)


# ── Merge Candidate & Identity Merge Endpoints ──────────────────────────────


@router.get("/merge-candidates", tags=["merge"])
def list_merge_candidates(
    status: str | None = Query(
        None, description="Filter by status: pending, auto_merged, analyst_merged, rejected"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List merge candidates, optionally filtered by status."""
    from app.models.merge_candidate import MergeCandidate
    from app.models.vessel import Vessel

    q = db.query(MergeCandidate).order_by(MergeCandidate.confidence_score.desc())
    if status:
        q = q.filter(MergeCandidate.status == status)

    total = q.count()
    candidates = q.offset(skip).limit(limit).all()

    vessel_ids = set()
    for c in candidates:
        vessel_ids.add(c.vessel_a_id)
        vessel_ids.add(c.vessel_b_id)
    vessel_map: dict = {}
    if vessel_ids:
        vessels = db.query(Vessel).filter(Vessel.vessel_id.in_(vessel_ids)).all()
        vessel_map = {v.vessel_id: v for v in vessels}

    results = []
    for c in candidates:
        va = vessel_map.get(c.vessel_a_id)
        vb = vessel_map.get(c.vessel_b_id)
        results.append(
            {
                "candidate_id": c.candidate_id,
                "vessel_a": {
                    "vessel_id": c.vessel_a_id,
                    "mmsi": va.mmsi if va else None,
                    "name": va.name if va else None,
                },
                "vessel_b": {
                    "vessel_id": c.vessel_b_id,
                    "mmsi": vb.mmsi if vb else None,
                    "name": vb.name if vb else None,
                },
                "distance_nm": c.distance_nm,
                "time_delta_hours": c.time_delta_hours,
                "confidence_score": c.confidence_score,
                "match_reasons": c.match_reasons_json,
                "satellite_corroboration": c.satellite_corroboration_json,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
                "resolved_by": c.resolved_by,
            }
        )
    return {"items": results, "total": total}


@router.get("/merge-candidates/{candidate_id}", tags=["merge"])
def get_merge_candidate(candidate_id: int, db: Session = Depends(get_db)):
    """Get merge candidate detail with satellite corroboration."""
    from app.models.merge_candidate import MergeCandidate
    from app.models.vessel import Vessel

    c = db.query(MergeCandidate).get(candidate_id)
    if not c:
        raise HTTPException(status_code=404, detail="Merge candidate not found")

    va = db.query(Vessel).get(c.vessel_a_id)
    vb = db.query(Vessel).get(c.vessel_b_id)

    def _vessel_summary(v):
        if not v:
            return None
        return {
            "vessel_id": v.vessel_id,
            "mmsi": v.mmsi,
            "name": v.name,
            "flag": v.flag,
            "vessel_type": v.vessel_type,
            "deadweight": v.deadweight,
            "year_built": v.year_built,
        }

    return {
        "candidate_id": c.candidate_id,
        "vessel_a": _vessel_summary(va),
        "vessel_b": _vessel_summary(vb),
        "vessel_a_last_position": {
            "lat": c.vessel_a_last_lat,
            "lon": c.vessel_a_last_lon,
            "time": c.vessel_a_last_time.isoformat() if c.vessel_a_last_time else None,
        },
        "vessel_b_first_position": {
            "lat": c.vessel_b_first_lat,
            "lon": c.vessel_b_first_lon,
            "time": c.vessel_b_first_time.isoformat() if c.vessel_b_first_time else None,
        },
        "distance_nm": c.distance_nm,
        "time_delta_hours": c.time_delta_hours,
        "confidence_score": c.confidence_score,
        "match_reasons": c.match_reasons_json,
        "satellite_corroboration": c.satellite_corroboration_json,
        "status": c.status,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
        "resolved_by": c.resolved_by,
    }


@router.post("/merge-candidates/{candidate_id}/confirm", tags=["merge"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def confirm_merge_candidate(
    candidate_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Analyst confirms a merge candidate — executes the merge."""
    from app.models.base import MergeCandidateStatusEnum
    from app.models.merge_candidate import MergeCandidate
    from app.modules.identity_resolver import execute_merge

    c = db.query(MergeCandidate).get(candidate_id)
    if not c:
        raise HTTPException(status_code=404, detail="Merge candidate not found")
    if c.status != MergeCandidateStatusEnum.PENDING:
        raise HTTPException(status_code=400, detail=f"Candidate is {c.status}, not pending")

    canonical_id = min(c.vessel_a_id, c.vessel_b_id)
    absorbed_id = max(c.vessel_a_id, c.vessel_b_id)

    result = execute_merge(
        db,
        canonical_id,
        absorbed_id,
        reason=f"Analyst confirmed candidate {candidate_id}",
        merged_by="analyst",
        candidate_id=candidate_id,
        commit=False,
    )

    if result.get("success"):
        c.status = MergeCandidateStatusEnum.ANALYST_MERGED
        c.resolved_at = datetime.now(UTC)
        c.resolved_by = "analyst"
        _audit_log(
            db, "merge_candidate_confirmed", "merge_candidate", candidate_id, request=request
        )
        db.commit()
    else:
        raise HTTPException(status_code=400, detail=result.get("error", "Merge failed"))

    return result


@router.post("/merge-candidates/{candidate_id}/reject", tags=["merge"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def reject_merge_candidate(
    candidate_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Analyst rejects a merge candidate."""
    from app.models.base import MergeCandidateStatusEnum
    from app.models.merge_candidate import MergeCandidate

    c = db.query(MergeCandidate).get(candidate_id)
    if not c:
        raise HTTPException(status_code=404, detail="Merge candidate not found")
    if c.status != MergeCandidateStatusEnum.PENDING:
        raise HTTPException(status_code=400, detail=f"Candidate is {c.status}, not pending")

    c.status = MergeCandidateStatusEnum.REJECTED
    c.resolved_at = datetime.now(UTC)
    c.resolved_by = "analyst"
    _audit_log(db, "merge_candidate_rejected", "merge_candidate", candidate_id, request=request)
    db.commit()

    return {"status": "rejected", "candidate_id": candidate_id}


@router.post("/vessels/merge", tags=["merge"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def manual_merge_vessels(
    request: Request,
    vessel_a_id: int = Query(..., description="First vessel ID"),
    vessel_b_id: int = Query(..., description="Second vessel ID"),
    reason: str = Query("", description="Reason for merge"),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Manually merge two vessels (analyst-driven linking)."""
    from app.modules.identity_resolver import execute_merge

    result = execute_merge(
        db,
        vessel_a_id,
        vessel_b_id,
        reason=reason,
        merged_by="analyst",
        commit=False,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Merge failed"))

    _audit_log(db, "manual_vessel_merge", "vessel", result.get("merge_op_id"), request=request)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Port Calls
# ---------------------------------------------------------------------------


@router.get("/port-calls/{vessel_id}", tags=["port-calls"])
def get_port_calls(
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    """List port calls for a vessel."""
    from app.models.port import Port
    from app.models.port_call import PortCall
    from app.models.vessel import Vessel

    _validate_date_range(date_from, date_to)
    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    q = db.query(PortCall).filter(PortCall.vessel_id == vessel_id)
    if date_from:
        q = q.filter(
            PortCall.arrival_utc >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            PortCall.arrival_utc <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    port_calls = q.order_by(PortCall.arrival_utc.desc()).all()

    items = []
    for pc in port_calls:
        port = db.query(Port).filter(Port.port_id == pc.port_id).first() if pc.port_id else None
        items.append(
            {
                "port_call_id": pc.port_call_id,
                "vessel_id": pc.vessel_id,
                "port_id": pc.port_id,
                "port_name": port.name if port else getattr(pc, "raw_port_name", None),
                "arrival_utc": pc.arrival_utc.isoformat() if pc.arrival_utc else None,
                "departure_utc": pc.departure_utc.isoformat() if pc.departure_utc else None,
                "source": pc.source if hasattr(pc, "source") else None,
            }
        )

    return {"vessel_id": vessel_id, "items": items, "total": len(items)}


@router.get("/vessels/{vessel_id}/voyage-prediction", tags=["detection"])
def get_vessel_voyage_prediction(
    vessel_id: int,
    db: Session = Depends(get_db),
):
    """Return voyage prediction (next destination) for a vessel."""
    from app.modules.voyage_predictor import predict_next_destination_enriched

    result = predict_next_destination_enriched(db, vessel_id)
    if result is None:
        msg = "Insufficient port call history"
        return {"vessel_id": vessel_id, "prediction": None, "message": msg}
    return result
