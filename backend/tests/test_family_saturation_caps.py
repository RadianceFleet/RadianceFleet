"""Tests for signal family saturation caps (_apply_family_caps).

Verifies that per-family caps prevent any single signal family from
dominating the score, while leaving other families untouched.
"""

from __future__ import annotations

from app.modules.risk_scoring import (
    _CAP_FAMILY_BEHAVIORAL,
    _CAP_FAMILY_GAP_AND_SPEED,
    _apply_family_caps,
)


def _base_config(enabled=True, **overrides):
    """Return a config dict with family_caps section."""
    caps = {
        "enabled": enabled,
        "gap_and_speed": 55,
        "spoofing_and_position": 60,
        "identity_and_ownership": 50,
        "voyage_and_sts": 50,
        "satellite_and_dark": 45,
        "watchlist": 60,
        "behavioral": 40,
    }
    caps.update(overrides)
    return {"family_caps": caps}


class TestFamilyCapApplied:
    """Cap applied when family exceeds threshold."""

    def test_gap_and_speed_capped(self):
        breakdown = {
            "gap_duration_24h_plus": 55,
            "speed_impossible": 40,
            "gap_frequency_5_in_30d": 50,
        }
        config = _base_config()
        55 + 40 + 50  # 145
        _apply_family_caps(breakdown, config)

        capped_total = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert capped_total <= 55
        assert "_family_cap_applied_gap_and_speed" in breakdown
        meta = breakdown["_family_cap_applied_gap_and_speed"]
        assert meta["original"] == 145
        assert meta["capped_to"] == 55

    def test_behavioral_capped(self):
        breakdown = {
            "behavioral_deviation_3sigma": 40,
            "ais_reporting_anomaly": 25,
        }
        config = _base_config()
        _apply_family_caps(breakdown, config)

        capped_total = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert capped_total <= 40
        assert "_family_cap_applied_behavioral" in breakdown


class TestFamilyCapNotApplied:
    """Cap NOT applied when below threshold."""

    def test_below_threshold_unchanged(self):
        breakdown = {
            "gap_duration_2h_4h": 5,
            "speed_spike_before_gap": 8,
        }
        config = _base_config()
        original = dict(breakdown)
        _apply_family_caps(breakdown, config)

        # Values should be unchanged
        assert breakdown["gap_duration_2h_4h"] == original["gap_duration_2h_4h"]
        assert breakdown["speed_spike_before_gap"] == original["speed_spike_before_gap"]
        assert "_family_cap_applied_gap_and_speed" not in breakdown


class TestMultipleFamiliesCapped:
    """Multiple families capped simultaneously."""

    def test_two_families_both_capped(self):
        breakdown = {
            # gap_and_speed: total 110 > cap 55
            "gap_duration_24h_plus": 55,
            "speed_impossible": 40,
            "gap_frequency_4_in_30d": 15,
            # behavioral: total 65 > cap 40
            "behavioral_deviation_3sigma": 40,
            "ais_reporting_anomaly": 25,
        }
        config = _base_config()
        _apply_family_caps(breakdown, config)

        gap_total = sum(
            v for k, v in breakdown.items()
            if k in _CAP_FAMILY_GAP_AND_SPEED and isinstance(v, (int, float)) and v > 0
        )
        behavioral_total = sum(
            v for k, v in breakdown.items()
            if k in _CAP_FAMILY_BEHAVIORAL and isinstance(v, (int, float)) and v > 0
        )
        # Allow +/-2 rounding variance
        assert gap_total <= 55 + 2
        assert behavioral_total <= 40 + 2
        assert "_family_cap_applied_gap_and_speed" in breakdown
        assert "_family_cap_applied_behavioral" in breakdown


class TestNegativeSignalsUnaffected:
    """Negative (legitimacy) signals unaffected by caps."""

    def test_legitimacy_signals_not_capped(self):
        breakdown = {
            # Positive identity signals over cap
            "imo_fabricated": 40,
            "stateless_mmsi": 30,
            # Legitimacy (negative) signals in same family area
            "legitimacy_gap_free_90d": -10,
            "legitimacy_ais_class_a_consistent": -5,
            "flag_white_list": -10,
        }
        config = _base_config()
        _apply_family_caps(breakdown, config)

        # Negative signals must remain unchanged
        assert breakdown["legitimacy_gap_free_90d"] == -10
        assert breakdown["legitimacy_ais_class_a_consistent"] == -5
        assert breakdown["flag_white_list"] == -10


class TestTraceabilityMetadata:
    """Traceability metadata present with original and capped totals."""

    def test_metadata_has_original_and_capped(self):
        breakdown = {
            "dark_zone_entry": 20,
            "dark_vessel_unmatched_in_corridor": 35,
        }
        config = _base_config()
        _apply_family_caps(breakdown, config)

        assert "_family_cap_applied_satellite_and_dark" in breakdown
        meta = breakdown["_family_cap_applied_satellite_and_dark"]
        assert meta["original"] == 55
        assert meta["capped_to"] == 45


class TestCapsBeforeMultiplier:
    """Cap happens before multiplier application (family total reduced)."""

    def test_risk_signals_reduced_after_cap(self):
        breakdown = {
            "gap_duration_24h_plus": 55,
            "speed_impossible": 40,
            "impossible_reappear": 40,
            # Other family signals (below their cap)
            "dark_zone_entry": 10,
        }
        config = _base_config()
        total_before = sum(
            v for v in breakdown.values() if isinstance(v, (int, float)) and v > 0
        )
        _apply_family_caps(breakdown, config)
        total_after = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert total_after < total_before


class TestDisabledConfig:
    """Config enabled: false bypasses entirely."""

    def test_disabled_bypasses_all(self):
        breakdown = {
            "gap_duration_24h_plus": 55,
            "speed_impossible": 40,
            "gap_frequency_5_in_30d": 50,
        }
        original = dict(breakdown)
        config = _base_config(enabled=False)
        _apply_family_caps(breakdown, config)

        # Nothing changed
        assert breakdown == original

    def test_missing_config_section_bypasses(self):
        breakdown = {
            "gap_duration_24h_plus": 55,
            "speed_impossible": 40,
        }
        original = dict(breakdown)
        _apply_family_caps(breakdown, {})
        assert breakdown == original


class TestSignalsOutsideOldPillars:
    """Signals outside old pillar sets ARE capped."""

    def test_gap_duration_signals_capped(self):
        """gap_duration signals are NOT in any old pillar but ARE in cap families."""
        breakdown = {
            "gap_duration_24h_plus": 55,
            "gap_duration_speed_spike_bonus": 20,
            "gap_frequency_5_in_30d": 50,
        }
        config = _base_config()
        _apply_family_caps(breakdown, config)

        total = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert total <= 55

    def test_dark_zone_signals_capped(self):
        """dark_zone signals capped in satellite_and_dark family."""
        breakdown = {
            "dark_zone_entry": 20,
            "dark_zone_exit_impossible": 35,
        }
        config = _base_config()
        _apply_family_caps(breakdown, config)

        total = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert total <= 45


class TestRoundingVariance:
    """Rounding variance stays within +/-2 pts of target cap."""

    def test_rounding_within_tolerance(self):
        breakdown = {
            "gap_duration_24h_plus": 55,
            "speed_impossible": 40,
            "gap_frequency_5_in_30d": 50,
            "impossible_reappear": 40,
            "near_impossible_reappear": 15,
        }
        config = _base_config()
        _apply_family_caps(breakdown, config)

        # Sum the capped family signals
        capped_total = sum(
            v for k, v in breakdown.items()
            if k in _CAP_FAMILY_GAP_AND_SPEED and isinstance(v, (int, float)) and v > 0
        )
        cap = 55
        assert abs(capped_total - cap) <= 2, (
            f"Capped total {capped_total} deviates >2 from cap {cap}"
        )


class TestDynamicSuffixSignals:
    """Dynamic-suffix signals (like loitering_201) are matched to correct family."""

    def test_loitering_dynamic_key(self):
        breakdown = {
            "loitering_201": 20,
            "loiter_gap_loiter_full_42": 25,
            "sts_event_7": 30,
        }
        config = _base_config(voyage_and_sts=40)
        _apply_family_caps(breakdown, config)

        total = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert total <= 40
        assert "_family_cap_applied_voyage_and_sts" in breakdown

    def test_watchlist_dynamic_key(self):
        """watchlist_OFAC_SDN, watchlist_EU_COUNCIL matched by prefix."""
        breakdown = {
            "watchlist_OFAC_SDN": 50,
            "watchlist_EU_COUNCIL": 50,
        }
        config = _base_config(watchlist=60)
        _apply_family_caps(breakdown, config)

        total = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert total <= 60
        assert "_family_cap_applied_watchlist" in breakdown

    def test_spoofing_dynamic_key(self):
        """spoofing_<type> signals matched by prefix."""
        breakdown = {
            "spoofing_anchor_spoof": 30,
            "spoofing_mmsi_reuse": 40,
        }
        config = _base_config(spoofing_and_position=60)
        _apply_family_caps(breakdown, config)

        total = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert total <= 60
        assert "_family_cap_applied_spoofing_and_position" in breakdown

    def test_convoy_dynamic_key(self):
        """convoy_<id> signals matched by prefix to identity_and_ownership."""
        breakdown = {
            "convoy_42": 30,
            "imo_fabricated": 40,
        }
        config = _base_config(identity_and_ownership=50)
        _apply_family_caps(breakdown, config)

        total = sum(
            v for k, v in breakdown.items()
            if not k.startswith("_") and isinstance(v, (int, float)) and v > 0
        )
        assert total <= 50


class TestProportionalScaling:
    """Verify that scaling is proportional across signals in a family."""

    def test_proportional_ratio_maintained(self):
        breakdown = {
            "gap_duration_24h_plus": 50,
            "speed_impossible": 50,
        }
        config = _base_config(gap_and_speed=55)
        _apply_family_caps(breakdown, config)

        # Both should be scaled by same factor (55/100 = 0.55)
        # After rounding: round(50 * 0.55) = 28 each
        assert breakdown["gap_duration_24h_plus"] == breakdown["speed_impossible"]
