"""Beneficial ownership transparency analysis.

Detects Special Purpose Vehicles (SPVs) and jurisdiction hopping patterns
commonly used by shadow fleet operators to obscure vessel ownership.

SPV detection heuristics:
  - Single-vessel company (1 asset)
  - Secrecy jurisdiction (MH, LR, PA, MT, CY, etc.)
  - Recent incorporation (within 2 years of vessel acquisition)
  - Nominee directors / shared registered agents
  - SPV = secrecy jurisdiction + at least one other indicator

Jurisdiction hopping:
  - Track incorporation_jurisdiction changes across VesselOwner records
  - Flag if 2+ distinct jurisdiction changes detected (+20 pts)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.normalize import normalize_owner_name
from app.modules.opencorporates_client import SECRECY_JURISDICTIONS

logger = logging.getLogger(__name__)

# Fuzzy match threshold for company name matching (reuse owner_dedup pattern)
FUZZY_MATCH_THRESHOLD = 85

# Common nominee director keywords
_NOMINEE_KEYWORDS = frozenset({
    "nominee", "corporate", "secretarial", "registered agent",
    "trust company", "management services", "corporate services",
    "directors ltd", "nominees ltd", "secretary",
})


def _is_nominee_director(name: str) -> bool:
    """Check if a director name suggests a nominee/corporate director."""
    name_lower = name.lower()
    return any(kw in name_lower for kw in _NOMINEE_KEYWORDS)


def _check_shared_registered_agent(officers: list[dict[str, Any]]) -> bool:
    """Check if company uses a corporate registered agent as director."""
    for officer in officers:
        name = officer.get("name", "")
        if _is_nominee_director(name):
            return True
    return False


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a date string (YYYY-MM-DD) into datetime."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def analyze_spv(
    owner_name: str,
    jurisdiction_code: str | None = None,
    incorporation_date: datetime | None = None,
    vessel_count: int = 1,
    officers: list[dict[str, Any]] | None = None,
    vessel_acquisition_date: datetime | None = None,
) -> dict[str, Any]:
    """Analyze whether a company is likely an SPV.

    SPV = secrecy jurisdiction + at least one other indicator.

    Args:
        owner_name: Company name.
        jurisdiction_code: 2-letter jurisdiction code.
        incorporation_date: When the company was incorporated.
        vessel_count: Number of vessels owned by this company.
        officers: List of officer dicts from OpenCorporates.
        vessel_acquisition_date: When the vessel was acquired.

    Returns:
        Dict with ``is_spv`` (bool), ``indicators`` (list), ``score_components`` (dict).
    """
    indicators: list[str] = []
    score_components: dict[str, int] = {}

    jur = (jurisdiction_code or "").upper()

    # 1. Secrecy jurisdiction
    is_secrecy = jur in SECRECY_JURISDICTIONS
    if is_secrecy:
        indicators.append("secrecy_jurisdiction")
        score_components["secrecy_jurisdiction"] = 10

    # 2. Single-vessel company
    if vessel_count <= 1:
        indicators.append("single_vessel_company")

    # 3. Recent incorporation (within 2 years of vessel acquisition)
    if incorporation_date is not None:
        ref_date = vessel_acquisition_date or datetime.now(UTC).replace(tzinfo=None)
        if isinstance(ref_date, datetime) and ref_date.tzinfo is not None:
            ref_date = ref_date.replace(tzinfo=None)
        if isinstance(incorporation_date, datetime) and incorporation_date.tzinfo is not None:
            incorporation_date = incorporation_date.replace(tzinfo=None)
        delta = abs((ref_date - incorporation_date).days)
        if delta <= 730:  # 2 years
            indicators.append("recent_incorporation")
            score_components["recent_incorporation"] = 10

    # 4. Nominee directors / shared registered agents
    if officers:
        has_nominee = _check_shared_registered_agent(officers)
        if has_nominee:
            indicators.append("nominee_director")
            score_components["nominee_director"] = 15

    # SPV = secrecy jurisdiction + at least one other indicator
    other_indicators = [i for i in indicators if i != "secrecy_jurisdiction"]
    is_spv = is_secrecy and len(other_indicators) >= 1

    if is_spv:
        score_components["spv"] = 15
        # Compound: SPV + shell indicators
        if len(indicators) >= 3:
            score_components["spv_shell_compound"] = 25

    return {
        "is_spv": is_spv,
        "indicators": indicators,
        "score_components": score_components,
    }


def detect_jurisdiction_hopping(
    db: Session,
    vessel_id: int,
) -> dict[str, Any]:
    """Detect jurisdiction hopping across VesselOwner records.

    Flags if 2+ distinct incorporation_jurisdiction changes detected.

    Returns:
        Dict with ``detected`` (bool), ``jurisdictions`` (list), ``hop_count`` (int).
    """
    from app.models.vessel_owner import VesselOwner

    owners = (
        db.query(VesselOwner)
        .filter(VesselOwner.vessel_id == vessel_id)
        .order_by(VesselOwner.owner_id)
        .all()
    )

    jurisdictions: list[str] = []
    for owner in owners:
        jur = getattr(owner, "incorporation_jurisdiction", None)
        if jur:
            jur = jur.upper()
            if not jurisdictions or jurisdictions[-1] != jur:
                jurisdictions.append(jur)

    distinct_changes = len(jurisdictions) - 1 if len(jurisdictions) > 1 else 0
    detected = distinct_changes >= 2

    return {
        "detected": detected,
        "jurisdictions": jurisdictions,
        "hop_count": distinct_changes,
    }


def enrich_vessel_ownership(
    db: Session,
    vessel_id: int,
) -> dict[str, Any]:
    """Enrich vessel ownership data via OpenCorporates.

    Skips owners that already have ``opencorporates_url`` set (cache).
    Tracks API usage via VerificationLog.

    Returns:
        Summary dict with ``enriched`` count and ``spv_detected`` list.
    """
    if not settings.OPENCORPORATES_ENABLED:
        return {"enriched": 0, "skipped": 0, "spv_detected": [], "disabled": True}

    from app.models.verification_log import VerificationLog
    from app.models.vessel_owner import VesselOwner
    from app.modules.opencorporates_client import search_companies

    owners = (
        db.query(VesselOwner)
        .filter(VesselOwner.vessel_id == vessel_id)
        .all()
    )

    enriched = 0
    skipped = 0
    spv_detected: list[dict[str, Any]] = []

    # Check monthly quota (soft limit)
    _check_quota_warning(db)

    for owner in owners:
        # Skip already enriched
        if owner.opencorporates_url:
            skipped += 1
            continue

        name = owner.owner_name
        if not name:
            continue

        # Search OpenCorporates for this owner
        results = search_companies(name)

        # Log API usage
        log_entry = VerificationLog(
            vessel_id=vessel_id,
            provider="opencorporates",
            response_status="ok" if results else "no_results",
            cost_usd=0.0,
            result_summary=f"search: {name} -> {len(results)} results",
        )
        db.add(log_entry)

        if not results:
            continue

        # Fuzzy match to find best company match
        normalized_name = normalize_owner_name(name)
        best_match: dict[str, Any] | None = None
        best_score = 0.0

        for company in results:
            company_name = company.get("name", "")
            normalized_company = normalize_owner_name(company_name)
            score = fuzz.token_sort_ratio(normalized_name, normalized_company)
            if score >= FUZZY_MATCH_THRESHOLD and score > best_score:
                best_score = score
                best_match = company

        if not best_match:
            continue

        # Update owner record
        owner.opencorporates_url = best_match.get("opencorporates_url", "")
        owner.company_number = best_match.get("company_number", "")
        jur = best_match.get("jurisdiction_code", "")
        owner.incorporation_jurisdiction = jur.upper() if jur else None

        inc_date = _parse_date(best_match.get("incorporation_date"))
        if inc_date:
            owner.incorporation_date = inc_date

        # Fetch officers for SPV analysis
        officers: list[dict[str, Any]] = []
        if owner.company_number and owner.incorporation_jurisdiction:
            from app.modules.opencorporates_client import fetch_officers

            officers = fetch_officers(
                owner.incorporation_jurisdiction.lower(),
                owner.company_number,
            )
            # Log officer fetch
            officer_log = VerificationLog(
                vessel_id=vessel_id,
                provider="opencorporates",
                response_status="ok" if officers else "no_results",
                cost_usd=0.0,
                result_summary=f"officers: {owner.company_number} -> {len(officers)} officers",
            )
            db.add(officer_log)

        # SPV analysis
        spv_result = analyze_spv(
            owner_name=name,
            jurisdiction_code=owner.incorporation_jurisdiction,
            incorporation_date=owner.incorporation_date,
            officers=officers,
        )
        owner.is_spv = spv_result["is_spv"]
        owner.spv_indicators_json = spv_result["indicators"] if spv_result["indicators"] else None

        if spv_result["is_spv"]:
            spv_detected.append({
                "owner_id": owner.owner_id,
                "owner_name": name,
                "indicators": spv_result["indicators"],
                "score_components": spv_result["score_components"],
            })

        enriched += 1

    db.commit()

    return {
        "enriched": enriched,
        "skipped": skipped,
        "spv_detected": spv_detected,
        "disabled": False,
    }


def score_ownership_transparency(
    db: Session,
    vessel_id: int,
    config: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Compute ownership transparency scoring signals for a vessel.

    Returns:
        Breakdown dict with scoring signal keys and point values.
    """
    from app.models.vessel_owner import VesselOwner
    from app.modules.scoring_config import load_scoring_config

    cfg = (config or load_scoring_config()).get("ownership_transparency", {})
    breakdown: dict[str, int] = {}

    owners = (
        db.query(VesselOwner)
        .filter(VesselOwner.vessel_id == vessel_id)
        .all()
    )

    if not owners:
        return breakdown

    # SPV signals from enriched owners
    for owner in owners:
        if owner.is_spv:
            breakdown["ownership_spv"] = cfg.get("spv", 15)

            # Check individual indicators from stored JSON
            indicators = owner.spv_indicators_json or []

            if "secrecy_jurisdiction" in indicators:
                breakdown["ownership_secrecy_jurisdiction"] = cfg.get("secrecy_jurisdiction", 10)
            if "recent_incorporation" in indicators:
                breakdown["ownership_recent_incorporation"] = cfg.get("recent_incorporation", 10)
            if "nominee_director" in indicators:
                breakdown["ownership_nominee_director"] = cfg.get("nominee_director", 15)
            if len(indicators) >= 3:
                breakdown["ownership_spv_shell_compound"] = cfg.get("spv_shell_compound", 25)
            break  # Use first SPV owner found

        # Non-SPV secrecy jurisdiction still scores
        jur = getattr(owner, "incorporation_jurisdiction", None)
        if jur and jur.upper() in SECRECY_JURISDICTIONS:
            breakdown.setdefault("ownership_secrecy_jurisdiction", cfg.get("secrecy_jurisdiction", 10))

    # Jurisdiction hopping
    jur_hop = detect_jurisdiction_hopping(db, vessel_id)
    if jur_hop["detected"]:
        breakdown["ownership_jurisdiction_hopping"] = cfg.get("jurisdiction_hopping", 20)

    return breakdown


def _check_quota_warning(db: Session) -> None:
    """Log a warning if OpenCorporates monthly quota is at 80%+."""
    from app.models.verification_log import VerificationLog

    now = datetime.now(UTC).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    count = (
        db.query(VerificationLog)
        .filter(
            VerificationLog.provider == "opencorporates",
            VerificationLog.request_time_utc >= month_start,
        )
        .count()
    )

    quota = settings.OPENCORPORATES_MONTHLY_QUOTA
    if count >= int(quota * 0.8):
        logger.warning(
            "OpenCorporates quota at %d/%d (%.0f%%) — approaching monthly limit",
            count,
            quota,
            (count / quota) * 100,
        )
