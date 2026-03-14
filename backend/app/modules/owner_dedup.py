"""Owner deduplication — fuzzy-match VesselOwner records into OwnerCluster groups.

Uses rapidfuzz token_sort_ratio with sorted-first-token bucketing for efficient
comparisons, then union-find clustering.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from rapidfuzz import fuzz

from app.config import settings
from app.models.owner_cluster import OwnerCluster
from app.models.owner_cluster_member import OwnerClusterMember
from app.models.vessel_owner import VesselOwner
from app.modules.normalize import normalize_owner_name

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 85

# Backward-compatible alias for tests that import the private name
_normalize_owner_name = normalize_owner_name


# ---------------------------------------------------------------------------
# Union-Find
# ---------------------------------------------------------------------------


class _UnionFind:
    """Disjoint-set (union-find) with path compression and union by rank."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}
        self.rank: dict[int, int] = {}

    def find(self, x: int) -> int:
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_owner_dedup(db) -> dict:
    """Cluster VesselOwner records by fuzzy name similarity.

    Returns dict with {status, clusters_created, owners_processed}.
    """
    if not getattr(settings, "FLEET_ANALYSIS_ENABLED", False):
        logger.info("Fleet analysis disabled — skipping owner dedup")
        return {"status": "disabled", "clusters_created": 0, "owners_processed": 0}

    owners: list[VesselOwner] = db.query(VesselOwner).all()
    if not owners:
        return {"status": "ok", "clusters_created": 0, "owners_processed": 0}

    # FK-safe cleanup of existing clusters before re-running
    try:
        from app.models.fleet_alert import FleetAlert

        db.query(FleetAlert).filter(FleetAlert.owner_cluster_id.isnot(None)).delete(
            synchronize_session="fetch"
        )
    except Exception:
        logger.debug("FleetAlert owner_cluster_id cleanup skipped", exc_info=True)
    db.query(OwnerClusterMember).delete(synchronize_session="fetch")
    db.query(OwnerCluster).delete(synchronize_session="fetch")
    db.flush()

    # Build normalized names + sorted-first-token buckets
    # Fix: First-letter bucketing defeats token_sort_ratio because "MARITIME ALPINE"
    # (bucket M) and "ALPINE MARITIME" (bucket A) are never compared.
    # Sorted-first-token: normalize → split → sort → first sorted token as key.
    norm_map: dict[int, str] = {}  # owner_id -> normalized name
    buckets: dict[str, list[int]] = defaultdict(list)  # bucket_key -> [owner_ids]

    for owner in owners:
        norm = normalize_owner_name(owner.owner_name)
        if not norm:
            continue
        norm_map[owner.owner_id] = norm
        tokens = sorted(norm.split())
        bucket_key = tokens[0] if tokens else ""
        buckets[bucket_key].append(owner.owner_id)

    # Pairwise comparison within buckets, union-find clustering
    uf = _UnionFind()
    similarity_scores: dict[tuple, float] = {}

    for _letter, ids in buckets.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a_id, b_id = ids[i], ids[j]
                score = fuzz.token_sort_ratio(norm_map[a_id], norm_map[b_id])
                if score >= SIMILARITY_THRESHOLD:
                    uf.union(a_id, b_id)
                    similarity_scores[(a_id, b_id)] = score

    # Group owners by cluster root
    clusters_map: dict[int, list[int]] = defaultdict(list)
    for oid in norm_map:
        root = uf.find(oid)
        clusters_map[root].append(oid)

    # Build lookup
    owner_by_id: dict[int, VesselOwner] = {o.owner_id: o for o in owners}

    clusters_created = 0
    for root_id, member_ids in clusters_map.items():
        # Pick canonical name = most common raw name variant
        name_counts: dict[str, int] = defaultdict(int)
        any_sanctioned = False
        country = None
        vessel_ids_seen: set = set()

        for mid in member_ids:
            owner = owner_by_id.get(mid)
            if not owner:
                continue
            name_counts[owner.owner_name] += 1
            if owner.is_sanctioned:
                any_sanctioned = True
            if owner.country and not country:
                country = owner.country
            vessel_ids_seen.add(owner.vessel_id)

        canonical = (
            max(name_counts, key=name_counts.get)
            if name_counts
            else norm_map.get(root_id, "UNKNOWN")
        )

        cluster = OwnerCluster(
            canonical_name=canonical,
            country=country,
            is_sanctioned=any_sanctioned,
            vessel_count=len(vessel_ids_seen),
        )
        db.add(cluster)
        db.flush()  # get cluster_id

        for mid in member_ids:
            pair_key_ab = (min(root_id, mid), max(root_id, mid))
            sim = similarity_scores.get(pair_key_ab, 100.0)  # self-match = 100
            member = OwnerClusterMember(
                cluster_id=cluster.cluster_id,
                owner_id=mid,
                similarity_score=sim,
            )
            db.add(member)

        clusters_created += 1

    db.commit()

    logger.info("Owner dedup complete: %d clusters from %d owners", clusters_created, len(norm_map))
    return {
        "status": "ok",
        "clusters_created": clusters_created,
        "owners_processed": len(norm_map),
    }
