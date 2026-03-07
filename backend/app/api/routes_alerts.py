"""Alert, scoring, and dashboard statistics endpoints."""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.config import settings
from app.schemas.alerts import BulkStatusUpdateRequest, NoteAddRequest
from app.api._helpers import _audit_log, _validate_date_range, limiter

logger = logging.getLogger(__name__)

router = APIRouter()


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
    ids: Optional[str] = Query(None, description="Comma-separated alert IDs to export"),
    db: Session = Depends(get_db),
):
    """Bulk export alerts as publication-ready CSV."""
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    _validate_date_range(date_from, date_to)

    q = db.query(AISGapEvent)
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        if id_list:
            q = q.filter(AISGapEvent.gap_event_id.in_(id_list))
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
            except Exception as e:
                logger.warning("Failed to deserialize geometry: %s", e)
                geojson_str = None
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
        is_false_positive=alert.is_false_positive,
        reviewed_by=alert.reviewed_by,
        review_date=alert.review_date,
    )


@router.post("/alerts/{alert_id}/status", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
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


@router.post("/alerts/{alert_id}/verdict", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def submit_alert_verdict(
    request: Request,
    alert_id: int,
    body: dict,
    db: Session = Depends(get_db),
):
    """Record analyst verdict (confirmed true-positive or false-positive)."""
    from app.models.gap_event import AISGapEvent
    from app.schemas.gap_event import AlertVerdictRequest

    body = AlertVerdictRequest(**body)

    valid_verdicts = {"confirmed_tp", "confirmed_fp"}
    if body.verdict not in valid_verdicts:
        raise HTTPException(status_code=400, detail=f"Invalid verdict. Must be one of: {sorted(valid_verdicts)}")

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.is_false_positive = body.verdict == "confirmed_fp"
    alert.reviewed_by = body.reviewed_by
    alert.review_date = datetime.now(timezone.utc)
    alert.status = body.verdict

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    verdict_note = f"[{timestamp}] Verdict: {body.verdict}"
    if body.reason:
        verdict_note += f" — {body.reason}"
    if body.reviewed_by:
        verdict_note += f" (by {body.reviewed_by})"
    existing_notes = alert.analyst_notes or ""
    alert.analyst_notes = f"{existing_notes}\n{verdict_note}".strip()

    _audit_log(db, "verdict", "alert", alert_id,
               {"verdict": body.verdict, "reason": body.reason, "reviewed_by": body.reviewed_by}, request)
    db.commit()
    return {"status": "ok", "verdict": body.verdict, "is_false_positive": alert.is_false_positive}


@router.post("/alerts/{alert_id}/notes", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def add_note(request: Request, alert_id: int, body: NoteAddRequest, db: Session = Depends(get_db)):
    from app.models.gap_event import AISGapEvent

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    # Accept both "notes" (frontend) and "text" (legacy) keys
    alert.analyst_notes = body.notes if body.notes is not None else (body.text or "")
    db.commit()
    return {"status": "ok"}


@router.post("/alerts/bulk-status", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def bulk_update_alert_status(request: Request, payload: BulkStatusUpdateRequest, db: Session = Depends(get_db)):
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
def export_evidence(alert_id: int, format: str = "json", request=None, db: Session = Depends(get_db)):
    from app.modules.evidence_export import export_evidence_card
    result = export_evidence_card(alert_id, format, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    _audit_log(db, "evidence_export", "alert", alert_id, {"format": format}, request)
    db.commit()
    return result


@router.post("/alerts/{alert_id}/export/gov-package", tags=["export"])
def export_gov_package(alert_id: int, db: Session = Depends(get_db)):
    """Export a structured gov alert package combining evidence card + hunt context."""
    from app.modules.evidence_export import export_gov_package as _export_gov

    result = _export_gov(alert_id, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@router.post("/score-alerts", tags=["scoring"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def score_alerts(request: Request, db: Session = Depends(get_db)):
    """Score all unscored gap events."""
    from app.modules.risk_scoring import score_all_alerts
    return score_all_alerts(db)


@router.post("/rescore-all-alerts", tags=["scoring"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def rescore_all_alerts(request: Request, db: Session = Depends(get_db)):
    """Clear and re-compute all risk scores (use after config changes)."""
    from app.modules.risk_scoring import rescore_all_alerts as _rescore
    return _rescore(db)


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

    # Use SQL aggregation instead of loading all rows into memory
    count_q = q.with_entities(
        func.count().label("total"),
        func.sum(case((AISGapEvent.risk_score >= 76, 1), else_=0)).label("critical"),
        func.sum(case((AISGapEvent.risk_score.between(51, 75), 1), else_=0)).label("high"),
        func.sum(case((AISGapEvent.risk_score.between(21, 50), 1), else_=0)).label("medium"),
        func.sum(case((AISGapEvent.risk_score < 21, 1), else_=0)).label("low"),
    ).first()

    total = count_q[0] or 0
    critical = int(count_q[1] or 0)
    high = int(count_q[2] or 0)
    medium = int(count_q[3] or 0)
    low = int(count_q[4] or 0)

    # Aggregate by status in SQL
    by_status: dict[str, int] = {}
    status_rows = q.with_entities(
        AISGapEvent.status, func.count()
    ).group_by(AISGapEvent.status).all()
    for row in status_rows:
        s = str(row[0].value) if hasattr(row[0], "value") else str(row[0])
        by_status[s] = row[1]

    # Aggregate by corridor in SQL
    by_corridor: dict[str, int] = {}
    corridor_rows = q.with_entities(
        AISGapEvent.corridor_id, func.count()
    ).group_by(AISGapEvent.corridor_id).all()
    for row in corridor_rows:
        key = str(row[0]) if row[0] else "no_corridor"
        by_corridor[key] = row[1]

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
