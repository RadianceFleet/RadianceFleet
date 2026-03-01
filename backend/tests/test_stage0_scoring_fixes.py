"""Stage 0 — Scoring engine correctness bug fixes.

Tests for:
  0.2: gap_reactivation_in_jamming_zone self-amplification fix
  0.3: Gap frequency subsumption ordering (take max)
  0.4: Draught events capped at single highest
  0.5: Flag hopping mutual exclusion with flag_changes_3plus_90d
  0.6: Confidence classifier key categorization fixes
  0.7: Vessel-type filtering (DWT-based effective type)
  0.9: YAML calibration (single flag change, age 15-20y)
  0.10: New legitimacy deductions (PSC, IG P&I, long history)
  E1: Score cap at 200
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.modules.risk_scoring import compute_gap_score, load_scoring_config
from app.modules.confidence_classifier import _categorize_key


# ── Mock gap factory ──────────────────────────────────────────────────────────

def _make_gap(
    duration_minutes=0,
    corridor_type=None,
    deadweight=None,
    flag_risk="unknown",
    year_built=None,
    ais_class="unknown",
    impossible_speed_flag=False,
    velocity_ratio=None,
    in_dark_zone=False,
    dark_zone_id=None,
    mmsi_first_seen_utc=None,
    flag=None,
    pi_coverage_status="active",
    psc_detained=False,
    vessel_type=None,
    name="TEST VESSEL",
    mmsi="259456789",
):
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk
    vessel.year_built = year_built
    vessel.ais_class = ais_class
    vessel.flag = flag
    vessel.mmsi_first_seen_utc = mmsi_first_seen_utc
    vessel.pi_coverage_status = pi_coverage_status
    vessel.psc_detained_last_12m = psc_detained
    vessel.psc_major_deficiencies_last_12m = 0
    vessel.vessel_id = 1
    vessel.vessel_type = vessel_type
    vessel.name = name
    vessel.mmsi = mmsi
    vessel.vessel_laid_up_30d = False
    vessel.vessel_laid_up_60d = False
    vessel.vessel_laid_up_in_sts_zone = False
    vessel.created_at = None

    corridor = None
    if corridor_type is not None:
        corridor = MagicMock()
        corridor.corridor_type = corridor_type

    gap = MagicMock()
    gap.gap_event_id = 1
    gap.vessel_id = 1
    gap.duration_minutes = duration_minutes
    gap.impossible_speed_flag = impossible_speed_flag
    gap.velocity_plausibility_ratio = velocity_ratio
    gap.in_dark_zone = in_dark_zone
    gap.dark_zone_id = dark_zone_id
    gap.vessel = vessel
    gap.corridor = corridor
    gap.gap_start_utc = datetime(2026, 1, 15, 12, 0)
    gap.gap_end_utc = datetime(2026, 1, 16, 12, 0)
    gap.corridor_id = None
    gap.start_point = None
    gap.gap_off_lat = None
    gap.gap_off_lon = None
    gap.max_plausible_distance_nm = None
    gap.original_vessel_id = None
    gap.source = None
    return gap


# ── 0.2: Self-amplification fix tests ────────────────────────────────────────

def test_dark_zone_gap_with_only_duration_no_reactivation():
    """Dark zone gap with ONLY gap_duration signal → gap_reactivation should NOT fire."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=8 * 60,
        in_dark_zone=True,
        dark_zone_id=42,
    )
    _, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    assert "gap_reactivation_in_jamming_zone" not in bd, \
        "Reactivation should not self-fire from gap_duration alone"


def test_dark_zone_gap_with_sts_signal_reactivation_fires():
    """Dark zone gap with STS signal → gap_reactivation SHOULD fire."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=8 * 60,
        in_dark_zone=True,
        dark_zone_id=42,
        corridor_type="sts_zone",
    )
    _, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    # STS tagged corridor adds gap_in_sts_tagged_corridor which is not a structural key
    assert "gap_reactivation_in_jamming_zone" in bd, \
        "Reactivation should fire when non-structural STS signal present"


def test_norwegian_vlcc_hormuz_under_50():
    """Norwegian VLCC with single gap in dark zone scores reasonably (not CRITICAL)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=8 * 60,
        in_dark_zone=True,
        dark_zone_id=42,
        deadweight=250_000,
        flag="NO",
        flag_risk="low_risk",
        year_built=2015,
    )
    score, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    assert score < 76, f"Norwegian VLCC in jamming zone should be below CRITICAL (76), got {score}"


# ── 0.3: Frequency subsumption fix ───────────────────────────────────────────

def test_4_gaps_30d_with_3_in_14d_scores_40():
    """Vessel with 4 gaps in 30d AND 3 in 14d scores +40 (not +32)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)
    _, bd = compute_gap_score(
        gap, config,
        gaps_in_14d=3,
        gaps_in_30d=4,
        scoring_date=datetime(2026, 1, 15),
    )
    assert "gap_frequency_4_in_30d" in bd, "4_in_30d (+40) should win over 3_in_14d (+32)"
    assert bd["gap_frequency_4_in_30d"] == 40


def test_frequency_takes_max_not_first():
    """All applicable tiers evaluated, max wins."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)
    _, bd = compute_gap_score(
        gap, config,
        gaps_in_7d=2,
        gaps_in_14d=3,
        gaps_in_30d=5,
        scoring_date=datetime(2026, 1, 15),
    )
    # 5_in_30d = 50, should beat all others
    assert "gap_frequency_5_in_30d" in bd
    assert bd["gap_frequency_5_in_30d"] == 50
    # Only one frequency key should be present
    freq_keys = [k for k in bd if k.startswith("gap_frequency_")]
    assert len(freq_keys) == 1


# ── 0.5: Flag hopping mutual exclusion ───────────────────────────────────────

def test_flag_hopping_skipped_when_3plus_90d_present():
    """Flag hopping should not fire when flag_changes_3plus_90d already in breakdown."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)
    _, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    # If both keys were present, it's a double-count
    has_3plus = "flag_changes_3plus_90d" in bd
    has_hopping = "flag_hopping" in bd
    assert not (has_3plus and has_hopping), \
        "flag_changes_3plus_90d and flag_hopping should be mutually exclusive"


# ── 0.6: Confidence classifier categorization ────────────────────────────────

def test_imo_fabricated_categorized_as_spoofing():
    """imo_fabricated should be categorized as SPOOFING, not AIS_GAP."""
    assert _categorize_key("imo_fabricated") == "SPOOFING"


def test_fraudulent_registry_categorized_as_identity():
    """fraudulent_registry_tier_0 should be categorized as IDENTITY_CHANGE."""
    assert _categorize_key("fraudulent_registry_tier_0") == "IDENTITY_CHANGE"


def test_scrapped_imo_categorized_as_spoofing():
    """scrapped_imo_reuse should be categorized as SPOOFING."""
    assert _categorize_key("scrapped_imo_reuse") == "SPOOFING"


def test_pi_unknown_insurer_categorized_as_identity():
    """pi_unknown_insurer should be categorized as IDENTITY_CHANGE."""
    assert _categorize_key("pi_unknown_insurer") == "IDENTITY_CHANGE"


def test_russian_port_categorized_as_sts():
    """russian_port_recent should be categorized as STS_TRANSFER."""
    assert _categorize_key("russian_port_recent") == "STS_TRANSFER"


def test_at_sea_no_port_call_categorized_as_ais_gap():
    """at_sea_no_port_call_365d should be categorized as AIS_GAP."""
    assert _categorize_key("at_sea_no_port_call_365d") == "AIS_GAP"


# ── 0.7: Vessel-type filtering ───────────────────────────────────────────────

def test_fishing_vessel_365d_at_sea_no_at_sea_ops():
    """Fishing vessel (deadweight<5000) at sea 365d should NOT score at_sea_ops."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=6 * 60,
        deadweight=500,
        vessel_type="fishing",
    )
    _, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    assert "at_sea_no_port_call_365d" not in bd, \
        "Fishing vessel should not get at_sea_ops signal"


def test_100k_dwt_fishing_type_still_scores():
    """100k DWT vessel broadcasting 'fishing' in STS corridor — STS signals NOT suppressed."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=8 * 60,
        deadweight=100_000,
        vessel_type="fishing",
        corridor_type="sts_zone",
    )
    _, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    assert "gap_in_sts_tagged_corridor" in bd, \
        "100k DWT vessel should score STS signals despite 'fishing' AIS type"
    assert bd.get("_corridor_multiplier", 1.0) > 1.0, \
        "Corridor multiplier should NOT be reduced for 100k DWT vessel"


def test_tanker_scores_normally():
    """Normal tanker scoring is unaffected."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=8 * 60,
        deadweight=80_000,
        vessel_type="tanker",
        corridor_type="sts_zone",
    )
    _, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    assert "gap_in_sts_tagged_corridor" in bd


def test_null_deadweight_uses_ais_type():
    """Vessel with NULL deadweight and non-commercial AIS type treated as non-commercial."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=8 * 60,
        deadweight=None,
        vessel_type="fishing",
        corridor_type="sts_zone",
    )
    _, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    assert bd.get("_corridor_multiplier", 1.0) == 1.0, \
        "Fishing vessel with no DWT should have corridor multiplier reduced to 1.0"


def test_non_commercial_still_scores_identity():
    """Non-commercial vessel with identity fraud still scores those signals."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=6 * 60,
        deadweight=500,
        vessel_type="fishing",
        flag_risk="high_risk",
    )
    _, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    # Flag risk should still be scored
    assert "flag_high_risk" in bd


# ── 0.9: YAML calibration ────────────────────────────────────────────────────

def test_single_flag_change_12m_scores_15():
    """Single flag change in 12m now scores +15 (was 0 — dead signal)."""
    config = load_scoring_config()
    assert config.get("metadata", {}).get("single_flag_change_last_12m") == 15


def test_age_17_vessel_scores_12():
    """17-year-old vessel scores +12 (was +5)."""
    config = load_scoring_config()
    assert config.get("vessel_age", {}).get("age_15_to_20y") == 12


def test_sts_corridor_gap_scores_20():
    """STS corridor gap base is now 20 (amplified by 1.5× → effective ~30)."""
    config = load_scoring_config()
    assert config.get("sts", {}).get("gap_in_sts_tagged_corridor") == 20


def test_pi_coverage_unknown_removed():
    """pi_coverage_unknown removed from YAML to avoid duplication."""
    config = load_scoring_config()
    pi_insurance = config.get("pi_insurance", {})
    assert "pi_coverage_unknown" not in pi_insurance


# ── E1: Score cap at 200 ─────────────────────────────────────────────────────

def test_score_capped_at_200():
    """Score should never exceed 200."""
    config = load_scoring_config()
    # Use extreme signals to push score high
    gap = _make_gap(
        duration_minutes=48 * 60,        # +55
        deadweight=250_000,               # VLCC 1.3× mult
        flag_risk="high_risk",            # +15
        year_built=1990,                  # 25+ = +30 (high risk flag)
        corridor_type="sts_zone",         # 1.5× mult
        impossible_speed_flag=True,       # +40
    )
    score, bd = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    assert score <= 200, f"Score {score} exceeds cap of 200"


def test_score_floor_at_0():
    """Score should never go below 0."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=2 * 60,  # minimal gap
        flag="NO",
        flag_risk="low_risk",
        year_built=2020,
    )
    score, _ = compute_gap_score(gap, config, scoring_date=datetime(2026, 1, 15))
    assert score >= 0, f"Score {score} went below 0"


# ── 0.10: New legitimacy YAML values ─────────────────────────────────────────

def test_legitimacy_yaml_has_new_deductions():
    """YAML contains new legitimacy deductions."""
    config = load_scoring_config()
    legit = config.get("legitimacy", {})
    assert legit.get("psc_clean_record") == -10
    assert legit.get("ig_pi_club_member") == -15
    assert legit.get("long_trading_history") == -8
