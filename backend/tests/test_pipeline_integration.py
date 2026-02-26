"""Integration tests — validates data flow across multiple components.

Tests scoring pipeline composition, speed subsumption, multiplier behavior,
interpolation method selection, vessel filtering, config consistency,
evidence export blocking, and scoring date reproducibility.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import pytest

from app.modules.risk_scoring import compute_gap_score, load_scoring_config


# ---------------------------------------------------------------------------
# Helpers (matches pattern from test_risk_scoring_complete.py)
# ---------------------------------------------------------------------------

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
    pre_gap_sog=None,
    vessel_laid_up_30d=False,
    vessel_laid_up_60d=False,
    vessel_laid_up_in_sts_zone=False,
    pi_coverage_status="active",
    psc_detained=False,
    psc_major_deficiencies=0,
    corridor_risk_weight=None,
    is_jamming_zone=False,
):
    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk
    vessel.year_built = year_built
    vessel.ais_class = ais_class
    vessel.flag = flag
    vessel.mmsi_first_seen_utc = mmsi_first_seen_utc
    vessel.vessel_laid_up_30d = vessel_laid_up_30d
    vessel.vessel_laid_up_60d = vessel_laid_up_60d
    vessel.vessel_laid_up_in_sts_zone = vessel_laid_up_in_sts_zone
    vessel.pi_coverage_status = pi_coverage_status
    vessel.psc_detained_last_12m = psc_detained
    vessel.psc_major_deficiencies_last_12m = psc_major_deficiencies

    corridor = None
    if corridor_type is not None:
        corridor = MagicMock()
        corridor.corridor_type = corridor_type
        corridor.risk_weight = corridor_risk_weight or 1.0
        corridor.is_jamming_zone = is_jamming_zone

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
    gap.pre_gap_sog = pre_gap_sog
    gap.gap_start_utc = datetime(2026, 1, 15, 12, 0)
    gap.gap_end_utc = datetime(2026, 1, 16, 12, 0)
    return gap


# ---------------------------------------------------------------------------
# Full Scoring Pipeline Integration
# ---------------------------------------------------------------------------

class TestScoringPipelineComposition:
    """Tests that the full scoring pipeline correctly composes signals
    from multiple categories and applies multipliers correctly."""

    def test_gap_duration_is_primary_signal(self):
        """A 26h gap should always produce a gap_duration signal."""
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=1560)  # 26h
        score, breakdown = compute_gap_score(gap, config)
        assert any("gap_duration" in k for k in breakdown), \
            f"Expected gap_duration in breakdown, got: {list(breakdown.keys())}"

    def test_score_never_goes_below_zero(self):
        """Even with many legitimacy deductions, final score should be >= 0."""
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=200, flag_risk="low", year_built=2024)
        score, breakdown = compute_gap_score(gap, config)
        assert score >= 0, f"Score should never be negative, got {score}"

    def test_breakdown_is_dict_with_string_keys(self):
        """Every breakdown should have string keys and numeric/string values."""
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=1560, year_built=2003)
        score, breakdown = compute_gap_score(gap, config)
        assert isinstance(breakdown, dict)
        for k, v in breakdown.items():
            assert isinstance(k, str), f"Key {k} is not a string"
            assert isinstance(v, (int, float, str)), f"Value for {k} is {type(v)}"

    def test_multiple_signals_compose_additively(self):
        """Gap duration + speed impossible should both appear in breakdown."""
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=1560, pre_gap_sog=35.0, deadweight=80000)
        score, breakdown = compute_gap_score(gap, config)
        has_duration = any("gap_duration" in k for k in breakdown)
        has_speed = "speed_impossible" in breakdown
        assert has_duration, "Expected gap_duration signal"
        assert has_speed, "Expected speed_impossible signal"
        assert score > breakdown.get("speed_impossible", 0), \
            "Total should exceed any single signal"


# ---------------------------------------------------------------------------
# Speed Impossible vs Spoof Subsumption
# ---------------------------------------------------------------------------

class TestSpeedSubsumption:
    """Speed impossible (>30kn) should supersede spoof; only one should fire."""

    def test_impossible_supersedes_spoof(self):
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=1560, pre_gap_sog=35.0, deadweight=80000)
        score, breakdown = compute_gap_score(gap, config)
        assert "speed_impossible" in breakdown
        assert "speed_spoof" not in breakdown
        assert "speed_spike_before_gap" not in breakdown

    def test_spoof_does_not_trigger_impossible(self):
        config = load_scoring_config()
        # 25kn is in spoof range for tanker (threshold ~24kn)
        gap = _make_gap(duration_minutes=1560, pre_gap_sog=25.0, deadweight=80000)
        score, breakdown = compute_gap_score(gap, config)
        assert "speed_impossible" not in breakdown

    def test_impossible_does_not_trigger_duration_bonus(self):
        """Speed impossible should NOT trigger the 1.4× gap duration bonus."""
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=1560, pre_gap_sog=35.0, deadweight=80000)
        score, breakdown = compute_gap_score(gap, config)
        assert "gap_duration_speed_spike_bonus" not in breakdown, \
            "Impossible speed should not trigger the 1.4× duration bonus"


# ---------------------------------------------------------------------------
# Dark Zone Scoring Integration
# ---------------------------------------------------------------------------

class TestDarkZoneIntegration:
    """Dark zone gaps get -10 deduction when gap is entirely within a jamming zone."""

    def test_interior_dark_zone_gap_gets_deduction(self):
        config = load_scoring_config()
        gap = _make_gap(
            duration_minutes=1560,
            in_dark_zone=True,
            corridor_type="dark_zone",
            is_jamming_zone=True,
        )
        score, breakdown = compute_gap_score(gap, config)
        dark_keys = [k for k in breakdown if "dark" in k.lower() or "jamming" in k.lower()]
        has_deduction = any(
            isinstance(breakdown.get(k), (int, float)) and breakdown[k] < 0
            for k in dark_keys
        )
        assert has_deduction, f"Expected dark zone deduction, got: {breakdown}"


# ---------------------------------------------------------------------------
# Gap Frequency Subsumption
# ---------------------------------------------------------------------------

class TestGapFrequencySubsumption:
    """The highest frequency tier should fire — not all of them."""

    def test_30d_frequency_subsumes_14d_and_7d(self):
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=6 * 60)
        _, breakdown = compute_gap_score(gap, config, gaps_in_7d=2, gaps_in_14d=3, gaps_in_30d=5)
        freq_keys = [k for k in breakdown if "frequency" in k.lower()]
        assert len(freq_keys) == 1, f"Expected one frequency signal, got: {freq_keys}"
        assert "30d" in freq_keys[0]


# ---------------------------------------------------------------------------
# Vessel Size Multiplier
# ---------------------------------------------------------------------------

class TestVesselSizeMultiplier:
    """Large vessels (>100k DWT) should get higher scores."""

    def test_large_vessel_amplification(self):
        config = load_scoring_config()
        gap_large = _make_gap(duration_minutes=1560, deadweight=150000)
        gap_medium = _make_gap(duration_minutes=1560, deadweight=50000)
        score_large, _ = compute_gap_score(gap_large, config)
        score_medium, _ = compute_gap_score(gap_medium, config)
        assert score_large >= score_medium, \
            f"Large vessel ({score_large}) should score >= medium ({score_medium})"


# ---------------------------------------------------------------------------
# Corridor Multiplier
# ---------------------------------------------------------------------------

class TestCorridorMultiplier:
    """Corridor multiplier amplifies positive signals."""

    def test_sts_zone_corridor_amplifies(self):
        config = load_scoring_config()
        gap_sts = _make_gap(duration_minutes=1560, corridor_type="sts_zone", corridor_risk_weight=2.0)
        gap_none = _make_gap(duration_minutes=1560)
        score_sts, _ = compute_gap_score(gap_sts, config)
        score_none, _ = compute_gap_score(gap_none, config)
        assert score_sts >= score_none, \
            f"STS zone ({score_sts}) should score >= no corridor ({score_none})"


# ---------------------------------------------------------------------------
# Legitimacy Signals
# ---------------------------------------------------------------------------

class TestLegitimacySignals:
    """Legitimacy deductions should reduce the score."""

    def test_low_flag_risk_deducts(self):
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=1560, flag_risk="low_risk")
        score, breakdown = compute_gap_score(gap, config)
        has_deduction = any(v < 0 for v in breakdown.values() if isinstance(v, (int, float)))
        assert has_deduction, "Expected legitimacy deduction for low-risk flag"

    def test_young_vessel_age_deducts(self):
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=1560, year_built=2024)
        _, breakdown = compute_gap_score(gap, config)
        age_key = [k for k in breakdown if "age" in k.lower() and "0_10" in k]
        assert len(age_key) > 0, "Expected age deduction for vessel built in 2024"
        assert breakdown[age_key[0]] < 0


# ---------------------------------------------------------------------------
# Evidence Export Blocking
# ---------------------------------------------------------------------------

class TestEvidenceExportBlocking:
    """Evidence export should be blocked for status='new' alerts (NFR7)."""

    def test_export_blocked_for_new_status(self):
        from app.modules.evidence_export import export_evidence_card
        db = MagicMock()
        gap = MagicMock()
        gap.status = "new"
        gap.gap_event_id = 100
        db.query.return_value.filter.return_value.first.return_value = gap
        result = export_evidence_card(100, "json", db)
        assert "error" in result


# ---------------------------------------------------------------------------
# Interpolation Method Selection
# ---------------------------------------------------------------------------

class TestInterpolationMethodSelection:
    """Movement envelope should select correct interpolation method based on gap duration."""

    def test_short_gap_uses_linear(self):
        from app.utils.interpolation import interpolate_linear
        positions, ellipse = interpolate_linear(
            start_lat=25.0, start_lon=55.0,
            end_lat=25.1, end_lon=55.1,
            duration_h=1.5,
        )
        assert len(positions) == 2
        assert ellipse is None

    def test_medium_gap_uses_hermite(self):
        from app.utils.interpolation import interpolate_hermite
        positions, ellipse = interpolate_hermite(
            start_lat=25.0, start_lon=55.0,
            end_lat=26.0, end_lon=56.0,
            start_sog=10.0, start_cog=45.0,
            end_sog=10.0, end_cog=45.0,
            duration_h=4.0,
        )
        assert len(positions) >= 10
        assert ellipse is not None
        assert "POLYGON" in ellipse

    def test_long_gap_uses_scenarios(self):
        from app.utils.interpolation import interpolate_scenarios
        positions, hull_wkt = interpolate_scenarios(
            start_lat=25.0, start_lon=55.0,
            end_lat=27.0, end_lon=57.0,
            start_sog=10.0, start_cog=45.0,
            max_speed_kn=15.0,
            duration_h=10.0,
        )
        assert len(positions) >= 2
        assert hull_wkt is not None
        assert "POLYGON" in hull_wkt


# ---------------------------------------------------------------------------
# Vessel Filter Config
# ---------------------------------------------------------------------------

class TestVesselFilterIntegration:
    """Vessel type filtering should work based on type keywords."""

    def test_tanker_detection_by_type(self):
        import app.utils.vessel_filter as vf
        vf._FILTER_CONFIG = None
        vessel = MagicMock()
        vessel.vessel_type = "Crude Oil Tanker"
        vessel.deadweight = 50000
        vessel.mmsi = "123456789"
        assert vf.is_tanker_type(vessel) is True

    def test_non_tanker_rejected(self):
        import app.utils.vessel_filter as vf
        vf._FILTER_CONFIG = None
        vessel = MagicMock()
        vessel.vessel_type = "Container Ship"
        vessel.deadweight = 5000
        vessel.mmsi = "987654321"
        assert vf.is_tanker_type(vessel) is False

    def test_tanker_by_dwt_fallback(self):
        import app.utils.vessel_filter as vf
        vf._FILTER_CONFIG = None
        vessel = MagicMock()
        vessel.vessel_type = None
        vessel.deadweight = 200000
        vessel.mmsi = "111222333"
        result = vf.is_tanker_type(vessel)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# YAML Config Consistency
# ---------------------------------------------------------------------------

class TestYAMLConfigConsistency:
    """All scoring signals should have a YAML config entry."""

    def test_all_config_sections_present(self):
        import yaml
        from pathlib import Path
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "risk_scoring.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        required_sections = [
            "gap_duration", "spoofing", "metadata", "legitimacy",
            "dark_zone", "corridor", "sts", "behavioral", "watchlist",
        ]
        for section in required_sections:
            assert section in config, f"Missing required config section: {section}"

    def test_gap_duration_keys_present(self):
        import yaml
        from pathlib import Path
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "risk_scoring.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        gd = config.get("gap_duration", {})
        expected_keys = ["2h_to_4h", "4h_to_8h", "8h_to_12h", "12h_to_24h", "24h_plus"]
        for key in expected_keys:
            assert key in gd, f"Missing gap_duration key: {key}"


# ---------------------------------------------------------------------------
# Scoring Date Reproducibility (NFR3)
# ---------------------------------------------------------------------------

class TestScoringDateReproducibility:
    """compute_gap_score should accept scoring_date param for NFR3 reproducibility."""

    def test_scoring_date_param_accepted(self):
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=1560, year_built=2003)
        score, breakdown = compute_gap_score(
            gap, config, scoring_date=datetime(2026, 1, 20)
        )
        assert isinstance(score, int)
        assert isinstance(breakdown, dict)
        assert any("gap_duration" in k for k in breakdown)

    def test_scoring_date_affects_age_calculation(self):
        """Different scoring dates should produce different age calculations."""
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=360, year_built=2001)
        _, bd_2026 = compute_gap_score(gap, config, scoring_date=datetime(2026, 6, 1))
        _, bd_2028 = compute_gap_score(gap, config, scoring_date=datetime(2028, 6, 1))
        # In 2026 age=25 (age_20_25y); in 2028 age=27 (age_25_plus)
        age_keys_2026 = [k for k in bd_2026 if "age" in k.lower()]
        age_keys_2028 = [k for k in bd_2028 if "age" in k.lower()]
        assert age_keys_2026 != age_keys_2028 or \
            any(bd_2026.get(k) != bd_2028.get(k) for k in set(age_keys_2026 + age_keys_2028))


# ---------------------------------------------------------------------------
# Port Detector Data Flow
# ---------------------------------------------------------------------------

class TestPortDetectorDataFlow:
    def test_port_call_detection_interface(self):
        from app.modules.port_detector import run_port_call_detection
        import inspect
        sig = inspect.signature(run_port_call_detection)
        params = list(sig.parameters.keys())
        assert "db" in params
