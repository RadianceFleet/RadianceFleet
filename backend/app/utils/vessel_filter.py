"""Configurable vessel type filtering (PRD FR2).

Replaces hardcoded `"tanker" in vessel_type.lower()` checks across detection modules
with a YAML-configurable filter supporting type keywords, DWT threshold, and manual
include/exclude lists.
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

_FILTER_CONFIG: dict[str, Any] | None = None


def _load_filter_config() -> dict[str, Any]:
    global _FILTER_CONFIG
    if _FILTER_CONFIG is None:
        config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "vessel_filter.yaml"
        if config_path.exists():
            with open(config_path) as f:
                _FILTER_CONFIG = yaml.safe_load(f) or {}
        else:
            logger.warning("vessel_filter.yaml not found — using defaults")
            _FILTER_CONFIG = {}
    return _FILTER_CONFIG


def is_tanker_type(vessel: Any) -> bool:
    """Check if a vessel qualifies as tanker-type based on configurable filters.

    Returns True if:
      - vessel.mmsi is in manual_include_mmsi, OR
      - vessel.vessel_type contains any tanker_type_keywords, OR
      - vessel.deadweight >= tanker_min_dwt

    Returns False if:
      - vessel.mmsi is in manual_exclude_mmsi
    """
    cfg = _load_filter_config()

    # Manual exclude takes priority
    mmsi = getattr(vessel, 'mmsi', None)
    exclude_list = cfg.get("manual_exclude_mmsi") or []
    if mmsi and str(mmsi) in [str(m) for m in exclude_list]:
        return False

    # Manual include
    include_list = cfg.get("manual_include_mmsi") or []
    if mmsi and str(mmsi) in [str(m) for m in include_list]:
        return True

    # Type keyword matching
    vtype = getattr(vessel, 'vessel_type', None)
    if vtype:
        vtype_lower = vtype.lower()
        keywords = cfg.get("tanker_type_keywords", ["tanker"])
        for kw in keywords:
            if kw.lower() in vtype_lower:
                return True

    # DWT fallback
    dwt = getattr(vessel, 'deadweight', None)
    min_dwt = cfg.get("tanker_min_dwt", 20_000)
    if isinstance(dwt, (int, float)) and dwt >= min_dwt:
        return True

    return False


def reload_filter_config() -> dict[str, Any]:
    """Force-reload filter config from disk."""
    global _FILTER_CONFIG
    _FILTER_CONFIG = None
    return _load_filter_config()
