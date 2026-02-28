from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, File, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.config import settings
from app.schemas.corridor import CorridorCreateRequest, CorridorUpdateRequest
from app.schemas.alerts import BulkStatusUpdateRequest, WatchlistAddRequest, NoteAddRequest

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# H2: Coverage Quality — load coverage.yaml at module level
# ---------------------------------------------------------------------------
import re
import yaml
from pathlib import Path
from unidecode import unidecode as _unidecode

_COVERAGE_DATA: dict = {}
# config/ is at repo root (one level above backend/)
_coverage_path = Path(__file__).resolve().parents[3] / "config" / "coverage.yaml"
if not _coverage_path.exists():
    # Fallback: try relative to backend/
    _coverage_path = Path(__file__).resolve().parents[2] / "config" / "coverage.yaml"
if _coverage_path.exists():
    with open(_coverage_path) as f:
        _COVERAGE_DATA = yaml.safe_load(f) or {}

_REGION_MATCH_ORDER: list[tuple[str, list[str]]] = [
    ("Nakhodka", ["nakhodka"]),
    ("Baltic", ["baltic", "primorsk", "ust luga", "kaliningrad", "oresund", "great belt", "murmansk"]),
    ("Turkish Straits", ["turkish", "bosphorus", "dardanelles", "ceyhan", "iskenderun"]),
    ("Black Sea", ["black sea", "kavkaz", "novorossiysk", "crimea", "bulgaria"]),
    ("Persian Gulf", ["hormuz", "fujairah", "khor al zubair", "basra", "gulf of oman"]),
    ("Singapore", ["singapore", "tanjung pelepas", "malacca"]),
    ("Mediterranean", ["mediterranean", "ceuta", "gibraltar", "laconian", "malta", "hurd", "ain sukhna", "nador", "cyprus", "syria", "ras lanuf"]),
    ("Far East", ["kozmino", "east china", "de kastri", "sakhalin", "daesan", "onsan", "ulsan", "yeosu", "shandong"]),
]


def _get_coverage_quality(corridor_name: str) -> str:
    """Map corridor name to coverage quality from coverage.yaml."""
    normalized = re.sub(r"[^a-z0-9 ]", " ", _unidecode(corridor_name).casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for region, keywords in _REGION_MATCH_ORDER:
        for kw in keywords:
            if kw in normalized:
                region_data = _COVERAGE_DATA.get(region, {})
                return region_data.get("quality", "UNKNOWN")
    return "UNKNOWN"


def _compute_data_age_hours(vessel, now) -> Optional[float]:
    """Compute data age in hours from vessel.last_ais_received_utc."""
    try:
        last_utc = getattr(vessel, "last_ais_received_utc", None)
        if last_utc is None or not isinstance(last_utc, datetime):
            return None
        tz_last = last_utc.replace(tzinfo=timezone.utc) if last_utc.tzinfo is None else last_utc
        return round((now - tz_last).total_seconds() / 3600, 1)
    except (TypeError, AttributeError):
        return None


def _compute_freshness_warning(vessel, now) -> Optional[str]:
    """Return warning string if vessel AIS data is stale (>48h)."""
    try:
        last_utc = getattr(vessel, "last_ais_received_utc", None)
        if last_utc is None or not isinstance(last_utc, datetime):
            return None
        tz_last = last_utc.replace(tzinfo=timezone.utc) if last_utc.tzinfo is None else last_utc
        if (now - tz_last).total_seconds() > 48 * 3600:
            return "AIS data is more than 48 hours old"
        return None
    except (TypeError, AttributeError):
        return None


def _audit_log(db: Session, action: str, entity_type: str, entity_id: int = None,
               details: dict = None, request: Request = None) -> None:
    """Record an analyst action for audit trail (PRD NFR5)."""
    from app.models.audit_log import AuditLog
    log = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        user_agent=request.headers.get("user-agent") if request else None,
        ip_address=request.client.host if request and request.client else None,
    )
    db.add(log)


def _validate_date_range(date_from: Optional[date], date_to: Optional[date]) -> None:
    """Reject if date_from is after date_to."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be <= date_to")


def _check_upload_size(file: UploadFile) -> None:
    """Reject uploads exceeding MAX_UPLOAD_SIZE_MB."""
    file.file.seek(0, 2)  # seek to end
    size_mb = file.file.tell() / (1024 * 1024)
    file.file.seek(0)  # reset
    if size_mb > settings.MAX_UPLOAD_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size_mb:.1f} MB). Max: {settings.MAX_UPLOAD_SIZE_MB} MB.",
        )


# ---------------------------------------------------------------------------
# AIS Ingestion
# ---------------------------------------------------------------------------

@router.post("/ais/import", tags=["ingestion"])
def import_ais(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Ingest AIS records from CSV. Updates ingestion status on app.state."""
    from app.modules.ingest import ingest_ais_csv

    _check_upload_size(file)

    # Update in-memory status
    state = getattr(request.app, "state", None)
    if state:
        request.app.state.ingestion_status = {
            "status": "running",
            "file_name": file.filename,
            "processed": 0,
            "accepted": 0,
            "rejected": 0,
            "percent_complete": None,
        }

    try:
        result = ingest_ais_csv(file.file, db)
        if state:
            request.app.state.ingestion_status = {
                "status": "completed",
                "file_name": file.filename,
                "processed": result["accepted"] + result["rejected"] + result["duplicates"],
                "accepted": result["accepted"],
                "rejected": result["rejected"],
                "percent_complete": 100.0,
            }
        _audit_log(db, "ais_import", "ingestion", details={
            "file_name": file.filename,
            "accepted": result.get("accepted", 0),
            "rejected": result.get("rejected", 0),
        }, request=request)
        db.commit()
        return result
    except Exception as e:
        if state:
            request.app.state.ingestion_status = {
                "status": "failed",
                "file_name": file.filename,
                "processed": 0,
                "accepted": 0,
                "rejected": 0,
                "error": str(e),
            }
        raise


@router.get("/ingestion-status", tags=["ingestion"])
def ingestion_status(request: Request):
    """Return current AIS ingestion job status."""
    state = getattr(request.app, "state", None)
    status = getattr(state, "ingestion_status", None) if state else None
    if status is None:
        return {"status": "idle", "processed": 0, "accepted": 0, "rejected": 0}
    return status


# ---------------------------------------------------------------------------
# Gap Detection
# ---------------------------------------------------------------------------

@router.post("/gaps/detect", tags=["detection"])
def detect_gaps(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Run AIS gap detection over the specified date range."""
    from app.modules.gap_detector import run_gap_detection
    return run_gap_detection(db, date_from=date_from, date_to=date_to)


# ---------------------------------------------------------------------------
# Spoofing Detection
# ---------------------------------------------------------------------------

@router.post("/spoofing/detect", tags=["detection"])
def detect_spoofing(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Run AIS spoofing detection (impossible speed, anchor-in-ocean, circle spoof, etc.)."""
    from app.modules.gap_detector import run_spoofing_detection
    return run_spoofing_detection(db, date_from=date_from, date_to=date_to)


@router.get("/spoofing/{vessel_id}", tags=["detection"])
def get_spoofing_events(vessel_id: int, db: Session = Depends(get_db)):
    from app.models.spoofing_anomaly import SpoofingAnomaly
    return db.query(SpoofingAnomaly).filter(SpoofingAnomaly.vessel_id == vessel_id).all()


@router.get("/loitering/{vessel_id}", tags=["detection"])
def get_loitering_events(vessel_id: int, db: Session = Depends(get_db)):
    from app.models.loitering_event import LoiteringEvent
    return db.query(LoiteringEvent).filter(LoiteringEvent.vessel_id == vessel_id).all()


@router.get("/sts-events", tags=["detection"])
def get_sts_events(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    from app.models.sts_transfer import StsTransferEvent
    limit = min(limit, settings.MAX_QUERY_LIMIT)
    q = db.query(StsTransferEvent).order_by(StsTransferEvent.start_time_utc.desc())
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@router.get("/alerts", tags=["alerts"])
def list_alerts(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    corridor_id: Optional[int] = None,
    vessel_id: Optional[int] = None,
    vessel_name: Optional[str] = None,
    min_score: Optional[int] = None,
    status: Optional[str] = None,
    sort_by: str = Query("risk_score", description="Field to sort by: risk_score|gap_start_utc|duration_minutes|vessel_name"),
    sort_order: str = Query("desc", description="asc or desc"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List AIS gap alerts with sorting and filtering."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    _validate_date_range(date_from, date_to)

    limit = min(limit, settings.MAX_QUERY_LIMIT)

    q = db.query(AISGapEvent).options(
        joinedload(AISGapEvent.vessel),
        joinedload(AISGapEvent.start_point),
    )
    if date_from:
        q = q.filter(AISGapEvent.gap_start_utc >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(AISGapEvent.gap_end_utc <= datetime.combine(date_to, datetime.max.time()))
    if corridor_id is not None:
        q = q.filter(AISGapEvent.corridor_id == corridor_id)
    if vessel_id is not None:
        q = q.filter(AISGapEvent.vessel_id == vessel_id)
    if vessel_name:
        q = q.join(Vessel, AISGapEvent.vessel_id == Vessel.vessel_id).filter(
            Vessel.name.ilike(f"%{vessel_name}%")
        )
    if min_score is not None:
        q = q.filter(AISGapEvent.risk_score >= min_score)
    if status:
        q = q.filter(AISGapEvent.status == status)

    sort_col_map = {
        "risk_score": AISGapEvent.risk_score,
        "gap_start_utc": AISGapEvent.gap_start_utc,
        "duration_minutes": AISGapEvent.duration_minutes,
    }
    sort_col = sort_col_map.get(sort_by, AISGapEvent.risk_score)
    if sort_order == "asc":
        q = q.order_by(sort_col.asc())
    else:
        q = q.order_by(sort_col.desc())

    total = q.count()
    results = q.offset(skip).limit(limit).all()
    items = []
    for r in results:
        item = {
            c.name: getattr(r, c.name) for c in r.__table__.columns
        }
        item["last_lat"] = r.start_point.lat if r.start_point else None
        item["last_lon"] = r.start_point.lon if r.start_point else None
        item["vessel_name"] = r.vessel.name if r.vessel else None
        item["vessel_mmsi"] = r.vessel.mmsi if r.vessel else None
        items.append(item)
    return {"items": items, "total": total}


@router.get("/alerts/map", tags=["alerts"])
def list_alert_map_points(db: Session = Depends(get_db)):
    """Lightweight map-only projection: returns only fields needed for map markers."""
    from app.models.gap_event import AISGapEvent

    results = (
        db.query(AISGapEvent)
        .options(joinedload(AISGapEvent.vessel), joinedload(AISGapEvent.start_point))
        .order_by(AISGapEvent.risk_score.desc())
        .limit(min(500, settings.MAX_QUERY_LIMIT))
        .all()
    )
    return {
        "points": [
            {
                "gap_event_id": r.gap_event_id,
                "last_lat": r.start_point.lat if r.start_point else None,
                "last_lon": r.start_point.lon if r.start_point else None,
                "risk_score": r.risk_score,
                "vessel_name": r.vessel.name if r.vessel else None,
                "gap_start_utc": r.gap_start_utc,
                "duration_minutes": r.duration_minutes,
            }
            for r in results
        ]
    }


@router.get("/alerts/export", tags=["alerts"])
def export_alerts_csv(
    status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    min_score: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Bulk export alerts as publication-ready CSV."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    _validate_date_range(date_from, date_to)

    q = db.query(AISGapEvent)
    if status:
        q = q.filter(AISGapEvent.status == status)
    if date_from:
        q = q.filter(AISGapEvent.gap_start_utc >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(AISGapEvent.gap_end_utc <= datetime.combine(date_to, datetime.max.time()))
    if min_score is not None:
        q = q.filter(AISGapEvent.risk_score >= min_score)

    alerts = q.order_by(AISGapEvent.risk_score.desc()).all()

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "alert_id", "vessel_mmsi", "vessel_name", "flag", "dwt",
            "gap_start_utc", "gap_end_utc", "duration_hours",
            "corridor_name", "risk_score", "status", "analyst_notes",
        ])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        try:
            for alert in alerts:
                vessel = alert.vessel
                corridor = alert.corridor
                writer.writerow([
                    alert.gap_event_id,
                    vessel.mmsi if vessel else "",
                    vessel.name if vessel else "",
                    vessel.flag if vessel else "",
                    vessel.deadweight if vessel else "",
                    alert.gap_start_utc.isoformat() if alert.gap_start_utc else "",
                    alert.gap_end_utc.isoformat() if alert.gap_end_utc else "",
                    round(alert.duration_minutes / 60, 2) if alert.duration_minutes else "",
                    corridor.name if corridor else "",
                    alert.risk_score,
                    alert.status,
                    alert.analyst_notes or "",
                ])
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
        except Exception as e:
            logger.error("CSV export error mid-stream: %s", e, exc_info=True)

    filename = f"radiancefleet_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/alerts/{alert_id}", tags=["alerts"])
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel
    from app.models.corridor import Corridor
    from app.models.movement_envelope import MovementEnvelope
    from app.models.satellite_check import SatelliteCheck
    from app.models.ais_point import AISPoint
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.loitering_event import LoiteringEvent
    from app.models.sts_transfer import StsTransferEvent
    from app.schemas.gap_event import (
        GapEventDetailRead, MovementEnvelopeRead, SatelliteCheckSummary, AISPointSummary,
        SpoofingAnomalySummary, LoiteringSummary, StsSummary,
    )
    import json

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    vessel = db.query(Vessel).filter(Vessel.vessel_id == alert.vessel_id).first()
    corridor = (
        db.query(Corridor).filter(Corridor.corridor_id == alert.corridor_id).first()
        if alert.corridor_id else None
    )
    envelope = db.query(MovementEnvelope).filter(
        MovementEnvelope.gap_event_id == alert_id
    ).first()
    sat_check = db.query(SatelliteCheck).filter(
        SatelliteCheck.gap_event_id == alert_id
    ).first()
    last_pt = (
        db.query(AISPoint).filter(AISPoint.ais_point_id == alert.start_point_id).first()
        if alert.start_point_id else None
    )
    first_pt = (
        db.query(AISPoint).filter(AISPoint.ais_point_id == alert.end_point_id).first()
        if alert.end_point_id else None
    )

    envelope_data = None
    if envelope:
        geojson_str = None
        if envelope.confidence_ellipse_geometry is not None:
            try:
                from app.utils.geo import load_geometry
                import shapely.geometry
                shape = load_geometry(envelope.confidence_ellipse_geometry)
                if shape is not None:
                    geojson_str = json.dumps(shapely.geometry.mapping(shape))
            except Exception:
                pass
        envelope_data = MovementEnvelopeRead(
            envelope_id=envelope.envelope_id,
            max_plausible_distance_nm=envelope.max_plausible_distance_nm,
            actual_gap_distance_nm=envelope.actual_gap_distance_nm,
            velocity_plausibility_ratio=envelope.velocity_plausibility_ratio,
            envelope_semi_major_nm=envelope.envelope_semi_major_nm,
            envelope_semi_minor_nm=envelope.envelope_semi_minor_nm,
            envelope_heading_degrees=envelope.envelope_heading_degrees,
            confidence_ellipse_geojson=json.loads(geojson_str) if geojson_str else None,
            interpolated_positions_json=envelope.interpolated_positions_json,
            estimated_method=str(envelope.estimated_method.value)
                if hasattr(envelope.estimated_method, "value") else envelope.estimated_method,
        )

    # Alert enrichment: linked anomalies
    spoofing_list = None
    if alert.vessel_id:
        spoofing_raw = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == alert.vessel_id,
            SpoofingAnomaly.start_time_utc >= alert.gap_start_utc - timedelta(days=1),
            SpoofingAnomaly.start_time_utc <= alert.gap_end_utc + timedelta(days=1),
        ).all()
        if spoofing_raw:
            spoofing_list = [SpoofingAnomalySummary(
                anomaly_id=s.anomaly_id,
                anomaly_type=str(s.anomaly_type.value) if hasattr(s.anomaly_type, "value") else str(s.anomaly_type),
                start_time_utc=s.start_time_utc,
                risk_score_component=s.risk_score_component,
                evidence_json=s.evidence_json,
            ) for s in spoofing_raw]

    loitering_list = None
    if alert.vessel_id:
        loitering_raw = db.query(LoiteringEvent).filter(
            LoiteringEvent.vessel_id == alert.vessel_id,
            LoiteringEvent.start_time_utc >= alert.gap_start_utc - timedelta(days=7),
            LoiteringEvent.start_time_utc <= alert.gap_end_utc + timedelta(days=7),
        ).all()
        if loitering_raw:
            loitering_list = [LoiteringSummary(
                loiter_id=le.loiter_id,
                start_time_utc=le.start_time_utc,
                duration_hours=le.duration_hours,
                mean_lat=le.mean_lat,
                mean_lon=le.mean_lon,
                median_sog_kn=le.median_sog_kn,
            ) for le in loitering_raw]

    sts_list = None
    if alert.vessel_id:
        sts_raw = db.query(StsTransferEvent).filter(
            or_(
                StsTransferEvent.vessel_1_id == alert.vessel_id,
                StsTransferEvent.vessel_2_id == alert.vessel_id,
            ),
            StsTransferEvent.start_time_utc >= alert.gap_start_utc - timedelta(days=7),
            StsTransferEvent.start_time_utc <= alert.gap_end_utc + timedelta(days=7),
        ).all()
        if sts_raw:
            sts_list = []
            for s in sts_raw:
                partner_id = s.vessel_2_id if s.vessel_1_id == alert.vessel_id else s.vessel_1_id
                partner = db.query(Vessel).filter(Vessel.vessel_id == partner_id).first()
                sts_list.append(StsSummary(
                    sts_id=s.sts_id,
                    partner_name=partner.name if partner else None,
                    partner_mmsi=partner.mmsi if partner else None,
                    detection_type=str(s.detection_type.value) if hasattr(s.detection_type, "value") else str(s.detection_type),
                    start_time_utc=s.start_time_utc,
                ))

    # H3: Prior similar count
    prior_count = db.query(func.count(AISGapEvent.gap_event_id)).filter(
        AISGapEvent.vessel_id == alert.vessel_id,
        AISGapEvent.corridor_id == alert.corridor_id,
        AISGapEvent.gap_event_id != alert.gap_event_id,
        AISGapEvent.gap_start_utc >= alert.gap_start_utc - timedelta(days=90),
        AISGapEvent.gap_start_utc < alert.gap_start_utc,
    ).scalar() or 0

    status_val = str(alert.status.value) if hasattr(alert.status, "value") else str(alert.status)
    return GapEventDetailRead(
        gap_event_id=alert.gap_event_id,
        vessel_id=alert.vessel_id,
        gap_start_utc=alert.gap_start_utc,
        gap_end_utc=alert.gap_end_utc,
        duration_minutes=alert.duration_minutes,
        corridor_id=alert.corridor_id,
        risk_score=alert.risk_score,
        risk_breakdown_json=alert.risk_breakdown_json,
        status=status_val,
        analyst_notes=alert.analyst_notes,
        impossible_speed_flag=alert.impossible_speed_flag,
        velocity_plausibility_ratio=alert.velocity_plausibility_ratio,
        max_plausible_distance_nm=alert.max_plausible_distance_nm,
        actual_gap_distance_nm=alert.actual_gap_distance_nm,
        in_dark_zone=alert.in_dark_zone,
        vessel_name=vessel.name if vessel else None,
        vessel_mmsi=vessel.mmsi if vessel else None,
        vessel_flag=vessel.flag if vessel else None,
        vessel_deadweight=vessel.deadweight if vessel else None,
        corridor_name=corridor.name if corridor else None,
        movement_envelope=envelope_data,
        satellite_check=SatelliteCheckSummary.model_validate(sat_check) if sat_check else None,
        last_point=AISPointSummary(
            timestamp_utc=last_pt.timestamp_utc, lat=last_pt.lat,
            lon=last_pt.lon, sog=last_pt.sog, cog=last_pt.cog
        ) if last_pt else None,
        first_point_after=AISPointSummary(
            timestamp_utc=first_pt.timestamp_utc, lat=first_pt.lat,
            lon=first_pt.lon, sog=first_pt.sog, cog=first_pt.cog
        ) if first_pt else None,
        spoofing_anomalies=spoofing_list,
        loitering_events=loitering_list,
        sts_events=sts_list,
        prior_similar_count=prior_count,
        is_recurring_pattern=prior_count >= 3,
    )


@router.post("/alerts/{alert_id}/status", tags=["alerts"])
def update_alert_status(
    alert_id: int,
    body: dict,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Explicitly set alert status. Body: {status: '...', reason: '...'}"""
    from app.models.gap_event import AISGapEvent
    from app.models.base import AlertStatusEnum
    from app.schemas.gap_event import AlertStatusUpdate
    body = AlertStatusUpdate(**body)

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    valid_statuses = [e.value for e in AlertStatusEnum]
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")

    old_status = alert.status
    alert.status = body.status
    if body.reason:
        existing_notes = alert.analyst_notes or ""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        alert.analyst_notes = f"{existing_notes}\n[{timestamp}] Status → {body.status}: {body.reason}".strip()

    _audit_log(db, "status_change", "alert", alert_id,
               {"old_status": old_status, "new_status": body.status, "reason": body.reason}, request)
    db.commit()
    return {"status": "ok", "new_status": body.status}


@router.post("/alerts/{alert_id}/notes", tags=["alerts"])
def add_note(alert_id: int, body: NoteAddRequest, db: Session = Depends(get_db)):
    from app.models.gap_event import AISGapEvent

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    # Accept both "notes" (frontend) and "text" (legacy) keys
    alert.analyst_notes = body.notes if body.notes is not None else (body.text or "")
    db.commit()
    return {"status": "ok"}


@router.post("/alerts/bulk-status", tags=["alerts"])
def bulk_update_alert_status(payload: BulkStatusUpdateRequest, db: Session = Depends(get_db)):
    """Bulk-update alert statuses for triage workflow."""
    from app.models.gap_event import AISGapEvent

    alert_ids = payload.alert_ids
    new_status = payload.status
    valid_statuses = {"new", "under_review", "needs_satellite_check", "documented", "dismissed"}

    if new_status not in valid_statuses:
        raise HTTPException(status_code=422, detail=f"Invalid status '{new_status}'. Must be one of: {', '.join(sorted(valid_statuses))}")

    updated = db.query(AISGapEvent).filter(
        AISGapEvent.gap_event_id.in_(alert_ids)
    ).update({"status": new_status}, synchronize_session="fetch")
    db.commit()
    return {"updated": updated}


@router.post("/alerts/{alert_id}/satellite-check", tags=["alerts"])
def prepare_satellite_check(alert_id: int, db: Session = Depends(get_db)):
    from app.modules.satellite_query import prepare_satellite_check as _prepare
    return _prepare(alert_id, db)


@router.post("/alerts/{alert_id}/export", tags=["alerts"])
def export_evidence(alert_id: int, format: str = "json", request: Request = None, db: Session = Depends(get_db)):
    from app.modules.evidence_export import export_evidence_card
    result = export_evidence_card(alert_id, format, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    _audit_log(db, "evidence_export", "alert", alert_id, {"format": format}, request)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Vessels
# ---------------------------------------------------------------------------

@router.get("/vessels", tags=["vessels"])
def search_vessels(
    search: Optional[str] = Query(None, description="MMSI, IMO, or vessel name"),
    flag: Optional[str] = None,
    vessel_type: Optional[str] = None,
    min_dwt: Optional[float] = Query(None, description="Minimum deadweight tonnage"),
    max_dwt: Optional[float] = Query(None, description="Maximum deadweight tonnage"),
    min_year_built: Optional[int] = Query(None, description="Minimum year built"),
    watchlist_only: bool = Query(False, description="Only vessels on active watchlists"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Search vessels by MMSI, IMO, or name. Returns summary with last risk score."""
    from app.models.vessel import Vessel
    from app.models.gap_event import AISGapEvent
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
                db.query(VesselWatchlist.vessel_id).filter(VesselWatchlist.is_active == True)
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
            .filter(VesselWatchlist.vessel_id == v.vessel_id, VesselWatchlist.is_active == True)
            .first()
        ) is not None
        entry = {
            "vessel_id": v.vessel_id,
            "mmsi": v.mmsi,
            "imo": v.imo,
            "name": v.name,
            "flag": v.flag,
            "vessel_type": v.vessel_type,
            "deadweight": v.deadweight,
            "last_risk_score": last_gap.risk_score if last_gap else None,
            "watchlist_status": on_watchlist,
        }
        if v.vessel_id in matched_via_alias:
            entry["matched_via_absorbed_mmsi"] = matched_via_alias[v.vessel_id]
        results.append(entry)
    return {"items": results, "total": total}


@router.get("/vessels/{vessel_id}", tags=["vessels"])
def get_vessel_detail(vessel_id: int, db: Session = Depends(get_db)):
    """Full vessel profile including watchlist, spoofing, loitering, STS, gap counts."""
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.loitering_event import LoiteringEvent
    from app.models.sts_transfer import StsTransferEvent
    from app.models.gap_event import AISGapEvent

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    # Absorbed vessel: return redirect info instead of 404
    _merged_into = vessel.merged_into_vessel_id
    if _merged_into is not None:
        from app.modules.identity_resolver import resolve_canonical
        canonical_id = resolve_canonical(vessel_id, db)
        return {
            "merged": True,
            "canonical_vessel_id": canonical_id,
            "redirect_url": f"/vessels/{canonical_id}",
            "absorbed_mmsi": vessel.mmsi,
        }

    now = datetime.now(timezone.utc)

    gaps_7d = db.query(AISGapEvent).filter(
        AISGapEvent.vessel_id == vessel_id,
        AISGapEvent.gap_start_utc >= now - timedelta(days=7),
    ).count()
    gaps_30d = db.query(AISGapEvent).filter(
        AISGapEvent.vessel_id == vessel_id,
        AISGapEvent.gap_start_utc >= now - timedelta(days=30),
    ).count()
    watchlist_entries = db.query(VesselWatchlist).filter(
        VesselWatchlist.vessel_id == vessel_id
    ).all()
    spoofing_30d = db.query(SpoofingAnomaly).filter(
        SpoofingAnomaly.vessel_id == vessel_id,
        SpoofingAnomaly.start_time_utc >= now - timedelta(days=30),
    ).all()
    loitering_30d = db.query(LoiteringEvent).filter(
        LoiteringEvent.vessel_id == vessel_id,
        LoiteringEvent.start_time_utc >= now - timedelta(days=30),
    ).all()
    sts_60d = db.query(StsTransferEvent).filter(
        or_(
            StsTransferEvent.vessel_1_id == vessel_id,
            StsTransferEvent.vessel_2_id == vessel_id,
        ),
        StsTransferEvent.start_time_utc >= now - timedelta(days=60),
    ).all()

    return {
        "vessel_id": vessel.vessel_id,
        "mmsi": vessel.mmsi,
        "imo": vessel.imo,
        "name": vessel.name,
        "flag": vessel.flag,
        "vessel_type": vessel.vessel_type,
        "deadweight": vessel.deadweight,
        "year_built": vessel.year_built,
        "ais_class": str(vessel.ais_class.value) if hasattr(vessel.ais_class, "value") else vessel.ais_class,
        "flag_risk_category": str(vessel.flag_risk_category.value) if hasattr(vessel.flag_risk_category, "value") else vessel.flag_risk_category,
        "pi_coverage_status": str(vessel.pi_coverage_status.value) if hasattr(vessel.pi_coverage_status, "value") else vessel.pi_coverage_status,
        "psc_detained_last_12m": vessel.psc_detained_last_12m,
        "mmsi_first_seen_utc": vessel.mmsi_first_seen_utc,
        "vessel_laid_up_30d": vessel.vessel_laid_up_30d,
        "vessel_laid_up_60d": vessel.vessel_laid_up_60d,
        "vessel_laid_up_in_sts_zone": vessel.vessel_laid_up_in_sts_zone,
        "watchlist_entries": [
            {"watchlist_entry_id": w.watchlist_entry_id, "watchlist_source": w.watchlist_source,
             "reason": w.reason, "date_listed": w.date_listed, "is_active": w.is_active}
            for w in watchlist_entries
        ],
        "spoofing_anomalies_30d": [
            {"anomaly_id": s.anomaly_id, "anomaly_type": str(s.anomaly_type.value) if hasattr(s.anomaly_type, "value") else s.anomaly_type,
             "start_time_utc": s.start_time_utc, "risk_score_component": s.risk_score_component}
            for s in spoofing_30d
        ],
        "loitering_events_30d": [
            {"loiter_id": le.loiter_id, "start_time_utc": le.start_time_utc,
             "duration_hours": le.duration_hours, "corridor_id": le.corridor_id}
            for le in loitering_30d
        ],
        "sts_events_60d": [
            {"sts_id": s.sts_id, "vessel_1_id": s.vessel_1_id, "vessel_2_id": s.vessel_2_id,
             "start_time_utc": s.start_time_utc, "detection_type": str(s.detection_type.value) if hasattr(s.detection_type, "value") else s.detection_type}
            for s in sts_60d
        ],
        "total_gaps_7d": gaps_7d,
        "total_gaps_30d": gaps_30d,
        "data_age_hours": _compute_data_age_hours(vessel, now),
        "data_freshness_warning": _compute_freshness_warning(vessel, now),
    }


@router.get("/vessels/{vessel_id}/alerts", tags=["vessels"])
def get_vessel_alerts(
    vessel_id: int,
    sort_by: str = Query("gap_start_utc", description="gap_start_utc or risk_score"),
    sort_order: str = Query("desc"),
    db: Session = Depends(get_db),
):
    """All gap events for a vessel, sorted."""
    from app.models.gap_event import AISGapEvent

    q = db.query(AISGapEvent).filter(AISGapEvent.vessel_id == vessel_id)
    sort_col = AISGapEvent.gap_start_utc if sort_by == "gap_start_utc" else AISGapEvent.risk_score
    q = q.order_by(sort_col.desc() if sort_order == "desc" else sort_col.asc())
    return q.all()


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
        .filter(VesselWatchlist.vessel_id == vessel_id, VesselWatchlist.is_active == True)
        .all()
    )
    return [WatchlistEntryRead.model_validate(e) for e in entries]


# ---------------------------------------------------------------------------
# Watchlist Import
# ---------------------------------------------------------------------------

@router.post("/watchlist/import", tags=["watchlist"])
async def import_watchlist_file(
    source: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Batch-import watchlist from CSV file. source: ofac | kse | opensanctions"""
    _check_upload_size(file)
    from app.modules.watchlist_loader import load_ofac_sdn, load_kse_list, load_opensanctions
    import tempfile
    import os

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


# ---------------------------------------------------------------------------
# Corridors
# ---------------------------------------------------------------------------

@router.get("/corridors", tags=["corridors"])
def list_corridors(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List all corridors with recent alert stats."""
    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent

    limit = min(limit, settings.MAX_QUERY_LIMIT)
    q = db.query(Corridor)
    total = q.count()
    corridors = q.offset(skip).limit(limit).all()
    now = datetime.now(timezone.utc)
    result = []
    for c in corridors:
        alert_7d = db.query(AISGapEvent).filter(
            AISGapEvent.corridor_id == c.corridor_id,
            AISGapEvent.gap_start_utc >= now - timedelta(days=7),
        ).count()
        alert_30d = db.query(AISGapEvent).filter(
            AISGapEvent.corridor_id == c.corridor_id,
            AISGapEvent.gap_start_utc >= now - timedelta(days=30),
        ).count()
        avg_score = db.query(func.avg(AISGapEvent.risk_score)).filter(
            AISGapEvent.corridor_id == c.corridor_id,
        ).scalar()
        result.append({
            "corridor_id": c.corridor_id,
            "name": c.name,
            "corridor_type": str(c.corridor_type.value) if hasattr(c.corridor_type, "value") else c.corridor_type,
            "risk_weight": c.risk_weight,
            "is_jamming_zone": c.is_jamming_zone,
            "description": c.description,
            "alert_count_7d": alert_7d,
            "alert_count_30d": alert_30d,
            "avg_risk_score": round(float(avg_score), 1) if avg_score else None,
            "coverage_quality": _get_coverage_quality(c.name),
        })
    return {"items": result, "total": total}


@router.get("/corridors/{corridor_id}", tags=["corridors"])
def get_corridor(corridor_id: int, db: Session = Depends(get_db)):
    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if not corridor:
        raise HTTPException(status_code=404, detail="Corridor not found")

    now = datetime.now(timezone.utc)
    alert_7d = db.query(AISGapEvent).filter(
        AISGapEvent.corridor_id == corridor_id,
        AISGapEvent.gap_start_utc >= now - timedelta(days=7),
    ).count()
    alert_30d = db.query(AISGapEvent).filter(
        AISGapEvent.corridor_id == corridor_id,
        AISGapEvent.gap_start_utc >= now - timedelta(days=30),
    ).count()

    return {
        "corridor_id": corridor.corridor_id,
        "name": corridor.name,
        "corridor_type": str(corridor.corridor_type.value) if hasattr(corridor.corridor_type, "value") else corridor.corridor_type,
        "risk_weight": corridor.risk_weight,
        "is_jamming_zone": corridor.is_jamming_zone,
        "description": corridor.description,
        "alert_count_7d": alert_7d,
        "alert_count_30d": alert_30d,
        "coverage_quality": _get_coverage_quality(corridor.name),
    }


@router.get("/corridors/geojson", tags=["corridors"])
def corridors_geojson(db: Session = Depends(get_db)):
    """Return all corridors as a GeoJSON FeatureCollection for map overlay."""
    from app.models.corridor import Corridor

    from app.utils.geo import load_geometry
    import shapely.geometry

    corridors = db.query(Corridor).all()
    features = []
    for c in corridors:
        geom_json = None
        try:
            shape = load_geometry(c.geometry)
            if shape is not None:
                geom_json = shapely.geometry.mapping(shape)
        except Exception:
            pass  # graceful degradation — geometry unavailable

        if geom_json:
            ct = str(c.corridor_type.value) if hasattr(c.corridor_type, "value") else c.corridor_type
            features.append({
                "type": "Feature",
                "geometry": geom_json,
                "properties": {
                    "corridor_id": c.corridor_id,
                    "name": c.name,
                    "corridor_type": ct,
                    "is_jamming_zone": c.is_jamming_zone,
                    "risk_weight": c.risk_weight,
                },
            })

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Dashboard Stats
# ---------------------------------------------------------------------------

@router.get("/stats", tags=["dashboard"])
def get_stats(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Dashboard statistics for the analyst overview."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    q = db.query(AISGapEvent)
    if date_from:
        q = q.filter(AISGapEvent.gap_start_utc >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(AISGapEvent.gap_end_utc <= datetime.combine(date_to, datetime.max.time()))

    all_alerts = q.all()

    total = len(all_alerts)
    critical = sum(1 for a in all_alerts if a.risk_score >= 76)
    high = sum(1 for a in all_alerts if 51 <= a.risk_score < 76)
    medium = sum(1 for a in all_alerts if 21 <= a.risk_score < 51)
    low = sum(1 for a in all_alerts if a.risk_score < 21)

    by_status: dict[str, int] = {}
    for a in all_alerts:
        s = str(a.status.value) if hasattr(a.status, "value") else str(a.status)
        by_status[s] = by_status.get(s, 0) + 1

    by_corridor: dict[str, int] = {}
    for a in all_alerts:
        key = str(a.corridor_id) if a.corridor_id else "no_corridor"
        by_corridor[key] = by_corridor.get(key, 0) + 1

    # Vessels with multiple gaps in last 7 days
    now = datetime.now(timezone.utc)
    multi_gap_subq = (
        db.query(AISGapEvent.vessel_id)
        .filter(AISGapEvent.gap_start_utc >= now - timedelta(days=7))
        .group_by(AISGapEvent.vessel_id)
        .having(func.count(AISGapEvent.gap_event_id) >= 2)
        .subquery()
    )
    multi_gap_vessels = db.query(func.count()).select_from(multi_gap_subq).scalar() or 0

    distinct_vessels = db.query(func.count(func.distinct(AISGapEvent.vessel_id))).scalar() or 0

    return {
        "alert_counts": {
            "total": total,
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
        },
        "by_status": by_status,
        "by_corridor": by_corridor,
        "vessels_with_multiple_gaps_7d": multi_gap_vessels,
        "distinct_vessels": distinct_vessels,
    }


# ---------------------------------------------------------------------------
# Watchlist Management
# ---------------------------------------------------------------------------

@router.post("/watchlist", tags=["watchlist"])
def add_to_watchlist(body: WatchlistAddRequest, request: Request, db: Session = Depends(get_db)):
    """Add a vessel to the local watchlist."""
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist

    vessel_id = body.vessel_id
    if not vessel_id:
        raise HTTPException(status_code=400, detail="vessel_id required")

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    entry = VesselWatchlist(
        vessel_id=vessel_id,
        watchlist_source=body.watchlist_source or body.source or "LOCAL_INVESTIGATION",
        reason=body.reason,
        is_active=True,
    )
    db.add(entry)
    _audit_log(db, "add", "watchlist", vessel_id, details={
        "reason": body.reason, "source": body.source,
    }, request=request)
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
    return db.query(VesselWatchlist).filter(
        VesselWatchlist.is_active == True
    ).offset(skip).limit(limit).all()


@router.delete("/watchlist/{watchlist_entry_id}", tags=["watchlist"])
def remove_from_watchlist(watchlist_entry_id: int, request: Request, db: Session = Depends(get_db)):
    """Remove a watchlist entry (soft delete)."""
    from app.models.vessel_watchlist import VesselWatchlist
    entry = db.query(VesselWatchlist).filter(
        VesselWatchlist.watchlist_entry_id == watchlist_entry_id
    ).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    entry.is_active = False
    _audit_log(db, "remove", "watchlist", watchlist_entry_id, request=request)
    db.commit()
    return {"status": "removed"}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@router.post("/score-alerts", tags=["scoring"])
def score_alerts(db: Session = Depends(get_db)):
    """Score all unscored gap events."""
    from app.modules.risk_scoring import score_all_alerts
    return score_all_alerts(db)


@router.post("/rescore-all-alerts", tags=["scoring"])
def rescore_all_alerts(db: Session = Depends(get_db)):
    """Clear and re-compute all risk scores (use after config changes)."""
    from app.modules.risk_scoring import rescore_all_alerts as _rescore
    return _rescore(db)


# ---------------------------------------------------------------------------
# Detection — Loitering and STS
# ---------------------------------------------------------------------------

@router.post("/loitering/detect", tags=["detection"])
def detect_loitering(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Run loitering detection and update laid-up vessel flags."""
    from app.modules.loitering_detector import run_loitering_detection, detect_laid_up_vessels
    result = run_loitering_detection(db, date_from=date_from, date_to=date_to)
    laid_up = detect_laid_up_vessels(db)
    result["laid_up_updated"] = laid_up.get("laid_up_updated", 0)
    return result


@router.post("/sts/detect", tags=["detection"])
def detect_sts(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Run ship-to-ship transfer detection.

    NOTE: Run /gaps/detect first — one_vessel_dark_during_proximity (+15) requires
    gap records to be present before STS detection runs.
    """
    from app.modules.sts_detector import detect_sts_events
    return detect_sts_events(db, date_from=date_from, date_to=date_to)


# ---------------------------------------------------------------------------
# Corridor CRUD (analyst-managed)
# ---------------------------------------------------------------------------

@router.post("/corridors", tags=["corridors"])
def create_corridor(body: CorridorCreateRequest, request: Request, db: Session = Depends(get_db)):
    """Create a new corridor. Body fields: name, corridor_type, risk_weight, description, is_jamming_zone, geometry_wkt (optional)."""
    from app.models.corridor import Corridor
    from app.models.base import CorridorTypeEnum

    ct_str = body.corridor_type
    valid_types = [e.value for e in CorridorTypeEnum]
    if ct_str not in valid_types:
        raise HTTPException(status_code=400, detail=f"corridor_type must be one of: {valid_types}")

    geom = None
    if body.geometry_wkt:
        try:
            from shapely import wkt as shapely_wkt
            shape = shapely_wkt.loads(body.geometry_wkt)
            geom = shape.wkt  # Store as WKT text
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid geometry_wkt: {e}")

    corridor = Corridor(
        name=body.name,
        corridor_type=ct_str,
        risk_weight=body.risk_weight,
        description=body.description,
        is_jamming_zone=body.is_jamming_zone,
        geometry=geom,
    )
    db.add(corridor)
    db.flush()
    _audit_log(db, "create", "corridor", corridor.corridor_id, details={
        "name": body.name, "corridor_type": ct_str,
    }, request=request)
    db.commit()
    return {"corridor_id": corridor.corridor_id, "status": "created"}


@router.patch("/corridors/{corridor_id}", tags=["corridors"])
def update_corridor(corridor_id: int, body: CorridorUpdateRequest, request: Request, db: Session = Depends(get_db)):
    """Update corridor metadata (name, risk_weight, description, corridor_type, is_jamming_zone).
    Geometry is not updatable via API to prevent accidental spatial data corruption."""
    from app.models.corridor import Corridor
    from app.models.base import CorridorTypeEnum

    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if not corridor:
        raise HTTPException(status_code=404, detail="Corridor not found")

    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        corridor.name = updates["name"]
    if "risk_weight" in updates:
        corridor.risk_weight = float(updates["risk_weight"])
    if "description" in updates:
        corridor.description = updates["description"]
    if "is_jamming_zone" in updates:
        corridor.is_jamming_zone = bool(updates["is_jamming_zone"])
    if "corridor_type" in updates:
        valid_types = [e.value for e in CorridorTypeEnum]
        if updates["corridor_type"] not in valid_types:
            raise HTTPException(status_code=400, detail=f"corridor_type must be one of: {valid_types}")
        corridor.corridor_type = updates["corridor_type"]

    _audit_log(db, "update", "corridor", corridor_id, details=updates, request=request)
    db.commit()
    return {
        "corridor_id": corridor.corridor_id,
        "name": corridor.name,
        "corridor_type": str(corridor.corridor_type.value) if hasattr(corridor.corridor_type, "value") else corridor.corridor_type,
        "risk_weight": corridor.risk_weight,
        "is_jamming_zone": corridor.is_jamming_zone,
        "status": "updated",
    }


@router.delete("/corridors/{corridor_id}", tags=["corridors"])
def delete_corridor(corridor_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete a corridor. Returns 409 if gap events are linked to it."""
    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent

    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if not corridor:
        raise HTTPException(status_code=404, detail="Corridor not found")

    linked_gaps = db.query(AISGapEvent).filter(
        AISGapEvent.corridor_id == corridor_id
    ).count()
    if linked_gaps > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete corridor: {linked_gaps} gap event(s) reference it. "
                   "Unlink or reassign those gaps first.",
        )

    _audit_log(db, "delete", "corridor", corridor_id, details={"name": corridor.name}, request=request)
    db.delete(corridor)
    db.commit()
    return {"status": "deleted", "corridor_id": corridor_id}


# ---------------------------------------------------------------------------
# GFW Dark Vessel Import
# ---------------------------------------------------------------------------

@router.post("/gfw/import", tags=["ingestion"])
async def import_gfw_detections(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Import pre-computed GFW vessel detection CSV (FR8).

    Download from: https://globalfishingwatch.org/data-download/
    Expected CSV columns: detect_id, timestamp, lat, lon, vessel_length_m, vessel_score, vessel_type
    """
    _check_upload_size(file)
    from app.modules.gfw_import import ingest_gfw_csv
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        return ingest_gfw_csv(db, tmp_path)
    finally:
        os.unlink(tmp_path)


# ─── Dark Vessel Detections ───────────────────────────────────────────────────

@router.get("/dark-vessels")
def list_dark_vessels(
    ais_match_result: Optional[str] = None,
    corridor_id: Optional[int] = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    from app.models.stubs import DarkVesselDetection
    q = db.query(DarkVesselDetection)
    if ais_match_result:
        q = q.filter(DarkVesselDetection.ais_match_result == ais_match_result)
    if corridor_id:
        q = q.filter(DarkVesselDetection.corridor_id == corridor_id)
    return q.offset(skip).limit(limit).all()


@router.get("/dark-vessels/{detection_id}")
def get_dark_vessel(detection_id: int, db: Session = Depends(get_db)):
    from app.models.stubs import DarkVesselDetection
    det = (
        db.query(DarkVesselDetection)
        .filter(DarkVesselDetection.detection_id == detection_id)
        .first()
    )
    if not det:
        raise HTTPException(status_code=404, detail="Detection not found")
    return det


# ─── Vessel Hunt (FR9) ────────────────────────────────────────────────────────

@router.post("/hunt/targets", status_code=201, tags=["hunt"])
def create_hunt_target(vessel_id: int, request: Request, db: Session = Depends(get_db)):
    """Register a vessel as a hunt target."""
    from app.modules.vessel_hunt import create_target_profile
    try:
        profile = create_target_profile(vessel_id, db)
        _audit_log(db, "create", "hunt_target", profile.profile_id, details={
            "vessel_id": vessel_id,
        }, request=request)
        db.commit()
        return {
            "profile_id": profile.profile_id,
            "vessel_id": profile.vessel_id,
            "deadweight_dwt": profile.deadweight_dwt,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/hunt/targets", tags=["hunt"])
def list_hunt_targets(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """List all hunt target profiles."""
    from app.models.stubs import VesselTargetProfile
    return db.query(VesselTargetProfile).offset(skip).limit(limit).all()


@router.get("/hunt/targets/{profile_id}", tags=["hunt"])
def get_hunt_target(profile_id: int, db: Session = Depends(get_db)):
    """Get a hunt target profile by ID."""
    from app.models.stubs import VesselTargetProfile
    profile = db.query(VesselTargetProfile).filter(
        VesselTargetProfile.profile_id == profile_id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Target profile not found")
    return profile


@router.post("/hunt/missions", status_code=201, tags=["hunt"])
def create_hunt_mission(
    target_profile_id: int,
    search_start_utc: str,
    search_end_utc: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a search mission with drift ellipse."""
    from app.modules.vessel_hunt import create_search_mission
    try:
        start = datetime.fromisoformat(search_start_utc)
        end = datetime.fromisoformat(search_end_utc)
        mission = create_search_mission(target_profile_id, start, end, db)
        _audit_log(db, "create", "search_mission", mission.mission_id, details={
            "target_profile_id": target_profile_id,
        }, request=request)
        db.commit()
        return {
            "mission_id": mission.mission_id,
            "vessel_id": mission.vessel_id,
            "max_radius_nm": mission.max_radius_nm,
            "elapsed_hours": mission.elapsed_hours,
            "status": mission.status,
            "search_ellipse_wkt": mission.search_ellipse_wkt,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/hunt/missions/{mission_id}", tags=["hunt"])
def get_hunt_mission(mission_id: int, db: Session = Depends(get_db)):
    """Get a search mission by ID."""
    from app.models.stubs import SearchMission
    mission = db.query(SearchMission).filter(
        SearchMission.mission_id == mission_id
    ).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@router.post("/hunt/missions/{mission_id}/find-candidates", status_code=201, tags=["hunt"])
def run_find_candidates(mission_id: int, request: Request, db: Session = Depends(get_db)):
    """Find and score dark vessel detections within mission drift ellipse."""
    from app.modules.vessel_hunt import find_hunt_candidates
    try:
        candidates = find_hunt_candidates(mission_id, db)
        _audit_log(db, "create", "hunt_candidate", mission_id, details={
            "candidates_found": len(candidates),
        }, request=request)
        db.commit()
        return [
            {
                "candidate_id": c.candidate_id,
                "hunt_score": c.hunt_score,
                "score_breakdown_json": c.score_breakdown_json,
                "detection_lat": c.detection_lat,
                "detection_lon": c.detection_lon,
                "analyst_review_status": c.analyst_review_status,
            }
            for c in candidates
        ]
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/hunt/missions/{mission_id}/candidates", tags=["hunt"])
def list_hunt_candidates(mission_id: int, db: Session = Depends(get_db)):
    """List all candidates for a mission."""
    from app.models.stubs import HuntCandidate
    return db.query(HuntCandidate).filter(
        HuntCandidate.mission_id == mission_id
    ).all()


@router.post("/hunt/missions/{mission_id}/confirm/{candidate_id}", tags=["hunt"])
def confirm_hunt_candidate(mission_id: int, candidate_id: int, db: Session = Depends(get_db)):
    """Confirm a hunt candidate and finalize the mission."""
    from app.modules.vessel_hunt import finalize_mission
    try:
        mission = finalize_mission(mission_id, candidate_id, db)
        return {
            "mission_id": mission.mission_id,
            "status": mission.status,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ─── Gov Alert Package (FR10) ────────────────────────────────────────────────

@router.post("/alerts/{alert_id}/export/gov-package", tags=["export"])
def export_gov_package(alert_id: int, db: Session = Depends(get_db)):
    """Export a structured gov alert package combining evidence card + hunt context."""
    from app.modules.evidence_export import export_gov_package as _export_gov

    result = _export_gov(alert_id, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# System / Health
# ---------------------------------------------------------------------------

@router.get("/health", tags=["system"])
def health_check(db: Session = Depends(get_db)):
    """Health check with DB latency measurement."""
    from sqlalchemy import text
    from app.config import settings

    t0 = time.time()
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
    latency_ms = round((time.time() - t0) * 1000, 1)

    return {
        "status": "ok",
        "version": getattr(settings, "VERSION", "1.0.0"),
        "database": {"status": db_status, "latency_ms": latency_ms},
    }


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@router.get("/audit-log", tags=["admin"])
def list_audit_logs(
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List audit log entries (PRD NFR5)."""
    from app.models.audit_log import AuditLog
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        q = q.filter(AuditLog.action == action)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    total = q.count()
    logs = q.offset(skip).limit(limit).all()
    return {
        "total": total,
        "logs": [
            {
                "audit_id": l.audit_id,
                "action": l.action,
                "entity_type": l.entity_type,
                "entity_id": l.entity_id,
                "details": l.details,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ],
    }


# ── Merge Candidate & Identity Merge Endpoints ──────────────────────────────

@router.get("/merge-candidates", tags=["merge"])
def list_merge_candidates(
    status: Optional[str] = Query(None, description="Filter by status: pending, auto_merged, analyst_merged, rejected"),
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

    results = []
    for c in candidates:
        va = db.query(Vessel).get(c.vessel_a_id)
        vb = db.query(Vessel).get(c.vessel_b_id)
        results.append({
            "candidate_id": c.candidate_id,
            "vessel_a": {"vessel_id": c.vessel_a_id, "mmsi": va.mmsi if va else None, "name": va.name if va else None},
            "vessel_b": {"vessel_id": c.vessel_b_id, "mmsi": vb.mmsi if vb else None, "name": vb.name if vb else None},
            "distance_nm": c.distance_nm,
            "time_delta_hours": c.time_delta_hours,
            "confidence_score": c.confidence_score,
            "match_reasons": c.match_reasons_json,
            "satellite_corroboration": c.satellite_corroboration_json,
            "status": c.status,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
            "resolved_by": c.resolved_by,
        })
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
            "vessel_id": v.vessel_id, "mmsi": v.mmsi, "name": v.name,
            "flag": v.flag, "vessel_type": v.vessel_type,
            "deadweight": v.deadweight, "year_built": v.year_built,
        }

    return {
        "candidate_id": c.candidate_id,
        "vessel_a": _vessel_summary(va),
        "vessel_b": _vessel_summary(vb),
        "vessel_a_last_position": {"lat": c.vessel_a_last_lat, "lon": c.vessel_a_last_lon, "time": c.vessel_a_last_time.isoformat() if c.vessel_a_last_time else None},
        "vessel_b_first_position": {"lat": c.vessel_b_first_lat, "lon": c.vessel_b_first_lon, "time": c.vessel_b_first_time.isoformat() if c.vessel_b_first_time else None},
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
def confirm_merge_candidate(
    candidate_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Analyst confirms a merge candidate — executes the merge."""
    from app.models.merge_candidate import MergeCandidate
    from app.models.base import MergeCandidateStatusEnum
    from app.modules.identity_resolver import execute_merge

    c = db.query(MergeCandidate).get(candidate_id)
    if not c:
        raise HTTPException(status_code=404, detail="Merge candidate not found")
    if c.status != MergeCandidateStatusEnum.PENDING:
        raise HTTPException(status_code=400, detail=f"Candidate is {c.status}, not pending")

    canonical_id = min(c.vessel_a_id, c.vessel_b_id)
    absorbed_id = max(c.vessel_a_id, c.vessel_b_id)

    result = execute_merge(
        db, canonical_id, absorbed_id,
        reason=f"Analyst confirmed candidate {candidate_id}",
        merged_by="analyst",
        candidate_id=candidate_id,
        commit=False,
    )

    if result.get("success"):
        c.status = MergeCandidateStatusEnum.ANALYST_MERGED
        c.resolved_at = datetime.utcnow()
        c.resolved_by = "analyst"
        _audit_log(db, "merge_candidate_confirmed", "merge_candidate", candidate_id, request=request)
        db.commit()  # Single atomic commit: merge + status + audit
    else:
        raise HTTPException(status_code=400, detail=result.get("error", "Merge failed"))

    return result


@router.post("/merge-candidates/{candidate_id}/reject", tags=["merge"])
def reject_merge_candidate(
    candidate_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Analyst rejects a merge candidate."""
    from app.models.merge_candidate import MergeCandidate
    from app.models.base import MergeCandidateStatusEnum

    c = db.query(MergeCandidate).get(candidate_id)
    if not c:
        raise HTTPException(status_code=404, detail="Merge candidate not found")
    if c.status != MergeCandidateStatusEnum.PENDING:
        raise HTTPException(status_code=400, detail=f"Candidate is {c.status}, not pending")

    c.status = MergeCandidateStatusEnum.REJECTED
    c.resolved_at = datetime.utcnow()
    c.resolved_by = "analyst"
    _audit_log(db, "merge_candidate_rejected", "merge_candidate", candidate_id, request=request)
    db.commit()

    return {"status": "rejected", "candidate_id": candidate_id}


@router.post("/vessels/merge", tags=["merge"])
def manual_merge_vessels(
    request: Request,
    vessel_a_id: int = Query(..., description="First vessel ID"),
    vessel_b_id: int = Query(..., description="Second vessel ID"),
    reason: str = Query("", description="Reason for merge"),
    db: Session = Depends(get_db),
):
    """Manually merge two vessels (analyst-driven linking)."""
    from app.modules.identity_resolver import execute_merge

    result = execute_merge(
        db, vessel_a_id, vessel_b_id,
        reason=reason,
        merged_by="analyst",
        commit=False,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Merge failed"))

    _audit_log(db, "manual_vessel_merge", "vessel", result.get("merge_op_id"), request=request)
    db.commit()  # Single atomic commit: merge + audit
    return result


@router.post("/merge-operations/{merge_op_id}/reverse", tags=["merge"])
def reverse_merge_operation(
    merge_op_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Undo a merge operation."""
    from app.modules.identity_resolver import reverse_merge

    result = reverse_merge(db, merge_op_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Reversal failed"))

    _audit_log(db, "merge_reversed", "merge_operation", merge_op_id, request=request)
    db.commit()
    return result


@router.get("/vessels/{vessel_id}/timeline", tags=["vessels"])
def get_vessel_timeline_endpoint(
    vessel_id: int,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Unified chronological event timeline for a vessel."""
    from app.models.vessel import Vessel
    from app.modules.identity_resolver import get_vessel_timeline

    vessel = db.query(Vessel).get(vessel_id)
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    events = get_vessel_timeline(db, vessel_id, limit=limit, offset=offset)
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


# ---------------------------------------------------------------------------
# H1: Data Freshness Monitoring
# ---------------------------------------------------------------------------

@router.get("/health/data-freshness", tags=["health"])
def get_data_freshness(db: Session = Depends(get_db)):
    """Data freshness monitoring -- reports AIS data staleness."""
    from app.models.vessel import Vessel

    now = datetime.now(timezone.utc)

    latest = db.query(func.max(Vessel.last_ais_received_utc)).scalar()

    one_hour_ago = now - timedelta(hours=1)
    twenty_four_hours_ago = now - timedelta(hours=24)

    vessels_1h = db.query(func.count(Vessel.vessel_id)).filter(
        Vessel.last_ais_received_utc >= one_hour_ago
    ).scalar() or 0

    vessels_24h = db.query(func.count(Vessel.vessel_id)).filter(
        Vessel.last_ais_received_utc >= twenty_four_hours_ago
    ).scalar() or 0

    staleness_minutes = None
    if latest:
        staleness_minutes = int((now - latest.replace(tzinfo=timezone.utc)).total_seconds() / 60)

    return {
        "latest_ais_utc": latest.isoformat() if latest else None,
        "staleness_minutes": staleness_minutes,
        "vessels_updated_last_1h": vessels_1h,
        "vessels_updated_last_24h": vessels_24h,
    }


# ---------------------------------------------------------------------------
# Detector API Exposure
# ---------------------------------------------------------------------------

@router.post("/detect/cross-receiver", tags=["detection"])
def trigger_cross_receiver_detection(request: Request, db: Session = Depends(get_db)):
    """Trigger cross-receiver anomaly detection."""
    from app.modules.cross_receiver_detector import detect_cross_receiver_anomalies
    result = detect_cross_receiver_anomalies(db)
    _audit_log(db, "detect_trigger", "cross_receiver", details=result, request=request)
    db.commit()
    return result


@router.post("/detect/handshake", tags=["detection"])
def trigger_handshake_detection(request: Request, db: Session = Depends(get_db)):
    """Trigger handshake (identity swap) detection."""
    from app.modules.handshake_detector import detect_handshakes
    result = detect_handshakes(db)
    _audit_log(db, "detect_trigger", "handshake", details=result, request=request)
    db.commit()
    return result


@router.post("/detect/mmsi-cloning", tags=["detection"])
def trigger_mmsi_cloning_detection(request: Request, db: Session = Depends(get_db)):
    """Trigger MMSI cloning detection."""
    from app.modules.mmsi_cloning_detector import detect_mmsi_cloning
    results = detect_mmsi_cloning(db)
    _audit_log(db, "detect_trigger", "mmsi_cloning", details={"clones_detected": len(results)}, request=request)
    db.commit()
    return {"clones_detected": len(results), "details": results}


@router.get("/port-calls/{vessel_id}", tags=["port-calls"])
def get_port_calls(vessel_id: int, db: Session = Depends(get_db)):
    """List port calls for a vessel."""
    from app.models.port_call import PortCall
    from app.models.port import Port
    from app.models.vessel import Vessel

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    port_calls = db.query(PortCall).filter(PortCall.vessel_id == vessel_id).order_by(PortCall.arrival_utc.desc()).all()

    items = []
    for pc in port_calls:
        port = db.query(Port).filter(Port.port_id == pc.port_id).first() if pc.port_id else None
        items.append({
            "port_call_id": pc.port_call_id,
            "vessel_id": pc.vessel_id,
            "port_id": pc.port_id,
            "port_name": port.name if port else getattr(pc, "raw_port_name", None),
            "arrival_utc": pc.arrival_utc.isoformat() if pc.arrival_utc else None,
            "departure_utc": pc.departure_utc.isoformat() if pc.departure_utc else None,
            "source": pc.source if hasattr(pc, "source") else None,
        })

    return {"vessel_id": vessel_id, "items": items, "total": len(items)}
