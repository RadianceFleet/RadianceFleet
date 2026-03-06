"""Merge execution and reversal — reassign FK records under canonical vessel.

Extracted from identity_resolver.py to reduce module size.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.orm import Session

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

logger = logging.getLogger(__name__)


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
        conflict = (
            db.query(VesselWatchlist)
            .filter(
                VesselWatchlist.vessel_id == canonical_id,
                VesselWatchlist.watchlist_source == entry.watchlist_source,
            )
            .first()
        )
        if conflict:
            if entry.match_confidence > conflict.match_confidence:
                conflict.match_confidence = entry.match_confidence
                conflict.reason = entry.reason
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
    if absorbed.mmsi_first_seen_utc:
        if canonical.mmsi_first_seen_utc is None or absorbed.mmsi_first_seen_utc < canonical.mmsi_first_seen_utc:
            canonical.mmsi_first_seen_utc = absorbed.mmsi_first_seen_utc

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
    from app.modules.identity_resolver import resolve_canonical

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
    db.query(AISGapEvent).filter(
        AISGapEvent.vessel_id == absorbed_id,
        AISGapEvent.original_vessel_id.is_(None),
    ).update(
        {AISGapEvent.original_vessel_id: absorbed_id},
        synchronize_session="fetch",
    )
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
            continue
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


def reverse_merge(db: Session, merge_op_id: int) -> dict:
    """Reverse a completed merge operation using the affected_records snapshot.

    Best-effort reversal. Reactivates absorbed vessel, re-creates deleted
    watchlist/STS records, clears evidence card provenance, resets candidate
    status.

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
            logger.warning(
                "Reverse merge %d: %d %s records NOT reassigned back (PK list not stored)",
                merge_op_id, count, name,
            )

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
