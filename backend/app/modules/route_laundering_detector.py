"""Route laundering detector -- identifies vessels using intermediary ports
to obscure Russian-origin cargo before delivery to sanctioned destinations.

A common sanctions-evasion technique is to route cargo through intermediary
ports (e.g., Fujairah, Sohar, Ceuta) to disguise Russian-origin petroleum.
This detector scans PortCall sequences per vessel for suspicious multi-hop
patterns defined in laundering_patterns.yaml.

When the YAML config is missing, falls back to the original 2 hardcoded
patterns (russian_intermediary_sanctioned and russian_intermediary).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import SpoofingTypeEnum
from app.models.port import Port
from app.models.port_call import PortCall
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)

# ── Sanctioned destination countries (simplified list) ────────────────────
# Countries under comprehensive oil import sanctions or where deliveries
# would require sanctions circumvention.
_SANCTIONED_DESTINATIONS: frozenset[str] = frozenset(
    {
        "KP",  # North Korea
        "SY",  # Syria
        "IR",  # Iran
        "CU",  # Cuba
        "VE",  # Venezuela
    }
)

_RUSSIAN_ORIGIN_COUNTRIES: frozenset[str] = frozenset(
    {
        "RU",  # Russia
    }
)

_INTERMEDIARY_CONFIG: dict[str, Any] | None = None

# Cached pattern templates and port categories
_PATTERN_TEMPLATES: dict[str, Any] | None = None
_PORT_CATEGORIES: dict[str, list[str]] | None = None
_TEMPORAL_BONUS_CONFIG: dict[str, Any] | None = None

# ── Hardcoded fallback patterns (backward compatibility) ─────────────────
_FALLBACK_PATTERNS: dict[str, dict] = {
    "russian_intermediary_sanctioned": {
        "hops": ["russian", "intermediary", "sanctioned"],
        "base_score": 35,
        "description": "Russia -> intermediary -> sanctioned destination",
    },
    "russian_intermediary": {
        "hops": ["russian", "intermediary"],
        "base_score": 20,
        "description": "Russia -> intermediary port",
    },
}

_FALLBACK_PORT_CATEGORIES: dict[str, list[str]] = {
    "russian": ["RU"],
    "sanctioned": ["SY", "KP", "IR", "CU", "VE"],
    "intermediary": [],  # filled from laundering_intermediaries.yaml
}


def _load_intermediary_config() -> list[dict]:
    """Load intermediary port list from YAML config."""
    global _INTERMEDIARY_CONFIG
    if _INTERMEDIARY_CONFIG is not None:
        return _INTERMEDIARY_CONFIG.get("intermediary_ports", [])

    # Derive config directory from the risk_scoring.yaml path (same base)
    scoring_config = Path(settings.RISK_SCORING_CONFIG)
    config_path = scoring_config.parent / "laundering_intermediaries.yaml"
    if not config_path.exists():
        logger.warning("laundering_intermediaries.yaml not found at %s", config_path)
        _INTERMEDIARY_CONFIG = {}
        return []

    with open(config_path) as f:
        _INTERMEDIARY_CONFIG = yaml.safe_load(f) or {}
    return _INTERMEDIARY_CONFIG.get("intermediary_ports", [])


def _get_intermediary_countries() -> frozenset[str]:
    """Return set of intermediary country codes from config."""
    ports = _load_intermediary_config()
    return frozenset(p.get("country", "").upper() for p in ports if p.get("country"))


def _get_intermediary_names() -> frozenset[str]:
    """Return set of intermediary port names (lowered) from config."""
    ports = _load_intermediary_config()
    return frozenset(p.get("name", "").lower() for p in ports if p.get("name"))


# ── Template engine ──────────────────────────────────────────────────────


def _load_pattern_templates(config_path: str | None = None) -> dict[str, dict]:
    """Load pattern templates from YAML config.

    Falls back to hardcoded 2-pattern set if file is missing.
    """
    global _PATTERN_TEMPLATES
    if _PATTERN_TEMPLATES is not None:
        return _PATTERN_TEMPLATES

    path = Path(config_path or settings.ROUTE_LAUNDERING_PATTERNS_CONFIG)
    if not path.exists():
        logger.warning(
            "laundering_patterns.yaml not found at %s -- using hardcoded fallback patterns",
            path,
        )
        _PATTERN_TEMPLATES = _FALLBACK_PATTERNS
        return _PATTERN_TEMPLATES

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    patterns = data.get("patterns", {})
    if not patterns:
        logger.warning("No patterns in laundering_patterns.yaml -- using hardcoded fallback")
        _PATTERN_TEMPLATES = _FALLBACK_PATTERNS
        return _PATTERN_TEMPLATES

    _PATTERN_TEMPLATES = patterns

    # Cache temporal bonus config
    global _TEMPORAL_BONUS_CONFIG
    _TEMPORAL_BONUS_CONFIG = data.get("temporal_bonus", {"enabled": False})

    return _PATTERN_TEMPLATES


def _load_port_categories(config_path: str | None = None) -> dict[str, list[str]]:
    """Load port categories from YAML config, merging intermediary countries.

    Merges countries from laundering_intermediaries.yaml into the 'intermediary'
    category to maintain backward compatibility.
    """
    global _PORT_CATEGORIES
    if _PORT_CATEGORIES is not None:
        return _PORT_CATEGORIES

    path = Path(config_path or settings.ROUTE_LAUNDERING_PATTERNS_CONFIG)
    if not path.exists():
        # Fallback: build categories from hardcoded constants + intermediary config
        categories = dict(_FALLBACK_PORT_CATEGORIES)
        intermediary_countries = _get_intermediary_countries()
        categories["intermediary"] = list(intermediary_countries)
        _PORT_CATEGORIES = categories
        return _PORT_CATEGORIES

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    categories = data.get("port_categories", {})
    if not categories:
        categories = dict(_FALLBACK_PORT_CATEGORIES)

    # Merge laundering_intermediaries.yaml countries into intermediary category
    intermediary_countries = _get_intermediary_countries()
    existing_intermediary = set(categories.get("intermediary", []))
    existing_intermediary.update(intermediary_countries)
    categories["intermediary"] = list(existing_intermediary)

    _PORT_CATEGORIES = categories

    # Also load temporal bonus config if available
    global _TEMPORAL_BONUS_CONFIG
    if _TEMPORAL_BONUS_CONFIG is None:
        _TEMPORAL_BONUS_CONFIG = data.get("temporal_bonus", {"enabled": False})

    return _PORT_CATEGORIES


def _classify_port_by_categories(
    port_country: str, categories: dict[str, list[str]]
) -> list[str]:
    """Return list of matching category names for a port country code.

    A port can match multiple categories (e.g., IR matches both 'iranian'
    and 'sanctioned').
    """
    if not port_country:
        return []
    upper = port_country.upper()
    return [cat_name for cat_name, codes in categories.items() if upper in codes]


def _match_pattern_template(
    port_calls_classified: list[tuple[PortCall, list[str]]],
    pattern_name: str,
    pattern: dict,
    categories: dict[str, list[str]],
) -> dict | None:
    """Greedy forward scan matching port sequence against template hop list.

    Returns match info dict with matched_ports, pattern_name, base_score,
    or None if no match.
    """
    hops = pattern.get("hops", [])
    if not hops or len(port_calls_classified) < len(hops):
        return None

    # Greedy forward scan: try to match each hop in sequence
    hop_idx = 0
    matched_ports: list[tuple[PortCall, str]] = []  # (port_call, matched_category)

    for pc, cats in port_calls_classified:
        if hop_idx >= len(hops):
            break
        required_cat = hops[hop_idx]
        if required_cat in cats:
            matched_ports.append((pc, required_cat))
            hop_idx += 1

    if hop_idx < len(hops):
        return None  # Not all hops matched

    return {
        "pattern_name": pattern_name,
        "base_score": pattern.get("base_score", 15),
        "description": pattern.get("description", ""),
        "matched_ports": matched_ports,
        "hop_count": len(hops),
    }


def _compute_temporal_bonus(
    matched_ports: list[tuple[PortCall, str]],
    threshold_hours: int = 48,
) -> int:
    """Return bonus points if all inter-hop transit times are < threshold_hours.

    Returns 0 if fewer than 2 matched ports or any transit exceeds threshold.
    """
    if len(matched_ports) < 2:
        return 0

    for i in range(len(matched_ports) - 1):
        pc_a = matched_ports[i][0]
        pc_b = matched_ports[i + 1][0]
        if pc_a.arrival_utc is None or pc_b.arrival_utc is None:
            return 0
        delta = pc_b.arrival_utc - pc_a.arrival_utc
        if delta.total_seconds() >= threshold_hours * 3600:
            return 0

    return 10  # Default bonus; overridden by YAML config in run_route_laundering_detection


def run_route_laundering_detection(db: Session) -> dict:
    """Detect vessels with route laundering patterns.

    Scans PortCall sequences per vessel for multi-hop patterns defined in
    laundering_patterns.yaml. Falls back to hardcoded 2-pattern set if
    config is missing.

    Returns:
        {"status": "ok", "anomalies_created": N, "vessels_checked": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.ROUTE_LAUNDERING_DETECTION_ENABLED:
        return {"status": "disabled"}

    lookback_days = settings.ROUTE_LAUNDERING_LOOKBACK_DAYS
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)

    # Load template engine data
    patterns = _load_pattern_templates()
    categories = _load_port_categories()
    intermediary_names = _get_intermediary_names()

    # Verify we have at least some intermediary data
    if not categories.get("intermediary") and not intermediary_names:
        logger.warning("No intermediary ports configured -- skipping route laundering detection")
        return {"status": "ok", "anomalies_created": 0, "vessels_checked": 0}

    # Load temporal bonus config
    temporal_bonus_enabled = settings.ROUTE_LAUNDERING_TEMPORAL_BONUS_ENABLED
    temporal_config = _TEMPORAL_BONUS_CONFIG or {"enabled": False}
    if temporal_bonus_enabled and temporal_config.get("enabled", False):
        threshold_hours = temporal_config.get("threshold_hours", 48)
        bonus_points = temporal_config.get("bonus_points", 10)
    else:
        threshold_hours = 48
        bonus_points = 0

    # Load all ports for country/name lookups
    all_ports = {p.port_id: p for p in db.query(Port).all()}

    # Also build intermediary countries/names for is_russian_oil_terminal fallback
    frozenset(categories.get("intermediary", []))

    vessels = db.query(Vessel).filter(Vessel.merged_into_vessel_id.is_(None)).all()
    anomalies_created = 0

    for vessel in vessels:
        # Get port calls in lookback window, ordered by arrival
        port_calls = (
            db.query(PortCall)
            .filter(
                PortCall.vessel_id == vessel.vessel_id,
                PortCall.arrival_utc >= cutoff,
            )
            .order_by(PortCall.arrival_utc)
            .all()
        )

        if len(port_calls) < 2:
            continue

        # Check for existing anomaly
        existing = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING,
            )
            .first()
        )
        if existing:
            continue

        # Classify each port call using the new category system
        classified: list[tuple[PortCall, list[str]]] = []
        for pc in port_calls:
            port = all_ports.get(pc.port_id) if pc.port_id else None
            cats = _classify_port_for_templates(
                port, pc.raw_port_name, categories, intermediary_names
            )
            classified.append((pc, cats))

        # Try all pattern templates, keep highest-scoring match
        best_match: dict | None = None
        best_score = 0

        for pname, pdef in patterns.items():
            match = _match_pattern_template(classified, pname, pdef, categories)
            if match is not None:
                score = match["base_score"]
                # Apply temporal bonus
                if bonus_points > 0:
                    tb = _compute_temporal_bonus(match["matched_ports"], threshold_hours)
                    if tb > 0:
                        score += bonus_points
                        match["temporal_bonus"] = bonus_points
                if score > best_score:
                    best_score = score
                    best_match = match

        if best_match is None:
            continue

        # Build evidence in the same format as before
        port_sequence = []
        for pc, cat in best_match["matched_ports"]:
            port_sequence.append(
                {
                    "port_id": pc.port_id,
                    "raw_name": pc.raw_port_name,
                    "category": cat,
                }
            )

        first_pc = best_match["matched_ports"][0][0]
        last_pc = best_match["matched_ports"][-1][0]

        evidence_json: dict[str, Any] = {
            "hop_count": best_match["hop_count"],
            "pattern": best_match["pattern_name"],
            "port_sequence": port_sequence,
        }
        if best_match.get("temporal_bonus"):
            evidence_json["temporal_bonus"] = best_match["temporal_bonus"]

        anomaly = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.ROUTE_LAUNDERING,
            start_time_utc=first_pc.arrival_utc,
            end_time_utc=last_pc.arrival_utc,
            risk_score_component=best_score,
            evidence_json=evidence_json,
        )
        db.add(anomaly)
        anomalies_created += 1

    db.commit()
    logger.info(
        "Route laundering: %d anomalies from %d vessels checked",
        anomalies_created,
        len(vessels),
    )
    return {
        "status": "ok",
        "anomalies_created": anomalies_created,
        "vessels_checked": len(vessels),
    }


def _classify_port_for_templates(
    port: Port | None,
    raw_name: str | None,
    categories: dict[str, list[str]],
    intermediary_names: frozenset[str],
) -> list[str]:
    """Classify a port into all matching categories for template matching.

    Handles is_russian_oil_terminal flag and intermediary name lookup as
    special cases for backward compatibility.
    """
    country = ""
    port_name = ""

    if port is not None:
        country = (port.country or "").upper()
        port_name = (port.name or "").lower()
        if getattr(port, "is_russian_oil_terminal", False):
            # Force russian category, plus any other matches
            cats = _classify_port_by_categories(country, categories)
            if "russian" not in cats:
                cats.append("russian")
            return cats

    if not country and raw_name:
        port_name = raw_name.lower()

    cats = _classify_port_by_categories(country, categories)

    # Also check intermediary names from laundering_intermediaries.yaml
    if not cats and port_name and port_name in intermediary_names:
        cats.append("intermediary")

    return cats


# ── Legacy compatibility functions ──────────────────────────────────────


def _classify_port(
    port: Port | None,
    raw_name: str | None,
    intermediary_countries: frozenset[str],
    intermediary_names: frozenset[str],
) -> str:
    """Classify a port as 'russian', 'intermediary', 'sanctioned', or 'other'.

    Kept for backward compatibility with existing callers.
    """
    country = ""
    port_name = ""

    if port is not None:
        country = (port.country or "").upper()
        port_name = (port.name or "").lower()
        if getattr(port, "is_russian_oil_terminal", False):
            return "russian"

    if not country and raw_name:
        port_name = raw_name.lower()

    if country in _RUSSIAN_ORIGIN_COUNTRIES:
        return "russian"
    if country in _SANCTIONED_DESTINATIONS:
        return "sanctioned"
    if country in intermediary_countries:
        return "intermediary"
    if port_name and port_name in intermediary_names:
        return "intermediary"

    return "other"


def _find_best_pattern(
    classified: list[tuple[PortCall, str]],
) -> tuple[int, dict] | None:
    """Find the best laundering pattern in the classified port call sequence.

    Returns (hop_count, evidence_dict) or None if no pattern found.
    Kept for backward compatibility.
    """
    best: tuple[int, dict] | None = None

    for i, (pc_i, cat_i) in enumerate(classified):
        if cat_i != "russian":
            continue

        # Look for intermediary after Russian port
        for j in range(i + 1, len(classified)):
            pc_j, cat_j = classified[j]
            if cat_j != "intermediary":
                continue

            # Found 2-hop: Russian -> intermediary
            # Look for sanctioned destination after intermediary
            found_3_hop = False
            for k in range(j + 1, len(classified)):
                pc_k, cat_k = classified[k]
                if cat_k == "sanctioned":
                    # 3-hop pattern
                    evidence = {
                        "pattern": "russian_intermediary_sanctioned",
                        "first_call_utc": pc_i.arrival_utc,
                        "last_call_utc": pc_k.arrival_utc,
                        "port_sequence": [
                            {
                                "port_id": pc_i.port_id,
                                "raw_name": pc_i.raw_port_name,
                                "category": "russian",
                            },
                            {
                                "port_id": pc_j.port_id,
                                "raw_name": pc_j.raw_port_name,
                                "category": "intermediary",
                            },
                            {
                                "port_id": pc_k.port_id,
                                "raw_name": pc_k.raw_port_name,
                                "category": "sanctioned",
                            },
                        ],
                    }
                    if best is None or best[0] < 3:
                        best = (3, evidence)
                    found_3_hop = True
                    break

            if not found_3_hop:
                # 2-hop pattern: Russian -> intermediary
                evidence = {
                    "pattern": "russian_intermediary",
                    "first_call_utc": pc_i.arrival_utc,
                    "last_call_utc": pc_j.arrival_utc,
                    "port_sequence": [
                        {
                            "port_id": pc_i.port_id,
                            "raw_name": pc_i.raw_port_name,
                            "category": "russian",
                        },
                        {
                            "port_id": pc_j.port_id,
                            "raw_name": pc_j.raw_port_name,
                            "category": "intermediary",
                        },
                    ],
                }
                if best is None or (best[0] < 2):
                    best = (2, evidence)

    return best


def _reset_caches() -> None:
    """Reset all module-level caches. Used by tests."""
    global _INTERMEDIARY_CONFIG, _PATTERN_TEMPLATES, _PORT_CATEGORIES, _TEMPORAL_BONUS_CONFIG
    _INTERMEDIARY_CONFIG = None
    _PATTERN_TEMPLATES = None
    _PORT_CATEGORIES = None
    _TEMPORAL_BONUS_CONFIG = None
