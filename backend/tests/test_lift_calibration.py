"""Tests for lift-based signal weight calibration suggestions."""

from __future__ import annotations

from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers — build mock live_signal_effectiveness() return values
# ---------------------------------------------------------------------------


def _make_effectiveness(signals: dict[str, tuple[int, int]]) -> list[dict]:
    """Build a list matching live_signal_effectiveness() output.

    Args:
        signals: mapping of signal_name -> (tp_count, fp_count)
    """
    total_tp = sum(tp for tp, _ in signals.values())
    total_fp = sum(fp for _, fp in signals.values())
    results = []
    for name, (tp, fp) in signals.items():
        tp_freq = tp / max(1, total_tp)
        fp_freq = fp / max(1, total_fp)
        if fp_freq > 0:
            lift = tp_freq / fp_freq
        elif tp_freq > 0:
            lift = "inf"
        else:
            lift = 0
        results.append({
            "signal": name,
            "tp_count": tp,
            "fp_count": fp,
            "tp_freq": round(tp_freq, 4),
            "fp_freq": round(fp_freq, 4),
            "lift": round(lift, 2) if isinstance(lift, float) else lift,
        })
    return results


def _mock_config():
    """Return a minimal scoring config dict for tests."""
    return {
        "gap_duration": {
            "2h_to_4h": 5,
            "4h_to_8h": 12,
            "8h_to_12h": 25,
            "12h_to_24h": 40,
            "24h_plus": 55,
        },
        "speed_anomaly": {
            "speed_spike": 8,
            "speed_spoof": 25,
            "speed_impossible": 40,
        },
        "spoofing": {
            "anchor_in_open_ocean": 10,
            "circle_pattern": 35,
        },
        "watchlist": {
            "vessel_on_ofac_sdn_list": 50,
            "vessel_on_eu_sanctions_list": 50,
            "vessel_on_kse_shadow_fleet_list": 30,
        },
        "flag_state": {
            "high_risk_registry": 15,
        },
        "movement_envelope": {
            "impossible_reappear": 40,
        },
        "metadata": {
            "callsign_change": 20,
        },
    }


# Patch targets
_PATCH_EFFECTIVENESS = "app.modules.validation_harness.live_signal_effectiveness"
_PATCH_CONFIG = "app.modules.scoring_config.load_scoring_config"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyOrInsufficient:
    """Empty DB or insufficient verdicts should return empty list."""

    def test_empty_effectiveness_returns_empty(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        with (
            patch(_PATCH_EFFECTIVENESS, return_value=[]),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)
        assert result == []

    def test_insufficient_total_verdicts_returns_empty(self):
        """Less than 20 total verdicts -> empty."""
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        # 5 TP + 5 FP = 10 total < 20
        signals = {"gap_duration_2h_4h": (5, 5)}
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)
        assert result == []


class TestLowLift:
    """Low lift (< 0.8) should suggest reduction."""

    def test_low_lift_suggests_reduction(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        # lift = tp_freq/fp_freq. Make signal fire mostly on FP.
        # tp_count=2 out of 12 total TP, fp_count=10 out of 12 total FP
        # tp_freq = 2/12 = 0.167, fp_freq = 10/12 = 0.833 -> lift ~ 0.2
        signals = {
            "gap_duration_8h_12h": (2, 10),
            "speed_spike_before_gap": (10, 2),  # padding to reach 20 verdicts
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        gap_suggestions = [s for s in result if s["signal"] == "gap_duration_8h_12h"]
        assert len(gap_suggestions) == 1
        s = gap_suggestions[0]
        assert s["direction"] == "reduce"
        assert s["suggested_adjustment_pct"] < 0
        assert s["configurable"] is True
        assert s["config_path"] == "gap_duration.8h_to_12h"
        assert s["current_weight"] == 25


class TestHighLift:
    """High lift (> 2.0) should suggest increase."""

    def test_high_lift_suggests_increase(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        # tp_count=10, fp_count=1 -> lift = (10/11) / (1/11) = 10.0
        signals = {
            "speed_spike_before_gap": (10, 1),
            "gap_duration_2h_4h": (1, 10),  # padding
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        speed_suggestions = [s for s in result if s["signal"] == "speed_spike_before_gap"]
        assert len(speed_suggestions) == 1
        s = speed_suggestions[0]
        assert s["direction"] == "increase"
        assert s["suggested_adjustment_pct"] > 0
        assert s["current_weight"] == 8


class TestNormalLift:
    """Normal lift (0.8 <= lift <= 2.0) produces no suggestion."""

    def test_normal_lift_no_suggestion(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        # tp_count=6, fp_count=6 -> lift = 1.0
        signals = {
            "gap_duration_2h_4h": (6, 6),
            "speed_spike_before_gap": (6, 6),  # padding
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        assert result == []


class TestAdjustmentCap:
    """Adjustment capped at 15%."""

    def test_increase_capped_at_15(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        # Extremely high lift
        signals = {
            "speed_spike_before_gap": (15, 0),
            "gap_duration_2h_4h": (0, 15),  # padding for FP
        }
        # tp_freq for speed = 15/15 = 1.0, fp_freq = 0/15 -> inf lift
        # But with 0 FP the lift computation handles inf
        # Let's use small FP count instead
        signals = {
            "speed_spike_before_gap": (14, 1),
            "gap_duration_2h_4h": (1, 14),
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        speed_suggestions = [s for s in result if s["signal"] == "speed_spike_before_gap"]
        assert len(speed_suggestions) == 1
        assert speed_suggestions[0]["suggested_adjustment_pct"] <= 15.0

    def test_reduction_capped_at_15(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        signals = {
            "gap_duration_24h_plus": (1, 14),
            "speed_spike_before_gap": (14, 1),
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        gap_suggestions = [s for s in result if s["signal"] == "gap_duration_24h_plus"]
        assert len(gap_suggestions) == 1
        assert gap_suggestions[0]["suggested_adjustment_pct"] >= -15.0


class TestWeightFloor:
    """Reduction never goes below 50% of configured weight."""

    def test_floor_enforced(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        # Use a small weight (5) so 50% floor = 2.5 rounds to 2
        # A 15% reduction of 5 = 4.25 which is above floor, so test with very low weight
        config = _mock_config()
        config["gap_duration"]["2h_to_4h"] = 4  # floor = 2

        signals = {
            "gap_duration_2h_4h": (1, 14),
            "speed_spike_before_gap": (14, 1),
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=config),
        ):
            result = generate_lift_based_suggestions(db=None)

        gap_suggestions = [s for s in result if s["signal"] == "gap_duration_2h_4h"]
        assert len(gap_suggestions) == 1
        s = gap_suggestions[0]
        # min_weight_floor should be round(4 * 0.5) = 2
        assert s["min_weight_floor"] == 2
        # Proposed weight = 4 * (1 + adj/100) should be >= 2
        if s["suggested_adjustment_pct"] is not None:
            proposed = s["current_weight"] * (1 + s["suggested_adjustment_pct"] / 100.0)
            assert proposed >= s["min_weight_floor"]


class TestMetadataKeysExcluded:
    """Metadata keys starting with _ must never appear in suggestions."""

    def test_underscore_keys_excluded(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        signals = {
            "_final_score": (10, 10),
            "_corridor_multiplier": (10, 10),
            "_vessel_size_multiplier": (10, 10),
            "_additive_subtotal": (10, 10),
            "gap_duration_8h_12h": (2, 10),
            "speed_spike_before_gap": (10, 2),
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        signal_names = {s["signal"] for s in result}
        assert "_final_score" not in signal_names
        assert "_corridor_multiplier" not in signal_names
        assert "_vessel_size_multiplier" not in signal_names
        assert "_additive_subtotal" not in signal_names


class TestDynamicKeyGrouping:
    """Dynamic per-event keys should be grouped by stripping numeric suffixes."""

    def test_loitering_events_grouped(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        # loitering_201 and loitering_202 -> merged into "loitering"
        signals = {
            "loitering_201": (5, 1),
            "loitering_202": (5, 1),
            "gap_duration_2h_4h": (1, 10),  # padding
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        signal_names = [s["signal"] for s in result]
        # Should not have loitering_201 or loitering_202 separately
        assert "loitering_201" not in signal_names
        assert "loitering_202" not in signal_names
        # The grouped "loitering" signal should appear (if lift is outside normal range)
        loitering_suggestions = [s for s in result if s["signal"] == "loitering"]
        # Combined: tp=10, fp=2 -> lift should be high
        # loitering is not in _SIGNAL_CONFIG_MAP so configurable=false
        if loitering_suggestions:
            assert loitering_suggestions[0]["configurable"] is False


class TestNonConfigurableSignals:
    """Signals without YAML mapping reported with configurable=false."""

    def test_non_configurable_signal(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        signals = {
            "some_unknown_signal": (1, 14),
            "speed_spike_before_gap": (14, 1),
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        unknown = [s for s in result if s["signal"] == "some_unknown_signal"]
        assert len(unknown) == 1
        assert unknown[0]["configurable"] is False
        assert unknown[0]["current_weight"] is None
        assert unknown[0]["suggested_adjustment_pct"] is None
        assert unknown[0]["config_path"] is None


class TestScheduledCalibrationIncludesLift:
    """run_scheduled_calibration includes lift_suggestions key."""

    def test_lift_suggestions_in_scheduled(self):
        from app.modules.fp_rate_tracker import run_scheduled_calibration

        with (
            patch("app.modules.fp_rate_tracker.generate_per_signal_suggestions", return_value=[]),
            patch("app.modules.fp_rate_tracker.generate_lift_based_suggestions", return_value=[{"signal": "test"}]),
            patch("app.config.settings") as mock_settings,
        ):
            mock_settings.AUTO_CALIBRATION_ENABLED = True
            result = run_scheduled_calibration(db=None)

        assert "lift_suggestions" in result
        assert result["lift_suggestions"] == [{"signal": "test"}]
        assert result["status"] == "ok"


class TestPerSignalMinimum:
    """Per-signal minimum 5 verdicts enforced."""

    def test_signal_below_minimum_excluded(self):
        from app.modules.fp_rate_tracker import generate_lift_based_suggestions

        # Signal with only 3 verdicts total (below the 5 minimum)
        signals = {
            "gap_duration_8h_12h": (1, 2),  # 3 total < 5 minimum
            "speed_spike_before_gap": (10, 10),  # padding for 20 min
        }
        with (
            patch(_PATCH_EFFECTIVENESS, return_value=_make_effectiveness(signals)),
            patch(_PATCH_CONFIG, return_value=_mock_config()),
        ):
            result = generate_lift_based_suggestions(db=None)

        # gap_duration_8h_12h should not appear due to per-signal minimum
        signal_names = [s["signal"] for s in result]
        assert "gap_duration_8h_12h" not in signal_names
