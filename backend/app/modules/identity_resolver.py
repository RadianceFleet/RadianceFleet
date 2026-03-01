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

from sqlalchemy import func, text, and_, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.audit_log import AuditLog
from app.models.evidence_card import EvidenceCard
from app.models.gap_event import AISGapEvent
from app.models.loitering_event import LoiteringEvent
from app.models.merge_candidate import MergeCandidate
from app.models.merge_operation import MergeOperation
from app.models.port_call import PortCall
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.sts_transfer import StsTransferEvent
from app.models.stubs import DarkVesselDetection, SearchMission, VesselTargetProfile
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.models.vessel_owner import VesselOwner
from app.models.vessel_watchlist import VesselWatchlist
from app.models.base import MergeCandidateStatusEnum
from app.utils.geo import haversine_nm
from app.utils.vessel_identity import (
    RUSSIAN_ORIGIN_FLAGS,
    is_suspicious_mid,
    mmsi_to_flag,
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
            except Exception:
                continue
    return False


# ---------------------------------------------------------------------------
# IMO checksum validation
# ---------------------------------------------------------------------------

def validate_imo_checksum(imo: str) -> bool:
    """Validate IMO number using the check digit algorithm.

    IMO numbers are 7 digits (prefixed with "IMO" optionally).
    Check digit = last digit. Sum of (digit_i * (7-i)) for i=0..5 mod 10 == check digit.
    """
    digits = imo.replace("IMO", "").replace("imo", "").strip()
    if not digits.isdigit() or len(digits) != 7:
        return False
    total = sum(int(digits[i]) * (7 - i) for i in range(6))
    return total % 10 == int(digits[6])


# ---------------------------------------------------------------------------
# Candidate detection — speed-feasibility matching
# ---------------------------------------------------------------------------

def detect_merge_candidates(
    db: Session,
    max_gap_days: int | None = None,
) -> dict:
    """Find potential same-vessel pairs across MMSI changes.

    Returns dict with counts: {candidates_created, auto_merged, skipped}.
    """
    if max_gap_days is None:
        max_gap_days = settings.MERGE_MAX_GAP_DAYS
    max_speed = settings.MERGE_MAX_SPEED_KN
    auto_threshold = settings.MERGE_AUTO_CONFIDENCE_THRESHOLD
    min_threshold = settings.MERGE_CANDIDATE_MIN_CONFIDENCE
    now = datetime.utcnow()
    cutoff = now - timedelta(days=max_gap_days)

    stats = {"candidates_created": 0, "auto_merged": 0, "skipped": 0}

    # 1. "Went dark" vessels: canonical, have a gap event, last AIS > 2h ago
    two_h_ago = now - timedelta(hours=2)
    dark_vessels = _find_dark_vessels(db, two_h_ago)

    # 2. "Newly appeared" vessels: canonical, mmsi_first_seen_utc within window
    new_vessels = _find_new_vessels(db, cutoff)

    if not dark_vessels or not new_vessels:
        return stats

    # Pre-load anchorage density info for filtering
    corridor_vessels_cache: dict[int, int] = {}

    # 3. Cross-match with speed feasibility
    for dark_v, dark_last in dark_vessels:
        for new_v, new_first in new_vessels:
            if dark_v.vessel_id == new_v.vessel_id:
                continue

            # Check pair not already evaluated
            existing = (
                db.query(MergeCandidate)
                .filter(
                    or_(
                        and_(
                            MergeCandidate.vessel_a_id == dark_v.vessel_id,
                            MergeCandidate.vessel_b_id == new_v.vessel_id,
                        ),
                        and_(
                            MergeCandidate.vessel_a_id == new_v.vessel_id,
                            MergeCandidate.vessel_b_id == dark_v.vessel_id,
                        ),
                    )
                )
                .first()
            )
            if existing:
                continue

            # Speed feasibility
            time_delta_h = (new_first["ts"] - dark_last["ts"]).total_seconds() / 3600
            if time_delta_h <= 0 or time_delta_h > max_gap_days * 24:
                continue

            distance = haversine_nm(
                dark_last["lat"], dark_last["lon"],
                new_first["lat"], new_first["lon"],
            )
            max_travel = time_delta_h * max_speed

            if distance > max_travel:
                continue

            # 4. Confidence scoring
            confidence, reasons = _score_candidate(
                db, dark_v, new_v, dark_last, new_first,
                distance, time_delta_h, max_travel,
                corridor_vessels_cache,
            )

            if confidence < min_threshold:
                stats["skipped"] += 1
                continue

            # Create candidate
            candidate = MergeCandidate(
                vessel_a_id=dark_v.vessel_id,
                vessel_b_id=new_v.vessel_id,
                vessel_a_last_lat=dark_last["lat"],
                vessel_a_last_lon=dark_last["lon"],
                vessel_a_last_time=dark_last["ts"],
                vessel_b_first_lat=new_first["lat"],
                vessel_b_first_lon=new_first["lon"],
                vessel_b_first_time=new_first["ts"],
                distance_nm=round(distance, 2),
                time_delta_hours=round(time_delta_h, 2),
                confidence_score=confidence,
                match_reasons_json=reasons,
            )

            if confidence >= auto_threshold:
                candidate.status = MergeCandidateStatusEnum.AUTO_MERGED
                candidate.resolved_at = now
                candidate.resolved_by = "auto"
            else:
                candidate.status = MergeCandidateStatusEnum.PENDING

            db.add(candidate)
            db.flush()  # get candidate_id
            stats["candidates_created"] += 1

            if confidence >= auto_threshold:
                # Auto-merge: lower vessel_id is canonical
                canonical_id = min(dark_v.vessel_id, new_v.vessel_id)
                absorbed_id = max(dark_v.vessel_id, new_v.vessel_id)
                result = execute_merge(
                    db, canonical_id, absorbed_id,
                    reason=f"Auto-merge: confidence {confidence}",
                    merged_by="auto",
                    candidate_id=candidate.candidate_id,
                )
                if result.get("success"):
                    stats["auto_merged"] += 1

    db.commit()
    logger.info("Merge candidate detection: %s", stats)
    return stats


def recheck_merges_for_imo_fraud(
    db: Session,
    pipeline_start_time: datetime | None = None,
) -> dict:
    """Post-merge recheck: flag auto-merges that may involve fraudulent IMOs.

    Called as Step 11d, after IMO fraud detection (Step 11b). Checks merges
    where IMO match was the dominant signal (>25% of total score) against
    newly-created IMO_FRAUD anomalies from this pipeline run.

    Does NOT auto-reverse merges (destructive). Instead creates a warning
    anomaly for analyst review.
    """
    from app.models.base import SpoofingTypeEnum

    if pipeline_start_time is None:
        pipeline_start_time = datetime.utcnow() - timedelta(hours=1)

    stats = {"checked": 0, "flagged": 0}

    # Find recent IMO_FRAUD anomalies from this pipeline run
    recent_frauds = (
        db.query(SpoofingAnomaly)
        .filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD,
            SpoofingAnomaly.created_at >= pipeline_start_time,
        )
        .all()
    )
    if not recent_frauds:
        return stats

    # Collect IMOs and vessel IDs from fraud anomalies
    fraud_vessel_ids = {f.vessel_id for f in recent_frauds}
    fraud_imos = set()
    for f in recent_frauds:
        ev = f.evidence_json or {}
        if isinstance(ev, dict) and "imo" in ev:
            fraud_imos.add(str(ev["imo"]))

    # Find auto-merged candidates where IMO was dominant
    merged = (
        db.query(MergeCandidate)
        .filter(
            MergeCandidate.status == MergeCandidateStatusEnum.AUTO_MERGED,
        )
        .all()
    )

    for cand in merged:
        stats["checked"] += 1
        reasons = cand.match_reasons_json or {}
        if "same_imo" not in reasons:
            continue

        imo_pts = reasons["same_imo"].get("points", 0)
        total = cand.confidence_score or 1
        if total > 0 and imo_pts / total <= 0.25:
            continue

        # Check if this IMO or these vessels are flagged
        cand_imo = reasons["same_imo"].get("imo", "")
        cand_vessels = {cand.vessel_a_id, cand.vessel_b_id}
        if cand_imo not in fraud_imos and not cand_vessels.intersection(fraud_vessel_ids):
            continue

        # Flag: create warning anomaly on the canonical vessel
        canonical_id = min(cand.vessel_a_id, cand.vessel_b_id)
        existing = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == canonical_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD,
                SpoofingAnomaly.evidence_json["subtype"].as_string() == "post_merge_imo_fraud",
            )
            .first()
        )
        if not existing:
            db.add(SpoofingAnomaly(
                vessel_id=canonical_id,
                anomaly_type=SpoofingTypeEnum.IMO_FRAUD,
                start_time_utc=datetime.utcnow(),
                end_time_utc=datetime.utcnow(),
                risk_score_component=0,
                evidence_json={
                    "subtype": "post_merge_imo_fraud",
                    "candidate_id": cand.candidate_id,
                    "imo": cand_imo,
                    "merged_vessels": list(cand_vessels),
                },
            ))
            stats["flagged"] += 1
            logger.warning(
                "Auto-merge candidate_id=%d may involve fraudulent IMO %s — manual review recommended",
                cand.candidate_id, cand_imo,
            )

    if stats["flagged"]:
        db.commit()

    logger.info("IMO fraud merge recheck: %s", stats)
    return stats


def _find_dark_vessels(
    db: Session, cutoff: datetime,
) -> list[tuple["Vessel", dict]]:
    """Find canonical vessels whose last AIS transmission is before cutoff."""
    from sqlalchemy import desc

    results = []
    # Vessels with gap events and recent gaps
    gap_vessels = (
        db.query(Vessel)
        .filter(
            Vessel.merged_into_vessel_id == None,  # noqa: E711
        )
        .join(AISGapEvent, AISGapEvent.vessel_id == Vessel.vessel_id)
        .distinct()
        .all()
    )

    for vessel in gap_vessels:
        last_pt = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id)
            .order_by(desc(AISPoint.timestamp_utc))
            .first()
        )
        if last_pt and last_pt.timestamp_utc < cutoff:
            results.append((vessel, {
                "lat": last_pt.lat,
                "lon": last_pt.lon,
                "ts": last_pt.timestamp_utc,
            }))
    return results


def _find_new_vessels(
    db: Session, cutoff: datetime,
) -> list[tuple["Vessel", dict]]:
    """Find canonical vessels first seen after cutoff."""
    results = []
    vessels = (
        db.query(Vessel)
        .filter(
            Vessel.merged_into_vessel_id == None,  # noqa: E711
            Vessel.mmsi_first_seen_utc != None,  # noqa: E711
            Vessel.mmsi_first_seen_utc >= cutoff,
        )
        .all()
    )

    for vessel in vessels:
        first_pt = (
            db.query(AISPoint)
            .filter(AISPoint.vessel_id == vessel.vessel_id)
            .order_by(AISPoint.timestamp_utc)
            .first()
        )
        if first_pt:
            results.append((vessel, {
                "lat": first_pt.lat,
                "lon": first_pt.lon,
                "ts": first_pt.timestamp_utc,
            }))
    return results


def _score_candidate(
    db: Session,
    dark_v: Vessel, new_v: Vessel,
    dark_last: dict, new_first: dict,
    distance: float, time_delta_h: float, max_travel: float,
    corridor_vessels_cache: dict,
) -> tuple[int, dict]:
    """Score a merge candidate. Returns (confidence 0-100, reasons dict)."""
    reasons: dict = {}
    score = 0

    # Proximity ratio: 0-20
    prox_ratio = 1.0 - (distance / max_travel) if max_travel > 0 else 0
    prox_pts = int(prox_ratio * 20)
    score += prox_pts
    reasons["proximity_ratio"] = {"points": prox_pts, "ratio": round(prox_ratio, 3)}

    # Time tightness: 0-10 (shorter gap = higher)
    time_pts = max(0, int(10 - time_delta_h / 24))
    score += time_pts
    reasons["time_tightness"] = {"points": time_pts, "hours": round(time_delta_h, 1)}

    # Same IMO (validated)
    if dark_v.imo and new_v.imo and dark_v.imo == new_v.imo:
        if validate_imo_checksum(dark_v.imo):
            score += 25
            reasons["same_imo"] = {"points": 25, "imo": dark_v.imo}

    # Same vessel_type
    if dark_v.vessel_type and new_v.vessel_type and dark_v.vessel_type == new_v.vessel_type:
        score += 10
        reasons["same_vessel_type"] = {"points": 10}

    # Similar DWT (within ±20%)
    if dark_v.deadweight and new_v.deadweight:
        ratio = min(dark_v.deadweight, new_v.deadweight) / max(dark_v.deadweight, new_v.deadweight)
        if ratio >= 0.8:
            score += 10
            reasons["similar_dwt"] = {"points": 10, "ratio": round(ratio, 3)}

    # Similar year_built (within ±3)
    if dark_v.year_built and new_v.year_built:
        if abs(dark_v.year_built - new_v.year_built) <= 3:
            score += 5
            reasons["similar_year_built"] = {"points": 5}

    # Dark vessel silent (no new AIS after new_vessel appeared)
    new_ais_after = (
        db.query(func.count(AISPoint.ais_point_id))
        .filter(
            AISPoint.vessel_id == dark_v.vessel_id,
            AISPoint.timestamp_utc > new_first["ts"],
        )
        .scalar()
    )
    if new_ais_after == 0:
        score += 10
        reasons["dark_vessel_silent"] = {"points": 10}

    # New MMSI suspicious MID
    if is_suspicious_mid(new_v.mmsi):
        score += 5
        reasons["suspicious_mid"] = {"points": 5}

    # RU-origin flag on new vessel
    new_flag = mmsi_to_flag(new_v.mmsi)
    if new_flag and new_flag in RUSSIAN_ORIGIN_FLAGS:
        score += 5
        reasons["ru_origin_flag"] = {"points": 5, "flag": new_flag}

    # Flag change (different MIDs)
    dark_flag = mmsi_to_flag(dark_v.mmsi)
    if dark_flag and new_flag and dark_flag != new_flag:
        score += 5
        reasons["flag_change"] = {"points": 5, "from": dark_flag, "to": new_flag}

    # Dark vessel was near Russian port
    if dark_last["ts"] and had_russian_port_call(db, dark_v.vessel_id, dark_last["ts"]):
        score += 10
        reasons["russian_port_call"] = {"points": 10}

    # ISM/P&I continuity bonus: shared ISM manager or P&I club across candidates
    if settings.ISM_CONTINUITY_SCORING_ENABLED:
        try:
            from app.models.vessel_owner import VesselOwner as _VO_ism
            dark_owner = db.query(_VO_ism).filter(
                _VO_ism.vessel_id == dark_v.vessel_id
            ).first()
            new_owner = db.query(_VO_ism).filter(
                _VO_ism.vessel_id == new_v.vessel_id
            ).first()
            if dark_owner and new_owner:
                _dark_ism = (dark_owner.ism_manager or "").strip().upper()
                _new_ism = (new_owner.ism_manager or "").strip().upper()
                if _dark_ism and _new_ism and _dark_ism == _new_ism:
                    score += 10
                    reasons["shared_ism_manager"] = {"points": 10, "ism_manager": _dark_ism}
                _dark_pi = (dark_owner.pi_club_name or "").strip().upper()
                _new_pi = (new_owner.pi_club_name or "").strip().upper()
                if _dark_pi and _new_pi and _dark_pi == _new_pi:
                    score += 10
                    reasons["shared_pi_club"] = {"points": 10, "pi_club": _dark_pi}
        except Exception:
            pass  # Graceful skip if VesselOwner query fails

    # --- Negative signals (anti-merge evidence) ---

    # DWT mismatch (>30% difference): strong anti-evidence
    if dark_v.deadweight and new_v.deadweight and "similar_dwt" not in reasons:
        dwt_ratio = min(dark_v.deadweight, new_v.deadweight) / max(dark_v.deadweight, new_v.deadweight)
        if dwt_ratio < 0.7:
            score = max(0, score - 15)
            reasons["dwt_mismatch"] = {"points": -15, "ratio": round(dwt_ratio, 3)}

    # Different vessel type: active penalty (not just 0 points)
    if (dark_v.vessel_type and new_v.vessel_type
            and dark_v.vessel_type != new_v.vessel_type
            and "same_vessel_type" not in reasons):
        score = max(0, score - 10)
        reasons["vessel_type_mismatch"] = {
            "points": -10,
            "dark": dark_v.vessel_type,
            "new": new_v.vessel_type,
        }

    # Conflicting port calls during gap period: both vessels at different ports
    if dark_last["ts"] and new_first["ts"]:
        gap_start = dark_last["ts"]
        gap_end = new_first["ts"]
        dark_ports = (
            db.query(PortCall.port_id)
            .filter(
                PortCall.vessel_id == dark_v.vessel_id,
                PortCall.arrival_utc >= gap_start,
                PortCall.arrival_utc <= gap_end,
                PortCall.port_id.isnot(None),
            )
            .all()
        )
        new_ports = (
            db.query(PortCall.port_id)
            .filter(
                PortCall.vessel_id == new_v.vessel_id,
                PortCall.arrival_utc >= gap_start,
                PortCall.arrival_utc <= gap_end,
                PortCall.port_id.isnot(None),
            )
            .all()
        )
        dark_port_ids = {p.port_id for p in dark_ports}
        new_port_ids = {p.port_id for p in new_ports}
        conflicting = dark_port_ids - new_port_ids
        if dark_port_ids and new_port_ids and conflicting:
            penalty = min(len(conflicting) * -15, -45)  # cap at -45
            score = max(0, score + penalty)
            reasons["conflicting_port_calls"] = {
                "points": penalty,
                "dark_ports": list(dark_port_ids),
                "new_ports": list(new_port_ids),
            }

    # Hard guard: overlapping AIS tracks block merge entirely.
    # If both vessels transmitted within the same hour, they are different
    # physical hulls — no merge regardless of score.
    overlap = _has_overlapping_ais(db, dark_v.vessel_id, new_v.vessel_id)
    if overlap:
        reasons["overlapping_ais_tracks"] = {"blocked": True}
        return 0, reasons

    # Anchorage density filter: require extra matching in busy STS areas
    density_count = _count_nearby_vessels(
        db, new_first["lat"], new_first["lon"], new_first["ts"],
        radius_nm=5.0, hours=6,
        corridor_vessels_cache=corridor_vessels_cache,
    )
    if density_count > 5:
        # Busy area: require IMO match or DWT+type+year_built
        has_strong_match = "same_imo" in reasons
        has_triple_match = (
            "similar_dwt" in reasons
            and "same_vessel_type" in reasons
            and "similar_year_built" in reasons
        )
        if not has_strong_match and not has_triple_match:
            penalty = -20
            score = max(0, score + penalty)
            reasons["anchorage_density_penalty"] = {
                "points": penalty,
                "nearby_vessels": density_count,
            }
        elif has_triple_match and not has_strong_match:
            # Sister ships: triple-match reduces penalty but doesn't eliminate it
            penalty = -10
            score = max(0, score + penalty)
            reasons["anchorage_density_penalty"] = {
                "points": penalty,
                "nearby_vessels": density_count,
                "triple_match_reduced": True,
            }

    # IMO fraud cross-check: if IMO match is the dominant signal, check
    # for prior-run IMO_FRAUD anomalies. If found, cap confidence to
    # prevent auto-merge (leave as PENDING for analyst review).
    if "same_imo" in reasons and score > 0:
        imo_pts = reasons["same_imo"]["points"]
        if score > 0 and imo_pts / score > 0.25:
            imo_val = reasons["same_imo"]["imo"]
            from app.models.base import SpoofingTypeEnum as _ST
            fraud_count = (
                db.query(func.count(SpoofingAnomaly.anomaly_id))
                .filter(
                    SpoofingAnomaly.anomaly_type == _ST.IMO_FRAUD,
                    SpoofingAnomaly.evidence_json["imo"].as_string() == imo_val,
                )
                .scalar()
            ) or 0
            if fraud_count == 0:
                # Fallback: check by vessel IDs associated with this IMO
                fraud_count = (
                    db.query(func.count(SpoofingAnomaly.anomaly_id))
                    .filter(
                        SpoofingAnomaly.anomaly_type == _ST.IMO_FRAUD,
                        SpoofingAnomaly.vessel_id.in_([dark_v.vessel_id, new_v.vessel_id]),
                    )
                    .scalar()
                ) or 0
            if fraud_count > 0:
                # Cap below auto-merge threshold to force manual review
                auto_thresh = settings.MERGE_AUTO_CONFIDENCE_THRESHOLD
                if score >= auto_thresh:
                    score = auto_thresh - 1
                reasons["imo_fraud_flag"] = {
                    "capped": True,
                    "prior_fraud_anomalies": fraud_count,
                    "imo": imo_val,
                }

    # Cap at 100
    score = min(100, max(0, score))
    return score, reasons


def _has_overlapping_ais(
    db: Session, vessel_a_id: int, vessel_b_id: int,
    granularity_seconds: int = 3600,
) -> bool:
    """Check if two vessels have AIS transmissions within the same time window.

    Uses hourly buckets: if both vessels transmitted at least once in any
    shared hour, they are different physical hulls and must NOT be merged.
    Dialect-aware: uses strftime on SQLite, EXTRACT(EPOCH) on Postgres.
    """
    dialect = db.bind.dialect.name if db.bind else "sqlite"
    if dialect == "postgresql":
        sql = """
            SELECT COUNT(*) FROM (
                SELECT CAST(EXTRACT(EPOCH FROM timestamp_utc) / :gran AS INTEGER) AS bucket
                FROM ais_points WHERE vessel_id = :va
                INTERSECT
                SELECT CAST(EXTRACT(EPOCH FROM timestamp_utc) / :gran AS INTEGER) AS bucket
                FROM ais_points WHERE vessel_id = :vb
            ) AS overlap
        """
    else:
        sql = """
            SELECT COUNT(*) FROM (
                SELECT CAST(strftime('%s', timestamp_utc) / :gran AS INTEGER) AS bucket
                FROM ais_points WHERE vessel_id = :va
                INTERSECT
                SELECT CAST(strftime('%s', timestamp_utc) / :gran AS INTEGER) AS bucket
                FROM ais_points WHERE vessel_id = :vb
            )
        """
    overlap_count = db.execute(
        text(sql),
        {"va": vessel_a_id, "vb": vessel_b_id, "gran": granularity_seconds},
    ).scalar()
    return (overlap_count or 0) > 0


def _count_nearby_vessels(
    db: Session,
    lat: float, lon: float, ts: datetime,
    radius_nm: float, hours: int,
    corridor_vessels_cache: dict,
) -> int:
    """Count distinct vessels transmitting near a position within a time window."""
    # Approximate degree offset for bounding box pre-filter
    deg = radius_nm / 60.0
    window_start = ts - timedelta(hours=hours)
    window_end = ts + timedelta(hours=hours)

    count = (
        db.query(func.count(func.distinct(AISPoint.vessel_id)))
        .filter(
            AISPoint.timestamp_utc >= window_start,
            AISPoint.timestamp_utc <= window_end,
            AISPoint.lat >= lat - deg,
            AISPoint.lat <= lat + deg,
            AISPoint.lon >= lon - deg,
            AISPoint.lon <= lon + deg,
        )
        .scalar()
    ) or 0
    return count


# ---------------------------------------------------------------------------
# Merge execution
# ---------------------------------------------------------------------------

def execute_merge(
    db: Session,
    canonical_id: int,
    absorbed_id: int,
    reason: str = "",
    merged_by: str = "auto",
    candidate_id: int | None = None,
    commit: bool = True,
) -> dict:
    """Merge absorbed vessel into canonical vessel.

    Reassigns all FK references, handles unique constraint conflicts,
    creates audit trail, and records undo information.

    Returns dict: {success, merge_op_id, affected_records}.
    """
    # Pre-checks: resolve to actual canonical
    canonical_id = resolve_canonical(canonical_id, db)
    absorbed_id = resolve_canonical(absorbed_id, db)

    if canonical_id == absorbed_id:
        return {"success": False, "error": "Same vessel after canonical resolution"}

    # Deterministic: lower ID is canonical
    if canonical_id > absorbed_id:
        canonical_id, absorbed_id = absorbed_id, canonical_id

    canonical = db.query(Vessel).get(canonical_id)
    absorbed = db.query(Vessel).get(absorbed_id)

    if not canonical or not absorbed:
        return {"success": False, "error": "Vessel not found"}
    if canonical.merged_into_vessel_id is not None:
        return {"success": False, "error": f"Canonical vessel {canonical_id} is already absorbed"}
    if absorbed.merged_into_vessel_id is not None:
        return {"success": False, "error": f"Absorbed vessel {absorbed_id} is already absorbed"}

    affected: dict = {"vessel_snapshot": {
        "mmsi": absorbed.mmsi,
        "name": absorbed.name,
        "imo": absorbed.imo,
        "flag": absorbed.flag,
        "vessel_type": absorbed.vessel_type,
        "deadweight": absorbed.deadweight,
        "year_built": absorbed.year_built,
    }}

    # 1. Annotate evidence cards with provenance (before FK reassignment)
    ec_ids = _annotate_evidence_cards(db, absorbed_id, absorbed.mmsi)
    affected["evidence_cards"] = ec_ids

    # 2. Merge watchlist (unique constraint: vessel_id + watchlist_source)
    wl_result = _merge_watchlist(db, canonical_id, absorbed_id)
    affected["watchlist"] = wl_result

    # 3. Merge STS events (handle self-STS and duplicate pair+time)
    sts_result = _merge_sts_events(db, canonical_id, absorbed_id)
    affected["sts_events"] = sts_result

    # 4. Merge vessel history (skip duplicates)
    vh_result = _merge_vessel_history(db, canonical_id, absorbed_id)
    affected["vessel_history"] = vh_result

    # 4b. Set forward provenance on gap events before FK reassignment.
    # original_vessel_id captures which identity generated the gap — used
    # by scoring to prevent frequency inflation after merges.
    db.query(AISGapEvent).filter(
        AISGapEvent.vessel_id == absorbed_id,
        AISGapEvent.original_vessel_id.is_(None),
    ).update(
        {AISGapEvent.original_vessel_id: absorbed_id},
        synchronize_session="fetch",
    )
    # Also tag canonical's own gaps (if not already tagged)
    db.query(AISGapEvent).filter(
        AISGapEvent.vessel_id == canonical_id,
        AISGapEvent.original_vessel_id.is_(None),
    ).update(
        {AISGapEvent.original_vessel_id: canonical_id},
        synchronize_session="fetch",
    )

    # 5. Reassign simple FK tables (no unique constraints)
    simple_result = _reassign_simple_fks(db, canonical_id, absorbed_id)
    affected.update(simple_result)

    # 6. Reassign AIS points (largest table — bulk SQL)
    ais_result = _reassign_ais_points(db, canonical_id, absorbed_id)
    affected["ais_points"] = ais_result

    # 7. Update canonical vessel metadata
    _update_canonical_metadata(db, canonical, absorbed)

    # 8. Record identity absorption in VesselHistory
    _record_merge_history(db, canonical, absorbed, merged_by)

    # 9. Mark absorbed
    absorbed.merged_into_vessel_id = canonical_id

    # 9b. Auto-reject pending candidates referencing the now-absorbed vessel
    stale_candidates = (
        db.query(MergeCandidate)
        .filter(
            or_(
                MergeCandidate.vessel_a_id == absorbed_id,
                MergeCandidate.vessel_b_id == absorbed_id,
            ),
            MergeCandidate.status == MergeCandidateStatusEnum.PENDING,
        )
        .all()
    )
    rejected_count = 0
    for cand in stale_candidates:
        if candidate_id is not None and cand.candidate_id == candidate_id:
            continue  # This candidate is being confirmed, not rejected
        cand.status = MergeCandidateStatusEnum.REJECTED
        cand.resolved_at = datetime.utcnow()
        cand.resolved_by = f"auto_absorption:{absorbed_id}"
        rejected_count += 1
    if rejected_count:
        logger.info("Auto-rejected %d stale merge candidates for absorbed vessel %d", rejected_count, absorbed_id)

    # 10. Create MergeOperation
    merge_op = MergeOperation(
        candidate_id=candidate_id,
        canonical_vessel_id=canonical_id,
        absorbed_vessel_id=absorbed_id,
        executed_by=merged_by,
        status="completed",
        affected_records_json=affected,
    )
    db.add(merge_op)
    db.flush()

    # 11. Create AuditLog entry
    audit = AuditLog(
        action="vessel_merge",
        entity_type="vessel",
        entity_id=canonical_id,
        details={
            "canonical_vessel_id": canonical_id,
            "absorbed_vessel_id": absorbed_id,
            "absorbed_mmsi": absorbed.mmsi,
            "reason": reason,
            "merge_op_id": merge_op.merge_op_id,
        },
    )
    db.add(audit)

    # 12. Commit (or flush for caller-controlled transactions)
    if commit:
        db.commit()
    else:
        db.flush()

    # 13. Rescore canonical vessel's gap events
    _rescore_vessel(db, canonical_id, commit=commit)

    logger.info(
        "Merged vessel %s (MMSI %s) into %s (MMSI %s) — op %s",
        absorbed_id, absorbed.mmsi, canonical_id, canonical.mmsi, merge_op.merge_op_id,
    )
    return {
        "success": True,
        "merge_op_id": merge_op.merge_op_id,
        "affected_records": affected,
    }


def _annotate_evidence_cards(db: Session, absorbed_id: int, absorbed_mmsi: str) -> list[int]:
    """Set provenance fields on evidence cards linked to absorbed vessel's gap events."""
    gap_ids = [
        g.gap_event_id
        for g in db.query(AISGapEvent.gap_event_id)
        .filter(AISGapEvent.vessel_id == absorbed_id)
        .all()
    ]
    if not gap_ids:
        return []

    cards = (
        db.query(EvidenceCard)
        .filter(EvidenceCard.gap_event_id.in_(gap_ids))
        .all()
    )
    ids = []
    for card in cards:
        card.original_vessel_id = absorbed_id
        card.original_mmsi = absorbed_mmsi
        ids.append(card.evidence_card_id)
    return ids


def _merge_watchlist(db: Session, canonical_id: int, absorbed_id: int) -> dict:
    """Merge watchlist entries, resolving unique constraint conflicts."""
    result = {"reassigned": 0, "conflicts_resolved": 0, "deleted_snapshots": []}

    absorbed_entries = (
        db.query(VesselWatchlist)
        .filter(VesselWatchlist.vessel_id == absorbed_id)
        .all()
    )

    for entry in absorbed_entries:
        # Check if canonical already has entry for same source
        conflict = (
            db.query(VesselWatchlist)
            .filter(
                VesselWatchlist.vessel_id == canonical_id,
                VesselWatchlist.watchlist_source == entry.watchlist_source,
            )
            .first()
        )
        if conflict:
            # Keep higher confidence
            if entry.match_confidence > conflict.match_confidence:
                conflict.match_confidence = entry.match_confidence
                conflict.reason = entry.reason
            # Delete absorbed entry (snapshot for undo)
            result["deleted_snapshots"].append({
                "watchlist_entry_id": entry.watchlist_entry_id,
                "watchlist_source": entry.watchlist_source,
                "reason": entry.reason,
                "match_confidence": entry.match_confidence,
            })
            db.delete(entry)
            result["conflicts_resolved"] += 1
        else:
            entry.vessel_id = canonical_id
            result["reassigned"] += 1

    return result


def _merge_sts_events(db: Session, canonical_id: int, absorbed_id: int) -> dict:
    """Merge STS events, handling self-STS and duplicate pair+time conflicts."""
    result = {"reassigned": 0, "self_sts_deleted": 0, "duplicates_resolved": 0, "deleted_snapshots": []}

    # All STS events involving absorbed vessel (as vessel_1 or vessel_2)
    sts_events = (
        db.query(StsTransferEvent)
        .filter(
            or_(
                StsTransferEvent.vessel_1_id == absorbed_id,
                StsTransferEvent.vessel_2_id == absorbed_id,
            )
        )
        .all()
    )

    for sts in sts_events:
        new_v1 = canonical_id if sts.vessel_1_id == absorbed_id else sts.vessel_1_id
        new_v2 = canonical_id if sts.vessel_2_id == absorbed_id else sts.vessel_2_id

        # Self-STS after reassignment (absorbed had STS with canonical)
        if new_v1 == new_v2:
            result["deleted_snapshots"].append({
                "sts_id": sts.sts_id,
                "vessel_1_id": sts.vessel_1_id,
                "vessel_2_id": sts.vessel_2_id,
                "start_time_utc": str(sts.start_time_utc),
                "end_time_utc": str(sts.end_time_utc) if sts.end_time_utc else None,
                "duration_minutes": sts.duration_minutes,
                "mean_proximity_meters": getattr(sts, "mean_proximity_meters", None),
                "risk_score_component": sts.risk_score_component,
                "type": "self_sts",
            })
            db.delete(sts)
            result["self_sts_deleted"] += 1
            continue

        # Check for duplicate pair+time
        existing = (
            db.query(StsTransferEvent)
            .filter(
                StsTransferEvent.sts_id != sts.sts_id,
                StsTransferEvent.vessel_1_id == new_v1,
                StsTransferEvent.vessel_2_id == new_v2,
                StsTransferEvent.start_time_utc == sts.start_time_utc,
            )
            .first()
        )
        if not existing:
            # Also check reversed pair
            existing = (
                db.query(StsTransferEvent)
                .filter(
                    StsTransferEvent.sts_id != sts.sts_id,
                    StsTransferEvent.vessel_1_id == new_v2,
                    StsTransferEvent.vessel_2_id == new_v1,
                    StsTransferEvent.start_time_utc == sts.start_time_utc,
                )
                .first()
            )

        if existing:
            # Keep higher risk_score_component
            if sts.risk_score_component > existing.risk_score_component:
                existing.risk_score_component = sts.risk_score_component
            result["deleted_snapshots"].append({
                "sts_id": sts.sts_id,
                "vessel_1_id": sts.vessel_1_id,
                "vessel_2_id": sts.vessel_2_id,
                "start_time_utc": str(sts.start_time_utc),
                "end_time_utc": str(sts.end_time_utc) if sts.end_time_utc else None,
                "duration_minutes": sts.duration_minutes,
                "mean_proximity_meters": getattr(sts, "mean_proximity_meters", None),
                "risk_score_component": sts.risk_score_component,
                "type": "duplicate",
            })
            db.delete(sts)
            result["duplicates_resolved"] += 1
        else:
            sts.vessel_1_id = new_v1
            sts.vessel_2_id = new_v2
            result["reassigned"] += 1

    return result


def _merge_vessel_history(db: Session, canonical_id: int, absorbed_id: int) -> dict:
    """Merge vessel history entries, skipping duplicates."""
    result = {"reassigned": 0, "duplicates_skipped": 0}

    entries = (
        db.query(VesselHistory)
        .filter(VesselHistory.vessel_id == absorbed_id)
        .all()
    )

    for entry in entries:
        # Check for exact duplicate on canonical
        dup = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == canonical_id,
                VesselHistory.field_changed == entry.field_changed,
                VesselHistory.old_value == entry.old_value,
                VesselHistory.new_value == entry.new_value,
                VesselHistory.observed_at == entry.observed_at,
            )
            .first()
        )
        if dup:
            result["duplicates_skipped"] += 1
            continue
        entry.vessel_id = canonical_id
        result["reassigned"] += 1

    return result


def _reassign_simple_fks(db: Session, canonical_id: int, absorbed_id: int) -> dict:
    """Reassign FK tables with no unique constraints (safe bulk UPDATE)."""
    result = {}

    tables = [
        ("gap_events", AISGapEvent, AISGapEvent.vessel_id),
        ("spoofing_anomalies", SpoofingAnomaly, SpoofingAnomaly.vessel_id),
        ("loitering_events", LoiteringEvent, LoiteringEvent.vessel_id),
        ("port_calls", PortCall, PortCall.vessel_id),
        ("vessel_owners", VesselOwner, VesselOwner.vessel_id),
        ("vessel_target_profiles", VesselTargetProfile, VesselTargetProfile.vessel_id),
        ("search_missions", SearchMission, SearchMission.vessel_id),
    ]

    for name, model, col in tables:
        count = (
            db.query(model)
            .filter(col == absorbed_id)
            .update({col: canonical_id}, synchronize_session="fetch")
        )
        result[name] = {"reassigned": count}

    # DarkVesselDetection uses matched_vessel_id (nullable)
    dvd_count = (
        db.query(DarkVesselDetection)
        .filter(DarkVesselDetection.matched_vessel_id == absorbed_id)
        .update({DarkVesselDetection.matched_vessel_id: canonical_id}, synchronize_session="fetch")
    )
    result["dark_vessel_detections"] = {"reassigned": dvd_count}

    return result


def _reassign_ais_points(db: Session, canonical_id: int, absorbed_id: int) -> dict:
    """Reassign AIS points in batches to avoid huge WAL entries."""
    total = 0
    batch_size = 50000
    min_id = None
    max_id = None

    while True:
        # Get batch of IDs
        batch = (
            db.query(AISPoint.ais_point_id)
            .filter(AISPoint.vessel_id == absorbed_id)
            .limit(batch_size)
            .all()
        )
        if not batch:
            break

        ids = [r.ais_point_id for r in batch]
        if min_id is None:
            min_id = ids[0]
        max_id = ids[-1]

        db.query(AISPoint).filter(AISPoint.ais_point_id.in_(ids)).update(
            {AISPoint.vessel_id: canonical_id}, synchronize_session="fetch"
        )
        total += len(ids)
        db.flush()

    return {"count": total, "id_range": [min_id, max_id] if total > 0 else None}


def _update_canonical_metadata(db: Session, canonical: Vessel, absorbed: Vessel) -> None:
    """Backfill missing metadata from absorbed vessel into canonical."""
    # Keep earliest mmsi_first_seen_utc
    if absorbed.mmsi_first_seen_utc:
        if canonical.mmsi_first_seen_utc is None or absorbed.mmsi_first_seen_utc < canonical.mmsi_first_seen_utc:
            canonical.mmsi_first_seen_utc = absorbed.mmsi_first_seen_utc

    # Backfill missing fields
    for field in ("imo", "deadweight", "year_built", "owner_name"):
        if getattr(canonical, field) is None and getattr(absorbed, field) is not None:
            setattr(canonical, field, getattr(absorbed, field))


def _record_merge_history(
    db: Session, canonical: Vessel, absorbed: Vessel, merged_by: str,
) -> None:
    """Record the MMSI absorption in VesselHistory."""
    source = "auto_merge" if merged_by == "auto" else "analyst_merge"
    entry = VesselHistory(
        vessel_id=canonical.vessel_id,
        field_changed="mmsi_absorbed",
        old_value=absorbed.mmsi,
        new_value=canonical.mmsi,
        observed_at=datetime.utcnow(),
        source=source,
    )
    db.add(entry)


def _rescore_vessel(db: Session, vessel_id: int, commit: bool = True) -> None:
    """Rescore all gap events for a specific vessel."""
    from app.modules.risk_scoring import load_scoring_config, compute_gap_score, _count_gaps_in_window

    config = load_scoring_config()
    alerts = db.query(AISGapEvent).filter(AISGapEvent.vessel_id == vessel_id).all()

    for alert in alerts:
        # Use provenance-aware counting to prevent inflation
        gaps_7d = _count_gaps_in_window(db, alert, 7)
        gaps_14d = _count_gaps_in_window(db, alert, 14)
        gaps_30d = _count_gaps_in_window(db, alert, 30)

        score, breakdown = compute_gap_score(
            alert, config,
            gaps_in_7d=gaps_7d,
            gaps_in_14d=gaps_14d,
            gaps_in_30d=gaps_30d,
            db=db,
            pre_gap_sog=getattr(alert, "pre_gap_sog", None),
        )
        alert.risk_score = score
        alert.risk_breakdown_json = breakdown

    if commit:
        db.commit()


# ---------------------------------------------------------------------------
# Merge reversal
# ---------------------------------------------------------------------------

def reverse_merge(db: Session, merge_op_id: int) -> dict:
    """Reverse a completed merge operation using the affected_records snapshot.

    Best-effort reversal. Reactivates absorbed vessel, re-creates deleted
    watchlist/STS records, clears evidence card provenance, resets candidate
    status. LIMITATION: AIS points and FK tables (gaps, spoofing, loitering,
    port calls, vessel owners) are NOT reassigned back — they remain on the
    canonical vessel. Safe to use within hours of merge before new AIS
    ingestion. Unsafe after new data has been ingested for the canonical
    vessel (new and old data become indistinguishable). A future version
    will store PK lists in affected_records_json for full reversal.

    Returns dict: {success, message}.
    """
    merge_op = db.query(MergeOperation).get(merge_op_id)
    if not merge_op:
        return {"success": False, "error": "MergeOperation not found"}
    if merge_op.status == "reversed":
        return {"success": False, "error": "Already reversed"}

    canonical_id = merge_op.canonical_vessel_id
    absorbed_id = merge_op.absorbed_vessel_id
    affected = merge_op.affected_records_json or {}

    # 1. Reactivate absorbed vessel
    absorbed = db.query(Vessel).get(absorbed_id)
    if not absorbed:
        return {"success": False, "error": f"Absorbed vessel {absorbed_id} not found"}
    absorbed.merged_into_vessel_id = None

    # 2. Reassign AIS points back
    ais_info = affected.get("ais_points", {})
    if ais_info.get("count", 0) > 0:
        # We need to identify which points belonged to absorbed.
        # Use the vessel_snapshot mmsi to find the original first_seen window
        # This is imprecise — for a robust undo we'd need to store exact point IDs.
        logger.warning(
            "Reverse merge %d: %d AIS points NOT reassigned back (PK list not stored)",
            merge_op_id, ais_info.get("count", 0),
        )

    # 3. Reassign simple FK tables back
    simple_tables = [
        ("gap_events", AISGapEvent, AISGapEvent.vessel_id),
        ("spoofing_anomalies", SpoofingAnomaly, SpoofingAnomaly.vessel_id),
        ("loitering_events", LoiteringEvent, LoiteringEvent.vessel_id),
        ("port_calls", PortCall, PortCall.vessel_id),
        ("vessel_owners", VesselOwner, VesselOwner.vessel_id),
        ("vessel_target_profiles", VesselTargetProfile, VesselTargetProfile.vessel_id),
        ("search_missions", SearchMission, SearchMission.vessel_id),
    ]

    for name, model, col in simple_tables:
        info = affected.get(name, {})
        count = info.get("reassigned", 0)
        if count > 0:
            # We stored count but not IDs — for full precision we'd need PK lists.
            logger.warning(
                "Reverse merge %d: %d %s records NOT reassigned back (PK list not stored)",
                merge_op_id, count, name,
            )

    # For AIS points, if we have id_range, reassign back
    ais_data = affected.get("ais_points", {})
    # This is a best-effort reversal — the snapshot has limited precision.
    # A future improvement would store all affected PKs.

    # 4. Re-create deleted watchlist entries
    wl_data = affected.get("watchlist", {})
    for snapshot in wl_data.get("deleted_snapshots", []):
        new_entry = VesselWatchlist(
            vessel_id=absorbed_id,
            watchlist_source=snapshot["watchlist_source"],
            reason=snapshot.get("reason"),
            match_confidence=snapshot.get("match_confidence", 0),
        )
        db.add(new_entry)

    # 5. Re-create deleted STS events
    sts_data = affected.get("sts_events", {})
    for snapshot in sts_data.get("deleted_snapshots", []):
        end_time = (
            datetime.fromisoformat(snapshot["end_time_utc"])
            if snapshot.get("end_time_utc")
            else datetime.fromisoformat(snapshot["start_time_utc"])
        )
        new_sts = StsTransferEvent(
            vessel_1_id=snapshot["vessel_1_id"],
            vessel_2_id=snapshot["vessel_2_id"],
            start_time_utc=datetime.fromisoformat(snapshot["start_time_utc"]),
            end_time_utc=end_time,
            risk_score_component=snapshot.get("risk_score_component", 0),
        )
        db.add(new_sts)

    # 6. Remove merge VesselHistory record
    db.query(VesselHistory).filter(
        VesselHistory.vessel_id == canonical_id,
        VesselHistory.field_changed == "mmsi_absorbed",
        VesselHistory.old_value == affected.get("vessel_snapshot", {}).get("mmsi"),
    ).delete()

    # 7. Clear evidence card provenance
    ec_ids = affected.get("evidence_cards", [])
    if ec_ids:
        db.query(EvidenceCard).filter(
            EvidenceCard.evidence_card_id.in_(ec_ids)
        ).update(
            {EvidenceCard.original_vessel_id: None, EvidenceCard.original_mmsi: None},
            synchronize_session="fetch",
        )

    # 8. Mark operation reversed
    merge_op.status = "reversed"

    # 9. Update candidate status if exists
    if merge_op.candidate_id:
        candidate = db.query(MergeCandidate).get(merge_op.candidate_id)
        if candidate:
            candidate.status = MergeCandidateStatusEnum.PENDING
            candidate.resolved_at = None
            candidate.resolved_by = None

    # 10. AuditLog
    audit = AuditLog(
        action="vessel_merge_reversed",
        entity_type="vessel",
        entity_id=canonical_id,
        details={
            "merge_op_id": merge_op_id,
            "canonical_vessel_id": canonical_id,
            "absorbed_vessel_id": absorbed_id,
        },
    )
    db.add(audit)

    db.commit()

    # 11. Rescore both vessels
    _rescore_vessel(db, canonical_id)
    _rescore_vessel(db, absorbed_id)

    logger.info("Reversed merge operation %s", merge_op_id)
    return {"success": True, "message": f"Merge operation {merge_op_id} reversed"}


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
) -> list[dict]:
    """Aggregate events from multiple tables into a chronological timeline."""
    events: list[dict] = []

    # 1. VesselHistory (identity changes, merges)
    for h in db.query(VesselHistory).filter(VesselHistory.vessel_id == vessel_id).all():
        events.append({
            "event_type": "identity_change",
            "timestamp": h.observed_at.isoformat() if h.observed_at else None,
            "summary": f"{h.field_changed}: {h.old_value} → {h.new_value}",
            "details": {"field": h.field_changed, "old": h.old_value, "new": h.new_value, "source": h.source},
            "related_entity_id": h.vessel_history_id,
        })

    # 2. AISGapEvent
    for g in db.query(AISGapEvent).filter(AISGapEvent.vessel_id == vessel_id).all():
        events.append({
            "event_type": "ais_gap",
            "timestamp": g.gap_start_utc.isoformat() if g.gap_start_utc else None,
            "summary": f"AIS gap: {g.duration_minutes}min, score {g.risk_score}",
            "details": {"duration_minutes": g.duration_minutes, "risk_score": g.risk_score, "status": g.status},
            "related_entity_id": g.gap_event_id,
        })

    # 3. SpoofingAnomaly
    for s in db.query(SpoofingAnomaly).filter(SpoofingAnomaly.vessel_id == vessel_id).all():
        events.append({
            "event_type": "spoofing",
            "timestamp": s.start_time_utc.isoformat() if s.start_time_utc else None,
            "summary": f"Spoofing: {s.anomaly_type}",
            "details": {"anomaly_type": s.anomaly_type, "implied_speed_kn": s.implied_speed_kn},
            "related_entity_id": s.anomaly_id,
        })

    # 4. LoiteringEvent
    for l in db.query(LoiteringEvent).filter(LoiteringEvent.vessel_id == vessel_id).all():
        events.append({
            "event_type": "loitering",
            "timestamp": l.start_time_utc.isoformat() if l.start_time_utc else None,
            "summary": f"Loitering: {l.duration_hours:.1f}h",
            "details": {"duration_hours": l.duration_hours, "lat": l.mean_lat, "lon": l.mean_lon},
            "related_entity_id": l.loiter_id,
        })

    # 5. StsTransferEvent (as vessel_1 or vessel_2)
    sts_events = (
        db.query(StsTransferEvent)
        .filter(
            or_(
                StsTransferEvent.vessel_1_id == vessel_id,
                StsTransferEvent.vessel_2_id == vessel_id,
            )
        )
        .all()
    )
    for sts in sts_events:
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
    for pc in db.query(PortCall).filter(PortCall.vessel_id == vessel_id).all():
        events.append({
            "event_type": "port_visit",
            "timestamp": pc.arrival_utc.isoformat() if pc.arrival_utc else None,
            "summary": f"Port call at port {pc.port_id}",
            "details": {"port_id": pc.port_id, "departure": pc.departure_utc.isoformat() if pc.departure_utc else None},
            "related_entity_id": pc.port_call_id,
        })

    # 7. MergeOperation (involving this vessel as canonical or absorbed)
    for mo in db.query(MergeOperation).filter(
        or_(
            MergeOperation.canonical_vessel_id == vessel_id,
            MergeOperation.absorbed_vessel_id == vessel_id,
        )
    ).all():
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
        # old_value is the absorbed MMSI
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
