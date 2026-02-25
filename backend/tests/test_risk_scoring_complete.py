"""Extended risk scoring tests covering new signal categories.

Tests cover:
  - Gap frequency subsumption hierarchy (7d / 14d / 30d windows)
  - New MMSI scoring (< 30 days old)
  - New MMSI + Russian-origin flag stacking
  - Score reproducibility (fixed scoring_date)
  - Dark zone interior deduction (-10 pts)
  - Legitimacy signals skipped when db=None
  - AIS class B mismatch for large tankers (DWT > 1000t)
  - AIS class B not flagged for small vessels (DWT <= 1000t)

All tests are unit-level: no database required.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.modules.risk_scoring import compute_gap_score, load_scoring_config, _score_band


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
    vessel_laid_up_30d=False,
    vessel_laid_up_60d=False,
    vessel_laid_up_in_sts_zone=False,
    pi_coverage_status="active",
    psc_detained=False,
    psc_major_deficiencies=0,
):
    """Build a fully-featured mock AISGapEvent for scoring tests.

    Uses plain strings for enum fields so _corridor_multiplier /
    _vessel_size_multiplier comparisons work without SQLAlchemy infrastructure.
    """
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk          # plain string, not enum
    vessel.year_built = year_built
    vessel.ais_class = ais_class                   # plain string, not enum
    vessel.flag = flag
    vessel.mmsi_first_seen_utc = mmsi_first_seen_utc
    vessel.vessel_laid_up_30d = vessel_laid_up_30d
    vessel.vessel_laid_up_60d = vessel_laid_up_60d
    vessel.vessel_laid_up_in_sts_zone = vessel_laid_up_in_sts_zone
    vessel.pi_coverage_status = pi_coverage_status
    vessel.psc_detained_last_12m = psc_detained
    vessel.psc_major_deficiencies_last_12m = psc_major_deficiencies
    vessel.vessel_id = 1

    corridor = None
    if corridor_type is not None:
        corridor = MagicMock()
        corridor.corridor_type = corridor_type     # plain string, e.g. "sts_zone"

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
    return gap


# ── Gap frequency subsumption tests ──────────────────────────────────────────

def test_gap_frequency_subsumption_30d():
    """5 gaps in 30d → only gap_frequency_5_in_30d fires; 14d and 7d keys absent."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)

    score, breakdown = compute_gap_score(
        gap, config,
        gaps_in_7d=2,
        gaps_in_14d=3,
        gaps_in_30d=5,
    )

    # The highest window fires
    assert "gap_frequency_5_in_30d" in breakdown, \
        "Expected 30d frequency key in breakdown"

    # Lower windows must be suppressed by subsumption
    assert "gap_frequency_3_in_14d" not in breakdown, \
        "14d frequency should be subsumed by 30d"
    assert "gap_frequency_2_in_7d" not in breakdown, \
        "7d frequency should be subsumed by 30d"


def test_gap_frequency_subsumption_14d():
    """3 gaps in 14d (< 5 in 30d) → only gap_frequency_3_in_14d fires."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)

    score, breakdown = compute_gap_score(
        gap, config,
        gaps_in_7d=2,
        gaps_in_14d=3,
        gaps_in_30d=4,   # 4 < 5 → 30d does NOT fire
    )

    assert "gap_frequency_3_in_14d" in breakdown, \
        "Expected 14d frequency key in breakdown"
    assert "gap_frequency_2_in_7d" not in breakdown, \
        "7d frequency should be subsumed by 14d"
    assert "gap_frequency_5_in_30d" not in breakdown, \
        "30d frequency should not fire (only 4 gaps)"


def test_gap_frequency_subsumption_7d_only():
    """2 gaps in 7d, fewer in longer windows → only gap_frequency_2_in_7d fires."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)

    score, breakdown = compute_gap_score(
        gap, config,
        gaps_in_7d=2,
        gaps_in_14d=2,   # < 3 → 14d does NOT fire
        gaps_in_30d=2,   # < 5 → 30d does NOT fire
    )

    assert "gap_frequency_2_in_7d" in breakdown, \
        "Expected 7d frequency key in breakdown"
    assert "gap_frequency_3_in_14d" not in breakdown
    assert "gap_frequency_5_in_30d" not in breakdown


def test_gap_frequency_values_match_config():
    """Frequency signal point values match risk_scoring.yaml definitions."""
    config = load_scoring_config()

    # Test each window individually by providing counts that trigger only one
    gap = _make_gap(duration_minutes=6 * 60)

    _, bd_30 = compute_gap_score(gap, config, gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=5)
    _, bd_14 = compute_gap_score(gap, config, gaps_in_7d=0, gaps_in_14d=3, gaps_in_30d=4)
    _, bd_7 = compute_gap_score(gap, config, gaps_in_7d=2, gaps_in_14d=0, gaps_in_30d=0)

    assert bd_30.get("gap_frequency_5_in_30d") == 50
    assert bd_14.get("gap_frequency_3_in_14d") == 32
    assert bd_7.get("gap_frequency_2_in_7d") == 18


# ── New MMSI scoring tests ────────────────────────────────────────────────────

def test_new_mmsi_adds_15pts():
    """MMSI first seen 14 days ago (< 30 day threshold) → +15 points."""
    config = load_scoring_config()
    scoring_date = datetime(2026, 2, 15)
    first_seen = datetime(2026, 2, 1)  # 14 days before scoring_date

    gap = _make_gap(duration_minutes=6 * 60, mmsi_first_seen_utc=first_seen)
    score, breakdown = compute_gap_score(gap, config, scoring_date=scoring_date)

    assert "new_mmsi_first_30d" in breakdown, \
        "Expected new_mmsi_first_30d signal for MMSI < 30 days old"
    assert breakdown["new_mmsi_first_30d"] == 15


def test_new_mmsi_not_fired_old_mmsi():
    """MMSI first seen 45 days ago (>= 30 day threshold) → no new_mmsi signal."""
    config = load_scoring_config()
    scoring_date = datetime(2026, 2, 15)
    first_seen = datetime(2026, 1, 1)  # 45 days before scoring_date

    gap = _make_gap(duration_minutes=6 * 60, mmsi_first_seen_utc=first_seen)
    score, breakdown = compute_gap_score(gap, config, scoring_date=scoring_date)

    assert "new_mmsi_first_30d" not in breakdown, \
        "MMSI older than 30 days should not trigger new_mmsi signal"


def test_new_mmsi_not_fired_when_none():
    """mmsi_first_seen_utc=None → new_mmsi signal is skipped entirely."""
    config = load_scoring_config()
    scoring_date = datetime(2026, 2, 15)

    gap = _make_gap(duration_minutes=6 * 60, mmsi_first_seen_utc=None)
    score, breakdown = compute_gap_score(gap, config, scoring_date=scoring_date)

    assert "new_mmsi_first_30d" not in breakdown


def test_new_mmsi_russian_flag_adds_40pts_total():
    """New MMSI + Comoros flag (KM) → +15 (new_mmsi) + +25 (russian_origin) = +40 total."""
    config = load_scoring_config()
    scoring_date = datetime(2026, 2, 15)
    first_seen = datetime(2026, 2, 1)  # 14 days — MMSI is new

    gap = _make_gap(
        duration_minutes=6 * 60,
        mmsi_first_seen_utc=first_seen,
        flag="KM",   # Comoros — on the Russian-origin flag list
    )
    score, breakdown = compute_gap_score(gap, config, scoring_date=scoring_date)

    assert "new_mmsi_first_30d" in breakdown, "Expected new MMSI signal"
    assert "new_mmsi_russian_origin_flag" in breakdown, "Expected Russian-origin flag signal"

    combined = breakdown["new_mmsi_first_30d"] + breakdown["new_mmsi_russian_origin_flag"]
    assert combined == 40, f"Expected +40 total from two MMSI signals, got {combined}"


def test_new_mmsi_non_russian_flag_no_stacking():
    """New MMSI + Norwegian flag (NO) → +15 only; russian_origin signal absent."""
    config = load_scoring_config()
    scoring_date = datetime(2026, 2, 15)
    first_seen = datetime(2026, 2, 5)  # 10 days — new MMSI

    gap = _make_gap(
        duration_minutes=6 * 60,
        mmsi_first_seen_utc=first_seen,
        flag="NO",   # Norway — NOT on the Russian-origin list
    )
    score, breakdown = compute_gap_score(gap, config, scoring_date=scoring_date)

    assert "new_mmsi_first_30d" in breakdown
    assert "new_mmsi_russian_origin_flag" not in breakdown


def test_russian_origin_flags_enumerated():
    """Each flag in the known Russian-origin set triggers the stacking signal."""
    config = load_scoring_config()
    scoring_date = datetime(2026, 2, 15)
    first_seen = datetime(2026, 2, 10)  # 5 days old

    # All flags documented in risk_scoring.py Phase 6.10
    russian_origin_flags = {"PW", "MH", "KM", "SL", "HN", "GA", "CM", "TZ"}

    for flag in russian_origin_flags:
        gap = _make_gap(
            duration_minutes=6 * 60,
            mmsi_first_seen_utc=first_seen,
            flag=flag,
        )
        _, breakdown = compute_gap_score(gap, config, scoring_date=scoring_date)
        assert "new_mmsi_russian_origin_flag" in breakdown, \
            f"Flag {flag} should trigger russian_origin signal"


# ── Score reproducibility tests ───────────────────────────────────────────────

def test_score_reproducibility():
    """Same inputs and fixed scoring_date → identical score and breakdown each call."""
    config = load_scoring_config()
    scoring_date = datetime(2026, 1, 15, 12, 0)
    gap = _make_gap(duration_minutes=25 * 60, corridor_type="sts_zone", deadweight=250_000)

    score1, bd1 = compute_gap_score(gap, config, scoring_date=scoring_date)
    score2, bd2 = compute_gap_score(gap, config, scoring_date=scoring_date)

    assert score1 == score2, "Scores must be identical for identical inputs"
    assert bd1 == bd2, "Breakdowns must be identical for identical inputs"


def test_score_changes_with_different_date():
    """A gap with a new MMSI: score at day 14 differs from score at day 35 (MMSI ages out)."""
    config = load_scoring_config()
    first_seen = datetime(2026, 1, 1)
    gap = _make_gap(duration_minutes=6 * 60, mmsi_first_seen_utc=first_seen)

    # Day 14 → MMSI still new
    score_early, bd_early = compute_gap_score(
        gap, config, scoring_date=datetime(2026, 1, 15)
    )
    # Day 35 → MMSI aged out
    score_late, bd_late = compute_gap_score(
        gap, config, scoring_date=datetime(2026, 2, 5)
    )

    assert "new_mmsi_first_30d" in bd_early
    assert "new_mmsi_first_30d" not in bd_late
    assert score_early > score_late, \
        "Score should be higher when MMSI is new than when it has aged out"


# ── Dark zone interior deduction tests ───────────────────────────────────────

def test_dark_zone_interior_reduces_score():
    """Short gap (< 60 min) entirely inside dark zone, no impossible speed → -10 deduction."""
    config = load_scoring_config()

    # A gap of 30 minutes with dark_zone_id set and impossible_speed=False
    # → the scoring engine applies 'dark_zone_deduction' (-10)
    gap_dz = _make_gap(
        duration_minutes=30,
        in_dark_zone=True,
        dark_zone_id=1,
        impossible_speed_flag=False,
    )
    gap_normal = _make_gap(duration_minutes=30, in_dark_zone=False)

    score_dz, bd_dz = compute_gap_score(gap_dz, config)
    score_normal, _ = compute_gap_score(gap_normal, config)

    assert score_dz <= score_normal, \
        "Dark zone interior deduction should reduce or match the normal score"
    assert "dark_zone_deduction" in bd_dz, \
        "Expected dark_zone_deduction key in breakdown"
    assert bd_dz["dark_zone_deduction"] == -10


def test_dark_zone_exit_impossible_adds_35():
    """Gap in dark zone with impossible speed flag → +35 (exit scenario)."""
    config = load_scoring_config()

    gap = _make_gap(
        duration_minutes=6 * 60,
        in_dark_zone=True,
        dark_zone_id=1,
        impossible_speed_flag=True,
    )
    _, breakdown = compute_gap_score(gap, config)

    assert "dark_zone_exit_impossible" in breakdown, \
        "Expected dark_zone_exit_impossible signal"
    assert breakdown["dark_zone_exit_impossible"] == 35


def test_dark_zone_entry_long_gap_adds_20():
    """Long gap (> 60 min) in dark zone with dark_zone_id, no impossible speed → +20 (entry)."""
    config = load_scoring_config()

    gap = _make_gap(
        duration_minutes=120,   # 2 hours — above the 60-min clip threshold
        in_dark_zone=True,
        dark_zone_id=1,
        impossible_speed_flag=False,
    )
    _, breakdown = compute_gap_score(gap, config)

    assert "dark_zone_entry" in breakdown, \
        "Expected dark_zone_entry signal for long gap with dark zone id"
    assert breakdown["dark_zone_entry"] == 20


def test_dark_zone_deduction_no_dark_zone_id():
    """in_dark_zone=True but dark_zone_id=None → else branch → dark_zone_deduction (-10)."""
    config = load_scoring_config()

    gap = _make_gap(
        duration_minutes=6 * 60,
        in_dark_zone=True,
        dark_zone_id=None,
        impossible_speed_flag=False,
    )
    _, breakdown = compute_gap_score(gap, config)

    assert "dark_zone_deduction" in breakdown
    assert breakdown["dark_zone_deduction"] == -10


# ── Legitimacy signal tests ───────────────────────────────────────────────────

def test_legitimacy_gap_free_not_applied_when_db_none():
    """When db=None, all DB-dependent legitimacy signals are skipped without error."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)

    score, breakdown = compute_gap_score(gap, config, db=None)

    # Should not raise; legitimacy signals simply absent
    assert "legitimacy_gap_free_90d" not in breakdown
    assert "legitimacy_ais_class_a_consistent" not in breakdown


def test_spoofing_signals_skipped_when_db_none():
    """When db=None, spoofing DB query phase is skipped — no spoofing keys added."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)

    score, breakdown = compute_gap_score(gap, config, db=None)

    # No spoofing_ prefixed key should appear (they require DB query)
    spoofing_keys = [k for k in breakdown if k.startswith("spoofing_")]
    assert len(spoofing_keys) == 0, \
        f"Expected no spoofing keys when db=None, found: {spoofing_keys}"


# ── AIS class mismatch tests ──────────────────────────────────────────────────

def test_ais_class_b_threshold_1000_dwt():
    """Class B transponder on a large tanker (DWT=5 000t > 1 000t) → ais_class_mismatch +50."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, ais_class="B", deadweight=5_000)

    score, breakdown = compute_gap_score(gap, config)

    assert "ais_class_mismatch" in breakdown, \
        "Expected ais_class_mismatch for large tanker using Class B"
    assert breakdown["ais_class_mismatch"] == 50


def test_ais_class_b_not_flagged_small_vessel():
    """Class B with DWT=800t (<= 1 000t threshold) → no mismatch (small vessels are exempt)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, ais_class="B", deadweight=800)

    score, breakdown = compute_gap_score(gap, config)

    assert "ais_class_mismatch" not in breakdown, \
        "Small vessel (DWT <= 1000) with Class B should not trigger mismatch"


def test_ais_class_a_no_mismatch():
    """Class A transponder regardless of DWT → no mismatch signal."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, ais_class="A", deadweight=250_000)

    score, breakdown = compute_gap_score(gap, config)

    assert "ais_class_mismatch" not in breakdown


def test_ais_class_b_boundary_exactly_1000_dwt():
    """Class B at exactly DWT=1000t is NOT above the 1000t threshold → no mismatch.

    The condition in the implementation is: deadweight > 1_000 (strictly greater).
    """
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, ais_class="B", deadweight=1_000)

    score, breakdown = compute_gap_score(gap, config)

    assert "ais_class_mismatch" not in breakdown, \
        "DWT=1000 is not > 1000 — boundary vessel should NOT be flagged"


def test_ais_class_b_boundary_1001_dwt():
    """Class B at DWT=1001t is strictly above threshold → mismatch fires."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, ais_class="B", deadweight=1_001)

    score, breakdown = compute_gap_score(gap, config)

    assert "ais_class_mismatch" in breakdown, \
        "DWT=1001 > 1000 — should trigger ais_class_mismatch"


# ── Combined scenario tests ───────────────────────────────────────────────────

def test_critical_score_sts_vlcc_from_extended_helper():
    """25h gap in STS zone with VLCC using extended _make_gap helper → critical band."""
    config = load_scoring_config()
    scoring_date = datetime(2026, 1, 15, 12, 0)

    gap = _make_gap(
        duration_minutes=25 * 60,
        corridor_type="sts_zone",
        deadweight=250_000,
    )
    score, breakdown = compute_gap_score(gap, config, scoring_date=scoring_date)

    assert score > 76, f"Expected critical score (>76), got {score}"
    assert _score_band(score) == "critical"
    assert breakdown["_corridor_multiplier"] == 2.0
    assert breakdown["_vessel_size_multiplier"] == 1.5


# ── Phase 1: Multiplier asymmetry tests ──────────────────────────────────

def test_legitimacy_not_amplified_by_corridor():
    """STS zone 2.0× corridor multiplier should NOT double the -15 legitimacy deduction.

    Legitimacy signals always deduct their face value regardless of zone.
    """
    config = load_scoring_config()
    # 6h gap, STS zone, gap-free 90d → legitimacy -15 should be exactly -15
    gap_sts = _make_gap(duration_minutes=6 * 60, corridor_type="sts_zone")
    gap_none = _make_gap(duration_minutes=6 * 60, corridor_type=None)

    # Use a mock db that returns 0 recent gaps (legitimacy_gap_free_90d fires)
    # and Class A AIS points (legitimacy_ais_class_a_consistent fires)
    mock_db = _make_full_mock_db(recent_gap_count=0, all_class_a=True)

    _, bd_sts = compute_gap_score(gap_sts, config, db=mock_db)
    mock_db2 = _make_full_mock_db(recent_gap_count=0, all_class_a=True)
    _, bd_none = compute_gap_score(gap_none, config, db=mock_db2)

    # Both should have the same legitimacy deduction values
    assert bd_sts.get("legitimacy_gap_free_90d") == -15, \
        "STS zone should NOT amplify the -15 deduction"
    assert bd_none.get("legitimacy_gap_free_90d") == -15


def test_legitimacy_not_amplified_by_vessel_size():
    """VLCC 1.5× size multiplier should NOT amplify the -15 legitimacy deduction."""
    config = load_scoring_config()
    gap_vlcc = _make_gap(duration_minutes=6 * 60, deadweight=250_000)
    gap_small = _make_gap(duration_minutes=6 * 60, deadweight=None)

    mock_db1 = _make_full_mock_db(recent_gap_count=0, all_class_a=True)
    mock_db2 = _make_full_mock_db(recent_gap_count=0, all_class_a=True)

    score_vlcc, bd_vlcc = compute_gap_score(gap_vlcc, config, db=mock_db1)
    score_small, bd_small = compute_gap_score(gap_small, config, db=mock_db2)

    # Legitimacy deduction should be identical regardless of vessel size
    assert bd_vlcc.get("legitimacy_gap_free_90d") == bd_small.get("legitimacy_gap_free_90d") == -15
    # But the risk signals should be amplified differently
    assert bd_vlcc["_vessel_size_multiplier"] == 1.5
    assert bd_small["_vessel_size_multiplier"] == 1.0


def test_vlcc_in_sts_zone_with_legitimacy():
    """Integration test: VLCC in STS zone with legitimacy signals.

    Risk signals should be amplified by 2.0 × 1.5 = 3.0×.
    Legitimacy signals should be at face value (-15, -5).
    """
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=6 * 60,
        corridor_type="sts_zone",
        deadweight=250_000,
    )
    mock_db = _make_full_mock_db(recent_gap_count=0, all_class_a=True)
    score, bd = compute_gap_score(gap, config, db=mock_db)

    # Verify multipliers
    assert bd["_corridor_multiplier"] == 2.0
    assert bd["_vessel_size_multiplier"] == 1.5

    # Verify legitimacy not amplified: sum of negative signals
    neg_sum = sum(v for k, v in bd.items() if not k.startswith("_") and isinstance(v, (int, float)) and v < 0)
    pos_sum = sum(v for k, v in bd.items() if not k.startswith("_") and isinstance(v, (int, float)) and v > 0)

    # final_score = round(pos_sum * 2.0 * 1.5 + neg_sum)
    expected = max(0, round(pos_sum * 2.0 * 1.5 + neg_sum))
    assert score == expected, f"Expected {expected}, got {score}"


def _make_full_mock_db(recent_gap_count=5, all_class_a=False):
    """Build a mock db that handles all the query patterns in compute_gap_score.

    Args:
        recent_gap_count: Number of recent gaps to return (0 triggers legitimacy_gap_free_90d).
        all_class_a: If True, return None for non-A AIS point query (triggers ais_class_a_consistent).
    """
    from app.models.gap_event import AISGapEvent as _GE
    from app.models.ais_point import AISPoint as _AP

    def query_side_effect(model):
        mock_chain = MagicMock()
        if model is _GE:
            # For gap frequency count queries
            mock_chain.filter.return_value.count.return_value = recent_gap_count
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.first.return_value = None
        elif model is _AP:
            if all_class_a:
                mock_chain.filter.return_value.first.return_value = None  # no non-A points
            else:
                mock_chain.filter.return_value.first.return_value = MagicMock()
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.count.return_value = 0
        else:
            # SpoofingAnomaly, LoiteringEvent, StsTransferEvent, VesselWatchlist, etc.
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.first.return_value = None
            mock_chain.filter.return_value.count.return_value = 0
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect
    return mock_db


def test_all_metadata_prefixed_keys_are_not_summed():
    """Keys starting with _ must not appear in the additive signals subtotal."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=25 * 60, corridor_type="sts_zone", deadweight=250_000)

    score, breakdown = compute_gap_score(gap, config)

    meta_keys = [k for k in breakdown if k.startswith("_")]
    signal_keys = [k for k in breakdown if not k.startswith("_")]

    # Recompute additive sum manually and verify it matches _additive_subtotal
    manual_subtotal = sum(v for k, v in breakdown.items() if not k.startswith("_"))
    assert manual_subtotal == breakdown["_additive_subtotal"], \
        f"Manual subtotal {manual_subtotal} != stored {breakdown['_additive_subtotal']}"

    # Metadata keys must all be present
    for key in ["_corridor_type", "_corridor_multiplier", "_vessel_size_class",
                "_vessel_size_multiplier", "_additive_subtotal", "_final_score"]:
        assert key in breakdown, f"Expected metadata key {key!r} in breakdown"


# ── New signal tests (v4 gap analysis) ───────────────────────────────────────

def test_gap_in_sts_corridor_adds_30_then_multiplied():
    """gap_in_sts_tagged_corridor: +30 is in additive subtotal BEFORE the 2.0× multiplier.

    Verification: subtract the 30 from the additive total and confirm final_score
    decreases by 30 × 2.0 × vessel_size_mult (not a flat 30 added post-multiply).
    """
    config = load_scoring_config()
    scoring_date = datetime(2026, 1, 15, 12, 0)

    # 6h gap in STS zone with VLCC (size mult = 1.5)
    gap_sts = _make_gap(duration_minutes=6 * 60, corridor_type="sts_zone", deadweight=250_000)
    gap_no_sts = _make_gap(duration_minutes=6 * 60, corridor_type=None, deadweight=250_000)

    score_sts, bd_sts = compute_gap_score(gap_sts, config, scoring_date=scoring_date)
    score_no_sts, bd_no_sts = compute_gap_score(gap_no_sts, config, scoring_date=scoring_date)

    assert "gap_in_sts_tagged_corridor" in bd_sts, \
        "Expected gap_in_sts_tagged_corridor signal in STS corridor gap"
    assert bd_sts["gap_in_sts_tagged_corridor"] == 30

    # The signal must be in the additive subtotal (before Phase 2 mult)
    # _additive_subtotal should include the +30
    assert bd_sts["_additive_subtotal"] >= bd_no_sts["_additive_subtotal"] + 30, \
        "gap_in_sts_tagged_corridor (+30) must be in the additive subtotal"


def test_speed_spoof_supersedes_spike():
    """Speed above spoof threshold → speed_spoof_before_gap (+25) fires; speed_spike_before_gap must NOT."""
    config = load_scoring_config()
    # Aframax (80–120k DWT): spoof_threshold=24 kn
    gap = _make_gap(duration_minutes=6 * 60, deadweight=100_000)
    # SOG above spoof threshold (24 kn) for Aframax
    _, breakdown = compute_gap_score(gap, config, pre_gap_sog=25.0)

    assert "speed_spoof_before_gap" in breakdown, "Spoof signal must fire"
    assert "speed_spike_before_gap" not in breakdown, \
        "Spike signal must be suppressed when spoof fires (subsumption)"
    assert breakdown["speed_spoof_before_gap"] == 25
    # 1.4× duration multiplier must also be applied (gap_duration exists at 6h)
    assert "gap_duration_speed_spike_bonus" in breakdown


def test_speed_spike_adds_8_with_multiplier_bonus():
    """Speed between spike and spoof thresholds → speed_spike_before_gap (+8) fires, spoof absent."""
    config = load_scoring_config()
    # Aframax: spike=20 kn, spoof=24 kn → 21 kn is between spike and spoof
    gap = _make_gap(duration_minutes=6 * 60, deadweight=100_000)
    _, breakdown = compute_gap_score(gap, config, pre_gap_sog=21.0)

    assert "speed_spike_before_gap" in breakdown, "Spike signal must fire"
    assert "speed_spoof_before_gap" not in breakdown, "Spoof must not fire below spoof threshold"
    assert breakdown["speed_spike_before_gap"] == 8
    assert "gap_duration_speed_spike_bonus" in breakdown, "1.4× bonus must apply for spike"


def test_speed_below_spike_threshold_no_signal():
    """SOG below spike threshold → no speed signal."""
    config = load_scoring_config()
    # Aframax: spike=20 kn → 15 kn is below
    gap = _make_gap(duration_minutes=6 * 60, deadweight=100_000)
    _, breakdown = compute_gap_score(gap, config, pre_gap_sog=15.0)

    assert "speed_spike_before_gap" not in breakdown
    assert "speed_spoof_before_gap" not in breakdown
    assert "gap_duration_speed_spike_bonus" not in breakdown


def test_pre_gap_sog_none_no_speed_signal():
    """pre_gap_sog=None (not available) → no speed spike/spoof signal."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, deadweight=100_000)
    _, breakdown = compute_gap_score(gap, config, pre_gap_sog=None)

    assert "speed_spoof_before_gap" not in breakdown
    assert "speed_spike_before_gap" not in breakdown


def _make_db_with_history(history_records):
    """Build a mock db where VesselHistory query returns the given records.

    All other model queries return empty results (to prevent cascade effects
    from SpoofingAnomaly, LoiteringEvent, StsTransferEvent, etc.).
    """
    from app.models.vessel_history import VesselHistory

    def query_side_effect(model):
        mock_chain = MagicMock()
        if model is VesselHistory:
            mock_chain.filter.return_value.all.return_value = history_records
        else:
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.first.return_value = None
            mock_chain.filter.return_value.count.return_value = 0
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect
    return mock_db


def _make_history(field_changed: str, days_before_gap: int) -> MagicMock:
    """Create a mock VesselHistory record observed N days before the gap start."""
    h = MagicMock()
    h.field_changed = field_changed
    # gap_start_utc in _make_gap is datetime(2026, 1, 15, 12, 0)
    h.observed_at = datetime(2026, 1, 15, 12, 0) - timedelta(days=days_before_gap)
    return h


def test_flag_change_7d_supersedes_30d():
    """Flag change within 7d → flag_change_7d (+35) fires; flag_change_30d must NOT also fire."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)
    flag_change_5d = _make_history("flag", days_before_gap=5)

    mock_db = _make_db_with_history([flag_change_5d])
    _, breakdown = compute_gap_score(gap, config, db=mock_db)

    assert "flag_change_7d" in breakdown, "7d flag change must fire"
    assert "flag_change_30d" not in breakdown, "30d must be suppressed when 7d fires"
    assert breakdown["flag_change_7d"] == 35


def test_flag_change_30d_fires_when_outside_7d():
    """Flag change 20d before gap (within 30d, outside 7d) → flag_change_30d (+25), not 7d."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)
    flag_change_20d = _make_history("flag", days_before_gap=20)

    mock_db = _make_db_with_history([flag_change_20d])
    _, breakdown = compute_gap_score(gap, config, db=mock_db)

    assert "flag_change_30d" in breakdown, "30d flag change must fire"
    assert "flag_change_7d" not in breakdown, "7d must not fire for 20-day-old change"
    assert breakdown["flag_change_30d"] == 25


def test_name_change_7d_fires_active_voyage():
    """Name change 5d before gap → name_change_during_voyage (+30)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)
    name_change_5d = _make_history("name", days_before_gap=5)

    mock_db = _make_db_with_history([name_change_5d])
    _, breakdown = compute_gap_score(gap, config, db=mock_db)

    assert "name_change_during_voyage" in breakdown, \
        "Name change within 7d of gap start must fire"
    assert breakdown["name_change_during_voyage"] == 30


def test_name_change_outside_7d_no_fire():
    """Name change 45d before gap (outside 7d guard) → no name_change signal (dry-dock rename)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)
    name_change_45d = _make_history("name", days_before_gap=45)

    mock_db = _make_db_with_history([name_change_45d])
    _, breakdown = compute_gap_score(gap, config, db=mock_db)

    assert "name_change_during_voyage" not in breakdown, \
        "Name change >7d before gap must not fire (dry-dock guard)"


def test_mmsi_change_adds_45():
    """VesselHistory record with field_changed='mmsi' → mmsi_change (+45)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)
    mmsi_change = _make_history("mmsi", days_before_gap=10)

    mock_db = _make_db_with_history([mmsi_change])
    _, breakdown = compute_gap_score(gap, config, db=mock_db)

    assert "mmsi_change" in breakdown, "MMSI change must fire +45"
    assert breakdown["mmsi_change"] == 45


# ── Phase 2: Detection logic fix tests ────────────────────────────────────

def test_sts_pairwise_dedup_3_vessel_cluster():
    """3-vessel cluster creates 2 STS events per vessel — only max score counts."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)

    # Simulate 2 STS events (vessel is in A-B and A-C pairs) with different scores
    sts1 = MagicMock()
    sts1.sts_id = 101
    sts1.risk_score_component = 35  # highest — in STS zone
    sts2 = MagicMock()
    sts2.sts_id = 102
    sts2.risk_score_component = 25  # lower — visible-visible

    def query_side_effect(model):
        mock_chain = MagicMock()
        from app.models.sts_transfer import StsTransferEvent
        if model is StsTransferEvent:
            mock_chain.filter.return_value.all.return_value = [sts1, sts2]
        else:
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.first.return_value = None
            mock_chain.filter.return_value.count.return_value = 0
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect

    _, bd = compute_gap_score(gap, config, db=mock_db)

    # Should have only 1 STS signal at the max value, not 2 summed
    sts_keys = [k for k in bd if k.startswith("sts_event_")]
    assert len(sts_keys) == 1, f"Expected 1 STS signal (deduped), got {len(sts_keys)}: {sts_keys}"
    assert bd[sts_keys[0]] == 35, "Should take the max STS score (35), not sum (60)"


def test_loiter_gap_loiter_full_cycle_25():
    """Loitering event with BOTH preceding and following gap → full cycle (+25)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)

    le = MagicMock()
    le.loiter_id = 1
    le.vessel_id = 1
    le.duration_hours = 8.0
    le.corridor_id = 1
    le.preceding_gap_id = 10
    le.following_gap_id = 20
    le.start_time_utc = datetime(2026, 1, 15, 10, 0)
    le.end_time_utc = datetime(2026, 1, 15, 18, 0)

    def query_side_effect(model):
        mock_chain = MagicMock()
        from app.models.loitering_event import LoiteringEvent
        if model is LoiteringEvent:
            mock_chain.filter.return_value.all.return_value = [le]
        else:
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.first.return_value = None
            mock_chain.filter.return_value.count.return_value = 0
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect

    _, bd = compute_gap_score(gap, config, db=mock_db)

    assert "loiter_gap_loiter_full_1" in bd, "Full cycle key expected"
    assert bd["loiter_gap_loiter_full_1"] == 25
    assert "loiter_gap_pattern_1" not in bd, "One-sided key should NOT be present"


def test_loiter_gap_loiter_one_sided_15():
    """Loitering event with only preceding gap → one-sided pattern (+15)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60)

    le = MagicMock()
    le.loiter_id = 2
    le.vessel_id = 1
    le.duration_hours = 8.0
    le.corridor_id = 1
    le.preceding_gap_id = 10
    le.following_gap_id = None
    le.start_time_utc = datetime(2026, 1, 15, 10, 0)
    le.end_time_utc = datetime(2026, 1, 15, 18, 0)

    def query_side_effect(model):
        mock_chain = MagicMock()
        from app.models.loitering_event import LoiteringEvent
        if model is LoiteringEvent:
            mock_chain.filter.return_value.all.return_value = [le]
        else:
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.first.return_value = None
            mock_chain.filter.return_value.count.return_value = 0
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect

    _, bd = compute_gap_score(gap, config, db=mock_db)

    assert "loiter_gap_pattern_2" in bd, "One-sided pattern key expected"
    assert bd["loiter_gap_pattern_2"] == 15
    assert "loiter_gap_loiter_full_2" not in bd, "Full cycle key should NOT be present"


def test_dark_zone_high_speed_entry_scores_suspicious():
    """High pre-gap SOG into a dark zone with short duration → entry (+20), not interior (-10)."""
    config = load_scoring_config()
    # 45-min gap, dark zone, pre-gap SOG of 22 kn (above spike threshold)
    gap = _make_gap(
        duration_minutes=45,
        in_dark_zone=True,
        dark_zone_id=1,
        impossible_speed_flag=False,
    )
    _, bd = compute_gap_score(gap, config, pre_gap_sog=22.0)

    assert "dark_zone_entry" in bd, "High-speed entry should score as suspicious"
    assert "dark_zone_deduction" not in bd, "Should NOT get interior deduction"
    assert bd["dark_zone_entry"] == 20


def test_dark_zone_slow_drift_interior_deduction():
    """Low pre-gap SOG into dark zone with short duration → interior deduction (-10)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=45,
        in_dark_zone=True,
        dark_zone_id=1,
        impossible_speed_flag=False,
    )
    _, bd = compute_gap_score(gap, config, pre_gap_sog=3.0)

    assert "dark_zone_deduction" in bd, "Slow drift should get interior deduction"
    assert bd["dark_zone_deduction"] == -10
    assert "dark_zone_entry" not in bd


def test_one_vessel_dark_increments_sts_score():
    """_apply_dark_vessel_bonus: overlapping AIS gap → sts_event.risk_score_component increases by 15."""
    from app.modules.sts_detector import _apply_dark_vessel_bonus
    from app.models.gap_event import AISGapEvent

    sts_event = MagicMock()
    sts_event.start_time_utc = datetime(2026, 1, 10, 8, 0)
    sts_event.end_time_utc = datetime(2026, 1, 10, 12, 0)
    sts_event.risk_score_component = 25  # base STS score

    mock_gap_record = MagicMock()

    def query_side_effect(model):
        mock_chain = MagicMock()
        if model is AISGapEvent:
            mock_chain.filter.return_value.first.return_value = mock_gap_record
        else:
            mock_chain.filter.return_value.first.return_value = None
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect

    config = {"sts": {"one_vessel_dark_during_proximity": 15}}
    _apply_dark_vessel_bonus(mock_db, sts_event, 1, 2, config)

    assert sts_event.risk_score_component == 40, \
        f"Expected 25 + 15 = 40, got {sts_event.risk_score_component}"


def test_one_vessel_dark_no_increment_when_no_gap():
    """_apply_dark_vessel_bonus: no overlapping gap → risk_score_component unchanged."""
    from app.modules.sts_detector import _apply_dark_vessel_bonus
    from app.models.gap_event import AISGapEvent

    sts_event = MagicMock()
    sts_event.start_time_utc = datetime(2026, 1, 10, 8, 0)
    sts_event.end_time_utc = datetime(2026, 1, 10, 12, 0)
    sts_event.risk_score_component = 25

    def query_side_effect(model):
        mock_chain = MagicMock()
        mock_chain.filter.return_value.first.return_value = None
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect

    config = {"sts": {"one_vessel_dark_during_proximity": 15}}
    _apply_dark_vessel_bonus(mock_db, sts_event, 1, 2, config)

    assert sts_event.risk_score_component == 25, "Score must not change when no gap overlaps"


def test_legitimate_trade_route_multiplier_0_7():
    """corridor_type=legitimate_trade_route → 0.7× corridor multiplier."""
    config = load_scoring_config()
    # 24h+ gap in a legitimate trade route (analyst-cleared)
    gap = _make_gap(duration_minutes=25 * 60, corridor_type="legitimate_trade_route", deadweight=None)

    _, breakdown = compute_gap_score(gap, config)

    assert breakdown["_corridor_type"] == "legitimate_trade_route"
    assert breakdown["_corridor_multiplier"] == 0.7


def test_pre_gap_sog_stored_at_detection():
    """detect_gaps_for_vessel() stores p1.sog as pre_gap_sog on the created AISGapEvent."""
    from app.modules.gap_detector import detect_gaps_for_vessel
    from app.models.ais_point import AISPoint

    base = datetime(2026, 1, 10, 0, 0)
    p1 = MagicMock()
    p1.ais_point_id = 1
    p1.vessel_id = 99
    p1.lat = 55.0
    p1.lon = 25.0
    p1.sog = 12.5  # this is what should be stored
    p1.cog = 180.0
    p1.heading = None
    p1.timestamp_utc = base
    p1.nav_status = None

    # p2 is 5 hours later → gap > GAP_MIN_HOURS (2h)
    p2 = MagicMock()
    p2.ais_point_id = 2
    p2.vessel_id = 99
    p2.lat = 55.5
    p2.lon = 25.5
    p2.sog = 0.0
    p2.cog = 0.0
    p2.heading = None
    p2.timestamp_utc = base + timedelta(hours=5)
    p2.nav_status = None

    vessel = MagicMock()
    vessel.vessel_id = 99
    vessel.deadweight = None
    vessel.vessel_type = None

    def query_side_effect(model):
        mock_chain = MagicMock()
        if model is AISPoint:
            mock_chain.filter.return_value.order_by.return_value.all.return_value = [p1, p2]
        else:
            # Existence check and all other queries return empty/None
            mock_chain.filter.return_value.first.return_value = None
            mock_chain.filter.return_value.all.return_value = []
            mock_chain.filter.return_value.count.return_value = 0
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect
    mock_db.get = MagicMock(return_value=None)

    detect_gaps_for_vessel(mock_db, vessel)

    # Find the AISGapEvent added (it has pre_gap_sog attribute)
    added_objects = [call.args[0] for call in mock_db.add.call_args_list]
    gap_events = [obj for obj in added_objects if hasattr(obj, "pre_gap_sog")]

    assert len(gap_events) >= 1, "Expected at least one AISGapEvent to be created"
    assert gap_events[0].pre_gap_sog == p1.sog, \
        f"Expected pre_gap_sog={p1.sog}, got {gap_events[0].pre_gap_sog}"


# ── Phase 5: Scoring edge cases ──────────────────────────────────────────

def test_deadweight_none_consistent_classification():
    """DWT=None vessel gets consistent speed thresholds across gap_detector and risk_scoring."""
    from app.utils.vessel import classify_vessel_speed
    from app.modules.gap_detector import _class_speed

    shared_speeds = classify_vessel_speed(None)
    detector_speeds = _class_speed(None)
    assert shared_speeds == detector_speeds, \
        f"Inconsistent: shared={shared_speeds}, detector={detector_speeds}"


def test_rescore_idempotency():
    """Scoring the same gap twice produces identical results."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=12 * 60, corridor_type="sts_zone", deadweight=150_000)

    score1, bd1 = compute_gap_score(gap, config)
    score2, bd2 = compute_gap_score(gap, config)

    assert score1 == score2
    assert bd1 == bd2


def test_zero_duration_gap():
    """Gap with 0 minutes should still score (vessel signals still apply)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=0, flag_risk="high_risk", year_built=1995)

    score, bd = compute_gap_score(gap, config)
    # Should still pick up vessel-level signals even with no duration signal
    assert "flag_high_risk" in bd or "vessel_age_25plus_high_risk" in bd


def test_no_multiplier_amplification():
    """When both multipliers are 1.0, final = additive subtotal."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, corridor_type=None, deadweight=None)

    score, bd = compute_gap_score(gap, config)

    assert bd["_corridor_multiplier"] == 1.0
    assert bd["_vessel_size_multiplier"] == 1.0
    # With both at 1.0, final should equal risk signals + legitimacy
    risk = sum(v for k, v in bd.items() if not k.startswith("_") and isinstance(v, (int, float)) and v > 0)
    legit = sum(v for k, v in bd.items() if not k.startswith("_") and isinstance(v, (int, float)) and v < 0)
    assert score == max(0, round(risk + legit))


# ── Phase 4: P&I insurance and PSC detention tests ───────────────────────

def test_pi_coverage_lapsed_adds_20():
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, pi_coverage_status="lapsed")
    _, bd = compute_gap_score(gap, config)
    assert bd.get("pi_coverage_lapsed") == 20


def test_pi_coverage_unknown_adds_5():
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, pi_coverage_status="unknown")
    _, bd = compute_gap_score(gap, config)
    assert bd.get("pi_coverage_unknown") == 5


def test_pi_coverage_active_no_signal():
    """Active P&I coverage should not add any P&I signal."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, pi_coverage_status="active")
    _, bd = compute_gap_score(gap, config)
    assert "pi_coverage_lapsed" not in bd
    assert "pi_coverage_unknown" not in bd


def test_psc_detained_adds_15():
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, psc_detained=True)
    _, bd = compute_gap_score(gap, config)
    assert bd.get("psc_detained_last_12m") == 15


def test_psc_major_deficiencies_adds_10():
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, psc_major_deficiencies=3)
    _, bd = compute_gap_score(gap, config)
    assert bd.get("psc_major_deficiencies_3_plus") == 10


def test_psc_major_deficiencies_below_threshold_no_signal():
    """Fewer than 3 major deficiencies should not trigger the signal."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=6 * 60, psc_major_deficiencies=2)
    _, bd = compute_gap_score(gap, config)
    assert "psc_major_deficiencies_3_plus" not in bd
