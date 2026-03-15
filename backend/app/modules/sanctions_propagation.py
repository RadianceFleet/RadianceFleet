"""Sanctions Propagation Engine — multi-hop propagation from sanctioned entities.

Propagates sanctions risk through shared infrastructure:
  - Depth 1: shared ISM manager, ship manager, DOC company, registered owner
  - Depth 2: second-hop shared managers from depth-1 vessels
  - Depth 3: OwnerCluster-based name-similarity propagation
  - Compound signal: P&I club + flag + any manager match bonus

Creates SanctionsPropagation DB records with risk scores capped at max_score.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models.sanctions_propagation import SanctionsPropagation
from app.models.vessel_owner import VesselOwner
from app.models.vessel_watchlist import VesselWatchlist
from app.modules.scoring_config import load_scoring_config

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize owner/manager name for comparison: strip + uppercase."""
    if not name:
        return ""
    return name.strip().upper()


def _get_scoring_config() -> dict[str, Any]:
    """Load sanctions_propagation scoring config section."""
    config = load_scoring_config()
    return config.get("sanctions_propagation", {
        "depth_1": 40,
        "depth_2": 25,
        "depth_3": 15,
        "compound_signal_bonus": 10,
        "max_score": 50,
    })


def _get_vessel_managers(db: Session, vessel_id: int) -> dict[str, str]:
    """Return dict of {manager_type: manager_name} for a vessel.

    Covers:
      - ism_manager column on any VesselOwner row for this vessel
      - ownership_type-based records (ship_manager, doc_company, registered_owner)
    """
    owners = db.query(VesselOwner).filter(VesselOwner.vessel_id == vessel_id).all()
    managers: dict[str, str] = {}

    for owner in owners:
        # ISM manager from dedicated column
        if owner.ism_manager and _normalize_name(owner.ism_manager):
            managers["shared_ism_manager"] = _normalize_name(owner.ism_manager)

        # Ownership-type based managers
        otype = owner.ownership_type
        if otype in ("ship_manager", "doc_company", "registered_owner") and owner.owner_name:
            norm = _normalize_name(owner.owner_name)
            if norm:
                key = f"shared_{otype}"
                managers[key] = norm

    return managers


def _find_shared_manager_vessels(
    db: Session,
    vessel_id: int,
    manager_type: str,
    manager_name: str,
    exclude_ids: set[int],
) -> list[tuple[int, str]]:
    """Find vessels sharing a specific manager with the given vessel.

    Returns list of (vessel_id, matched_name) tuples.
    """
    normalized = _normalize_name(manager_name)
    if not normalized:
        return []

    results: list[tuple[int, str]] = []

    if manager_type == "shared_ism_manager":
        # ISM manager is stored as a column on VesselOwner
        owners = (
            db.query(VesselOwner)
            .filter(VesselOwner.ism_manager.isnot(None))
            .all()
        )
        for owner in owners:
            if owner.vessel_id in exclude_ids or owner.vessel_id == vessel_id:
                continue
            if _normalize_name(owner.ism_manager) == normalized:
                results.append((owner.vessel_id, owner.ism_manager))
    else:
        # Ship manager, DOC company, registered owner — stored as ownership_type rows
        # Extract the actual ownership_type from the key (e.g. "shared_ship_manager" -> "ship_manager")
        otype = manager_type.replace("shared_", "", 1)
        owners = (
            db.query(VesselOwner)
            .filter(VesselOwner.ownership_type == otype)
            .all()
        )
        for owner in owners:
            if owner.vessel_id in exclude_ids or owner.vessel_id == vessel_id:
                continue
            if _normalize_name(owner.owner_name) == normalized:
                results.append((owner.vessel_id, owner.owner_name))

    return results


def _check_compound_signal(db: Session, vessel_id: int, source_vessel_id: int) -> bool:
    """Check if two vessels share P&I club + flag + any manager.

    Returns True if compound signal detected.
    """
    from app.models.vessel import Vessel

    # Get flags
    v1 = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    v2 = db.query(Vessel).filter(Vessel.vessel_id == source_vessel_id).first()

    if not v1 or not v2 or not v1.flag or not v2.flag:
        return False

    if _normalize_name(v1.flag) != _normalize_name(v2.flag):
        return False

    # Check P&I club match
    owners_v1 = db.query(VesselOwner).filter(VesselOwner.vessel_id == vessel_id).all()
    owners_v2 = db.query(VesselOwner).filter(VesselOwner.vessel_id == source_vessel_id).all()

    pi_clubs_v1 = {
        _normalize_name(o.pi_club_name)
        for o in owners_v1
        if o.pi_club_name and _normalize_name(o.pi_club_name)
    }
    pi_clubs_v2 = {
        _normalize_name(o.pi_club_name)
        for o in owners_v2
        if o.pi_club_name and _normalize_name(o.pi_club_name)
    }

    if not pi_clubs_v1 or not pi_clubs_v2 or not pi_clubs_v1.intersection(pi_clubs_v2):
        return False

    # Check any shared manager
    managers_v1 = _get_vessel_managers(db, vessel_id)
    managers_v2 = _get_vessel_managers(db, source_vessel_id)

    for mtype, mname in managers_v1.items():
        if mtype in managers_v2 and managers_v2[mtype] == mname:
            return True

    return False


def propagate_sanctions_multi_hop(
    db: Session,
    vessel_id: int | None = None,
) -> list[SanctionsPropagation]:
    """Main entry point for multi-hop sanctions propagation.

    If vessel_id given, propagate from that specific sanctioned vessel.
    Otherwise, propagate from all vessels with active watchlist hits.

    Returns list of created SanctionsPropagation records.
    """
    if not settings.SANCTIONS_PROPAGATION_ENABLED:
        logger.info("Sanctions propagation disabled -- skipping")
        return []

    score_cfg = _get_scoring_config()
    max_depth = getattr(settings, "SANCTIONS_PROPAGATION_MAX_DEPTH", 3)
    max_score = score_cfg.get("max_score", 50)

    # Find sanctioned vessels to propagate from
    if vessel_id is not None:
        sanctioned_vessel_ids = {vessel_id}
    else:
        # Find all vessels with active watchlist hits
        watchlist_entries = (
            db.query(VesselWatchlist.vessel_id)
            .filter(VesselWatchlist.is_active == True)  # noqa: E712
            .distinct()
            .all()
        )
        sanctioned_vessel_ids = {row[0] for row in watchlist_entries}

    if not sanctioned_vessel_ids:
        logger.info("No sanctioned vessels found for propagation")
        return []

    # Deactivate previous propagation records for these sources
    for src_vid in sanctioned_vessel_ids:
        db.query(SanctionsPropagation).filter(
            SanctionsPropagation.source_vessel_id == src_vid,
            SanctionsPropagation.is_active == True,  # noqa: E712
        ).update({"is_active": False})

    all_records: list[SanctionsPropagation] = []
    vessel_score_totals: dict[int, float] = {}  # Track total score per vessel

    for src_vid in sanctioned_vessel_ids:
        records = _propagate_from_vessel(
            db, src_vid, sanctioned_vessel_ids, score_cfg, max_depth, max_score,
            vessel_score_totals,
        )
        all_records.extend(records)

    db.commit()

    logger.info(
        "Sanctions propagation complete: %d records created from %d sanctioned vessels",
        len(all_records),
        len(sanctioned_vessel_ids),
    )
    return all_records


def _propagate_from_vessel(
    db: Session,
    source_vessel_id: int,
    all_sanctioned_ids: set[int],
    score_cfg: dict[str, Any],
    max_depth: int,
    max_score: float,
    vessel_score_totals: dict[int, float],
) -> list[SanctionsPropagation]:
    """Propagate sanctions from a single sanctioned vessel through shared infrastructure."""
    records: list[SanctionsPropagation] = []

    # Get source vessel's managers
    source_managers = _get_vessel_managers(db, source_vessel_id)
    if not source_managers:
        return records

    # Track vessels already processed at each depth to avoid duplicates
    exclude_ids = set(all_sanctioned_ids)

    # ── Depth 1: Direct shared managers ──────────────────────────────────
    depth_1_score = score_cfg.get("depth_1", 40)
    depth_1_vessels: set[int] = set()

    for mtype, mname in source_managers.items():
        shared = _find_shared_manager_vessels(db, source_vessel_id, mtype, mname, exclude_ids)
        for vid, matched_name in shared:
            if vid in depth_1_vessels:
                continue
            depth_1_vessels.add(vid)

            current_total = vessel_score_totals.get(vid, 0.0)
            capped_score = min(depth_1_score, max_score - current_total)
            if capped_score <= 0:
                continue

            record = SanctionsPropagation(
                vessel_id=vid,
                source_vessel_id=source_vessel_id,
                propagation_depth=1,
                propagation_type=mtype,
                propagation_path_json=json.dumps([source_vessel_id, vid]),
                shared_fields_json=json.dumps({mtype: matched_name}),
                risk_score_component=capped_score,
                is_active=True,
            )
            db.add(record)
            records.append(record)
            vessel_score_totals[vid] = current_total + capped_score

    # Check compound signal for depth-1 vessels
    compound_bonus = score_cfg.get("compound_signal_bonus", 10)
    for vid in depth_1_vessels:
        if _check_compound_signal(db, vid, source_vessel_id):
            current_total = vessel_score_totals.get(vid, 0.0)
            capped_bonus = min(compound_bonus, max_score - current_total)
            if capped_bonus <= 0:
                continue

            record = SanctionsPropagation(
                vessel_id=vid,
                source_vessel_id=source_vessel_id,
                propagation_depth=1,
                propagation_type="compound_signal",
                propagation_path_json=json.dumps([source_vessel_id, vid]),
                shared_fields_json=json.dumps({"compound": "pi_club+flag+manager"}),
                risk_score_component=capped_bonus,
                is_active=True,
            )
            db.add(record)
            records.append(record)
            vessel_score_totals[vid] = current_total + capped_bonus

    if max_depth < 2:
        return records

    # ── Depth 2: Second-hop shared managers ──────────────────────────────
    depth_2_score = score_cfg.get("depth_2", 25)
    depth_2_exclude = exclude_ids | depth_1_vessels
    depth_2_vessels: set[int] = set()

    for d1_vid in depth_1_vessels:
        d1_managers = _get_vessel_managers(db, d1_vid)
        for mtype, mname in d1_managers.items():
            shared = _find_shared_manager_vessels(db, d1_vid, mtype, mname, depth_2_exclude)
            for vid, matched_name in shared:
                if vid in depth_2_vessels:
                    continue
                depth_2_vessels.add(vid)

                current_total = vessel_score_totals.get(vid, 0.0)
                capped_score = min(depth_2_score, max_score - current_total)
                if capped_score <= 0:
                    continue

                record = SanctionsPropagation(
                    vessel_id=vid,
                    source_vessel_id=source_vessel_id,
                    propagation_depth=2,
                    propagation_type=mtype,
                    propagation_path_json=json.dumps([source_vessel_id, d1_vid, vid]),
                    shared_fields_json=json.dumps({mtype: matched_name}),
                    risk_score_component=capped_score,
                    is_active=True,
                )
                db.add(record)
                records.append(record)
                vessel_score_totals[vid] = current_total + capped_score

    if max_depth < 3:
        return records

    # ── Depth 3: Cluster-based propagation ───────────────────────────────
    depth_3_score = score_cfg.get("depth_3", 15)
    depth_3_exclude = depth_2_exclude | depth_2_vessels

    # Collect owner_ids from depth-1 and depth-2 vessels
    related_vessel_ids = depth_1_vessels | depth_2_vessels
    if not related_vessel_ids:
        return records

    from app.models.owner_cluster import OwnerCluster  # noqa: F401
    from app.models.owner_cluster_member import OwnerClusterMember

    # Find owner_ids for related vessels
    related_owners = (
        db.query(VesselOwner.owner_id)
        .filter(VesselOwner.vessel_id.in_(related_vessel_ids))
        .all()
    )
    related_owner_ids = {row[0] for row in related_owners}
    if not related_owner_ids:
        return records

    # Find clusters containing these owners
    cluster_members = (
        db.query(OwnerClusterMember)
        .filter(OwnerClusterMember.owner_id.in_(related_owner_ids))
        .all()
    )
    cluster_ids = {m.cluster_id for m in cluster_members}
    if not cluster_ids:
        return records

    # Find all members in those clusters
    all_cluster_members = (
        db.query(OwnerClusterMember)
        .filter(OwnerClusterMember.cluster_id.in_(cluster_ids))
        .all()
    )
    all_cluster_owner_ids = {m.owner_id for m in all_cluster_members} - related_owner_ids

    if not all_cluster_owner_ids:
        return records

    # Find vessels owned by these cluster members
    cluster_vessel_owners = (
        db.query(VesselOwner)
        .filter(VesselOwner.owner_id.in_(all_cluster_owner_ids))
        .all()
    )

    for owner in cluster_vessel_owners:
        vid = owner.vessel_id
        if vid in depth_3_exclude:
            continue
        depth_3_exclude.add(vid)

        current_total = vessel_score_totals.get(vid, 0.0)
        capped_score = min(depth_3_score, max_score - current_total)
        if capped_score <= 0:
            continue

        record = SanctionsPropagation(
            vessel_id=vid,
            source_vessel_id=source_vessel_id,
            propagation_depth=3,
            propagation_type="owner_cluster",
            propagation_path_json=json.dumps([source_vessel_id, "cluster", vid]),
            shared_fields_json=json.dumps({"owner_id": owner.owner_id, "owner_name": owner.owner_name}),
            risk_score_component=capped_score,
            is_active=True,
        )
        db.add(record)
        records.append(record)
        vessel_score_totals[vid] = current_total + capped_score

    return records


def get_vessel_propagations(db: Session, vessel_id: int) -> list[dict[str, Any]]:
    """Get active propagation records for a vessel."""
    records = (
        db.query(SanctionsPropagation)
        .filter(
            SanctionsPropagation.vessel_id == vessel_id,
            SanctionsPropagation.is_active == True,  # noqa: E712
        )
        .all()
    )
    return [
        {
            "id": r.id,
            "vessel_id": r.vessel_id,
            "source_vessel_id": r.source_vessel_id,
            "source_owner_id": r.source_owner_id,
            "propagation_depth": r.propagation_depth,
            "propagation_type": r.propagation_type,
            "propagation_path": json.loads(r.propagation_path_json) if r.propagation_path_json else [],
            "shared_fields": json.loads(r.shared_fields_json) if r.shared_fields_json else {},
            "risk_score_component": r.risk_score_component,
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in records
    ]
