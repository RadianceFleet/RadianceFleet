"""Flag Risk Analyzer — data-driven per-flag continuous risk scoring (v2).

Replaces the flat 3-tier flag classification with continuous composite scores
derived from PSC detention rates, false-positive rates, fleet composition,
flag-hopping frequency, and registry transparency.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# ── Static Transparency Index ────────────────────────────────────────────────
# Scores 0-100 where higher = less transparent = riskier.
# Sources: Paris MOU white/grey/black lists, OECD transparency data.
TRANSPARENCY_INDEX: dict[str, float] = {
    # High transparency (score 20)
    "GB": 20, "NO": 20, "DK": 20, "SE": 20, "DE": 20, "NL": 20, "JP": 20, "SG": 20,
    # Medium transparency (score 50)
    "GR": 50, "CY": 50, "MT": 50, "HK": 50, "BS": 50,
    # Low transparency (score 80)
    "PA": 80, "LR": 80, "MH": 80, "KM": 80, "TZ": 80, "VU": 80,
    "CM": 80, "GQ": 80, "PW": 80, "TG": 80,
}

# Default transparency score for flags not in the lookup
_DEFAULT_TRANSPARENCY_SCORE = 50.0


def _compute_psc_detention_score(db: Session, flag_code: str) -> tuple[float, int, int]:
    """Compute PSC detention score for a flag.

    Returns (score, vessel_count, detention_count).
    Score is 0-100 based on detention-to-vessel ratio vs global average.
    """
    from app.models.psc_detention import PscDetention
    from app.models.vessel import Vessel

    vessel_count = (
        db.query(sa_func.count(Vessel.vessel_id))
        .filter(Vessel.flag == flag_code)
        .scalar()
    ) or 0

    if vessel_count == 0:
        return 0.0, 0, 0

    detention_count = (
        db.query(sa_func.count(PscDetention.psc_detention_id))
        .join(Vessel, Vessel.vessel_id == PscDetention.vessel_id)
        .filter(Vessel.flag == flag_code)
        .scalar()
    ) or 0

    flag_rate = detention_count / vessel_count

    # Global average detention rate
    total_vessels = db.query(sa_func.count(Vessel.vessel_id)).scalar() or 1
    total_detentions = db.query(sa_func.count(PscDetention.psc_detention_id)).scalar() or 0
    global_rate = total_detentions / total_vessels if total_vessels > 0 else 0.0

    # Score: ratio of flag rate to global rate, scaled to 0-100
    if global_rate <= 0:
        score = min(flag_rate * 100, 100.0)
    else:
        ratio = flag_rate / global_rate
        # ratio=0 -> score 0, ratio=1 -> score 50, ratio>=2 -> score 100
        score = min(ratio * 50, 100.0)

    return round(score, 2), vessel_count, detention_count


def _compute_fp_rate_score(db: Session, flag_code: str) -> tuple[float, float]:
    """Compute false-positive rate score for a flag (inverted: low FP = higher risk).

    Returns (score, fp_rate).
    """
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    total_gaps = (
        db.query(sa_func.count(AISGapEvent.gap_event_id))
        .join(Vessel, Vessel.vessel_id == AISGapEvent.vessel_id)
        .filter(Vessel.flag == flag_code)
        .scalar()
    ) or 0

    if total_gaps == 0:
        return 50.0, 0.0  # neutral score when no data

    fp_gaps = (
        db.query(sa_func.count(AISGapEvent.gap_event_id))
        .join(Vessel, Vessel.vessel_id == AISGapEvent.vessel_id)
        .filter(Vessel.flag == flag_code, AISGapEvent.is_false_positive.is_(True))
        .scalar()
    ) or 0

    fp_rate = fp_gaps / total_gaps

    # Inverted: low FP rate = higher risk (more gaps are real)
    # FP rate 0% -> score 100 (all gaps genuine = risky)
    # FP rate 100% -> score 0 (all gaps are FP = not risky)
    score = round((1.0 - fp_rate) * 100, 2)

    return score, round(fp_rate, 4)


def _compute_fleet_composition_score(db: Session, flag_code: str) -> float:
    """Compute fleet composition score based on proportion of high-scoring vessels.

    Uses max gap event risk_score per vessel as the vessel's score proxy.
    """
    from app.models.gap_event import AISGapEvent
    from app.models.vessel import Vessel

    # Get max risk_score per vessel for this flag
    rows = (
        db.query(
            Vessel.vessel_id,
            sa_func.max(AISGapEvent.risk_score).label("max_score"),
        )
        .join(AISGapEvent, AISGapEvent.vessel_id == Vessel.vessel_id)
        .filter(Vessel.flag == flag_code)
        .group_by(Vessel.vessel_id)
        .all()
    )

    if not rows:
        return 0.0

    total = len(rows)
    # "high-scoring" = max risk_score >= 50
    high_scoring = sum(1 for _, max_score in rows if max_score is not None and max_score >= 50)
    proportion = high_scoring / total

    # Scale to 0-100
    return round(min(proportion * 200, 100.0), 2)  # 50% high-scoring -> 100


def _compute_flag_hopping_score(db: Session, flag_code: str) -> float:
    """Compute flag hopping score based on incoming flag changes in last 12 months."""
    from app.models.vessel import Vessel
    from app.models.vessel_history import VesselHistory

    cutoff = datetime.utcnow() - timedelta(days=365)

    # Count flag changes where vessels changed TO this flag in last 12 months
    incoming_changes = (
        db.query(sa_func.count(VesselHistory.vessel_history_id))
        .join(Vessel, Vessel.vessel_id == VesselHistory.vessel_id)
        .filter(
            VesselHistory.field_changed == "flag",
            VesselHistory.new_value == flag_code,
            VesselHistory.observed_at >= cutoff,
        )
        .scalar()
    ) or 0

    # Count current vessels under this flag
    vessel_count = (
        db.query(sa_func.count(Vessel.vessel_id))
        .filter(Vessel.flag == flag_code)
        .scalar()
    ) or 0

    if vessel_count == 0:
        return 0.0

    # Ratio of incoming changes to fleet size
    ratio = incoming_changes / vessel_count
    # 0 changes -> 0, ratio=0.5 -> 50, ratio>=1 -> 100
    return round(min(ratio * 100, 100.0), 2)


def _get_transparency_score(flag_code: str) -> float:
    """Look up static transparency score for a flag code."""
    return TRANSPARENCY_INDEX.get(flag_code.upper(), _DEFAULT_TRANSPARENCY_SCORE)


def _assign_tier(composite_score: float) -> str:
    """Assign risk tier from composite score."""
    if composite_score >= 70:
        return "HIGH"
    elif composite_score >= 40:
        return "MEDIUM"
    return "LOW"


def compute_flag_risk_profiles(db: Session) -> list:
    """Compute per-flag risk profiles from DB data.

    Returns list of FlagRiskProfile objects (not yet committed to DB).
    """
    from app.models.flag_risk_profile import FlagRiskProfile
    from app.models.vessel import Vessel

    # Get all distinct flags
    flags = (
        db.query(Vessel.flag)
        .filter(Vessel.flag.isnot(None), Vessel.flag != "")
        .distinct()
        .all()
    )

    w_psc = settings.FLAG_RISK_PSC_WEIGHT
    w_fp = settings.FLAG_RISK_FP_WEIGHT
    w_fleet = settings.FLAG_RISK_FLEET_WEIGHT
    w_hopping = settings.FLAG_RISK_HOPPING_WEIGHT
    w_transparency = settings.FLAG_RISK_TRANSPARENCY_WEIGHT

    profiles = []
    for (flag_code,) in flags:
        if not flag_code:
            continue
        flag_upper = flag_code.upper().strip()
        if not flag_upper:
            continue

        psc_score, vessel_count, detention_count = _compute_psc_detention_score(db, flag_code)
        fp_score, fp_rate = _compute_fp_rate_score(db, flag_code)
        fleet_score = _compute_fleet_composition_score(db, flag_code)
        hopping_score = _compute_flag_hopping_score(db, flag_code)
        transparency_score = _get_transparency_score(flag_upper)

        composite = (
            w_psc * psc_score
            + w_fp * fp_score
            + w_fleet * fleet_score
            + w_hopping * hopping_score
            + w_transparency * transparency_score
        )
        composite = round(min(max(composite, 0.0), 100.0), 2)
        tier = _assign_tier(composite)

        evidence = {
            "weights": {
                "psc": w_psc, "fp": w_fp, "fleet": w_fleet,
                "hopping": w_hopping, "transparency": w_transparency,
            },
            "component_scores": {
                "psc_detention": psc_score,
                "fp_rate": fp_score,
                "fleet_composition": fleet_score,
                "flag_hopping": hopping_score,
                "transparency": transparency_score,
            },
            "raw": {
                "vessel_count": vessel_count,
                "detention_count": detention_count,
                "fp_rate": fp_rate,
            },
            "computed_at": datetime.utcnow().isoformat(),
        }

        profile = FlagRiskProfile(
            flag_code=flag_upper,
            psc_detention_score=psc_score,
            fp_rate_score=fp_score,
            fleet_composition_score=fleet_score,
            flag_hopping_score=hopping_score,
            transparency_score=transparency_score,
            composite_score=composite,
            risk_tier=tier,
            vessel_count=vessel_count,
            detention_count=detention_count,
            fp_rate=fp_rate,
            evidence_json=json.dumps(evidence),
        )
        profiles.append(profile)

    return profiles


def get_flag_risk_score(db: Session, flag_code: str) -> Optional:
    """Look up precomputed profile for a flag code.

    Returns FlagRiskProfile or None.
    """
    from app.models.flag_risk_profile import FlagRiskProfile

    if not flag_code:
        return None

    return (
        db.query(FlagRiskProfile)
        .filter(FlagRiskProfile.flag_code == flag_code.upper().strip())
        .first()
    )


def persist_profiles(db: Session, profiles: list) -> int:
    """Persist computed profiles to DB, replacing existing ones.

    Returns number of profiles persisted.
    """
    from app.models.flag_risk_profile import FlagRiskProfile

    # Delete existing profiles
    db.query(FlagRiskProfile).delete()

    for profile in profiles:
        db.add(profile)

    db.commit()
    return len(profiles)
