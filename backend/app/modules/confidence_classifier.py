"""Multi-signal confidence classification for dark fleet vessels.

Maps a vessel's aggregated risk signals across multiple evidence
categories into a confidence level (CONFIRMED / HIGH / MEDIUM / LOW / NONE).

The classifier runs after scoring and examines the risk breakdown of each
vessel's gap events, spoofing anomalies, and watchlist matches to produce
a holistic classification stored on the Vessel model.

Evidence categories:
  AIS_GAP          — gap duration, frequency, dark zone, movement envelope
  SPOOFING         — identity fraud, track manipulation, fake positions
  STS_TRANSFER     — STS proximity, patterns, dark-dark STS
  IDENTITY_CHANGE  — flag/name changes, callsign, class switching, merges
  LOITERING        — loiter-gap patterns, laid-up vessels
  FLEET_PATTERN    — fleet-level analysis, owner clusters
  WATCHLIST        — sanctions list matches, OFAC/EU/KSE
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)

# ── Breakdown key → category mapping ─────────────────────────────────────────

_CATEGORY_MAP: dict[str, str] = {}

_AIS_GAP_PREFIXES = (
    "gap_duration", "gap_frequency", "impossible_reappear",
    "near_impossible_reappear", "dark_zone", "selective_dark_zone",
    "movement_envelope", "speed_impossible", "speed_spike",
    "speed_spoof", "feed_outage",
)
_SPOOFING_PREFIXES = (
    "spoofing_", "track_naturalness", "stale_ais",
    "stateless_mmsi", "imo_fraud", "cross_receiver",
    "identity_swap", "fake_",
)
_STS_PREFIXES = (
    "sts_event", "sts_", "gap_in_sts_tagged_corridor",
    "repeat_sts", "dark_dark_sts", "draught_",
)
_IDENTITY_PREFIXES = (
    "flag_change", "flag_AND_name", "callsign_change",
    "class_switching", "flag_hopping", "rename_velocity",
    "invalid_metadata", "ais_class_mismatch",
)
_LOITERING_PREFIXES = (
    "loiter_", "vessel_laid_up",
)
_FLEET_PREFIXES = (
    "fleet_", "owner_cluster", "shared_manager", "shared_pi",
    "convoy_", "ownership_",
)
_WATCHLIST_PREFIXES = (
    "watchlist_", "owner_or_manager_on_sanctions",
)


def _categorize_key(key: str) -> str:
    """Map a risk breakdown key to its evidence category."""
    for prefix in _WATCHLIST_PREFIXES:
        if key.startswith(prefix):
            return "WATCHLIST"
    for prefix in _SPOOFING_PREFIXES:
        if key.startswith(prefix):
            return "SPOOFING"
    for prefix in _STS_PREFIXES:
        if key.startswith(prefix):
            return "STS_TRANSFER"
    for prefix in _IDENTITY_PREFIXES:
        if key.startswith(prefix):
            return "IDENTITY_CHANGE"
    for prefix in _LOITERING_PREFIXES:
        if key.startswith(prefix):
            return "LOITERING"
    for prefix in _FLEET_PREFIXES:
        if key.startswith(prefix):
            return "FLEET_PATTERN"
    for prefix in _AIS_GAP_PREFIXES:
        if key.startswith(prefix):
            return "AIS_GAP"
    # Default: signals like vessel_age, flag_state, pi_coverage, psc go to AIS_GAP
    # (they are contextual modifiers of the gap score, not standalone categories)
    return "AIS_GAP"


def classify_vessel_confidence(
    vessel: Vessel,
    total_score: int,
    breakdown: dict[str, int | float],
    has_watchlist_match: bool = False,
    analyst_verified: bool = False,
) -> tuple[str, dict]:
    """Classify a single vessel's dark fleet confidence.

    Args:
        vessel: The vessel to classify.
        total_score: Aggregated risk score (max across gap events).
        breakdown: Combined risk breakdown from the highest-scoring gap.
        has_watchlist_match: True if vessel has any VesselWatchlist entries.
        analyst_verified: True if an analyst has manually confirmed.

    Returns:
        (confidence_level, evidence_dict) where evidence_dict maps
        category → total points.
    """
    # Aggregate points per category
    category_scores: dict[str, float] = defaultdict(float)
    for key, value in breakdown.items():
        if not isinstance(value, (int, float)):
            continue
        if value <= 0:
            continue  # Skip deductions — they don't contribute to evidence
        cat = _categorize_key(key)
        category_scores[cat] += value

    evidence = dict(category_scores)
    categories_with_signal = {cat for cat, pts in category_scores.items() if pts > 0}

    # CONFIRMED: watchlist match or analyst-verified
    if analyst_verified or has_watchlist_match:
        return "CONFIRMED", evidence

    # HIGH: score ≥ 76 AND (≥2 categories OR single category ≥80 pts)
    if total_score >= 76:
        any_category_80_plus = any(pts >= 80 for pts in category_scores.values())
        if len(categories_with_signal) >= 2 or any_category_80_plus:
            return "HIGH", evidence

    # MEDIUM: score ≥ 51 AND ≥1 category with ≥30 pts
    if total_score >= 51:
        any_category_30_plus = any(pts >= 30 for pts in category_scores.values())
        if any_category_30_plus:
            return "MEDIUM", evidence

    # LOW: score 21-50
    if total_score >= 21:
        return "LOW", evidence

    # NONE: score < 21
    return "NONE", evidence


def classify_all_vessels(db: Session) -> dict:
    """Run confidence classification on all vessels with scored gap events.

    Updates ``Vessel.dark_fleet_confidence`` and ``Vessel.confidence_evidence_json``.

    Returns:
        ``{"classified": N, "by_level": {"CONFIRMED": X, "HIGH": Y, ...}}``
    """
    from app.models.vessel_watchlist import VesselWatchlist

    # Get the highest-scoring gap event per vessel
    vessels_with_gaps = (
        db.query(Vessel)
        .join(AISGapEvent, AISGapEvent.vessel_id == Vessel.vessel_id)
        .filter(AISGapEvent.risk_score > 0)
        .all()
    )

    # Pre-load watchlist presence
    watchlist_vessel_ids = {
        row[0] for row in db.query(VesselWatchlist.vessel_id).distinct().all()
    }

    classified = 0
    by_level: dict[str, int] = defaultdict(int)

    for vessel in vessels_with_gaps:
        # Find the highest-scoring gap for this vessel
        best_gap = (
            db.query(AISGapEvent)
            .filter(AISGapEvent.vessel_id == vessel.vessel_id)
            .order_by(AISGapEvent.risk_score.desc())
            .first()
        )
        if best_gap is None or best_gap.risk_score == 0:
            continue

        breakdown = best_gap.risk_breakdown_json or {}
        has_watchlist = vessel.vessel_id in watchlist_vessel_ids

        confidence, evidence = classify_vessel_confidence(
            vessel,
            total_score=best_gap.risk_score,
            breakdown=breakdown,
            has_watchlist_match=has_watchlist,
        )

        vessel.dark_fleet_confidence = confidence
        vessel.confidence_evidence_json = json.dumps(evidence)
        classified += 1
        by_level[confidence] += 1

    if classified > 0:
        db.commit()

    logger.info(
        "Confidence classification: %d vessels — %s",
        classified,
        dict(by_level),
    )
    return {"classified": classified, "by_level": dict(by_level)}
