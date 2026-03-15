"""Tests for speed_spike_gap_multiplier_enabled config flag.

The flag gates the 1.4x gap_duration bonus that fires when a speed spike/spoof
precedes a gap. When disabled (default in YAML), the bonus must not appear.
When enabled, backward-compatible behavior is preserved.
"""

from copy import deepcopy
from datetime import datetime
from unittest.mock import MagicMock

from app.modules.risk_scoring import compute_gap_score, load_scoring_config


def _make_gap(duration_minutes=360, deadweight=100_000):
    """Create a mock GapEvent matching the structure expected by compute_gap_score."""
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = "unknown"
    vessel.year_built = None
    vessel.ais_class = "unknown"
    vessel.flag = None
    vessel.mmsi_first_seen_utc = None
    vessel.vessel_laid_up_30d = False
    vessel.vessel_laid_up_60d = False
    vessel.vessel_laid_up_in_sts_zone = False
    vessel.pi_coverage_status = "active"
    vessel.psc_detained_last_12m = False
    vessel.psc_major_deficiencies_last_12m = 0
    vessel.vessel_id = 1

    gap = MagicMock()
    gap.gap_event_id = 1
    gap.vessel_id = 1
    gap.duration_minutes = duration_minutes
    gap.impossible_speed_flag = False
    gap.velocity_plausibility_ratio = None
    gap.in_dark_zone = False
    gap.dark_zone_id = None
    gap.vessel = vessel
    gap.corridor = None
    gap.gap_start_utc = datetime(2026, 1, 15, 12, 0)
    gap.gap_end_utc = datetime(2026, 1, 16, 12, 0)
    return gap


class TestMultiplierDisabledByDefault:
    """With default YAML config (speed_spike_gap_multiplier_enabled: false),
    the gap_duration_speed_spike_bonus must not appear."""

    def test_no_bonus_with_speed_spike_precedes(self):
        config = load_scoring_config()
        assert config["speed_anomaly"].get("speed_spike_gap_multiplier_enabled") is False
        gap = _make_gap(duration_minutes=6 * 60)
        _, breakdown = compute_gap_score(gap, config, speed_spike_precedes=True)
        assert "gap_duration_speed_spike_bonus" not in breakdown

    def test_no_bonus_with_pre_gap_sog_above_spike(self):
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=6 * 60)
        _, breakdown = compute_gap_score(gap, config, pre_gap_sog=21.0)
        assert "speed_spike_before_gap" in breakdown, "Spike signal still fires"
        assert "gap_duration_speed_spike_bonus" not in breakdown

    def test_no_bonus_with_pre_gap_sog_above_spoof(self):
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=6 * 60)
        _, breakdown = compute_gap_score(gap, config, pre_gap_sog=25.0)
        assert "speed_spoof_before_gap" in breakdown, "Spoof signal still fires"
        assert "gap_duration_speed_spike_bonus" not in breakdown


class TestMultiplierEnabledOverride:
    """When speed_spike_gap_multiplier_enabled is explicitly True,
    the bonus must appear (backward compat)."""

    def test_bonus_present_when_enabled(self):
        config = deepcopy(load_scoring_config())
        config["speed_anomaly"]["speed_spike_gap_multiplier_enabled"] = True
        gap = _make_gap(duration_minutes=6 * 60)
        _, breakdown = compute_gap_score(gap, config, pre_gap_sog=21.0)
        assert "speed_spike_before_gap" in breakdown
        assert "gap_duration_speed_spike_bonus" in breakdown

    def test_bonus_present_for_spoof_when_enabled(self):
        config = deepcopy(load_scoring_config())
        config["speed_anomaly"]["speed_spike_gap_multiplier_enabled"] = True
        gap = _make_gap(duration_minutes=6 * 60)
        _, breakdown = compute_gap_score(gap, config, pre_gap_sog=25.0)
        assert "speed_spoof_before_gap" in breakdown
        assert "gap_duration_speed_spike_bonus" in breakdown


class TestSpeedSignalsIndependentOfMultiplier:
    """Speed spike/spoof signals must be scored regardless of multiplier setting."""

    def test_spike_signal_scored_when_disabled(self):
        config = load_scoring_config()
        assert config["speed_anomaly"].get("speed_spike_gap_multiplier_enabled") is False
        gap = _make_gap(duration_minutes=6 * 60)
        _, breakdown = compute_gap_score(gap, config, pre_gap_sog=21.0)
        assert "speed_spike_before_gap" in breakdown
        assert breakdown["speed_spike_before_gap"] == 8

    def test_spoof_signal_scored_when_disabled(self):
        config = load_scoring_config()
        gap = _make_gap(duration_minutes=6 * 60)
        _, breakdown = compute_gap_score(gap, config, pre_gap_sog=25.0)
        assert "speed_spoof_before_gap" in breakdown
        assert breakdown["speed_spoof_before_gap"] == 25
