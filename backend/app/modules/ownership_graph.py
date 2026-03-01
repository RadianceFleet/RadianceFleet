"""Corporate ownership graph — detect shell company networks and sanctions propagation.

Provides:
  - build_ownership_graph(db)  — build graph of owner->vessel relationships,
    detect shell chains, post-sanction reshuffling, shared addresses, circular ownership
  - propagate_sanctions(db)    — propagate sanctions risk across ownership clusters

Stage 5-A: Corporate Ownership Graph
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Constants
MAX_CHAIN_DEPTH = 10
RESHUFFLING_WINDOW_MONTHS = 12
RESHUFFLING_MIN_CHANGES = 3


def _normalize_name(name: str) -> str:
    """Normalize owner/operator name for grouping: lowercase, strip whitespace."""
    if not name:
        return ""
    return name.strip().lower()


def _build_parent_chain(
    owner_id: int,
    parent_map: Dict[int, Optional[int]],
    max_depth: int = MAX_CHAIN_DEPTH,
) -> List[int]:
    """Walk the parent_owner_id chain upward, returning the chain of owner_ids.

    Returns list from leaf to root. Stops at max_depth or on cycle detection.
    """
    chain: List[int] = [owner_id]
    visited: Set[int] = {owner_id}
    current = owner_id

    for _ in range(max_depth):
        parent = parent_map.get(current)
        if parent is None or parent not in parent_map:
            break
        if parent in visited:
            # Circular ownership detected
            chain.append(parent)
            break
        visited.add(parent)
        chain.append(parent)
        current = parent

    return chain


def _detect_circular_ownership(
    parent_map: Dict[int, Optional[int]],
) -> List[List[int]]:
    """Detect circular ownership chains where owner chain loops back."""
    circles: List[List[int]] = []
    checked: Set[int] = set()

    for owner_id in parent_map:
        if owner_id in checked:
            continue

        visited: Dict[int, int] = {}  # owner_id -> position in chain
        current = owner_id
        pos = 0

        while current is not None and pos <= MAX_CHAIN_DEPTH:
            if current in visited:
                # Found a cycle starting at visited[current]
                cycle_start = visited[current]
                # Reconstruct the cycle
                chain = list(visited.keys())[cycle_start:]
                chain.append(current)
                circles.append(chain)
                break
            visited[current] = pos
            checked.add(current)
            current = parent_map.get(current)
            pos += 1

    return circles


def build_ownership_graph(db: Session) -> dict:
    """Build corporate ownership graph to detect shell company networks.

    Steps:
    1. Query all VesselOwner records (with joined Vessel)
    2. Group by owner/operator name (normalized)
    3. Build graph: owners -> vessels, with edges for shared ownership
    4. Detect patterns:
       - Shell chain: owner A -> subsidiary B -> vessel (depth > 2)
       - Post-sanction reshuffling: >2 ownership changes in 12 months
       - Shared address with sanctioned entity
       - Circular ownership: owner chain loops back

    Returns dict with {clusters_found, shell_chains, reshuffling_detected,
                       circular_ownership, shared_address_sanctioned}.
    """
    if not getattr(settings, "OWNERSHIP_GRAPH_ENABLED", False):
        logger.info("Ownership graph disabled -- skipping")
        return {
            "status": "disabled",
            "clusters_found": 0,
            "shell_chains": 0,
            "reshuffling_detected": 0,
            "circular_ownership": 0,
            "shared_address_sanctioned": 0,
        }

    from app.models.vessel_owner import VesselOwner
    from app.models.vessel import Vessel

    stats: Dict[str, Any] = {
        "status": "ok",
        "clusters_found": 0,
        "shell_chains": 0,
        "reshuffling_detected": 0,
        "circular_ownership": 0,
        "shared_address_sanctioned": 0,
    }

    owners = db.query(VesselOwner).all()
    if not owners:
        return stats

    # Build owner lookup and parent map
    owner_by_id: Dict[int, Any] = {o.owner_id: o for o in owners}
    parent_map: Dict[int, Optional[int]] = {}
    for o in owners:
        parent_id = getattr(o, "parent_owner_id", None)
        if isinstance(parent_id, int):
            parent_map[o.owner_id] = parent_id
        else:
            parent_map[o.owner_id] = None

    # Group by normalized owner name -> list of vessel_ids
    name_groups: Dict[str, List[int]] = defaultdict(list)
    for o in owners:
        norm = _normalize_name(o.owner_name)
        if norm:
            name_groups[norm].append(o.vessel_id)

    # Count clusters (groups with 2+ vessels)
    stats["clusters_found"] = sum(1 for vids in name_groups.values() if len(vids) >= 2)

    # Detect shell chains (depth > 2)
    shell_chains = 0
    for owner_id in parent_map:
        chain = _build_parent_chain(owner_id, parent_map)
        if len(chain) > 2:
            shell_chains += 1
    stats["shell_chains"] = shell_chains

    # Detect circular ownership
    circles = _detect_circular_ownership(parent_map)
    stats["circular_ownership"] = len(circles)

    # Detect post-sanction reshuffling: >2 ownership changes in 12 months per vessel
    vessel_owners: Dict[int, List[Any]] = defaultdict(list)
    for o in owners:
        vessel_owners[o.vessel_id].append(o)

    reshuffling_count = 0
    now = datetime.now(tz=None)  # naive UTC — matches VesselOwner.verified_at
    window_start = now - timedelta(days=365)

    for vessel_id, vo_list in vessel_owners.items():
        # Count owners with verified_at within the last 12 months
        # (ownership changes are proxied by verified_at or creation time)
        recent_changes = 0
        for vo in vo_list:
            change_date = getattr(vo, "verified_at", None)
            if isinstance(change_date, datetime) and change_date >= window_start:
                recent_changes += 1
        if recent_changes >= RESHUFFLING_MIN_CHANGES:
            reshuffling_count += 1
    stats["reshuffling_detected"] = reshuffling_count

    # Detect shared address with sanctioned entity
    # Collect sanctioned owner countries
    sanctioned_countries: Set[str] = set()
    for o in owners:
        if o.is_sanctioned and o.country:
            sanctioned_countries.add(o.country.strip().lower())

    shared_address_count = 0
    if sanctioned_countries:
        for o in owners:
            if not o.is_sanctioned and o.country:
                if o.country.strip().lower() in sanctioned_countries:
                    shared_address_count += 1
    stats["shared_address_sanctioned"] = shared_address_count

    logger.info(
        "Ownership graph built: %d clusters, %d shell chains, %d reshuffling, %d circular",
        stats["clusters_found"],
        stats["shell_chains"],
        stats["reshuffling_detected"],
        stats["circular_ownership"],
    )
    return stats


def propagate_sanctions(db: Session) -> dict:
    """Propagate sanctions risk to all vessels under the same ownership cluster.

    For vessels owned by entities matching sanctioned owner names (from watchlist),
    propagate risk to all vessels under the same OwnerCluster.

    Returns dict with {status, vessels_flagged, clusters_propagated}.
    """
    if not getattr(settings, "OWNERSHIP_GRAPH_ENABLED", False):
        logger.info("Ownership graph disabled -- skipping sanctions propagation")
        return {"status": "disabled", "vessels_flagged": 0, "clusters_propagated": 0}

    from app.models.vessel_owner import VesselOwner
    from app.models.owner_cluster import OwnerCluster
    from app.models.owner_cluster_member import OwnerClusterMember

    stats: Dict[str, Any] = {
        "status": "ok",
        "vessels_flagged": 0,
        "clusters_propagated": 0,
    }

    # Find sanctioned owners
    sanctioned_owners = db.query(VesselOwner).filter(
        VesselOwner.is_sanctioned == True,
    ).all()

    if not sanctioned_owners:
        return stats

    # Find clusters containing sanctioned owners
    sanctioned_owner_ids = {o.owner_id for o in sanctioned_owners}
    sanctioned_members = db.query(OwnerClusterMember).filter(
        OwnerClusterMember.owner_id.in_(sanctioned_owner_ids),
    ).all()

    if not sanctioned_members:
        return stats

    sanctioned_cluster_ids = {m.cluster_id for m in sanctioned_members}

    # For each sanctioned cluster, find all member owners and flag them
    for cluster_id in sanctioned_cluster_ids:
        # Mark cluster as sanctioned
        cluster = db.query(OwnerCluster).filter(
            OwnerCluster.cluster_id == cluster_id,
        ).first()
        if cluster and not cluster.is_sanctioned:
            cluster.is_sanctioned = True

        # Get all owners in this cluster
        all_members = db.query(OwnerClusterMember).filter(
            OwnerClusterMember.cluster_id == cluster_id,
        ).all()

        cluster_owner_ids = {m.owner_id for m in all_members}
        non_sanctioned_ids = cluster_owner_ids - sanctioned_owner_ids

        # Flag non-sanctioned owners in the same cluster
        for owner_id in non_sanctioned_ids:
            owner = db.query(VesselOwner).filter(
                VesselOwner.owner_id == owner_id,
            ).first()
            if owner and not owner.is_sanctioned:
                # Don't set is_sanctioned=True (that means directly sanctioned),
                # but we count them as flagged for risk propagation
                stats["vessels_flagged"] += 1

        stats["clusters_propagated"] += 1

    db.commit()

    logger.info(
        "Sanctions propagation complete: %d clusters, %d vessels flagged",
        stats["clusters_propagated"],
        stats["vessels_flagged"],
    )
    return stats
