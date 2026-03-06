"""Detection, corridor, hunt, dark vessel, and fleet endpoints."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.schemas.corridor import CorridorCreateRequest, CorridorUpdateRequest
from app.api._helpers import _audit_log, _validate_date_range, _get_coverage_quality, limiter

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Gap / Spoofing / Loitering / STS Detection
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
def get_spoofing_events(
    vessel_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    from app.models.spoofing_anomaly import SpoofingAnomaly
    _validate_date_range(date_from, date_to)
    q = db.query(SpoofingAnomaly).filter(SpoofingAnomaly.vessel_id == vessel_id)
    if date_from:
        q = q.filter(SpoofingAnomaly.start_time_utc >= datetime(date_from.year, date_from.month, date_from.day))
    if date_to:
        q = q.filter(SpoofingAnomaly.start_time_utc <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59))
    return q.all()


@router.get("/loitering/{vessel_id}", tags=["detection"])
def get_loitering_events(
    vessel_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    from app.models.loitering_event import LoiteringEvent
    _validate_date_range(date_from, date_to)
    q = db.query(LoiteringEvent).filter(LoiteringEvent.vessel_id == vessel_id)
    if date_from:
        q = q.filter(LoiteringEvent.start_time_utc >= datetime(date_from.year, date_from.month, date_from.day))
    if date_to:
        q = q.filter(LoiteringEvent.start_time_utc <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59))
    return q.all()


@router.get("/sts-events", tags=["detection"])
def get_sts_events(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    from app.models.sts_transfer import StsTransferEvent
    _validate_date_range(date_from, date_to)
    limit = min(limit, settings.MAX_QUERY_LIMIT)
    q = db.query(StsTransferEvent)
    if date_from:
        q = q.filter(StsTransferEvent.start_time_utc >= datetime(date_from.year, date_from.month, date_from.day))
    if date_to:
        q = q.filter(StsTransferEvent.start_time_utc <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59))
    q = q.order_by(StsTransferEvent.start_time_utc.desc())
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


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
# Phase K/L/M/N Detector Endpoints
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


@router.post("/detect/track-naturalness", tags=["detection"])
def trigger_track_naturalness(request: Request, db: Session = Depends(get_db)):
    """Trigger track naturalness (synthetic track) detection."""
    if not settings.TRACK_NATURALNESS_ENABLED:
        return {"status": "disabled"}
    from app.modules.track_naturalness_detector import run_track_naturalness_detection
    result = run_track_naturalness_detection(db)
    _audit_log(db, "detect_trigger", "track_naturalness", details=result, request=request)
    db.commit()
    return result


@router.post("/detect/draught", tags=["detection"])
def trigger_draught_detection(request: Request, db: Session = Depends(get_db)):
    """Trigger draught change detection."""
    if not settings.DRAUGHT_DETECTION_ENABLED:
        return {"status": "disabled"}
    from app.modules.draught_detector import run_draught_detection
    result = run_draught_detection(db)
    _audit_log(db, "detect_trigger", "draught", details=result, request=request)
    db.commit()
    return result


@router.post("/detect/stateless-mmsi", tags=["detection"])
def trigger_stateless_mmsi(request: Request, db: Session = Depends(get_db)):
    """Trigger stateless MMSI detection."""
    if not settings.STATELESS_MMSI_DETECTION_ENABLED:
        return {"status": "disabled"}
    from app.modules.stateless_detector import run_stateless_detection
    result = run_stateless_detection(db)
    _audit_log(db, "detect_trigger", "stateless_mmsi", details=result, request=request)
    db.commit()
    return result


@router.post("/detect/flag-hopping", tags=["detection"])
def trigger_flag_hopping(request: Request, db: Session = Depends(get_db)):
    """Trigger flag hopping detection."""
    if not settings.FLAG_HOPPING_DETECTION_ENABLED:
        return {"status": "disabled"}
    from app.modules.flag_hopping_detector import run_flag_hopping_detection
    result = run_flag_hopping_detection(db)
    _audit_log(db, "detect_trigger", "flag_hopping", details=result, request=request)
    db.commit()
    return result


@router.post("/detect/imo-fraud", tags=["detection"])
def trigger_imo_fraud(request: Request, db: Session = Depends(get_db)):
    """Trigger IMO fraud detection."""
    if not settings.IMO_FRAUD_DETECTION_ENABLED:
        return {"status": "disabled"}
    from app.modules.imo_fraud_detector import run_imo_fraud_detection
    result = run_imo_fraud_detection(db)
    _audit_log(db, "detect_trigger", "imo_fraud", details=result, request=request)
    db.commit()
    return result


@router.post("/detect/dark-sts", tags=["detection"])
def trigger_dark_sts(request: Request, db: Session = Depends(get_db)):
    """Trigger dark-dark STS hypothesis generation."""
    if not settings.DARK_STS_DETECTION_ENABLED:
        return {"status": "disabled"}
    from app.modules.sts_detector import detect_sts_events
    result = detect_sts_events(db)
    _audit_log(db, "detect_trigger", "dark_sts", details=result, request=request)
    db.commit()
    return result


@router.get("/satellite-tasking/candidates", tags=["detection"])
def get_satellite_tasking_candidates(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List satellite tasking candidates from dark-dark STS analysis."""
    try:
        from app.models.satellite_tasking_candidate import SatelliteTaskingCandidate
        candidates = (
            db.query(SatelliteTaskingCandidate)
            .order_by(SatelliteTaskingCandidate.created_utc.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {
            "candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "corridor_id": c.corridor_id,
                    "vessel_a_id": c.vessel_a_id,
                    "vessel_b_id": c.vessel_b_id,
                    "gap_overlap_hours": c.gap_overlap_hours,
                    "proximity_nm": c.proximity_nm,
                    "confidence_level": c.confidence_level,
                    "risk_score_component": c.risk_score_component,
                    "created_utc": c.created_utc.isoformat() if c.created_utc else None,
                }
                for c in candidates
            ],
            "total": len(candidates),
        }
    except Exception as e:
        logger.debug("Satellite tasking candidates fetch failed: %s", e)
        return {"candidates": [], "total": 0}


# ---------------------------------------------------------------------------
# Phase O: Fleet & Owner Intelligence Endpoints
# ---------------------------------------------------------------------------

@router.post("/detect/owner-dedup", tags=["detection"])
def trigger_owner_dedup(request: Request, db: Session = Depends(get_db)):
    """Trigger owner name deduplication and clustering."""
    if not settings.FLEET_ANALYSIS_ENABLED:
        return {"status": "disabled"}
    from app.modules.owner_dedup import run_owner_dedup
    result = run_owner_dedup(db)
    _audit_log(db, "detect_trigger", "owner_dedup", details=result, request=request)
    db.commit()
    return result


@router.post("/detect/fleet-patterns", tags=["detection"])
def trigger_fleet_patterns(request: Request, db: Session = Depends(get_db)):
    """Trigger fleet pattern analysis."""
    if not settings.FLEET_ANALYSIS_ENABLED:
        return {"status": "disabled"}
    from app.modules.fleet_analyzer import run_fleet_analysis
    result = run_fleet_analysis(db)
    _audit_log(db, "detect_trigger", "fleet_patterns", details=result, request=request)
    db.commit()
    return result


@router.get("/fleet/clusters", tags=["fleet"])
def list_fleet_clusters(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List owner clusters."""
    try:
        from app.models.owner_cluster import OwnerCluster
        clusters = (
            db.query(OwnerCluster)
            .order_by(OwnerCluster.vessel_count.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {
            "clusters": [
                {
                    "cluster_id": c.cluster_id,
                    "canonical_name": c.canonical_name,
                    "country": c.country,
                    "is_sanctioned": c.is_sanctioned,
                    "vessel_count": c.vessel_count,
                }
                for c in clusters
            ],
            "total": len(clusters),
        }
    except Exception as e:
        logger.debug("Owner clusters fetch failed: %s", e)
        return {"clusters": [], "total": 0}


@router.get("/fleet/clusters/{cluster_id}", tags=["fleet"])
def get_fleet_cluster(cluster_id: int, db: Session = Depends(get_db)):
    """Get details for a specific owner cluster."""
    try:
        from app.models.owner_cluster import OwnerCluster
        from app.models.owner_cluster_member import OwnerClusterMember
        from app.models.vessel_owner import VesselOwner

        cluster = db.query(OwnerCluster).filter(OwnerCluster.cluster_id == cluster_id).first()
        if not cluster:
            raise HTTPException(status_code=404, detail="Cluster not found")

        members = (
            db.query(OwnerClusterMember)
            .filter(OwnerClusterMember.cluster_id == cluster_id)
            .all()
        )
        member_details = []
        for m in members:
            owner = db.query(VesselOwner).filter(VesselOwner.owner_id == m.owner_id).first()
            member_details.append({
                "member_id": m.member_id,
                "owner_id": m.owner_id,
                "owner_name": owner.owner_name if owner else None,
                "similarity_score": m.similarity_score,
            })

        return {
            "cluster_id": cluster.cluster_id,
            "canonical_name": cluster.canonical_name,
            "country": cluster.country,
            "is_sanctioned": cluster.is_sanctioned,
            "vessel_count": cluster.vessel_count,
            "members": member_details,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Owner cluster detail fetch failed for cluster %s: %s", cluster_id, e)
        raise HTTPException(status_code=500, detail="Error fetching cluster details")


@router.get("/fleet/alerts", tags=["fleet"])
def list_fleet_alerts(
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List fleet-level alerts."""
    try:
        from app.models.fleet_alert import FleetAlert
        alerts = (
            db.query(FleetAlert)
            .order_by(FleetAlert.created_utc.desc())
            .limit(min(limit, 200))
            .all()
        )
        return {
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "owner_cluster_id": a.owner_cluster_id,
                    "alert_type": a.alert_type,
                    "vessel_ids": a.vessel_ids_json,
                    "evidence": a.evidence_json,
                    "risk_score_component": a.risk_score_component,
                    "created_utc": a.created_utc.isoformat() if a.created_utc else None,
                }
                for a in alerts
            ],
            "total": len(alerts),
        }
    except Exception as e:
        logger.debug("Fleet alerts fetch failed: %s", e)
        return {"alerts": [], "total": 0}


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
    from sqlalchemy import case

    limit = min(limit, settings.MAX_QUERY_LIMIT)
    q = db.query(Corridor)
    total = q.count()
    corridors = q.offset(skip).limit(limit).all()
    now = datetime.now(timezone.utc)

    corridor_ids = [c.corridor_id for c in corridors]
    stats_map: dict = {}
    if corridor_ids:
        stats_rows = db.query(
            AISGapEvent.corridor_id,
            func.sum(case(
                (AISGapEvent.gap_start_utc >= now - timedelta(days=7), 1),
                else_=0,
            )).label("alert_7d"),
            func.sum(case(
                (AISGapEvent.gap_start_utc >= now - timedelta(days=30), 1),
                else_=0,
            )).label("alert_30d"),
            func.avg(AISGapEvent.risk_score).label("avg_score"),
        ).filter(
            AISGapEvent.corridor_id.in_(corridor_ids),
        ).group_by(AISGapEvent.corridor_id).all()
        for row in stats_rows:
            stats_map[row[0]] = {
                "alert_7d": int(row[1] or 0),
                "alert_30d": int(row[2] or 0),
                "avg_score": round(float(row[3]), 1) if row[3] else None,
            }

    result = []
    for c in corridors:
        s = stats_map.get(c.corridor_id, {})
        result.append({
            "corridor_id": c.corridor_id,
            "name": c.name,
            "corridor_type": str(c.corridor_type.value) if hasattr(c.corridor_type, "value") else c.corridor_type,
            "risk_weight": c.risk_weight,
            "is_jamming_zone": c.is_jamming_zone,
            "description": c.description,
            "alert_count_7d": s.get("alert_7d", 0),
            "alert_count_30d": s.get("alert_30d", 0),
            "avg_risk_score": s.get("avg_score"),
            "coverage_quality": _get_coverage_quality(c.name),
        })
    return {"items": result, "total": total}


@router.get("/corridors/{corridor_id}", tags=["corridors"])
def get_corridor(corridor_id: int, db: Session = Depends(get_db)):
    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent

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

    corridors = db.query(Corridor).all()
    features = []
    for c in corridors:
        geom = None
        if c.geometry:
            try:
                from app.utils.geo import load_geometry
                import shapely.geometry
                shape = load_geometry(c.geometry)
                if shape is not None:
                    geom = shapely.geometry.mapping(shape)
            except Exception as e:
                logger.debug("Corridor geometry deserialization failed for corridor %s: %s", c.corridor_id, e)
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "corridor_id": c.corridor_id,
                "name": c.name,
                "corridor_type": str(c.corridor_type.value) if hasattr(c.corridor_type, "value") else c.corridor_type,
                "risk_weight": c.risk_weight,
                "is_jamming_zone": c.is_jamming_zone,
            },
        })
    return {"type": "FeatureCollection", "features": features}


@router.post("/corridors", tags=["corridors"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_corridor(body: CorridorCreateRequest, request: Request, db: Session = Depends(get_db)):
    """Create a new corridor."""
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
            geom = shape.wkt
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
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def update_corridor(corridor_id: int, body: CorridorUpdateRequest, request: Request, db: Session = Depends(get_db)):
    """Update corridor metadata."""
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
@limiter.limit(settings.RATE_LIMIT_ADMIN)
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


@router.get("/corridors/{corridor_id}/activity", tags=["corridors"])
def get_corridor_activity(
    corridor_id: int,
    granularity: str = Query("week", description="day, week, or month"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Time-series activity for a corridor: gap counts, vessel counts, avg risk."""
    from app.models.gap_event import AISGapEvent
    from app.models.corridor import Corridor

    _validate_date_range(date_from, date_to)

    if granularity not in ("day", "week", "month"):
        raise HTTPException(status_code=422, detail="granularity must be day, week, or month")

    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if not corridor:
        raise HTTPException(status_code=404, detail="Corridor not found")

    q = db.query(AISGapEvent).filter(AISGapEvent.corridor_id == corridor_id)

    if date_from:
        q = q.filter(AISGapEvent.gap_start_utc >= datetime(date_from.year, date_from.month, date_from.day))
    if date_to:
        q = q.filter(AISGapEvent.gap_start_utc <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59))

    if granularity == "day":
        bucket = func.strftime("%Y-%m-%d", AISGapEvent.gap_start_utc)
    elif granularity == "week":
        bucket = func.strftime("%Y-W%W", AISGapEvent.gap_start_utc)
    else:
        bucket = func.strftime("%Y-%m", AISGapEvent.gap_start_utc)

    rows = (
        q.group_by(bucket)
        .with_entities(
            bucket.label("period"),
            func.count(AISGapEvent.gap_event_id).label("gap_count"),
            func.count(func.distinct(AISGapEvent.vessel_id)).label("distinct_vessels"),
            func.avg(AISGapEvent.risk_score).label("avg_risk"),
        )
        .order_by(bucket)
        .all()
    )

    return [
        {
            "period_start": row.period,
            "gap_count": row.gap_count,
            "distinct_vessels": row.distinct_vessels,
            "avg_risk_score": round(float(row.avg_risk), 1) if row.avg_risk else 0.0,
        }
        for row in rows
    ]


# ─── Dark Vessel Detections ───────────────────────────────────────────────────

@router.get("/dark-vessels")
def list_dark_vessels(
    ais_match_result: Optional[str] = None,
    corridor_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    from app.models.stubs import DarkVesselDetection
    _validate_date_range(date_from, date_to)
    q = db.query(DarkVesselDetection)
    if ais_match_result:
        q = q.filter(DarkVesselDetection.ais_match_result == ais_match_result)
    if corridor_id:
        q = q.filter(DarkVesselDetection.corridor_id == corridor_id)
    if date_from:
        q = q.filter(DarkVesselDetection.detection_time_utc >= datetime(date_from.year, date_from.month, date_from.day))
    if date_to:
        q = q.filter(DarkVesselDetection.detection_time_utc <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59))
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


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
@limiter.limit(settings.RATE_LIMIT_ADMIN)
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
@limiter.limit(settings.RATE_LIMIT_ADMIN)
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
@limiter.limit(settings.RATE_LIMIT_ADMIN)
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
def list_hunt_candidates(
    mission_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List all candidates for a mission (paginated)."""
    from app.models.stubs import HuntCandidate
    q = db.query(HuntCandidate).filter(
        HuntCandidate.mission_id == mission_id
    )
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.post("/hunt/missions/{mission_id}/confirm/{candidate_id}", tags=["hunt"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def confirm_hunt_candidate(request: Request, mission_id: int, candidate_id: int, db: Session = Depends(get_db)):
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
