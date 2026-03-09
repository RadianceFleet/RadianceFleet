"""Alert, scoring, and dashboard statistics endpoints."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload

from app.api._helpers import _audit_log, _validate_date_range, limiter
from app.auth import require_auth, require_senior_or_admin
from app.config import settings
from app.database import get_db
from app.schemas.alerts import BulkStatusUpdateRequest, NoteAddRequest

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@router.get("/alerts", tags=["alerts"])
def list_alerts(
    date_from: date | None = None,
    date_to: date | None = None,
    corridor_id: int | None = None,
    vessel_id: int | None = None,
    vessel_name: str | None = None,
    min_score: int | None = None,
    status: str | None = None,
    assigned_to: int | None = None,
    sort_by: str = Query(
        "risk_score",
        description="Field to sort by: risk_score|gap_start_utc|duration_minutes|vessel_name",
    ),
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
        joinedload(AISGapEvent.assigned_analyst),
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
    if assigned_to is not None:
        q = q.filter(AISGapEvent.assigned_to == assigned_to)

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
        item = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        item["last_lat"] = r.start_point.lat if r.start_point else None
        item["last_lon"] = r.start_point.lon if r.start_point else None
        item["vessel_name"] = r.vessel.name if r.vessel else None
        item["vessel_mmsi"] = r.vessel.mmsi if r.vessel else None
        item["assigned_to"] = (
            r.assigned_to if isinstance(getattr(r, "assigned_to", None), int) else None
        )
        item["assigned_to_username"] = (
            r.assigned_analyst.username
            if isinstance(getattr(r, "assigned_to", None), int)
            and getattr(r, "assigned_analyst", None)
            else None
        )
        item["version"] = r.version if isinstance(getattr(r, "version", None), int) else 1
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
                "vessel_id": r.vessel_id,
                "vessel_name": r.vessel.name if r.vessel else None,
                "gap_start_utc": r.gap_start_utc,
                "duration_minutes": r.duration_minutes,
            }
            for r in results
        ]
    }


@router.get("/alerts/export", tags=["alerts"])
def export_alerts_csv(
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    min_score: int | None = None,
    ids: str | None = Query(None, description="Comma-separated alert IDs to export"),
    columns: str | None = Query(None, description="Comma-separated column names to include"),
    db: Session = Depends(get_db),
):
    """Bulk export alerts as publication-ready CSV."""
    from app.models.gap_event import AISGapEvent

    _validate_date_range(date_from, date_to)

    q = db.query(AISGapEvent).options(
        joinedload(AISGapEvent.vessel),
        joinedload(AISGapEvent.corridor),
    )
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

    all_columns = [
        "alert_id",
        "vessel_mmsi",
        "vessel_name",
        "flag",
        "dwt",
        "gap_start_utc",
        "gap_end_utc",
        "duration_hours",
        "corridor_name",
        "risk_score",
        "status",
        "analyst_notes",
    ]

    if columns:
        requested = [c.strip() for c in columns.split(",") if c.strip()]
        selected = [c for c in requested if c in all_columns]
        if not selected:
            selected = all_columns
    else:
        selected = all_columns

    def _row_dict(alert):
        vessel = alert.vessel
        corridor = alert.corridor
        return {
            "alert_id": alert.gap_event_id,
            "vessel_mmsi": vessel.mmsi if vessel else "",
            "vessel_name": vessel.name if vessel else "",
            "flag": vessel.flag if vessel else "",
            "dwt": vessel.deadweight if vessel else "",
            "gap_start_utc": alert.gap_start_utc.isoformat() if alert.gap_start_utc else "",
            "gap_end_utc": alert.gap_end_utc.isoformat() if alert.gap_end_utc else "",
            "duration_hours": round(alert.duration_minutes / 60, 2)
            if alert.duration_minutes
            else "",
            "corridor_name": corridor.name if corridor else "",
            "risk_score": alert.risk_score,
            "status": str(alert.status.value) if hasattr(alert.status, 'value') else str(alert.status),
            "analyst_notes": alert.analyst_notes or "",
        }

    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(selected)
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)

        try:
            for alert in alerts:
                row = _row_dict(alert)
                writer.writerow([row[col] for col in selected])
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)
        except Exception as e:
            logger.error("CSV export error mid-stream: %s", e, exc_info=True)

    filename = f"radiancefleet_export_{datetime.now(UTC).strftime('%Y%m%d_%H%M')}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/alerts/my", tags=["alerts"])
def my_alerts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Get alerts assigned to current analyst."""
    from app.models.gap_event import AISGapEvent

    q = (
        db.query(AISGapEvent)
        .options(
            joinedload(AISGapEvent.vessel),
        )
        .filter(AISGapEvent.assigned_to == auth["analyst_id"])
    )
    total = q.count()
    results = q.order_by(AISGapEvent.risk_score.desc()).offset(skip).limit(limit).all()
    items = []
    for r in results:
        item = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        item["vessel_name"] = r.vessel.name if r.vessel else None
        item["vessel_mmsi"] = r.vessel.mmsi if r.vessel else None
        items.append(item)
    return {"items": items, "total": total}


# ---------------------------------------------------------------------------
# Saved Filters
# ---------------------------------------------------------------------------


@router.get("/alerts/saved-filters", tags=["alerts"])
def list_saved_filters(
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """List saved filters for the current analyst."""
    from app.models.saved_filter import SavedFilter

    filters = (
        db.query(SavedFilter)
        .filter(SavedFilter.analyst_id == auth["analyst_id"])
        .order_by(SavedFilter.created_at.desc())
        .all()
    )
    return {
        "items": [
            {
                "filter_id": f.filter_id,
                "name": f.name,
                "filter_json": f.filter_json,
                "is_default": f.is_default,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in filters
        ]
    }


@router.post("/alerts/saved-filters", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_saved_filter(
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Save a filter configuration."""
    from app.models.saved_filter import SavedFilter

    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    filter_json = body.get("filter_json", {})
    is_default = body.get("is_default", False)

    # If setting as default, clear other defaults
    if is_default:
        db.query(SavedFilter).filter(
            SavedFilter.analyst_id == auth["analyst_id"],
            SavedFilter.is_default,
        ).update({"is_default": False})

    sf = SavedFilter(
        analyst_id=auth["analyst_id"],
        name=name,
        filter_json=filter_json,
        is_default=is_default,
    )
    db.add(sf)
    db.commit()
    return {"filter_id": sf.filter_id, "name": sf.name}


@router.delete("/alerts/saved-filters/{filter_id}", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def delete_saved_filter(
    filter_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Delete a saved filter."""
    from app.models.saved_filter import SavedFilter

    deleted = (
        db.query(SavedFilter)
        .filter(
            SavedFilter.filter_id == filter_id,
            SavedFilter.analyst_id == auth["analyst_id"],
        )
        .delete()
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Filter not found")
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Alert Trends
# ---------------------------------------------------------------------------


@router.get("/alerts/trends", tags=["dashboard"])
def get_alert_trends(
    period: str = Query("7d", description="7d, 30d, or 90d"),
    db: Session = Depends(get_db),
):
    """Time-bucketed alert counts for trend analysis."""
    from app.models.gap_event import AISGapEvent

    days_map = {"7d": 7, "30d": 30, "90d": 90}
    days = days_map.get(period, 7)
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)

    # Bucket by day
    alerts = db.query(AISGapEvent).filter(AISGapEvent.gap_start_utc >= cutoff).all()

    daily_counts: dict[str, dict] = {}
    for a in alerts:
        day_key = a.gap_start_utc.strftime("%Y-%m-%d") if a.gap_start_utc else "unknown"
        if day_key not in daily_counts:
            daily_counts[day_key] = {
                "date": day_key,
                "total": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "reviewed": 0,
            }
        daily_counts[day_key]["total"] += 1
        score = a.risk_score or 0
        if score >= 76:
            daily_counts[day_key]["critical"] += 1
        elif score >= 51:
            daily_counts[day_key]["high"] += 1
        elif score >= 21:
            daily_counts[day_key]["medium"] += 1
        else:
            daily_counts[day_key]["low"] += 1
        status = str(a.status.value) if hasattr(a.status, "value") else str(a.status)
        if status not in ("new",):
            daily_counts[day_key]["reviewed"] += 1

    buckets = sorted(daily_counts.values(), key=lambda x: x["date"])
    total_new = sum(b["total"] - b["reviewed"] for b in buckets)
    total_reviewed = sum(b["reviewed"] for b in buckets)

    return {
        "period": period,
        "buckets": buckets,
        "summary": {
            "total_new": total_new,
            "total_reviewed": total_reviewed,
            "review_ratio": round(total_reviewed / max(1, total_new + total_reviewed), 3),
        },
    }


@router.get("/alerts/{alert_id}", tags=["alerts"])
def get_alert(alert_id: int, db: Session = Depends(get_db)):
    from app.models.ais_point import AISPoint
    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent
    from app.models.loitering_event import LoiteringEvent
    from app.models.movement_envelope import MovementEnvelope
    from app.models.satellite_check import SatelliteCheck
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.sts_transfer import StsTransferEvent
    from app.models.vessel import Vessel
    from app.schemas.gap_event import (
        AISPointSummary,
        GapEventDetailRead,
        LoiteringSummary,
        MovementEnvelopeRead,
        SatelliteCheckSummary,
        SpoofingAnomalySummary,
        StsSummary,
    )

    alert = (
        db.query(AISGapEvent)
        .options(
            joinedload(AISGapEvent.vessel),
            joinedload(AISGapEvent.corridor),
            joinedload(AISGapEvent.assigned_analyst),
        )
        .filter(AISGapEvent.gap_event_id == alert_id)
        .first()
    )
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    vessel = alert.vessel
    corridor = alert.corridor
    envelope = db.query(MovementEnvelope).filter(MovementEnvelope.gap_event_id == alert_id).first()
    sat_check = db.query(SatelliteCheck).filter(SatelliteCheck.gap_event_id == alert_id).first()
    last_pt = (
        db.query(AISPoint).filter(AISPoint.ais_point_id == alert.start_point_id).first()
        if alert.start_point_id
        else None
    )
    first_pt = (
        db.query(AISPoint).filter(AISPoint.ais_point_id == alert.end_point_id).first()
        if alert.end_point_id
        else None
    )

    envelope_data = None
    if envelope:
        geojson_str = None
        if envelope.confidence_ellipse_geometry is not None:
            try:
                import shapely.geometry

                from app.utils.geo import load_geometry

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
            if hasattr(envelope.estimated_method, "value")
            else envelope.estimated_method,
        )

    # Alert enrichment: linked anomalies
    spoofing_list = None
    if alert.vessel_id:
        spoofing_raw = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == alert.vessel_id,
                SpoofingAnomaly.start_time_utc >= alert.gap_start_utc - timedelta(days=1),
                SpoofingAnomaly.start_time_utc <= alert.gap_end_utc + timedelta(days=1),
            )
            .all()
        )
        if spoofing_raw:
            spoofing_list = [
                SpoofingAnomalySummary(
                    anomaly_id=s.anomaly_id,
                    anomaly_type=str(s.anomaly_type.value)
                    if hasattr(s.anomaly_type, "value")
                    else str(s.anomaly_type),
                    start_time_utc=s.start_time_utc,
                    risk_score_component=s.risk_score_component,
                    evidence_json=s.evidence_json,
                )
                for s in spoofing_raw
            ]

    loitering_list = None
    if alert.vessel_id:
        loitering_raw = (
            db.query(LoiteringEvent)
            .filter(
                LoiteringEvent.vessel_id == alert.vessel_id,
                LoiteringEvent.start_time_utc >= alert.gap_start_utc - timedelta(days=7),
                LoiteringEvent.start_time_utc <= alert.gap_end_utc + timedelta(days=7),
            )
            .all()
        )
        if loitering_raw:
            loitering_list = [
                LoiteringSummary(
                    loiter_id=le.loiter_id,
                    start_time_utc=le.start_time_utc,
                    duration_hours=le.duration_hours,
                    mean_lat=le.mean_lat,
                    mean_lon=le.mean_lon,
                    median_sog_kn=le.median_sog_kn,
                )
                for le in loitering_raw
            ]

    sts_list = None
    if alert.vessel_id:
        sts_raw = (
            db.query(StsTransferEvent)
            .filter(
                or_(
                    StsTransferEvent.vessel_1_id == alert.vessel_id,
                    StsTransferEvent.vessel_2_id == alert.vessel_id,
                ),
                StsTransferEvent.start_time_utc >= alert.gap_start_utc - timedelta(days=7),
                StsTransferEvent.start_time_utc <= alert.gap_end_utc + timedelta(days=7),
            )
            .all()
        )
        if sts_raw:
            sts_list = []
            for s in sts_raw:
                partner_id = s.vessel_2_id if s.vessel_1_id == alert.vessel_id else s.vessel_1_id
                partner = db.query(Vessel).filter(Vessel.vessel_id == partner_id).first()
                sts_list.append(
                    StsSummary(
                        sts_id=s.sts_id,
                        partner_name=partner.name if partner else None,
                        partner_mmsi=partner.mmsi if partner else None,
                        detection_type=str(s.detection_type.value)
                        if hasattr(s.detection_type, "value")
                        else str(s.detection_type),
                        start_time_utc=s.start_time_utc,
                    )
                )

    # H3: Prior similar count
    prior_count = (
        db.query(func.count(AISGapEvent.gap_event_id))
        .filter(
            AISGapEvent.vessel_id == alert.vessel_id,
            AISGapEvent.corridor_id == alert.corridor_id,
            AISGapEvent.gap_event_id != alert.gap_event_id,
            AISGapEvent.gap_start_utc >= alert.gap_start_utc - timedelta(days=90),
            AISGapEvent.gap_start_utc < alert.gap_start_utc,
        )
        .scalar()
        or 0
    )

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
            timestamp_utc=last_pt.timestamp_utc,
            lat=last_pt.lat,
            lon=last_pt.lon,
            sog=last_pt.sog,
            cog=last_pt.cog,
        )
        if last_pt
        else None,
        first_point_after=AISPointSummary(
            timestamp_utc=first_pt.timestamp_utc,
            lat=first_pt.lat,
            lon=first_pt.lon,
            sog=first_pt.sog,
            cog=first_pt.cog,
        )
        if first_pt
        else None,
        spoofing_anomalies=spoofing_list,
        loitering_events=loitering_list,
        sts_events=sts_list,
        prior_similar_count=prior_count,
        is_recurring_pattern=prior_count >= 3,
        is_false_positive=alert.is_false_positive,
        reviewed_by=alert.reviewed_by,
        review_date=alert.review_date,
        assigned_to=getattr(alert, "assigned_to", None)
        if isinstance(getattr(alert, "assigned_to", None), int)
        else None,
        assigned_to_username=alert.assigned_analyst.username
        if isinstance(getattr(alert, "assigned_to", None), int)
        and getattr(alert, "assigned_analyst", None)
        else None,
        assigned_at=alert.assigned_at.isoformat()
        if isinstance(getattr(alert, "assigned_at", None), datetime)
        else None,
        version=alert.version if isinstance(getattr(alert, "version", None), int) else 1,
    )


@router.post("/alerts/{alert_id}/status", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def update_alert_status(
    alert_id: int,
    body: dict,
    request: Request = None,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Explicitly set alert status. Body: {status: '...', reason: '...'}"""
    from app.models.base import AlertStatusEnum
    from app.models.gap_event import AISGapEvent
    from app.schemas.gap_event import AlertStatusUpdate

    body = AlertStatusUpdate(**body)

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Optimistic locking check
    if body.version is not None and alert.version != body.version:
        raise HTTPException(status_code=409, detail="Version conflict")

    valid_statuses = [e.value for e in AlertStatusEnum]
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}"
        )

    old_status = alert.status
    alert.status = body.status
    alert.version += 1
    if body.reason:
        existing_notes = alert.analyst_notes or ""
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        alert.analyst_notes = (
            f"{existing_notes}\n[{timestamp}] Status → {body.status}: {body.reason}".strip()
        )

    _audit_log(
        db,
        "status_change",
        "alert",
        alert_id,
        {"old_status": old_status, "new_status": body.status, "reason": body.reason},
        request,
        analyst_id=auth["analyst_id"],
    )
    db.commit()
    return {"status": "ok", "new_status": body.status}


@router.post("/alerts/{alert_id}/verdict", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def submit_alert_verdict(
    request: Request,
    alert_id: int,
    body: dict,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Record analyst verdict (confirmed true-positive or false-positive)."""
    from app.models.gap_event import AISGapEvent
    from app.schemas.gap_event import AlertVerdictRequest

    body = AlertVerdictRequest(**body)

    valid_verdicts = {"confirmed_tp", "confirmed_fp"}
    if body.verdict not in valid_verdicts:
        raise HTTPException(
            status_code=400, detail=f"Invalid verdict. Must be one of: {sorted(valid_verdicts)}"
        )

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    # Optimistic locking check
    if body.version is not None and alert.version != body.version:
        raise HTTPException(status_code=409, detail="Version conflict")

    alert.is_false_positive = body.verdict == "confirmed_fp"
    alert.reviewed_by = auth["username"]
    alert.review_date = datetime.now(UTC)
    alert.status = body.verdict
    alert.version += 1

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    verdict_note = f"[{timestamp}] Verdict: {body.verdict}"
    if body.reason:
        verdict_note += f" — {body.reason}"
    verdict_note += f" (by {auth['username']})"
    existing_notes = alert.analyst_notes or ""
    alert.analyst_notes = f"{existing_notes}\n{verdict_note}".strip()

    _audit_log(
        db,
        "verdict",
        "alert",
        alert_id,
        {"verdict": body.verdict, "reason": body.reason, "reviewed_by": auth["username"]},
        request,
        analyst_id=auth["analyst_id"],
    )
    db.commit()
    return {"status": "ok", "verdict": body.verdict, "is_false_positive": alert.is_false_positive}


@router.post("/alerts/{alert_id}/notes", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def add_note(
    request: Request,
    alert_id: int,
    body: NoteAddRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
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
def bulk_update_alert_status(
    request: Request,
    payload: BulkStatusUpdateRequest,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Bulk-update alert statuses for triage workflow."""
    from app.models.gap_event import AISGapEvent

    alert_ids = payload.alert_ids
    new_status = payload.status
    valid_statuses = {"new", "under_review", "needs_satellite_check", "documented", "dismissed"}

    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{new_status}'. Must be one of: {', '.join(sorted(valid_statuses))}",
        )

    updated = (
        db.query(AISGapEvent)
        .filter(AISGapEvent.gap_event_id.in_(alert_ids))
        .update({"status": new_status}, synchronize_session="fetch")
    )
    db.commit()
    return {"updated": updated}


@router.post("/alerts/{alert_id}/satellite-check", tags=["alerts"])
def prepare_satellite_check(alert_id: int, db: Session = Depends(get_db)):
    from app.modules.satellite_query import prepare_satellite_check as _prepare

    return _prepare(alert_id, db)


@router.post("/alerts/{alert_id}/export", tags=["alerts"])
def export_evidence(
    alert_id: int,
    format: str = "json",
    request=None,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    if format == "pdf":
        from app.modules.evidence_pdf import export_evidence_pdf

        try:
            pdf_bytes = export_evidence_pdf(alert_id, db)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        _audit_log(
            db,
            "evidence_export",
            "alert",
            alert_id,
            {"format": "pdf"},
            request,
            analyst_id=auth["analyst_id"],
        )
        db.commit()
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="evidence_{alert_id}.pdf"'},
        )
    from app.modules.evidence_export import export_evidence_card

    result = export_evidence_card(alert_id, format, db)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    # Track exported_by on evidence card
    from app.models.evidence_card import EvidenceCard

    card = (
        db.query(EvidenceCard)
        .filter(EvidenceCard.gap_event_id == alert_id)
        .order_by(EvidenceCard.created_at.desc())
        .first()
    )
    if card:
        card.exported_by = auth["analyst_id"]
        card.approval_status = "draft"
    _audit_log(
        db,
        "evidence_export",
        "alert",
        alert_id,
        {"format": format},
        request,
        analyst_id=auth["analyst_id"],
    )
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
# Evidence Approval
# ---------------------------------------------------------------------------


@router.post("/evidence-cards/{card_id}/approve", tags=["evidence"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def approve_evidence(
    card_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Senior/admin approves evidence card."""
    from app.models.evidence_card import EvidenceCard

    card = db.query(EvidenceCard).filter(EvidenceCard.evidence_card_id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Evidence card not found")
    if card.approval_status == "approved":
        raise HTTPException(status_code=400, detail="Already approved")
    card.approved_by = auth["analyst_id"]
    card.approved_at = datetime.now(UTC)
    card.approval_status = "approved"
    _audit_log(
        db,
        "evidence_approve",
        "evidence_card",
        card_id,
        {"approved_by": auth["username"]},
        request,
        analyst_id=auth["analyst_id"],
    )
    db.commit()
    return {"status": "ok", "approval_status": "approved"}


@router.post("/evidence-cards/{card_id}/reject", tags=["evidence"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def reject_evidence(
    card_id: int,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Senior/admin rejects evidence card with notes."""
    from app.models.evidence_card import EvidenceCard

    card = db.query(EvidenceCard).filter(EvidenceCard.evidence_card_id == card_id).first()
    if not card:
        raise HTTPException(status_code=404, detail="Evidence card not found")
    card.approved_by = auth["analyst_id"]
    card.approved_at = datetime.now(UTC)
    card.approval_status = "rejected"
    card.approval_notes = body.get("notes", "")
    _audit_log(
        db,
        "evidence_reject",
        "evidence_card",
        card_id,
        {"rejected_by": auth["username"], "notes": body.get("notes")},
        request,
        analyst_id=auth["analyst_id"],
    )
    db.commit()
    return {"status": "ok", "approval_status": "rejected"}


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


@router.post("/alerts/{alert_id}/assign", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def assign_alert(
    alert_id: int,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Assign alert to an analyst."""
    from app.models.analyst import Analyst
    from app.models.gap_event import AISGapEvent

    analyst_id = body.get("analyst_id")
    if not analyst_id:
        raise HTTPException(status_code=422, detail="analyst_id required")
    analyst = db.query(Analyst).filter(Analyst.analyst_id == analyst_id).first()
    if not analyst:
        raise HTTPException(status_code=404, detail="Analyst not found")
    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.assigned_to = analyst_id
    alert.assigned_at = datetime.now(UTC)
    _audit_log(
        db,
        "assign",
        "alert",
        alert_id,
        {"analyst_id": analyst_id, "by": auth["analyst_id"]},
        request,
    )
    db.commit()
    return {"status": "ok", "assigned_to": analyst_id}


@router.delete("/alerts/{alert_id}/assign", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def unassign_alert(
    alert_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Unassign alert."""
    from app.models.gap_event import AISGapEvent

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.assigned_to = None
    alert.assigned_at = None
    db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Edit Locks
# ---------------------------------------------------------------------------


@router.post("/alerts/{alert_id}/lock", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def acquire_lock(
    alert_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Acquire edit lock. Returns 409 if held by another analyst."""
    from app.models.alert_edit_lock import AlertEditLock
    from app.models.gap_event import AISGapEvent

    alert = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    now = datetime.now(UTC)
    # Clean expired locks
    db.query(AlertEditLock).filter(AlertEditLock.expires_at < now).delete()

    existing = db.query(AlertEditLock).filter(AlertEditLock.alert_id == alert_id).first()
    if existing:
        if existing.analyst_id != auth["analyst_id"]:
            raise HTTPException(
                status_code=409, detail=f"Lock held by analyst {existing.analyst_id}"
            )
        # Extend own lock
        existing.expires_at = now + timedelta(seconds=settings.EDIT_LOCK_TTL_SECONDS)
        db.commit()
        return {
            "lock_id": existing.lock_id,
            "alert_id": alert_id,
            "analyst_id": auth["analyst_id"],
            "analyst_username": auth["username"],
            "acquired_at": existing.acquired_at.isoformat(),
            "expires_at": existing.expires_at.isoformat(),
        }

    lock = AlertEditLock(
        alert_id=alert_id,
        analyst_id=auth["analyst_id"],
        acquired_at=now,
        expires_at=now + timedelta(seconds=settings.EDIT_LOCK_TTL_SECONDS),
    )
    db.add(lock)
    db.commit()
    return {
        "lock_id": lock.lock_id,
        "alert_id": alert_id,
        "analyst_id": auth["analyst_id"],
        "analyst_username": auth["username"],
        "acquired_at": lock.acquired_at.isoformat(),
        "expires_at": lock.expires_at.isoformat(),
    }


@router.post("/alerts/{alert_id}/lock/heartbeat", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def heartbeat_lock(
    alert_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Extend lock TTL."""
    from app.models.alert_edit_lock import AlertEditLock

    lock = (
        db.query(AlertEditLock)
        .filter(
            AlertEditLock.alert_id == alert_id,
            AlertEditLock.analyst_id == auth["analyst_id"],
        )
        .first()
    )
    if not lock:
        raise HTTPException(status_code=404, detail="No lock held")
    lock.expires_at = datetime.now(UTC) + timedelta(seconds=settings.EDIT_LOCK_TTL_SECONDS)
    db.commit()
    return {"status": "ok", "expires_at": lock.expires_at.isoformat()}


@router.delete("/alerts/{alert_id}/lock", tags=["alerts"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def release_lock(
    alert_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Release edit lock."""
    from app.models.alert_edit_lock import AlertEditLock

    deleted = (
        db.query(AlertEditLock)
        .filter(
            AlertEditLock.alert_id == alert_id,
            AlertEditLock.analyst_id == auth["analyst_id"],
        )
        .delete()
    )
    db.commit()
    return {"status": "ok", "released": deleted > 0}


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
def rescore_all_alerts(
    request: Request,
    diff: bool = Query(False, description="If true, return before/after score changes"),
    db: Session = Depends(get_db),
):
    """Clear and re-compute all risk scores (use after config changes)."""
    if diff:
        from app.models.gap_event import AISGapEvent
        from app.modules.risk_scoring import rescore_all_alerts as _rescore

        # Capture before scores
        alerts_before = {}
        for alert in db.query(AISGapEvent).all():
            alerts_before[alert.gap_event_id] = {
                "score": alert.risk_score,
                "band": _score_band_label(alert.risk_score),
            }

        result = _rescore(db)

        # Capture after and compute diff
        changes = []
        for alert in db.query(AISGapEvent).all():
            before = alerts_before.get(alert.gap_event_id, {"score": 0, "band": "low"})
            after_score = alert.risk_score
            after_band = _score_band_label(after_score)
            if before["score"] != after_score:
                changes.append(
                    {
                        "gap_event_id": alert.gap_event_id,
                        "before_score": before["score"],
                        "after_score": after_score,
                        "before_band": before["band"],
                        "after_band": after_band,
                        "delta": after_score - before["score"],
                    }
                )

        result["diff"] = {
            "total_changed": len(changes),
            "band_changes": sum(1 for c in changes if c["before_band"] != c["after_band"]),
            "changes": changes[:100],  # Limit to first 100
        }
        return result

    from app.modules.risk_scoring import rescore_all_alerts as _rescore

    return _rescore(db)


def _score_band_label(score: int) -> str:
    if score >= 76:
        return "critical"
    if score >= 51:
        return "high"
    if score >= 21:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Dashboard Stats
# ---------------------------------------------------------------------------


@router.get("/stats", tags=["dashboard"])
def get_stats(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    """Dashboard statistics for the analyst overview."""
    from app.models.gap_event import AISGapEvent

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
    status_rows = (
        q.with_entities(AISGapEvent.status, func.count()).group_by(AISGapEvent.status).all()
    )
    for row in status_rows:
        s = str(row[0].value) if hasattr(row[0], "value") else str(row[0])
        by_status[s] = row[1]

    # Aggregate by corridor in SQL
    by_corridor: dict[str, int] = {}
    corridor_rows = (
        q.with_entities(AISGapEvent.corridor_id, func.count())
        .group_by(AISGapEvent.corridor_id)
        .all()
    )
    for row in corridor_rows:
        key = str(row[0]) if row[0] else "no_corridor"
        by_corridor[key] = row[1]

    # Vessels with multiple gaps in last 7 days
    now = datetime.now(UTC)
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
