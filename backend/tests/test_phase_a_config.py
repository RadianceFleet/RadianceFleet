"""Tests for Phase A1-3 config changes: STS corridors, BB/GN flags, 15-20y age signal.

Covers:
  - 5 new STS corridors in corridors.yaml
  - BB (Barbados) and GN (Guinea) added to RUSSIAN_ORIGIN_FLAGS
  - MID-to-flag mapping for 314 (BB) and 632 (GN)
  - flag_to_risk_category HIGH_RISK for BB and GN
  - New vessel age bracket: 15-20y scores +5
  - Age 10-15y scores 0 (split from old 10-20y)
"""
import pathlib
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import yaml

from app.models.base import FlagRiskEnum
from app.modules.risk_scoring import compute_gap_score, load_scoring_config
from app.utils.vessel_identity import (
    RUSSIAN_ORIGIN_FLAGS,
    MID_TO_FLAG,
    flag_to_risk_category,
    mmsi_to_flag,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

CORRIDORS_YAML = pathlib.Path(__file__).resolve().parents[2] / "config" / "corridors.yaml"


def _load_corridors() -> list[dict]:
    with open(CORRIDORS_YAML) as f:
        data = yaml.safe_load(f) or {}
    return data.get("corridors", [])


def _make_gap(
    duration_minutes: int = 360,
    year_built: int | None = None,
    flag_risk: str = "unknown",
    flag: str | None = None,
    corridor_type: str | None = None,
    deadweight: int | None = None,
    ais_class: str = "unknown",
):
    """Minimal mock gap for scoring tests."""
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk
    vessel.year_built = year_built
    vessel.ais_class = ais_class
    vessel.flag = flag
    vessel.mmsi_first_seen_utc = None
    vessel.vessel_laid_up_30d = False
    vessel.vessel_laid_up_60d = False
    vessel.vessel_laid_up_in_sts_zone = False
    vessel.pi_coverage_status = "active"
    vessel.psc_detained_last_12m = False
    vessel.psc_major_deficiencies_last_12m = 0
    vessel.vessel_id = 1

    corridor = None
    if corridor_type is not None:
        corridor = MagicMock()
        corridor.corridor_type = corridor_type

    gap = MagicMock()
    gap.gap_event_id = 1
    gap.vessel_id = 1
    gap.duration_minutes = duration_minutes
    gap.impossible_speed_flag = False
    gap.velocity_plausibility_ratio = None
    gap.in_dark_zone = False
    gap.dark_zone_id = None
    gap.vessel = vessel
    gap.corridor = corridor
    gap.gap_start_utc = datetime(2026, 1, 15, 12, 0)
    gap.gap_end_utc = datetime(2026, 1, 16, 12, 0)
    return gap


# ── Test: corridors.yaml has 5 new STS zones ────────────────────────────────

NEW_STS_NAMES = [
    "Gulf of Oman offshore STS",
    "Bulgaria offshore STS",
    "Cyprus offshore STS",
    "Cape Verde / South Atlantic STS",
    "Khor al Zubair / Basra",
]


def test_corridors_yaml_has_new_sts_zones():
    """All 5 new STS corridors exist with correct corridor_type."""
    corridors = _load_corridors()
    names = {c["name"] for c in corridors}

    for expected in NEW_STS_NAMES:
        assert expected in names, f"Missing STS corridor: {expected}"

    # Verify they're all sts_zone type
    by_name = {c["name"]: c for c in corridors}
    for expected in NEW_STS_NAMES:
        assert by_name[expected]["corridor_type"] == "sts_zone"
        assert by_name[expected]["is_jamming_zone"] is False


# ── Test: BB and GN in RUSSIAN_ORIGIN_FLAGS ──────────────────────────────────

def test_bb_gn_in_russian_origin_flags():
    """BB (Barbados) and GN (Guinea) must be in RUSSIAN_ORIGIN_FLAGS."""
    assert "BB" in RUSSIAN_ORIGIN_FLAGS
    assert "GN" in RUSSIAN_ORIGIN_FLAGS


# ── Test: MID-to-flag mapping for BB and GN ──────────────────────────────────

def test_mid_to_flag_bb_gn():
    """MID 314 maps to BB, MID 632 maps to GN."""
    assert mmsi_to_flag("314123456") == "BB"
    assert mmsi_to_flag("632123456") == "GN"


def test_mid_to_flag_dict_entries():
    """MID_TO_FLAG dict has the right entries."""
    assert MID_TO_FLAG["314"] == "BB"
    assert MID_TO_FLAG["632"] == "GN"


# ── Test: BB and GN classify as HIGH_RISK ────────────────────────────────────

def test_bb_gn_flag_risk_high():
    """BB and GN must classify as HIGH_RISK."""
    assert flag_to_risk_category("BB") == FlagRiskEnum.HIGH_RISK
    assert flag_to_risk_category("GN") == FlagRiskEnum.HIGH_RISK


# ── Test: vessel age 15-20y scores +5 ────────────────────────────────────────

def test_vessel_age_15_20y_scores():
    """Vessel age 17 (year_built giving age 17) should produce vessel_age_15_20y: 12."""
    config = load_scoring_config()
    # year_built=2009 scored at 2026-01-15 => age=17
    gap = _make_gap(duration_minutes=6 * 60, year_built=2009)
    _, breakdown = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))

    assert "vessel_age_15_20y" in breakdown, "Expected vessel_age_15_20y in breakdown"
    assert breakdown["vessel_age_15_20y"] == 12  # calibrated from 5 to 12 (KSE avg 17y)


def test_vessel_age_12y_scores_zero():
    """Vessel age 12 should produce vessel_age_10_15y: 0."""
    config = load_scoring_config()
    # year_built=2014 scored at 2026-01-15 => age=12
    gap = _make_gap(duration_minutes=6 * 60, year_built=2014)
    _, breakdown = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))

    assert "vessel_age_10_15y" in breakdown, "Expected vessel_age_10_15y in breakdown"
    assert breakdown["vessel_age_10_15y"] == 0
    assert "vessel_age_15_20y" not in breakdown, "15-20y should NOT fire for age 12"
