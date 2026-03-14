"""Merge candidate detection — speed-feasibility matching and chain detection.

Extracted from identity_resolver.py to reduce module size.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.base import MergeCandidateStatusEnum
from app.models.gap_event import AISGapEvent
from app.models.merge_candidate import MergeCandidate
from app.models.port_call import PortCall
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.sts_transfer import StsTransferEvent
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.utils.geo import haversine_nm
from app.utils.vessel_identity import (
    RUSSIAN_ORIGIN_FLAGS,
    is_suspicious_mid,
    mmsi_to_flag,
    validate_imo_checksum,
)

logger = logging.getLogger(__name__)


def _find_dark_vessels(
    db: Session,
    cutoff: datetime,
) -> list[tuple[Vessel, dict]]:
    """Find canonical vessels whose last AIS transmission is before cutoff."""
    from sqlalchemy import desc

    results = []
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
            results.append(
                (
                    vessel,
                    {
                        "lat": last_pt.lat,
                        "lon": last_pt.lon,
                        "ts": last_pt.timestamp_utc,
                    },
                )
            )
    return results


def _find_new_vessels(
    db: Session,
    cutoff: datetime,
) -> list[tuple[Vessel, dict]]:
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
            results.append(
                (
                    vessel,
                    {
                        "lat": first_pt.lat,
                        "lon": first_pt.lon,
                        "ts": first_pt.timestamp_utc,
                    },
                )
            )
    return results


def _build_history_cache(
    db: Session, vessel_ids: set[int]
) -> dict[int, dict[str, list[tuple[str, str, datetime]]]]:
    """Batch-load VesselHistory. Returns {vessel_id: {field: [(old_val, new_val, observed_at)]}}."""
    if not vessel_ids:
        return {}
    id_list = list(vessel_ids)
    records = []
    for i in range(0, len(id_list), 500):
        batch = id_list[i : i + 500]
        records.extend(
            db.query(
                VesselHistory.vessel_id,
                VesselHistory.field_changed,
                VesselHistory.old_value,
                VesselHistory.new_value,
                VesselHistory.observed_at,
            )
            .filter(VesselHistory.vessel_id.in_(batch))
            .all()
        )
    cache: dict[int, dict[str, list[tuple[str, str, datetime]]]] = {}
    for vid, field, old_val, new_val, obs_at in records:
        bucket = cache.setdefault(vid, {}).setdefault(field, [])
        bucket.append(
            (
                (old_val or "").strip().upper(),
                (new_val or "").strip().upper(),
                obs_at,
            )
        )
    return cache


def _build_encounter_cache(db: Session, vessel_ids: set[int]) -> dict[tuple[int, int], datetime]:
    """Batch-load GFW encounter pairs. Returns {(min_id, max_id): earliest_start_time_utc}."""
    from app.models.base import STSDetectionTypeEnum

    if not vessel_ids:
        return {}
    id_list = list(vessel_ids)
    rows = []
    for i in range(0, len(id_list), 500):
        batch = id_list[i : i + 500]
        rows.extend(
            db.query(
                StsTransferEvent.vessel_1_id,
                StsTransferEvent.vessel_2_id,
                StsTransferEvent.start_time_utc,
            )
            .filter(
                StsTransferEvent.detection_type == STSDetectionTypeEnum.GFW_ENCOUNTER,
                or_(
                    StsTransferEvent.vessel_1_id.in_(batch),
                    StsTransferEvent.vessel_2_id.in_(batch),
                ),
            )
            .all()
        )
    cache: dict[tuple[int, int], datetime] = {}
    for v1, v2, ts in rows:
        key = (min(v1, v2), max(v1, v2))
        if key not in cache or ts < cache[key]:
            cache[key] = ts
    return cache


def _get_historical_values(cache: dict, vessel_id: int, field_name: str) -> set[str]:
    """All distinct non-empty values from history (union of old_val and new_val)."""
    values = set()
    for old_v, new_v, _ in cache.get(vessel_id, {}).get(field_name, []):
        if old_v:
            values.add(old_v)
        if new_v:
            values.add(new_v)
    return values


def _get_recent_change_count(cache: dict, vessel_id: int, cutoff: datetime) -> int:
    """Count distinct fields with REAL transitions after cutoff.

    Only counts rows where both old_val and new_val are populated and different.
    Excludes snapshot observations (old_value="" from enrichment imports).
    """
    vessel_cache = cache.get(vessel_id, {})
    fields_changed = 0
    for field in ("name", "flag", "callsign"):
        entries = vessel_cache.get(field, [])
        if any(
            old_v and new_v and old_v != new_v and obs_at and obs_at >= cutoff
            for old_v, new_v, obs_at in entries
        ):
            fields_changed += 1
    return fields_changed


def _has_overlapping_ais(
    db: Session,
    vessel_a_id: int,
    vessel_b_id: int,
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
    lat: float,
    lon: float,
    ts: datetime,
    radius_nm: float,
    hours: int,
    corridor_vessels_cache: dict,
) -> int:
    """Count distinct vessels transmitting near a position within a time window."""
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


def _score_candidate(
    db: Session,
    dark_v: Vessel,
    new_v: Vessel,
    dark_last: dict,
    new_first: dict,
    distance: float,
    time_delta_h: float,
    max_travel: float,
    corridor_vessels_cache: dict,
    history_cache: dict | None = None,
    encounter_cache: dict | None = None,
) -> tuple[int, dict]:
    """Score a merge candidate. Returns (confidence 0-100, reasons dict)."""
    from app.modules.identity_resolver import had_russian_port_call

    history_cache = history_cache or {}
    encounter_cache = encounter_cache or {}
    reasons: dict = {}
    score = 0

    # Encounter anti-merge: if GFW recorded these two vessels meeting,
    # they are different physical hulls
    pair_key = (min(dark_v.vessel_id, new_v.vessel_id), max(dark_v.vessel_id, new_v.vessel_id))
    encounter_ts = encounter_cache.get(pair_key)
    if encounter_ts and dark_last.get("ts") and encounter_ts > dark_last["ts"]:
        reasons["encounter_after_gap"] = {
            "blocked": True,
            "event_date": str(encounter_ts),
        }
        return 0, reasons

    # Proximity ratio: 0-20
    prox_ratio = 1.0 - (distance / max_travel) if max_travel > 0 else 0
    prox_pts = int(prox_ratio * 20)
    score += prox_pts
    reasons["proximity_ratio"] = {"points": prox_pts, "ratio": round(prox_ratio, 3)}

    # Time tightness: 0-10 (shorter gap = higher)
    time_pts = max(0, int(10 - time_delta_h / 24))
    score += time_pts
    reasons["time_tightness"] = {"points": time_pts, "hours": round(time_delta_h, 1)}

    # Same IMO (validated) — or different IMO (hard block)
    if dark_v.imo and new_v.imo:
        if dark_v.imo == new_v.imo:
            if validate_imo_checksum(dark_v.imo):
                score += 25
                reasons["same_imo"] = {"points": 25, "imo": dark_v.imo}
        else:
            # Different valid IMOs = definitively different physical ships.
            reasons["imo_mismatch"] = {
                "blocked": True,
                "dark_imo": dark_v.imo,
                "new_imo": new_v.imo,
            }
            return 0, reasons

    # Historical IMO cross-reference
    if "same_imo" not in reasons and "imo_mismatch" not in reasons:  # noqa: SIM102
        if settings.HISTORY_CROSS_REFERENCE_ENABLED:
            from app.utils.vessel_identity import validate_imo_checksum as _validate_imo_hist

            dark_imos = _get_historical_values(history_cache, dark_v.vessel_id, "imo")
            new_imos = _get_historical_values(history_cache, new_v.vessel_id, "imo")
            if dark_v.imo:
                dark_imos = dark_imos | {dark_v.imo.upper()}
            if new_v.imo:
                new_imos = new_imos | {new_v.imo.upper()}
            shared = dark_imos & new_imos
            if shared:
                imo_val = next(iter(shared))
                if _validate_imo_hist(imo_val):
                    score += 20
                    reasons["historical_shared_imo"] = {"points": 20, "imo": imo_val}

    # Same vessel_type (with tanker-category normalization)
    from app.utils.vessel_filter import is_tanker_type

    _dark_tanker_by_type = bool(dark_v.vessel_type and "tanker" in dark_v.vessel_type.lower())
    _new_tanker_by_type = bool(new_v.vessel_type and "tanker" in new_v.vessel_type.lower())
    if dark_v.vessel_type and new_v.vessel_type:
        if dark_v.vessel_type == new_v.vessel_type:
            score += 10
            reasons["same_vessel_type"] = {"points": 10}
        elif _dark_tanker_by_type and _new_tanker_by_type:
            score += 10
            reasons["same_vessel_type"] = {"points": 10, "note": "both_tanker_category"}
    elif not dark_v.vessel_type and not new_v.vessel_type:  # noqa: SIM102
        if is_tanker_type(dark_v) and is_tanker_type(new_v):
            score += 5
            reasons["same_vessel_type"] = {"points": 5, "note": "dwt_inferred_tanker"}

    # Similar DWT (within ±20%)
    if dark_v.deadweight and new_v.deadweight:
        ratio = min(dark_v.deadweight, new_v.deadweight) / max(dark_v.deadweight, new_v.deadweight)
        if ratio >= 0.8:
            score += 10
            reasons["similar_dwt"] = {"points": 10, "ratio": round(ratio, 3)}

    # Similar year_built (within ±3)
    if dark_v.year_built and new_v.year_built:  # noqa: SIM102
        if abs(dark_v.year_built - new_v.year_built) <= 3:
            score += 5
            reasons["similar_year_built"] = {"points": 5}

    # Fuzzy name matching (unidecode normalization + rapidfuzz token_sort_ratio)
    try:
        from rapidfuzz.fuzz import token_sort_ratio
        from unidecode import unidecode

        _dark_name = (unidecode(dark_v.name or "")).strip().upper()
        _new_name = (unidecode(new_v.name or "")).strip().upper()
        if _dark_name and _new_name and len(_dark_name) > 2 and len(_new_name) > 2:
            name_sim = token_sort_ratio(_dark_name, _new_name)
            if name_sim >= 95:
                score += 10
                reasons["similar_name"] = {"points": 10, "ratio": round(name_sim, 1)}
            elif name_sim >= 80:
                score += 5
                reasons["similar_name"] = {"points": 5, "ratio": round(name_sim, 1)}
    except ImportError:
        pass  # unidecode/rapidfuzz not installed

    # Exact callsign match
    _dark_cs = (getattr(dark_v, "callsign", None) or "").strip().upper()
    _new_cs = (getattr(new_v, "callsign", None) or "").strip().upper()
    if _dark_cs and _new_cs and _dark_cs == _new_cs:
        score += 8
        reasons["same_callsign"] = {"points": 8, "callsign": _dark_cs}

    # Historical callsign cross-reference
    if "same_callsign" not in reasons and settings.HISTORY_CROSS_REFERENCE_ENABLED:
        dark_cs = _get_historical_values(history_cache, dark_v.vessel_id, "callsign")
        new_cs = _get_historical_values(history_cache, new_v.vessel_id, "callsign")
        if dark_v.callsign:
            dark_cs = dark_cs | {dark_v.callsign.upper()}
        if new_v.callsign:
            new_cs = new_cs | {new_v.callsign.upper()}
        shared_cs = dark_cs & new_cs
        if shared_cs:
            score += 8
            reasons["historical_shared_callsign"] = {"points": 8, "callsign": next(iter(shared_cs))}

    # Identity change velocity signal
    if settings.HISTORY_CROSS_REFERENCE_ENABLED:
        _velocity_cutoff = new_first["ts"] - timedelta(days=90) if new_first.get("ts") else None
        if _velocity_cutoff:
            changes = _get_recent_change_count(history_cache, new_v.vessel_id, _velocity_cutoff)
            if changes >= 2:
                score += 10
                reasons["identity_change_velocity"] = {"points": 10, "fields_changed": changes}

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

            dark_owner = db.query(_VO_ism).filter(_VO_ism.vessel_id == dark_v.vessel_id).first()
            new_owner = db.query(_VO_ism).filter(_VO_ism.vessel_id == new_v.vessel_id).first()
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
            logger.warning("PI club comparison failed", exc_info=True)

    # Fingerprint similarity bonus: behavioral fingerprint matching
    if settings.FINGERPRINT_ENABLED:
        try:
            from app.modules.vessel_fingerprint import fingerprint_merge_bonus

            fp_bonus = fingerprint_merge_bonus(db, dark_v.vessel_id, new_v.vessel_id)
            if fp_bonus != 0:
                score = max(0, score + fp_bonus)
                reasons["fingerprint_match"] = {"points": fp_bonus}
        except Exception:
            logger.warning("Fingerprint similarity check failed", exc_info=True)

    # --- Negative signals (anti-merge evidence) ---

    # DWT mismatch (>30% difference): strong anti-evidence
    if dark_v.deadweight and new_v.deadweight and "similar_dwt" not in reasons:
        dwt_ratio = min(dark_v.deadweight, new_v.deadweight) / max(
            dark_v.deadweight, new_v.deadweight
        )
        if dwt_ratio < 0.7:
            score = max(0, score - 15)
            reasons["dwt_mismatch"] = {"points": -15, "ratio": round(dwt_ratio, 3)}

    # Different vessel type: active penalty (not just 0 points)
    if (
        dark_v.vessel_type
        and new_v.vessel_type
        and dark_v.vessel_type != new_v.vessel_type
        and "same_vessel_type" not in reasons
    ):
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
    overlap = _has_overlapping_ais(db, dark_v.vessel_id, new_v.vessel_id)
    if overlap:
        reasons["overlapping_ais_tracks"] = {"blocked": True}
        return 0, reasons

    # Anchorage density filter: require extra matching in busy STS areas
    density_count = _count_nearby_vessels(
        db,
        new_first["lat"],
        new_first["lon"],
        new_first["ts"],
        radius_nm=5.0,
        hours=6,
        corridor_vessels_cache=corridor_vessels_cache,
    )
    if density_count > 5:
        has_strong_match = any(
            k in reasons
            for k in (
                "same_imo",
                "historical_shared_imo",
                "same_callsign",
                "historical_shared_callsign",
                "similar_name",
                "shared_ism_manager",
            )
        )
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
            penalty = -10
            score = max(0, score + penalty)
            reasons["anchorage_density_penalty"] = {
                "points": penalty,
                "nearby_vessels": density_count,
                "triple_match_reduced": True,
            }

    # IMO fraud cross-check
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
                fraud_count = (
                    db.query(func.count(SpoofingAnomaly.anomaly_id))
                    .filter(
                        SpoofingAnomaly.anomaly_type == _ST.IMO_FRAUD,
                        SpoofingAnomaly.vessel_id.in_([dark_v.vessel_id, new_v.vessel_id]),
                    )
                    .scalar()
                ) or 0
            if fraud_count > 0:
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


def detect_merge_candidates(
    db: Session,
    max_gap_days: int | None = None,
    require_identity_anchor: bool = False,
) -> dict:
    """Find potential same-vessel pairs across MMSI changes.

    Returns dict with counts: {candidates_created, auto_merged, skipped}.
    """
    from app.modules.merge_execution import execute_merge

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
        logger.info(
            "No merge candidates: dark_vessels=%d, new_vessels=%d. "
            "Dark requires gap events + last AIS >2h ago. "
            "New requires mmsi_first_seen_utc within %d days.",
            len(dark_vessels or []),
            len(new_vessels or []),
            max_gap_days,
        )
        return stats

    # Pre-load anchorage density info for filtering
    corridor_vessels_cache: dict[int, int] = {}

    all_vessel_ids = {v.vessel_id for v, _ in dark_vessels} | {v.vessel_id for v, _ in new_vessels}
    _history_cache = _build_history_cache(db, all_vessel_ids)
    _encounter_pairs = _build_encounter_cache(db, all_vessel_ids)

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
                dark_last["lat"],
                dark_last["lon"],
                new_first["lat"],
                new_first["lon"],
            )
            max_travel = time_delta_h * max_speed

            if distance > max_travel:
                continue

            # 4. Confidence scoring
            confidence, reasons = _score_candidate(
                db,
                dark_v,
                new_v,
                dark_last,
                new_first,
                distance,
                time_delta_h,
                max_travel,
                corridor_vessels_cache,
                history_cache=_history_cache,
                encounter_cache=_encounter_pairs,
            )

            if confidence < min_threshold:
                stats["skipped"] += 1
                continue

            # Extended pass pre-filter: require strong identity anchor
            if require_identity_anchor:
                has_imo = "same_imo" in reasons
                has_triple = (
                    "similar_dwt" in reasons
                    and "same_vessel_type" in reasons
                    and "similar_year_built" in reasons
                )
                if not has_imo and not has_triple:
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

            can_auto_merge = confidence >= auto_threshold
            if can_auto_merge and confidence < 85:
                has_strong_id = any(
                    k in reasons
                    for k in ("same_imo", "same_callsign", "similar_name", "shared_ism_manager")
                )
                if not has_strong_id:
                    can_auto_merge = False

            if can_auto_merge:
                candidate.status = MergeCandidateStatusEnum.AUTO_MERGED
                candidate.resolved_at = now
                candidate.resolved_by = "auto"
            else:
                candidate.status = MergeCandidateStatusEnum.PENDING

            db.add(candidate)
            db.flush()
            stats["candidates_created"] += 1

            if can_auto_merge:
                canonical_id = min(dark_v.vessel_id, new_v.vessel_id)
                absorbed_id = max(dark_v.vessel_id, new_v.vessel_id)
                merge_result = execute_merge(
                    db,
                    canonical_id,
                    absorbed_id,
                    reason=f"Auto-merge: confidence {confidence}",
                    merged_by="auto",
                    candidate_id=candidate.candidate_id,
                )
                if merge_result.get("success"):
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
            db.add(
                SpoofingAnomaly(
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
                )
            )
            stats["flagged"] += 1
            logger.warning(
                "Auto-merge candidate_id=%d may involve fraudulent IMO %s — manual review recommended",
                cand.candidate_id,
                cand_imo,
            )

    if stats["flagged"]:
        db.commit()

    logger.info("IMO fraud merge recheck: %s", stats)
    return stats


def detect_merge_chains(db: Session) -> dict:
    """Find connected components of confirmed-merged vessels and create MergeChain records.

    Algorithm:
      1. Query MergeCandidate rows with status IN (AUTO_MERGED, ANALYST_MERGED)
         and confidence_score >= 50.
      2. Build undirected graph: each candidate connects vessel_a_id and vessel_b_id
      3. Find connected components (BFS)
      4. For components with >= 3 vessels, create MergeChain records

    Returns stats dict: {chains_created, chains_by_band: {HIGH, MEDIUM, LOW}}.
    """
    from app.models.merge_chain import MergeChain

    if not settings.MERGE_CHAIN_DETECTION_ENABLED:
        return {"chains_created": 0, "chains_by_band": {}, "skipped": "feature_disabled"}

    stats: dict = {"chains_created": 0, "chains_by_band": {"HIGH": 0, "MEDIUM": 0, "LOW": 0}}

    # 1. Query qualifying merge candidates (only confirmed merges)
    candidates = (
        db.query(MergeCandidate)
        .filter(
            MergeCandidate.status.in_(
                [
                    MergeCandidateStatusEnum.AUTO_MERGED,
                    MergeCandidateStatusEnum.ANALYST_MERGED,
                ]
            ),
            MergeCandidate.confidence_score >= 50,
        )
        .all()
    )

    if not candidates:
        return stats

    # 2. Build undirected adjacency list
    adjacency: dict[int, set[int]] = {}
    edge_map: dict[tuple[int, int], MergeCandidate] = {}

    for cand in candidates:
        a, b = cand.vessel_a_id, cand.vessel_b_id
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
        edge_key = (min(a, b), max(a, b))
        edge_map[edge_key] = cand

    # 3. BFS to find connected components
    visited: set[int] = set()
    components: list[list[int]] = []

    for node in adjacency:
        if node in visited:
            continue
        component: list[int] = []
        queue = deque([node])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        components.append(component)

    # 4. Create MergeChain for components with >= 3 vessels
    for component in components:
        if len(component) < 3:
            continue

        # Order chronologically by earliest merge candidate date
        vessel_time: dict[int, datetime] = {}
        for vid in component:
            earliest = None
            for cand in candidates:
                if cand.vessel_a_id == vid or cand.vessel_b_id == vid:
                    t = cand.created_at
                    if t and (earliest is None or t < earliest):
                        earliest = t
            vessel_time[vid] = earliest or datetime.utcnow()

        ordered_ids = sorted(component, key=lambda v: vessel_time[v])

        # Collect link candidate_ids
        link_ids = []
        for i in range(len(ordered_ids) - 1):
            a, b = ordered_ids[i], ordered_ids[i + 1]
            edge_key = (min(a, b), max(a, b))
            cand = edge_map.get(edge_key)
            if cand:
                link_ids.append(cand.candidate_id)

        # Chain confidence = min(link confidence scores)
        link_scores = []
        for cand in candidates:
            a, b = cand.vessel_a_id, cand.vessel_b_id
            if a in component and b in component:
                link_scores.append(cand.confidence_score)

        chain_confidence = min(link_scores) if link_scores else 0.0

        # Confidence band
        if chain_confidence >= 75:
            band = "HIGH"
        elif chain_confidence >= 50:
            band = "MEDIUM"
        else:
            band = "LOW"

        # Check for scrapped IMO in chain
        has_scrapped_imo = False
        for vid in ordered_ids:
            v = db.query(Vessel).get(vid)
            if v and v.imo:  # noqa: SIM102
                if not validate_imo_checksum(v.imo):
                    has_scrapped_imo = True
                    break

        # Dedup: skip if chain with same vessel_ids_json already exists
        existing = (
            db.query(MergeChain)
            .filter(
                MergeChain.vessel_ids_json == ordered_ids,
            )
            .first()
        )
        if existing:
            continue

        chain = MergeChain(
            vessel_ids_json=ordered_ids,
            links_json=link_ids,
            chain_length=len(ordered_ids),
            confidence=chain_confidence,
            confidence_band=band,
            evidence_json={
                "has_scrapped_imo": has_scrapped_imo,
                "link_count": len(link_ids),
            },
        )
        db.add(chain)
        stats["chains_created"] += 1
        stats["chains_by_band"][band] += 1

    db.commit()
    logger.info("Merge chain detection: %s", stats)
    return stats


def extended_merge_pass(db: Session) -> dict:
    """Extended merge candidate detection with 180-day window and higher confidence.

    Only considers candidates with strong identity signals:
      - Same IMO number
      - DWT + type + year_built triple match
      - Scrapped IMO link (invalid checksum on either vessel)

    Returns stats dict from detect_merge_candidates or lightweight wrapper.
    """
    if not settings.MERGE_CHAIN_DETECTION_ENABLED:
        return {"candidates_created": 0, "extended": True, "skipped": "feature_disabled"}

    extended_stats = detect_merge_candidates(
        db,
        max_gap_days=180,
        require_identity_anchor=True,
    )
    extended_stats["extended"] = True
    return extended_stats
