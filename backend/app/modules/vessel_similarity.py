"""Comparative vessel similarity — behavioral fingerprint + ownership overlap.

Combines Mahalanobis-distance fingerprint comparison with ownership graph
overlap to find vessels that are operationally and structurally similar to
a flagged vessel.
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# ── Distance normalisation ──────────────────────────────────────────────────

_DISTANCE_SCALE = 10.0  # controls steepness of 1/(1+d/scale) mapping


def _normalise_distance(distance: float) -> float:
    """Map Mahalanobis distance to [0, 1] similarity via 1/(1 + d/scale)."""
    return 1.0 / (1.0 + distance / _DISTANCE_SCALE)


def _distance_band(distance: float) -> str:
    """Assign a qualitative band to a fingerprint distance."""
    if distance <= 3.0:
        return "near"
    elif distance <= 6.0:
        return "moderate"
    return "far"


def _similarity_tier(composite: float) -> str:
    """Assign HIGH/MEDIUM/LOW tier based on composite score."""
    if composite >= 0.7:
        return "HIGH"
    elif composite >= 0.4:
        return "MEDIUM"
    return "LOW"


# ── Ownership similarity ────────────────────────────────────────────────────


def compute_ownership_similarity(db: Session, a_id: int, b_id: int) -> dict[str, Any]:
    """Compute ownership similarity between two vessels.

    Checks:
      - Shared OwnerCluster membership
      - Shared ISM manager
      - Shared P&I club
      - Same owner name (normalised via unidecode)
      - Same country

    Returns dict with ``score`` (0-1) and ``breakdown`` details.
    """
    from app.models.owner_cluster_member import OwnerClusterMember
    from app.models.vessel_owner import VesselOwner

    try:
        from unidecode import unidecode
    except ImportError:  # pragma: no cover
        def unidecode(s: str) -> str:
            return s

    owners_a = db.query(VesselOwner).filter(VesselOwner.vessel_id == a_id).all()
    owners_b = db.query(VesselOwner).filter(VesselOwner.vessel_id == b_id).all()

    breakdown: dict[str, Any] = {
        "shared_cluster": False,
        "shared_ism_manager": False,
        "shared_pi_club": False,
        "same_owner_name": False,
        "same_country": False,
    }

    if not owners_a or not owners_b:
        return {"score": 0.0, "breakdown": breakdown}

    # Shared OwnerCluster
    owner_ids_a = {o.owner_id for o in owners_a}
    owner_ids_b = {o.owner_id for o in owners_b}

    clusters_a: set[int] = set()
    clusters_b: set[int] = set()
    if owner_ids_a:
        for m in db.query(OwnerClusterMember).filter(
            OwnerClusterMember.owner_id.in_(owner_ids_a)
        ).all():
            clusters_a.add(m.cluster_id)
    if owner_ids_b:
        for m in db.query(OwnerClusterMember).filter(
            OwnerClusterMember.owner_id.in_(owner_ids_b)
        ).all():
            clusters_b.add(m.cluster_id)

    shared_clusters = clusters_a & clusters_b
    if shared_clusters:
        breakdown["shared_cluster"] = True

    # Shared ISM manager
    ism_a = {o.ism_manager.strip().lower() for o in owners_a if o.ism_manager}
    ism_b = {o.ism_manager.strip().lower() for o in owners_b if o.ism_manager}
    if ism_a & ism_b:
        breakdown["shared_ism_manager"] = True

    # Shared P&I club
    pi_a = {o.pi_club_name.strip().lower() for o in owners_a if o.pi_club_name}
    pi_b = {o.pi_club_name.strip().lower() for o in owners_b if o.pi_club_name}
    if pi_a & pi_b:
        breakdown["shared_pi_club"] = True

    # Same owner name (normalised)
    names_a = {unidecode(o.owner_name).strip().lower() for o in owners_a if o.owner_name}
    names_b = {unidecode(o.owner_name).strip().lower() for o in owners_b if o.owner_name}
    if names_a & names_b:
        breakdown["same_owner_name"] = True

    # Same country
    countries_a = {o.country.strip().lower() for o in owners_a if o.country}
    countries_b = {o.country.strip().lower() for o in owners_b if o.country}
    if countries_a & countries_b:
        breakdown["same_country"] = True

    # Score: weighted sum of boolean signals
    weights = {
        "shared_cluster": 0.35,
        "shared_ism_manager": 0.20,
        "shared_pi_club": 0.15,
        "same_owner_name": 0.20,
        "same_country": 0.10,
    }
    score = sum(weights[k] for k, v in breakdown.items() if v)

    return {"score": round(score, 4), "breakdown": breakdown}


# ── Composite similarity ────────────────────────────────────────────────────


def compute_composite_similarity(
    db: Session,
    vessel_id: int,
    candidate_id: int,
    *,
    include_ownership: bool = True,
) -> dict[str, Any] | None:
    """Compute weighted composite similarity between two vessels.

    Returns dict with composite score, fingerprint distance, ownership info,
    and tier assignment.  Returns ``None`` if fingerprints are unavailable.
    """
    from app.models.vessel_fingerprint import VesselFingerprint
    from app.modules.vessel_fingerprint import mahalanobis_distance

    fp_source = (
        db.query(VesselFingerprint)
        .filter(VesselFingerprint.vessel_id == vessel_id)
        .first()
    )
    fp_target = (
        db.query(VesselFingerprint)
        .filter(VesselFingerprint.vessel_id == candidate_id)
        .first()
    )
    if fp_source is None or fp_target is None:
        return None

    distance = mahalanobis_distance(fp_source, fp_target)
    fp_similarity = _normalise_distance(distance)
    band = _distance_band(distance)

    fp_weight = settings.VESSEL_SIMILARITY_FINGERPRINT_WEIGHT
    own_weight = settings.VESSEL_SIMILARITY_OWNERSHIP_WEIGHT

    ownership_result: dict[str, Any] = {"score": 0.0, "breakdown": {}}
    if include_ownership:
        ownership_result = compute_ownership_similarity(db, vessel_id, candidate_id)

    composite = fp_weight * fp_similarity + own_weight * ownership_result["score"]
    tier = _similarity_tier(composite)

    return {
        "source_vessel_id": vessel_id,
        "target_vessel_id": candidate_id,
        "fingerprint_distance": round(distance, 4),
        "fingerprint_similarity": round(fp_similarity, 4),
        "fingerprint_band": band,
        "ownership_similarity_score": ownership_result["score"],
        "ownership_breakdown": ownership_result.get("breakdown", {}),
        "composite_similarity_score": round(composite, 4),
        "similarity_tier": tier,
    }


# ── Top-level finder ────────────────────────────────────────────────────────


def find_similar_vessels(
    db: Session,
    vessel_id: int,
    *,
    limit: int = 20,
    include_ownership: bool = True,
) -> list[dict[str, Any]]:
    """Find vessels similar to *vessel_id* by behavioural fingerprint + ownership.

    Uses ``rank_candidates`` from vessel_fingerprint for eliminative filtering,
    then enriches each candidate with ownership overlap and re-sorts by
    composite similarity score.
    """
    if not settings.VESSEL_SIMILARITY_ENABLED:
        return []

    from app.modules.vessel_fingerprint import rank_candidates

    # Get behavioural candidates (up to 500, pre-filtered by type/DWT/AIS class)
    candidates = rank_candidates(db, vessel_id, limit=500)
    if not candidates:
        return []

    results: list[dict[str, Any]] = []
    for cand in candidates:
        comp = compute_composite_similarity(
            db,
            vessel_id,
            cand["vessel_id"],
            include_ownership=include_ownership,
        )
        if comp is None:
            continue
        results.append(comp)

    # Sort descending by composite score
    results.sort(key=lambda r: r["composite_similarity_score"], reverse=True)

    return results[:limit]


# ── Persistence helpers ─────────────────────────────────────────────────────


def persist_similarity_results(
    db: Session,
    results: list[dict[str, Any]],
) -> list[Any]:
    """Persist a list of similarity result dicts to the database.

    Upserts: if a (source, target) pair already exists, it is updated.
    """
    from app.models.vessel_similarity_result import VesselSimilarityResult

    records: list[VesselSimilarityResult] = []
    now = datetime.now(UTC)

    for r in results:
        existing = (
            db.query(VesselSimilarityResult)
            .filter(
                VesselSimilarityResult.source_vessel_id == r["source_vessel_id"],
                VesselSimilarityResult.target_vessel_id == r["target_vessel_id"],
            )
            .first()
        )
        if existing:
            existing.fingerprint_distance = r["fingerprint_distance"]
            existing.fingerprint_band = r["fingerprint_band"]
            existing.ownership_similarity_score = r["ownership_similarity_score"]
            existing.composite_similarity_score = r["composite_similarity_score"]
            existing.similarity_tier = r["similarity_tier"]
            existing.details_json = {
                "fingerprint_similarity": r.get("fingerprint_similarity"),
                "ownership_breakdown": r.get("ownership_breakdown"),
            }
            existing.created_at = now
            records.append(existing)
        else:
            rec = VesselSimilarityResult(
                source_vessel_id=r["source_vessel_id"],
                target_vessel_id=r["target_vessel_id"],
                fingerprint_distance=r["fingerprint_distance"],
                fingerprint_band=r["fingerprint_band"],
                ownership_similarity_score=r["ownership_similarity_score"],
                composite_similarity_score=r["composite_similarity_score"],
                similarity_tier=r["similarity_tier"],
                details_json={
                    "fingerprint_similarity": r.get("fingerprint_similarity"),
                    "ownership_breakdown": r.get("ownership_breakdown"),
                },
                created_at=now,
            )
            db.add(rec)
            records.append(rec)

    db.flush()
    return records
