"""Scoring configuration loading and operator/registry whitelists.

Extracted from risk_scoring.py to reduce module size.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

_SCORING_CONFIG: dict[str, Any] | None = None

_EXPECTED_SECTIONS = [
    "gap_duration", "gap_frequency", "speed_anomaly", "movement_envelope",
    "spoofing", "metadata", "vessel_age", "flag_state", "vessel_size_multiplier",
    "watchlist", "dark_zone", "sts", "behavioral", "legitimacy", "corridor",
    "score_bands", "ais_class", "dark_vessel", "pi_insurance", "psc_detention",
    "sts_patterns",
    "track_naturalness", "draught", "identity_fraud", "dark_sts", "fleet",
    "pi_validation", "fraudulent_registry",
    "stale_ais", "at_sea_operations",
    "ism_continuity", "rename_velocity",
    "destination", "sts_chains", "scrapped_registry", "track_replay",
    "merge_chains",
    "ownership_graph", "convoy", "voyage",
    "route_laundering", "pi_cycling", "sparse_transmission", "vessel_type_consistency",
    "watchlist_stub_scoring",
]

# Module-level watchlist key mapping (shared by compute_gap_score and score_watchlist_stubs)
_WATCHLIST_KEY_MAP = {
    "OFAC_SDN": "vessel_on_ofac_sdn_list",
    "EU_COUNCIL": "vessel_on_eu_sanctions_list",
    "KSE_SHADOW": "vessel_on_kse_shadow_fleet_list",
}
_WATCHLIST_DEFAULTS = {
    "OFAC_SDN": 50, "EU_COUNCIL": 50, "KSE_SHADOW": 30,
}


def load_scoring_config() -> dict[str, Any]:
    global _SCORING_CONFIG
    if _SCORING_CONFIG is None:
        config_path = Path(settings.RISK_SCORING_CONFIG)
        if not config_path.exists():
            logger.warning("risk_scoring.yaml not found at %s — using empty config", config_path)
            _SCORING_CONFIG = {}
        else:
            with open(config_path) as f:
                _SCORING_CONFIG = yaml.safe_load(f) or {}
        missing = [s for s in _EXPECTED_SECTIONS if s not in _SCORING_CONFIG]
        if missing:
            logger.warning("risk_scoring.yaml missing sections: %s", ", ".join(missing))
        # Validate numeric values in scoring ranges
        for section_name in _EXPECTED_SECTIONS:
            section = _SCORING_CONFIG.get(section_name, {})
            if isinstance(section, dict):
                for key, val in section.items():
                    if isinstance(val, (int, float)):
                        if section_name in ("corridor", "vessel_size_multiplier"):
                            if not (0 <= val <= 10):
                                logger.warning("risk_scoring.yaml %s.%s=%s outside [0,10]", section_name, key, val)
                        elif not (-50 <= val <= 200):
                            logger.warning("risk_scoring.yaml %s.%s=%s outside [-50,200]", section_name, key, val)
    return _SCORING_CONFIG


def reload_scoring_config() -> dict[str, Any]:
    """Force-reload scoring config from disk (e.g. after YAML edits)."""
    global _SCORING_CONFIG
    _SCORING_CONFIG = None
    return load_scoring_config()


# ── Legitimate operator whitelist (false positive suppression) ───────────────
_LEGITIMATE_OPERATORS_CONFIG: dict[str, Any] | None = None


def _load_legitimate_operators_config() -> dict[str, Any]:
    """Lazy-load and cache the legitimate operators whitelist YAML."""
    global _LEGITIMATE_OPERATORS_CONFIG
    if _LEGITIMATE_OPERATORS_CONFIG is None:
        config_path = Path(settings.RISK_SCORING_CONFIG).parent / "legitimate_operators.yaml"
        if not config_path.exists():
            logger.warning("legitimate_operators.yaml not found at %s", config_path)
            _LEGITIMATE_OPERATORS_CONFIG = {}
        else:
            with open(config_path) as f:
                _LEGITIMATE_OPERATORS_CONFIG = yaml.safe_load(f) or {}
    return _LEGITIMATE_OPERATORS_CONFIG


def _is_whitelisted_operator(mmsi: str | int | None) -> bool:
    """Return True if vessel MMSI is in the legitimate operators whitelist."""
    if mmsi is None:
        return False
    ops = _load_legitimate_operators_config()
    whitelisted = {str(m) for m in ops.get("whitelisted_mmsis", [])}
    return str(mmsi) in whitelisted


# ── P&I club validation config (Stage 2-A) ─────────────────────────────────
_PI_CLUBS_CONFIG: dict[str, Any] | None = None


def _load_pi_clubs_config() -> dict[str, Any]:
    """Lazy-load and cache the legitimate P&I clubs YAML."""
    global _PI_CLUBS_CONFIG
    if _PI_CLUBS_CONFIG is None:
        config_path = Path(settings.RISK_SCORING_CONFIG).parent / "legitimate_pi_clubs.yaml"
        if not config_path.exists():
            logger.warning("legitimate_pi_clubs.yaml not found at %s", config_path)
            _PI_CLUBS_CONFIG = {}
        else:
            with open(config_path) as f:
                _PI_CLUBS_CONFIG = yaml.safe_load(f) or {}
    return _PI_CLUBS_CONFIG


# ── Fraudulent registry config (Stage 2-B) ─────────────────────────────────
_FRAUDULENT_REGISTRIES_CONFIG: dict[str, Any] | None = None


def _load_fraudulent_registries_config() -> dict[str, Any]:
    """Lazy-load and cache the fraudulent registries YAML."""
    global _FRAUDULENT_REGISTRIES_CONFIG
    if _FRAUDULENT_REGISTRIES_CONFIG is None:
        config_path = Path(settings.RISK_SCORING_CONFIG).parent / "fraudulent_registries.yaml"
        if not config_path.exists():
            logger.warning("fraudulent_registries.yaml not found at %s", config_path)
            _FRAUDULENT_REGISTRIES_CONFIG = {}
        else:
            with open(config_path) as f:
                _FRAUDULENT_REGISTRIES_CONFIG = yaml.safe_load(f) or {}
    return _FRAUDULENT_REGISTRIES_CONFIG
