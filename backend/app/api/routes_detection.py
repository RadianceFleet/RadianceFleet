"""Detection, corridor, hunt, dark vessel, and fleet endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api._helpers import _audit_log, _get_coverage_quality, _validate_date_range, limiter
from app.auth import require_auth
from app.config import settings
from app.database import get_db
from app.schemas.corridor import CorridorCreateRequest, CorridorUpdateRequest
from app.schemas.hunt import (
    DarkVesselDetectionRead,
    DarkVesselListResponse,
    HuntCandidateListResponse,
    HuntTargetCreateRequest,
    MissionFinalizeRequest,
    SearchMissionCreateRequest,
    SearchMissionRead,
    VesselTargetProfileRead,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Gap / Spoofing / Loitering / STS Detection
# ---------------------------------------------------------------------------


@router.post("/gaps/detect", tags=["detection"])
def detect_gaps(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run AIS gap detection over the specified date range."""
    from app.modules.gap_detector import run_gap_detection

    return run_gap_detection(db, date_from=date_from, date_to=date_to)


@router.post("/spoofing/detect", tags=["detection"])
def detect_spoofing(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run AIS spoofing detection (impossible speed, anchor-in-ocean, circle spoof, etc.)."""
    from app.modules.gap_detector import run_spoofing_detection

    return run_spoofing_detection(db, date_from=date_from, date_to=date_to)


@router.get("/spoofing/{vessel_id}", tags=["detection"])
def get_spoofing_events(
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    from app.models.spoofing_anomaly import SpoofingAnomaly

    _validate_date_range(date_from, date_to)
    q = db.query(SpoofingAnomaly).filter(SpoofingAnomaly.vessel_id == vessel_id)
    if date_from:
        q = q.filter(
            SpoofingAnomaly.start_time_utc
            >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            SpoofingAnomaly.start_time_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    results = q.all()
    return {"items": results, "total": len(results)}


@router.get("/spoofing", tags=["detection"])
def get_global_spoofing(
    date_from: date | None = None,
    date_to: date | None = None,
    anomaly_type: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Global spoofing anomalies list."""
    from app.models.spoofing_anomaly import SpoofingAnomaly

    _validate_date_range(date_from, date_to)
    limit = min(limit, settings.MAX_QUERY_LIMIT)

    q = db.query(SpoofingAnomaly)
    if anomaly_type:
        q = q.filter(SpoofingAnomaly.anomaly_type == anomaly_type)
    if date_from:
        q = q.filter(
            SpoofingAnomaly.start_time_utc
            >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            SpoofingAnomaly.start_time_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    q = q.order_by(SpoofingAnomaly.start_time_utc.desc())
    total = q.count()
    items = q.offset(skip).limit(limit).all()

    return {
        "items": [
            {
                "anomaly_id": e.anomaly_id,
                "vessel_id": e.vessel_id,
                "anomaly_type": str(e.anomaly_type.value)
                if hasattr(e.anomaly_type, "value")
                else e.anomaly_type,
                "start_time_utc": e.start_time_utc.isoformat() if e.start_time_utc else None,
                "risk_score_component": e.risk_score_component,
            }
            for e in items
        ],
        "total": total,
    }


@router.get("/loitering/{vessel_id}", tags=["detection"])
def get_loitering_events(
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    from app.models.loitering_event import LoiteringEvent

    _validate_date_range(date_from, date_to)
    q = db.query(LoiteringEvent).filter(LoiteringEvent.vessel_id == vessel_id)
    if date_from:
        q = q.filter(
            LoiteringEvent.start_time_utc
            >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            LoiteringEvent.start_time_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    results = q.all()
    return {"items": results, "total": len(results)}


@router.get("/sts-chains", tags=["detection"])
def get_sts_chains(
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List STS relay chain alerts with vessel names."""
    from app.models.fleet_alert import FleetAlert
    from app.models.vessel import Vessel

    _validate_date_range(date_from, date_to)
    limit = min(limit, settings.MAX_QUERY_LIMIT)

    q = db.query(FleetAlert).filter(FleetAlert.alert_type == "sts_relay_chain")
    if date_from:
        q = q.filter(
            FleetAlert.created_utc >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            FleetAlert.created_utc <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    q = q.order_by(FleetAlert.created_utc.desc())
    total = q.count()
    alerts = q.offset(skip).limit(limit).all()

    # Collect all vessel IDs and batch-fetch names
    all_vessel_ids: set[int] = set()
    for a in alerts:
        ev = a.evidence_json or {}
        for vid in ev.get("chain_vessel_ids", []):
            all_vessel_ids.add(vid)

    vessel_name_map: dict[int, str | None] = {}
    if all_vessel_ids:
        rows = (
            db.query(Vessel.vessel_id, Vessel.name)
            .filter(Vessel.vessel_id.in_(all_vessel_ids))
            .all()
        )
        vessel_name_map = {r[0]: r[1] for r in rows}

    items = []
    for a in alerts:
        ev = a.evidence_json or {}
        chain_vessel_ids = ev.get("chain_vessel_ids", [])
        items.append(
            {
                "alert_id": a.alert_id,
                "chain_vessel_ids": chain_vessel_ids,
                "vessel_names": {vid: vessel_name_map.get(vid) for vid in chain_vessel_ids},
                "intermediary_vessel_ids": ev.get("intermediary_vessel_ids", []),
                "hops": ev.get("hops", []),
                "chain_length": len(chain_vessel_ids),
                "risk_score_component": a.risk_score_component,
                "created_utc": a.created_utc.isoformat() if a.created_utc else None,
            }
        )

    return {"items": items, "total": total}


@router.get("/loitering", tags=["detection"])
def get_global_loitering(
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Global loitering events list for map overlay."""
    from app.models.loitering_event import LoiteringEvent

    _validate_date_range(date_from, date_to)
    limit = min(limit, settings.MAX_QUERY_LIMIT)

    q = db.query(LoiteringEvent)
    if date_from:
        q = q.filter(
            LoiteringEvent.start_time_utc
            >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            LoiteringEvent.start_time_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    q = q.order_by(LoiteringEvent.start_time_utc.desc())
    total = q.count()
    items = q.offset(skip).limit(limit).all()

    return {
        "items": [
            {
                "loiter_id": e.loiter_id,
                "vessel_id": e.vessel_id,
                "mean_lat": e.mean_lat,
                "mean_lon": e.mean_lon,
                "duration_hours": e.duration_hours,
                "corridor_id": e.corridor_id,
                "start_time_utc": e.start_time_utc.isoformat() if e.start_time_utc else None,
                "median_sog_kn": e.median_sog_kn,
            }
            for e in items
        ],
        "total": total,
    }


@router.get("/sts-events", tags=["detection"])
def get_sts_events(
    date_from: date | None = None,
    date_to: date | None = None,
    vessel_id: int | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    from app.models.sts_transfer import StsTransferEvent

    _validate_date_range(date_from, date_to)
    limit = min(limit, settings.MAX_QUERY_LIMIT)
    q = db.query(StsTransferEvent)
    if vessel_id is not None:
        q = q.filter(
            or_(
                StsTransferEvent.vessel_1_id == vessel_id, StsTransferEvent.vessel_2_id == vessel_id
            )
        )
    if date_from:
        q = q.filter(
            StsTransferEvent.start_time_utc
            >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            StsTransferEvent.start_time_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    q = q.order_by(StsTransferEvent.start_time_utc.desc())
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.patch("/sts-events/{sts_id}", tags=["detection"])
def validate_sts_event(
    sts_id: int,
    user_validated: bool | None = None,
    confidence_override: float | None = Query(None, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Analyst validation: confirm/reject an STS transfer event."""
    from app.models.sts_transfer import StsTransferEvent

    event = db.query(StsTransferEvent).filter(StsTransferEvent.sts_id == sts_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="STS event not found")
    if user_validated is not None:
        event.user_validated = user_validated
    if confidence_override is not None:
        event.confidence_override = confidence_override
    db.commit()
    return {
        "sts_id": sts_id,
        "user_validated": event.user_validated,
        "confidence_override": event.confidence_override,
    }


@router.get("/route-laundering/{vessel_id}", tags=["detection"])
def get_route_laundering(
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    """Get route laundering anomalies for a vessel."""
    from app.models.base import SpoofingTypeEnum
    from app.models.spoofing_anomaly import SpoofingAnomaly

    _validate_date_range(date_from, date_to)
    q = db.query(SpoofingAnomaly).filter(
        SpoofingAnomaly.vessel_id == vessel_id,
        SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING,
    )
    if date_from:
        q = q.filter(
            SpoofingAnomaly.start_time_utc
            >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            SpoofingAnomaly.start_time_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    results = q.order_by(SpoofingAnomaly.start_time_utc.desc()).all()
    return {"items": results, "total": len(results)}


@router.post("/loitering/detect", tags=["detection"])
def detect_loitering(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run loitering detection and update laid-up vessel flags."""
    from app.modules.loitering_detector import detect_laid_up_vessels, run_loitering_detection

    result = run_loitering_detection(db, date_from=date_from, date_to=date_to)
    laid_up = detect_laid_up_vessels(db)
    result["laid_up_updated"] = laid_up.get("laid_up_updated", 0)
    return result


@router.post("/sts/detect", tags=["detection"])
def detect_sts(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run ship-to-ship transfer detection.

    NOTE: Run /gaps/detect first — one_vessel_dark_during_proximity (+15) requires
    gap records to be present before STS detection runs.
    """
    from app.modules.sts_detector import detect_sts_events

    return detect_sts_events(db, date_from=date_from, date_to=date_to)


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
            "items": [
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
        from sqlalchemy.exc import OperationalError

        if isinstance(e, OperationalError):
            logger.debug("Owner clusters fetch failed (empty DB?): %s", e)
        else:
            logger.warning("Owner clusters fetch failed: %s", e)
        return {"items": [], "total": 0}


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
            db.query(OwnerClusterMember).filter(OwnerClusterMember.cluster_id == cluster_id).all()
        )
        member_details = []
        for m in members:
            owner = db.query(VesselOwner).filter(VesselOwner.owner_id == m.owner_id).first()
            member_details.append(
                {
                    "member_id": m.member_id,
                    "owner_id": m.owner_id,
                    "owner_name": owner.owner_name if owner else None,
                    "similarity_score": m.similarity_score,
                }
            )

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
        raise HTTPException(status_code=500, detail="Error fetching cluster details") from e


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
    from sqlalchemy import case

    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent

    limit = min(limit, settings.MAX_QUERY_LIMIT)
    q = db.query(Corridor)
    total = q.count()
    corridors = q.offset(skip).limit(limit).all()
    now = datetime.now(UTC)

    corridor_ids = [c.corridor_id for c in corridors]
    stats_map: dict = {}
    if corridor_ids:
        stats_rows = (
            db.query(
                AISGapEvent.corridor_id,
                func.sum(
                    case(
                        (AISGapEvent.gap_start_utc >= now - timedelta(days=7), 1),
                        else_=0,
                    )
                ).label("alert_7d"),
                func.sum(
                    case(
                        (AISGapEvent.gap_start_utc >= now - timedelta(days=30), 1),
                        else_=0,
                    )
                ).label("alert_30d"),
                func.avg(AISGapEvent.risk_score).label("avg_score"),
            )
            .filter(
                AISGapEvent.corridor_id.in_(corridor_ids),
            )
            .group_by(AISGapEvent.corridor_id)
            .all()
        )
        for row in stats_rows:
            stats_map[row[0]] = {
                "alert_7d": int(row[1] or 0),
                "alert_30d": int(row[2] or 0),
                "avg_score": round(float(row[3]), 1) if row[3] else None,
            }

    result = []
    for c in corridors:
        s = stats_map.get(c.corridor_id, {})
        result.append(
            {
                "corridor_id": c.corridor_id,
                "name": c.name,
                "corridor_type": str(c.corridor_type.value)
                if hasattr(c.corridor_type, "value")
                else c.corridor_type,
                "risk_weight": c.risk_weight,
                "is_jamming_zone": c.is_jamming_zone,
                "description": c.description,
                "alert_count_7d": s.get("alert_7d", 0),
                "alert_count_30d": s.get("alert_30d", 0),
                "avg_risk_score": s.get("avg_score"),
                "coverage_quality": _get_coverage_quality(c.name),
            }
        )
    return {"items": result, "total": total}


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
                import shapely.geometry

                from app.utils.geo import load_geometry

                shape = load_geometry(c.geometry)
                if shape is not None:
                    geom = shapely.geometry.mapping(shape)
            except Exception as e:
                logger.warning(
                    "Corridor geometry deserialization failed for corridor %s: %s", c.corridor_id, e
                )
        features.append(
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "corridor_id": c.corridor_id,
                    "name": c.name,
                    "corridor_type": str(c.corridor_type.value)
                    if hasattr(c.corridor_type, "value")
                    else c.corridor_type,
                    "risk_weight": c.risk_weight,
                    "is_jamming_zone": c.is_jamming_zone,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.get("/corridors/{corridor_id}", tags=["corridors"])
def get_corridor(corridor_id: int, db: Session = Depends(get_db)):
    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent

    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if not corridor:
        raise HTTPException(status_code=404, detail="Corridor not found")

    now = datetime.now(UTC)
    alert_7d = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.corridor_id == corridor_id,
            AISGapEvent.gap_start_utc >= now - timedelta(days=7),
        )
        .count()
    )
    alert_30d = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.corridor_id == corridor_id,
            AISGapEvent.gap_start_utc >= now - timedelta(days=30),
        )
        .count()
    )

    return {
        "corridor_id": corridor.corridor_id,
        "name": corridor.name,
        "corridor_type": str(corridor.corridor_type.value)
        if hasattr(corridor.corridor_type, "value")
        else corridor.corridor_type,
        "risk_weight": corridor.risk_weight,
        "is_jamming_zone": corridor.is_jamming_zone,
        "description": corridor.description,
        "alert_count_7d": alert_7d,
        "alert_count_30d": alert_30d,
        "coverage_quality": _get_coverage_quality(corridor.name),
    }


@router.post("/corridors", tags=["corridors"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_corridor(body: CorridorCreateRequest, request: Request, db: Session = Depends(get_db), _auth: dict = Depends(require_auth)):
    """Create a new corridor."""
    from app.models.base import CorridorTypeEnum
    from app.models.corridor import Corridor

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
            raise HTTPException(status_code=400, detail=f"Invalid geometry_wkt: {e}") from e

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
    _audit_log(
        db,
        "create",
        "corridor",
        corridor.corridor_id,
        details={
            "name": body.name,
            "corridor_type": ct_str,
        },
        request=request,
    )
    db.commit()
    return {"corridor_id": corridor.corridor_id, "status": "created"}


@router.patch("/corridors/{corridor_id}", tags=["corridors"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def update_corridor(
    corridor_id: int, body: CorridorUpdateRequest, request: Request, db: Session = Depends(get_db), _auth: dict = Depends(require_auth)
):
    """Update corridor metadata."""
    from app.models.base import CorridorTypeEnum
    from app.models.corridor import Corridor

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
            raise HTTPException(
                status_code=400, detail=f"corridor_type must be one of: {valid_types}"
            )
        corridor.corridor_type = updates["corridor_type"]

    _audit_log(db, "update", "corridor", corridor_id, details=updates, request=request)
    db.commit()
    return {
        "corridor_id": corridor.corridor_id,
        "name": corridor.name,
        "corridor_type": str(corridor.corridor_type.value)
        if hasattr(corridor.corridor_type, "value")
        else corridor.corridor_type,
        "risk_weight": corridor.risk_weight,
        "is_jamming_zone": corridor.is_jamming_zone,
        "status": "updated",
    }


@router.delete("/corridors/{corridor_id}", tags=["corridors"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def delete_corridor(corridor_id: int, request: Request, db: Session = Depends(get_db), _auth: dict = Depends(require_auth)):
    """Delete a corridor. Returns 409 if gap events are linked to it."""
    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent

    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if not corridor:
        raise HTTPException(status_code=404, detail="Corridor not found")

    linked_gaps = db.query(AISGapEvent).filter(AISGapEvent.corridor_id == corridor_id).count()
    if linked_gaps > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete corridor: {linked_gaps} gap event(s) reference it. "
            "Unlink or reassign those gaps first.",
        )

    _audit_log(
        db, "delete", "corridor", corridor_id, details={"name": corridor.name}, request=request
    )
    db.delete(corridor)
    db.commit()
    return {"status": "deleted", "corridor_id": corridor_id}


@router.get("/corridors/{corridor_id}/activity", tags=["corridors"])
def get_corridor_activity(
    corridor_id: int,
    granularity: str = Query("week", description="day, week, or month"),
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    """Time-series activity for a corridor: gap counts, vessel counts, avg risk."""
    from app.models.corridor import Corridor
    from app.models.gap_event import AISGapEvent

    _validate_date_range(date_from, date_to)

    if granularity not in ("day", "week", "month"):
        raise HTTPException(status_code=422, detail="granularity must be day, week, or month")

    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if not corridor:
        raise HTTPException(status_code=404, detail="Corridor not found")

    q = db.query(AISGapEvent).filter(AISGapEvent.corridor_id == corridor_id)

    if date_from:
        q = q.filter(
            AISGapEvent.gap_start_utc >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            AISGapEvent.gap_start_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )

    dialect_name = db.bind.dialect.name if db.bind else "sqlite"

    if dialect_name == "postgresql":
        if granularity == "day":
            bucket = func.to_char(AISGapEvent.gap_start_utc, "YYYY-MM-DD")
        elif granularity == "week":
            bucket = func.to_char(AISGapEvent.gap_start_utc, 'IYYY-"W"IW')
        else:
            bucket = func.to_char(AISGapEvent.gap_start_utc, "YYYY-MM")
    else:
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


@router.get("/dark-vessels", response_model=DarkVesselListResponse)
def list_dark_vessels(
    ais_match_result: str | None = None,
    corridor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
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
        q = q.filter(
            DarkVesselDetection.detection_time_utc
            >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            DarkVesselDetection.detection_time_utc
            <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.get("/dark-vessels/{detection_id}", response_model=DarkVesselDetectionRead)
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


@router.get("/hunt/targets", tags=["hunt"], response_model=list[VesselTargetProfileRead])
def list_hunt_targets(
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)
):
    """List all hunt target profiles."""
    from app.models.stubs import VesselTargetProfile

    return db.query(VesselTargetProfile).offset(skip).limit(limit).all()


@router.get("/hunt/targets/{profile_id}", tags=["hunt"], response_model=VesselTargetProfileRead)
def get_hunt_target(profile_id: int, db: Session = Depends(get_db)):
    """Get a hunt target profile by ID."""
    from app.models.stubs import VesselTargetProfile

    profile = (
        db.query(VesselTargetProfile).filter(VesselTargetProfile.profile_id == profile_id).first()
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Target profile not found")
    return profile


@router.get("/hunt/missions", tags=["hunt"], response_model=list[SearchMissionRead])
def list_hunt_missions(
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)
):
    """List all search missions."""
    from app.models.stubs import SearchMission

    return (
        db.query(SearchMission)
        .order_by(SearchMission.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/hunt/missions/{mission_id}", tags=["hunt"], response_model=SearchMissionRead)
def get_hunt_mission(mission_id: int, db: Session = Depends(get_db)):
    """Get a search mission by ID."""
    from app.models.stubs import SearchMission

    mission = db.query(SearchMission).filter(SearchMission.mission_id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@router.get(
    "/hunt/missions/{mission_id}/candidates",
    tags=["hunt"],
    response_model=HuntCandidateListResponse,
)
def list_hunt_candidates(
    mission_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List all candidates for a mission (paginated)."""
    from app.models.stubs import HuntCandidate

    q = db.query(HuntCandidate).filter(HuntCandidate.mission_id == mission_id)
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {"items": items, "total": total}


@router.post("/hunt/targets", tags=["hunt"], response_model=VesselTargetProfileRead)
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_hunt_target(
    body: HuntTargetCreateRequest, request: Request, db: Session = Depends(get_db)
):
    """Create a vessel target profile for hunt."""
    from app.models.stubs import VesselTargetProfile
    from app.models.vessel import Vessel

    vessel = db.query(Vessel).filter(Vessel.vessel_id == body.vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    profile = VesselTargetProfile(
        vessel_id=body.vessel_id,
        deadweight_dwt=vessel.deadweight,
        last_ais_position_lat=body.last_lat,
        last_ais_position_lon=body.last_lon,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.post("/hunt/missions", tags=["hunt"], response_model=SearchMissionRead)
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_hunt_mission(
    body: SearchMissionCreateRequest, request: Request, db: Session = Depends(get_db)
):
    """Create a search mission for a target profile."""
    from app.models.stubs import SearchMission, VesselTargetProfile

    if body.search_end_utc <= body.search_start_utc:
        raise HTTPException(status_code=400, detail="search_end_utc must be after search_start_utc")

    profile = (
        db.query(VesselTargetProfile)
        .filter(VesselTargetProfile.profile_id == body.target_profile_id)
        .first()
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Target profile not found")

    mission = SearchMission(
        vessel_id=profile.vessel_id,
        profile_id=profile.profile_id,
        search_start_utc=body.search_start_utc,
        search_end_utc=body.search_end_utc,
        center_lat=profile.last_ais_position_lat,
        center_lon=profile.last_ais_position_lon,
    )
    db.add(mission)
    db.commit()
    db.refresh(mission)
    return mission


@router.post(
    "/hunt/missions/{mission_id}/analyze", tags=["hunt"], response_model=HuntCandidateListResponse
)
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def analyze_hunt_mission(mission_id: int, request: Request, db: Session = Depends(get_db)):
    """Run hunt analysis on a mission (synchronous)."""
    from app.models.stubs import SearchMission

    mission = db.query(SearchMission).filter(SearchMission.mission_id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")

    candidates = []
    try:
        from app.modules.vessel_hunt import find_hunt_candidates

        candidates = find_hunt_candidates(mission.mission_id, db)
    except ImportError:
        logger.warning("vessel_hunt module not available, returning empty candidates")

    return {"items": candidates, "total": len(candidates)}


@router.put("/hunt/missions/{mission_id}/finalize", tags=["hunt"], response_model=SearchMissionRead)
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def finalize_hunt_mission(
    mission_id: int,
    body: MissionFinalizeRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Finalize a mission by selecting a candidate."""
    from app.models.stubs import HuntCandidate, SearchMission

    mission = db.query(SearchMission).filter(SearchMission.mission_id == mission_id).first()
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    if getattr(mission, "status", None) == "finalized":
        raise HTTPException(status_code=409, detail="Mission already finalized")

    candidate = (
        db.query(HuntCandidate)
        .filter(
            HuntCandidate.candidate_id == body.candidate_id,
            HuntCandidate.mission_id == mission_id,
        )
        .first()
    )
    if not candidate:
        raise HTTPException(
            status_code=400, detail="Candidate not found or does not belong to this mission"
        )

    mission.status = "finalized"
    db.commit()
    db.refresh(mission)
    return mission


# ---------------------------------------------------------------------------
# Merge Chains
# ---------------------------------------------------------------------------


@router.get("/merge-chains", tags=["merge-chains"])
def list_merge_chains(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    min_confidence: float | None = None,
    confidence_band: str | None = None,
    db: Session = Depends(get_db),
):
    """List merge chains with hydrated vessel nodes and edges."""
    from app.models.merge_candidate import MergeCandidate
    from app.models.vessel import Vessel
    from app.modules.merge_chain import get_merge_chain_count, get_merge_chains

    chains = get_merge_chains(
        db,
        skip=skip,
        limit=limit,
        min_confidence=min_confidence,
        confidence_band=confidence_band,
    )
    total = get_merge_chain_count(
        db,
        min_confidence=min_confidence,
        confidence_band=confidence_band,
    )

    items = []
    for chain in chains:
        vessel_ids = chain.vessel_ids_json or []
        link_ids = chain.links_json or []

        # Hydrate vessel nodes
        vessels = {}
        if vessel_ids:
            rows = db.query(Vessel).filter(Vessel.vessel_id.in_(vessel_ids)).all()
            vessels = {v.vessel_id: v for v in rows}

        nodes = []
        for vid in vessel_ids:
            v = vessels.get(vid)
            nodes.append(
                {
                    "vessel_id": vid,
                    "mmsi": v.mmsi if v else None,
                    "name": v.name if v else None,
                    "flag": getattr(v, "flag", None) if v else None,
                    "role": "primary" if vid == vessel_ids[0] else "absorbed",
                }
            )

        # Hydrate edges from merge candidates
        edges = []
        if link_ids:
            mc_rows = (
                db.query(MergeCandidate).filter(MergeCandidate.candidate_id.in_(link_ids)).all()
            )
            for mc in mc_rows:
                edges.append(
                    {
                        "candidate_id": mc.candidate_id,
                        "source_id": mc.vessel_a_id,
                        "target_id": mc.vessel_b_id,
                        "confidence": mc.confidence_score,
                        "evidence": None,
                    }
                )

        items.append(
            {
                "chain_id": chain.chain_id,
                "confidence_band": chain.confidence_band,
                "confidence": chain.confidence,
                "chain_length": chain.chain_length,
                "nodes": nodes,
                "edges": edges,
            }
        )

    return {"items": items, "total": total}


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@router.get("/coverage/geojson", tags=["coverage"])
def coverage_geojson():
    """AIS coverage quality regions as GeoJSON FeatureCollection."""
    from pathlib import Path

    import yaml

    config_path = Path(settings.COVERAGE_CONFIG)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    if not config_path.exists():
        raise HTTPException(status_code=404, detail=f"Coverage config not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text()) or {}

    features = []
    for region_key, region_data in raw.items():
        if not isinstance(region_data, dict):
            continue

        geometry = None
        wkt = region_data.get("geometry_wkt") or region_data.get("geometry")
        if wkt:
            try:
                import shapely.geometry
                from shapely import wkt as shapely_wkt

                shape = shapely_wkt.loads(wkt)
                geometry = shapely.geometry.mapping(shape)
            except Exception:
                logger.debug("Coverage WKT geometry parsing failed", exc_info=True)

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": region_data.get("name", region_key),
                    "quality": region_data.get("quality", "UNKNOWN"),
                    "description": region_data.get("description", ""),
                },
                "geometry": geometry,
            }
        )

    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Signal Effectiveness
# ---------------------------------------------------------------------------


@router.get("/accuracy/signal-effectiveness", tags=["scoring"])
def signal_effectiveness_endpoint(db: Session = Depends(get_db)):
    """Per-signal FP rate and lift from analyst verdicts."""
    from app.modules.validation_harness import live_signal_effectiveness

    return live_signal_effectiveness(db)


# ---------------------------------------------------------------------------
# Satellite Orders
# ---------------------------------------------------------------------------


@router.get("/satellite/providers", tags=["satellite"])
def list_satellite_providers(db: Session = Depends(get_db)):
    """List configured satellite providers and budget."""
    from app.modules.satellite_order_manager import get_satellite_budget_status
    from app.modules.satellite_providers import list_providers

    providers = list_providers()
    configured = []
    for p in providers:
        key_field = f"{p.upper()}_API_KEY"
        configured.append({"name": p, "configured": bool(getattr(settings, key_field, None))})
    budget = get_satellite_budget_status(db)
    return {"providers": configured, "budget": budget}


@router.get("/satellite/orders", tags=["satellite"])
def list_satellite_orders(
    status: str | None = None,
    provider: str | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List satellite orders."""
    from app.models.satellite_order import SatelliteOrder

    q = db.query(SatelliteOrder).order_by(SatelliteOrder.created_utc.desc())
    if status:
        q = q.filter(SatelliteOrder.status == status)
    if provider:
        q = q.filter(SatelliteOrder.provider == provider)
    total = q.count()
    orders = q.offset(skip).limit(limit).all()
    return {
        "total": total,
        "orders": [
            {
                "satellite_order_id": o.satellite_order_id,
                "provider": o.provider,
                "order_type": o.order_type,
                "external_order_id": o.external_order_id,
                "status": o.status,
                "cost_usd": o.cost_usd,
                "created_utc": o.created_utc.isoformat() if o.created_utc else None,
                "updated_utc": o.updated_utc.isoformat() if o.updated_utc else None,
            }
            for o in orders
        ],
    }


@router.get("/satellite/orders/{order_id}", tags=["satellite"])
def get_satellite_order(order_id: int, db: Session = Depends(get_db)):
    """Get satellite order detail."""
    from app.models.satellite_order import SatelliteOrder

    order = db.query(SatelliteOrder).filter(SatelliteOrder.satellite_order_id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return {c.name: getattr(order, c.name) for c in order.__table__.columns}


@router.post("/satellite/orders/search", tags=["satellite"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def search_satellite_archive(request: Request, body: dict, db: Session = Depends(get_db), _auth: dict = Depends(require_auth)):
    """Search satellite archive for an alert."""
    from app.modules.satellite_order_manager import search_archive_for_alert

    alert_id = body.get("alert_id")
    provider = body.get("provider", "planet")
    if not alert_id:
        raise HTTPException(status_code=422, detail="alert_id required")
    try:
        return search_archive_for_alert(db, alert_id, provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/satellite/orders/{order_id}/submit", tags=["satellite"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def submit_satellite_order(
    order_id: int,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Submit a draft satellite order."""
    from app.modules.satellite_order_manager import submit_order

    scene_ids = body.get("scene_ids", [])
    try:
        return submit_order(db, order_id, scene_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/satellite/orders/{order_id}/cancel", tags=["satellite"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def cancel_satellite_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Cancel a satellite order."""
    from app.modules.satellite_order_manager import cancel_order

    try:
        return cancel_order(db, order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/satellite/orders/poll", tags=["satellite"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def poll_satellite_orders(request: Request, db: Session = Depends(get_db), _auth: dict = Depends(require_auth)):
    """Poll status of active satellite orders."""
    from app.modules.satellite_order_manager import poll_order_status

    return {"results": poll_order_status(db)}


@router.get("/satellite/budget", tags=["satellite"])
def satellite_budget(db: Session = Depends(get_db)):
    """Current satellite imagery budget status."""
    from app.modules.satellite_order_manager import get_satellite_budget_status

    return get_satellite_budget_status(db)


# ---------------------------------------------------------------------------
# Yente Sanctions Screening
# ---------------------------------------------------------------------------


@router.post("/detect/screen-vessel/{vessel_id}", tags=["detection"])
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def screen_vessel(
    vessel_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Screen a vessel against sanctions lists via the yente API.

    Requires authentication.  Returns 503 if yente is disabled, 404 if vessel
    not found.
    """
    if not settings.YENTE_ENABLED:
        raise HTTPException(status_code=503, detail="Yente sanctions screening is disabled")

    from app.models.vessel import Vessel
    from app.modules.watchlist_loader import screen_vessel_via_yente

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    result = screen_vessel_via_yente(db, vessel)
    return {"vessel_id": vessel_id, "screening": result}


@router.post("/cluster-trajectories", tags=["detection"])
def cluster_trajectories(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run DBSCAN trajectory clustering on AIS data.

    Groups vessel trajectory segments into clusters using a weighted
    haversine distance metric. Identifies anomalous patterns and noise.
    Requires DBSCAN_CLUSTERING_ENABLED=true.
    """
    from app.modules.dbscan_trajectory_detector import run_trajectory_clustering

    dt_from = datetime.combine(date_from, datetime.min.time()) if date_from else None
    dt_to = datetime.combine(date_to, datetime.max.time()) if date_to else None
    return run_trajectory_clustering(db, date_from=dt_from, date_to=dt_to)


@router.get("/clusters", tags=["detection"])
def list_clusters(
    include_noise: bool = Query(False, description="Include noise pseudo-cluster"),
    db: Session = Depends(get_db),
):
    """List all trajectory clusters."""
    from app.modules.dbscan_trajectory_detector import get_clusters

    return get_clusters(db, include_noise=include_noise)


@router.get("/clusters/{vessel_id}", tags=["detection"])
def get_vessel_clusters(
    vessel_id: int,
    db: Session = Depends(get_db),
):
    """Get trajectory cluster memberships for a vessel."""
    from app.modules.dbscan_trajectory_detector import get_vessel_cluster_memberships

    return get_vessel_cluster_memberships(db, vessel_id)


# ---------------------------------------------------------------------------
# AIS Reporting Anomaly Detection
# ---------------------------------------------------------------------------


@router.post("/detect/reporting-anomaly", tags=["detection"])
def detect_reporting_anomaly(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run AIS reporting rate anomaly detection across all vessels."""
    from app.modules.ais_reporting_anomaly_detector import run_reporting_anomaly_detection

    return run_reporting_anomaly_detection(db)


@router.get("/detect/reporting-anomaly/{vessel_id}", tags=["detection"])
def get_reporting_anomaly(
    vessel_id: int,
    db: Session = Depends(get_db),
):
    """Analyse AIS reporting patterns for a single vessel."""
    from app.models.vessel import Vessel
    from app.modules.ais_reporting_anomaly_detector import analyse_vessel_reporting

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    return analyse_vessel_reporting(db, vessel_id)


# ---------------------------------------------------------------------------
# Ownership Transparency
# ---------------------------------------------------------------------------


@router.post("/detect/ownership-transparency/{vessel_id}", tags=["detection"])
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
def analyze_ownership_transparency(
    vessel_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_auth),
):
    """Analyze beneficial ownership transparency for a vessel.

    Enriches ownership records via OpenCorporates, detects SPV structures
    and jurisdiction hopping.  Requires authentication.
    Returns 503 if OpenCorporates is disabled, 404 if vessel not found.
    """
    if not settings.OPENCORPORATES_ENABLED:
        raise HTTPException(
            status_code=503, detail="OpenCorporates ownership transparency is disabled"
        )

    from app.models.vessel import Vessel
    from app.modules.ownership_transparency import (
        detect_jurisdiction_hopping,
        enrich_vessel_ownership,
        score_ownership_transparency,
    )

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    enrichment = enrich_vessel_ownership(db, vessel_id)
    jur_hopping = detect_jurisdiction_hopping(db, vessel_id)
    scoring = score_ownership_transparency(db, vessel_id)

    return {
        "vessel_id": vessel_id,
        "enrichment": enrichment,
        "jurisdiction_hopping": jur_hopping,
        "scoring": scoring,
    }


@router.post("/validate-gaps-sar", tags=["detection"])
def validate_gaps_sar(
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Cross-correlate AIS gaps with SAR/VIIRS detections (v4.0)."""
    from app.modules.gap_sar_validator import validate_gaps_with_sar

    dt_from = datetime.combine(date_from, datetime.min.time()) if date_from else None
    dt_to = datetime.combine(date_to, datetime.max.time()) if date_to else None
    return validate_gaps_with_sar(db, date_from=dt_from, date_to=dt_to)


# ---------------------------------------------------------------------------
# Isolation Forest Anomaly Detection
# ---------------------------------------------------------------------------


@router.post("/detect/isolation-forest", tags=["detection"])
def detect_isolation_forest(
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run Isolation Forest multi-feature anomaly detection across all vessels."""
    from app.modules.isolation_forest_detector import run_isolation_forest_detection

    return run_isolation_forest_detection(db)


@router.get("/detect/isolation-forest/{vessel_id}", tags=["detection"])
def get_isolation_forest_anomaly(
    vessel_id: int,
    db: Session = Depends(get_db),
):
    """Get Isolation Forest anomaly result for a specific vessel."""
    from app.modules.isolation_forest_detector import get_vessel_anomaly

    result = get_vessel_anomaly(db, vessel_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No anomaly record for this vessel")
    return result


# ---------------------------------------------------------------------------
# Insurance Gap Timeline Detection
# ---------------------------------------------------------------------------


@router.post("/detect/insurance-gaps", tags=["detection"])
def detect_insurance_gaps_endpoint(
    vessel_id: int = Query(..., description="Vessel ID to analyse"),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_auth),
):
    """Run insurance gap timeline detection for a vessel."""
    from app.models.vessel import Vessel
    from app.modules.insurance_gap_detector import detect_insurance_gaps

    vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if not vessel:
        raise HTTPException(status_code=404, detail="Vessel not found")

    gaps = detect_insurance_gaps(db, vessel_id)
    return {"status": "ok", "vessel_id": vessel_id, "gaps_found": len(gaps), "gaps": gaps}


@router.get("/detect/insurance-gaps/{vessel_id}", tags=["detection"])
def get_insurance_gaps(
    vessel_id: int,
    db: Session = Depends(get_db),
):
    """Get insurance gap events for a vessel."""
    from app.modules.insurance_gap_detector import get_vessel_insurance_gaps

    gaps = get_vessel_insurance_gaps(db, vessel_id)
    return {"vessel_id": vessel_id, "total": len(gaps), "items": gaps}
