"""Vessel identity merging — detect, score, execute and reverse merges.

Shadow fleet vessels swap transponder identities between port visits.
This module links "went dark" vessel A to "newly appeared" vessel B using
speed-feasibility matching, then merges all FK records under a single
canonical vessel_id.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.audit_log import AuditLog
from app.models.gap_event import AISGapEvent
from app.models.loitering_event import LoiteringEvent
from app.models.merge_candidate import MergeCandidate
from app.models.merge_operation import MergeOperation
from app.models.port_call import PortCall
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.sts_transfer import StsTransferEvent
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.models.base import MergeCandidateStatusEnum
from app.utils.geo import haversine_nm
from app.utils.vessel_identity import validate_imo_checksum

from app.modules.merge_candidates import *  # noqa: F401,F403
from app.modules.merge_execution import *  # noqa: F401,F403
from app.modules.merge_candidates import (
    _find_dark_vessels,
    _find_new_vessels,
    _build_history_cache,
    _build_encounter_cache,
    _get_historical_values,
    _get_recent_change_count,
    _score_candidate,
    _has_overlapping_ais,
    _count_nearby_vessels,
    detect_merge_candidates,
    detect_merge_chains,
    extended_merge_pass,
    recheck_merges_for_imo_fraud,
)
from app.modules.merge_execution import (
    execute_merge,
    reverse_merge,
    _annotate_evidence_cards,
    _merge_watchlist,
    _merge_sts_events,
    _merge_vessel_history,
    _reassign_simple_fks,
    _reassign_ais_points,
    _update_canonical_metadata,
    _record_merge_history,
    _rescore_vessel,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical resolution
# ---------------------------------------------------------------------------

def resolve_canonical(vessel_id: int, db: Session) -> int:
    """Walk merge chain to ultimate canonical vessel. Max 20 hops, cycle detection."""
    seen: set[int] = set()
    current = vessel_id
    for _ in range(20):
        if current in seen:
            raise ValueError(f"Circular merge chain detected at vessel_id={current}")
        seen.add(current)
        vessel = db.query(Vessel).get(current)
        if vessel is None or vessel.merged_into_vessel_id is None:
            return current
        current = vessel.merged_into_vessel_id
    raise ValueError(f"Merge chain exceeds 20 hops from vessel_id={vessel_id}")


# ---------------------------------------------------------------------------
# Russian port call check (extracted from risk_scoring for reuse)
# ---------------------------------------------------------------------------

def had_russian_port_call(db: Session, vessel_id: int, before: datetime, days: int = 30) -> bool:
    """Check if vessel had AIS positions near a Russian oil terminal."""
    from app.models.port import Port

    terminals = db.query(Port).filter(Port.is_russian_oil_terminal == True).all()  # noqa: E712
    if not terminals:
        return False

    window_start = before - timedelta(days=days)
    points = (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel_id,
            AISPoint.timestamp_utc >= window_start,
            AISPoint.timestamp_utc <= before,
        )
        .all()
    )

    for pt in points:
        for terminal in terminals:
            try:
                from app.utils.geo import load_geometry
                port_shape = load_geometry(terminal.geometry)
                if port_shape is None:
                    continue
                centroid = port_shape.centroid
                dist = haversine_nm(pt.lat, pt.lon, centroid.y, centroid.x)
                if dist <= 5.0:
                    return True
            except Exception as e:
                logger.debug("Failed to check terminal proximity: %s", e)
                continue
    return False


# ---------------------------------------------------------------------------
# Merge readiness diagnostic
# ---------------------------------------------------------------------------

def diagnose_merge_readiness(db: Session) -> dict:
    """Read-only diagnostic: check whether the database has enough data for
    identity merging to produce useful results.

    Returns a dict with counts, issues list, and current merge config.
    """
    now = datetime.utcnow()
    max_gap_days = settings.MERGE_MAX_GAP_DAYS
    two_h_ago = now - timedelta(hours=2)
    cutoff = now - timedelta(days=max_gap_days)

    # Total canonical vessels (not merged into another)
    total_vessels = (
        db.query(func.count(Vessel.vessel_id))
        .filter(Vessel.merged_into_vessel_id.is_(None))
        .scalar()
    ) or 0

    # Vessels with at least one gap event
    vessels_with_gaps = (
        db.query(func.count(func.distinct(AISGapEvent.vessel_id)))
        .scalar()
    ) or 0

    # Dark candidates (gap events + last AIS > 2h ago)
    dark_vessels = _find_dark_vessels(db, two_h_ago)
    dark_candidates = len(dark_vessels)

    # New candidates (mmsi_first_seen within window)
    new_vessels = _find_new_vessels(db, cutoff)
    new_candidates = len(new_vessels)

    # Average AIS points per vessel
    total_points = (
        db.query(func.count(AISPoint.ais_point_id)).scalar()
    ) or 0
    avg_points = total_points / total_vessels if total_vessels > 0 else 0.0

    # Build issues list
    issues: list[str] = []
    if vessels_with_gaps == 0:
        issues.append("No vessels have gap events")
    if dark_candidates == 0:
        issues.append(
            "No dark candidates found (need vessels with gap events + last AIS >2h ago)"
        )
    if new_candidates == 0:
        issues.append(
            f"No new vessel candidates found (need vessels with mmsi_first_seen within {max_gap_days} days)"
        )
    if total_vessels > 0 and avg_points < 5:
        issues.append(
            f"Sparse AIS data: average {avg_points:.1f} points per vessel (need 4+ for most detectors)"
        )

    return {
        "total_vessels": total_vessels,
        "dark_candidates": dark_candidates,
        "new_candidates": new_candidates,
        "vessels_with_gaps": vessels_with_gaps,
        "avg_points_per_vessel": round(avg_points, 1),
        "issues": issues,
        "merge_config": {
            "max_gap_days": settings.MERGE_MAX_GAP_DAYS,
            "max_speed_kn": settings.MERGE_MAX_SPEED_KN,
            "auto_threshold": settings.MERGE_AUTO_CONFIDENCE_THRESHOLD,
            "min_threshold": settings.MERGE_CANDIDATE_MIN_CONFIDENCE,
        },
    }


# ---------------------------------------------------------------------------
# Zombie IMO detection
# ---------------------------------------------------------------------------

def detect_zombie_imos(db: Session) -> list[dict]:
    """Find vessels transmitting IMOs that fail checksum validation or
    are potentially scrapped (detected via GFW vessel info).

    Returns list of dicts: {vessel_id, mmsi, imo, issue}.
    """
    results = []
    vessels = (
        db.query(Vessel)
        .filter(
            Vessel.imo != None,  # noqa: E711
            Vessel.merged_into_vessel_id == None,  # noqa: E711
        )
        .all()
    )

    for v in vessels:
        if not validate_imo_checksum(v.imo):
            results.append({
                "vessel_id": v.vessel_id,
                "mmsi": v.mmsi,
                "imo": v.imo,
                "issue": "imo_fabricated",
            })

    return results


# ---------------------------------------------------------------------------
# Vessel timeline (query-time aggregation)
# ---------------------------------------------------------------------------

def get_vessel_timeline(
    db: Session, vessel_id: int, limit: int = 100, offset: int = 0,
    start_dt: Optional[datetime] = None, end_dt: Optional[datetime] = None,
) -> list[dict]:
    """Aggregate events from multiple tables into a chronological timeline."""
    events: list[dict] = []

    # 1. VesselHistory (identity changes, merges)
    q1 = db.query(VesselHistory).filter(VesselHistory.vessel_id == vessel_id)
    if start_dt is not None:
        q1 = q1.filter(VesselHistory.observed_at >= start_dt)
    if end_dt is not None:
        q1 = q1.filter(VesselHistory.observed_at <= end_dt)
    for h in q1.all():
        events.append({
            "event_type": "identity_change",
            "timestamp": h.observed_at.isoformat() if h.observed_at else None,
            "summary": f"{h.field_changed}: {h.old_value} → {h.new_value}",
            "details": {"field": h.field_changed, "old": h.old_value, "new": h.new_value, "source": h.source},
            "related_entity_id": h.vessel_history_id,
        })

    # 2. AISGapEvent
    q2 = db.query(AISGapEvent).filter(AISGapEvent.vessel_id == vessel_id)
    if start_dt is not None:
        q2 = q2.filter(AISGapEvent.gap_start_utc >= start_dt)
    if end_dt is not None:
        q2 = q2.filter(AISGapEvent.gap_start_utc <= end_dt)
    for g in q2.all():
        events.append({
            "event_type": "ais_gap",
            "timestamp": g.gap_start_utc.isoformat() if g.gap_start_utc else None,
            "summary": f"AIS gap: {g.duration_minutes}min, score {g.risk_score}",
            "details": {"duration_minutes": g.duration_minutes, "risk_score": g.risk_score, "status": g.status},
            "related_entity_id": g.gap_event_id,
        })

    # 3. SpoofingAnomaly
    q3 = db.query(SpoofingAnomaly).filter(SpoofingAnomaly.vessel_id == vessel_id)
    if start_dt is not None:
        q3 = q3.filter(SpoofingAnomaly.start_time_utc >= start_dt)
    if end_dt is not None:
        q3 = q3.filter(SpoofingAnomaly.start_time_utc <= end_dt)
    for s in q3.all():
        events.append({
            "event_type": "spoofing",
            "timestamp": s.start_time_utc.isoformat() if s.start_time_utc else None,
            "summary": f"Spoofing: {s.anomaly_type}",
            "details": {"anomaly_type": s.anomaly_type, "implied_speed_kn": s.implied_speed_kn},
            "related_entity_id": s.anomaly_id,
        })

    # 4. LoiteringEvent
    q4 = db.query(LoiteringEvent).filter(LoiteringEvent.vessel_id == vessel_id)
    if start_dt is not None:
        q4 = q4.filter(LoiteringEvent.start_time_utc >= start_dt)
    if end_dt is not None:
        q4 = q4.filter(LoiteringEvent.start_time_utc <= end_dt)
    for l in q4.all():
        events.append({
            "event_type": "loitering",
            "timestamp": l.start_time_utc.isoformat() if l.start_time_utc else None,
            "summary": f"Loitering: {l.duration_hours:.1f}h",
            "details": {"duration_hours": l.duration_hours, "lat": l.mean_lat, "lon": l.mean_lon},
            "related_entity_id": l.loiter_id,
        })

    # 5. StsTransferEvent (as vessel_1 or vessel_2)
    q5 = db.query(StsTransferEvent).filter(
        or_(
            StsTransferEvent.vessel_1_id == vessel_id,
            StsTransferEvent.vessel_2_id == vessel_id,
        )
    )
    if start_dt is not None:
        q5 = q5.filter(StsTransferEvent.start_time_utc >= start_dt)
    if end_dt is not None:
        q5 = q5.filter(StsTransferEvent.start_time_utc <= end_dt)
    for sts in q5.all():
        partner_id = sts.vessel_2_id if sts.vessel_1_id == vessel_id else sts.vessel_1_id
        events.append({
            "event_type": "sts_transfer",
            "timestamp": sts.start_time_utc.isoformat() if sts.start_time_utc else None,
            "summary": f"STS with vessel {partner_id}: {sts.duration_minutes}min",
            "details": {
                "partner_vessel_id": partner_id,
                "duration_minutes": sts.duration_minutes,
                "detection_type": sts.detection_type,
            },
            "related_entity_id": sts.sts_id,
        })

    # 6. PortCall
    q6 = db.query(PortCall).filter(PortCall.vessel_id == vessel_id)
    if start_dt is not None:
        q6 = q6.filter(PortCall.arrival_utc >= start_dt)
    if end_dt is not None:
        q6 = q6.filter(PortCall.arrival_utc <= end_dt)
    for pc in q6.all():
        events.append({
            "event_type": "port_visit",
            "timestamp": pc.arrival_utc.isoformat() if pc.arrival_utc else None,
            "summary": f"Port call at port {pc.port_id}",
            "details": {"port_id": pc.port_id, "departure": pc.departure_utc.isoformat() if pc.departure_utc else None},
            "related_entity_id": pc.port_call_id,
        })

    # 7. MergeOperation (involving this vessel as canonical or absorbed)
    q7 = db.query(MergeOperation).filter(
        or_(
            MergeOperation.canonical_vessel_id == vessel_id,
            MergeOperation.absorbed_vessel_id == vessel_id,
        )
    )
    if start_dt is not None:
        q7 = q7.filter(MergeOperation.executed_at >= start_dt)
    if end_dt is not None:
        q7 = q7.filter(MergeOperation.executed_at <= end_dt)
    for mo in q7.all():
        role = "canonical" if mo.canonical_vessel_id == vessel_id else "absorbed"
        other_id = mo.absorbed_vessel_id if role == "canonical" else mo.canonical_vessel_id
        events.append({
            "event_type": "identity_merge",
            "timestamp": mo.executed_at.isoformat() if mo.executed_at else None,
            "summary": f"Merge ({role}): vessel {other_id}, by {mo.executed_by}",
            "details": {"role": role, "other_vessel_id": other_id, "status": mo.status},
            "related_entity_id": mo.merge_op_id,
        })

    # Sort chronologically
    events.sort(key=lambda e: e.get("timestamp") or "")
    return events[offset:offset + limit]


# ---------------------------------------------------------------------------
# Vessel aliases
# ---------------------------------------------------------------------------

def get_vessel_aliases(db: Session, vessel_id: int) -> list[dict]:
    """Get all MMSIs this vessel has used (via VesselHistory mmsi_absorbed records)."""
    aliases = []

    # Current MMSI
    vessel = db.query(Vessel).get(vessel_id)
    if vessel:
        aliases.append({
            "mmsi": vessel.mmsi,
            "name": vessel.name,
            "flag": vessel.flag,
            "status": "current",
        })

    # Absorbed MMSIs
    absorbed_records = (
        db.query(VesselHistory)
        .filter(
            VesselHistory.vessel_id == vessel_id,
            VesselHistory.field_changed == "mmsi_absorbed",
        )
        .order_by(VesselHistory.observed_at)
        .all()
    )

    for record in absorbed_records:
        absorbed_vessel = (
            db.query(Vessel)
            .filter(Vessel.mmsi == record.old_value)
            .first()
        )
        aliases.append({
            "mmsi": record.old_value,
            "name": absorbed_vessel.name if absorbed_vessel else None,
            "flag": absorbed_vessel.flag if absorbed_vessel else None,
            "status": "absorbed",
            "absorbed_at": record.observed_at.isoformat() if record.observed_at else None,
        })

    return aliases


# ---------------------------------------------------------------------------
# Chain invalidation
# ---------------------------------------------------------------------------

def invalidate_chains_for_rejected_candidate(db: Session, candidate_id: int) -> int:
    """Mark chains as stale when a merge candidate is rejected.

    When an analyst rejects a candidate, any chain that includes that
    candidate link is no longer reliable.  This function finds all
    MergeChain rows whose ``links_json`` references the rejected
    *candidate_id* and deletes them so they can be re-detected on the
    next chain-detection pass without the rejected link.

    Returns the number of chains invalidated (deleted).
    """
    from app.models.merge_chain import MergeChain

    chains = db.query(MergeChain).all()
    invalidated = 0
    for chain in chains:
        links = chain.links_json or []
        if candidate_id in links:
            db.delete(chain)
            invalidated += 1

    if invalidated > 0:
        db.commit()

    logger.info(
        "Invalidated %d merge chain(s) referencing rejected candidate %d",
        invalidated,
        candidate_id,
    )
    return invalidated
