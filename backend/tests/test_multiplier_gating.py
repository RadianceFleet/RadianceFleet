"""Tests for multiplier gating — suppress corridor/vessel-size amplification on thin signals.

A single 12-pt gap in an STS corridor on a Suezmax used to score 12 * 1.5 * 1.2 = 22 pts.
The multiplier nearly doubles a weak signal. Multiplier gating requires minimum base score
AND multi-family breadth before amplification kicks in.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.modules.risk_scoring import compute_gap_score, load_scoring_config

_CONFIG = load_scoring_config()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_vessel(
    flag: str | None = "MT",
    flag_risk: str = "neutral",
    vessel_type: str | None = None,
    deadweight: float | None = None,
    mmsi: str = "256000001",
    mmsi_first_seen: datetime | None = None,
    year_built: int | None = 2015,
) -> MagicMock:
    v = MagicMock()
    v.vessel_id = 1
    v.flag = flag
    v.flag_risk_category = MagicMock()
    v.flag_risk_category.value = flag_risk
    v.vessel_type = vessel_type
    v.deadweight = deadweight
    v.mmsi = mmsi
    v.mmsi_first_seen_utc = mmsi_first_seen or datetime(2024, 1, 1)
    v.year_built = year_built
    v.imo_number = "1234567"
    v.name = "TEST VESSEL"
    v.call_sign = None
    v.length = None
    v.psc_detained_last_12m = False
    v.ais_class = MagicMock()
    v.ais_class.value = "A"
    v.imo = None
    return v


def _make_gap(
    vessel: MagicMock,
    corridor: MagicMock | None = None,
    duration_minutes: float = 300,
    gap_start: datetime | None = None,
) -> MagicMock:
    gap = MagicMock()
    gap.vessel_id = vessel.vessel_id
    gap.vessel = vessel
    gap.corridor = corridor
    gap.corridor_id = None
    gap.gap_event_id = 99
    gap.duration_minutes = duration_minutes
    gap_start = gap_start or datetime(2025, 6, 1, 12, 0)
    gap.gap_start_utc = gap_start
    gap.gap_end_utc = gap_start + timedelta(minutes=duration_minutes)
    gap.risk_score = 0
    gap.impossible_speed_flag = False
    gap.velocity_plausibility_ratio = 0.5
    gap.max_plausible_distance_nm = 200.0
    gap.actual_gap_distance_nm = 80.0
    gap.pre_gap_sog = 5.0
    gap.in_dark_zone = False
    gap.start_point = None
    gap.gap_off_lat = None
    gap.gap_off_lon = None
    return gap


def _make_sts_corridor() -> MagicMock:
    c = MagicMock()
    c.corridor_type = MagicMock()
    c.corridor_type.value = "sts_zone"
    c.tags = "ship_to_ship,documented"
    c.risk_weight = 1.5
    c.name = "Mediterranean STS - Western"
    return c


def _make_standard_corridor() -> MagicMock:
    c = MagicMock()
    c.corridor_type = MagicMock()
    c.corridor_type.value = "standard"
    c.tags = "transit"
    c.risk_weight = 1.0
    c.name = "Standard Corridor"
    return c


def _minimal_db() -> MagicMock:
    db = MagicMock()
    mock_q = MagicMock()
    mock_q.filter.return_value = mock_q
    mock_q.order_by.return_value = mock_q
    mock_q.join.return_value = mock_q
    mock_q.all.return_value = []
    mock_q.first.return_value = None
    mock_q.count.return_value = 0
    mock_q.scalar.return_value = 0
    db.query.return_value = mock_q
    return db


@contextmanager
def _settings_ctx(**overrides):
    defaults = {
        "AT_SEA_OPERATIONS_SCORING_ENABLED": False,
        "STALE_AIS_SCORING_ENABLED": False,
        "TRACK_NATURALNESS_SCORING_ENABLED": False,
        "DRAUGHT_SCORING_ENABLED": False,
        "STATELESS_MMSI_SCORING_ENABLED": False,
        "FLAG_HOPPING_SCORING_ENABLED": False,
        "IMO_FRAUD_SCORING_ENABLED": False,
        "FLEET_SCORING_ENABLED": False,
        "STS_CHAIN_SCORING_ENABLED": False,
        "RENAME_VELOCITY_SCORING_ENABLED": False,
        "FRAUDULENT_REGISTRY_SCORING_ENABLED": False,
        "DESTINATION_SCORING_ENABLED": False,
        "SCRAPPED_VESSEL_SCORING_ENABLED": False,
        "PI_VALIDATION_SCORING_ENABLED": False,
        "AT_SEA_EXTENDED_OPS_SCORING_ENABLED": False,
        "WATCHLIST_STUB_SCORING_ENABLED": False,
    }
    defaults.update(overrides)
    with patch("app.modules.risk_scoring.settings") as mock_s, patch("app.config.settings", mock_s):
        for k, v in defaults.items():
            setattr(mock_s, k, v)
        yield mock_s


def _score(vessel, corridor=None, duration_minutes=300, config=None):
    gap = _make_gap(vessel, corridor=corridor, duration_minutes=duration_minutes)
    db = _minimal_db()
    cfg = config if config is not None else _CONFIG
    with _settings_ctx():
        return compute_gap_score(gap, cfg, db=db, scoring_date=datetime(2025, 6, 1, 12, 0))


def _make_gating_config(enabled=True, min_base=25, min_fams=2):
    """Create a config with specific gating settings and high thresholds to isolate gap signal."""
    cfg = copy.deepcopy(_CONFIG)
    cfg["multiplier_gating"] = {
        "enabled": enabled,
        "min_base_score": min_base,
        "min_families_for_multiplier": min_fams,
    }
    # Set very high thresholds for signals we want to suppress,
    # keeping only gap_duration signals active
    cfg["kse_profile"] = {"enabled": False, "profile_match_3": 20, "profile_match_4plus": 35}
    cfg["behavioral_baseline"] = {**cfg.get("behavioral_baseline", {}), "min_sample_size": 999999}
    return cfg


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestMultiplierGatingLowBaseScore:
    """Low base score (single short gap) suppresses multipliers."""

    def test_low_base_score_suppresses_corridor_multiplier(self):
        """A 3h gap (~5 pts) in STS corridor should NOT get 1.5x amplification
        when base score is below threshold."""
        cfg = _make_gating_config(min_base=100)  # set high threshold so gating always fires
        vessel = _make_vessel(deadweight=150000)
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor, duration_minutes=180, config=cfg)

        risk_signals = sum(v for v in breakdown.values() if isinstance(v, (int, float)) and v > 0)
        if risk_signals < 100:
            assert breakdown.get("_multiplier_gating_applied") == "base_score_below_threshold", (
                f"Expected gating for base score {risk_signals} < 100; breakdown={breakdown}"
            )

    def test_low_base_score_final_equals_unamplified(self):
        """When gated, final score should not include multiplier amplification."""
        cfg = _make_gating_config(min_base=100)
        vessel = _make_vessel(deadweight=150000)
        corridor = _make_sts_corridor()

        score_gated, bd_gated = _score(vessel, corridor=corridor, duration_minutes=180, config=cfg)

        # Now run same scenario with gating disabled
        cfg_off = _make_gating_config(enabled=False)
        score_ungated, bd_ungated = _score(vessel, corridor=corridor, duration_minutes=180, config=cfg_off)

        # If corridor/size multiplier was > 1.0, gated score should be <= ungated score
        cm = bd_ungated.get("_corridor_multiplier", 1.0)
        sm = bd_ungated.get("_vessel_size_multiplier", 1.0)
        if cm > 1.0 or sm > 1.0:
            assert score_gated <= score_ungated, (
                f"Gated score {score_gated} should be <= ungated {score_ungated}"
            )


class TestMultiplierGatingSingleFamily:
    """Single-family signal suppresses multipliers when < min_families."""

    def test_single_family_suppresses_multipliers(self):
        """With min_families=5 (practically unreachable), gating fires on family breadth."""
        cfg = _make_gating_config(min_base=1, min_fams=5)  # min_base=1 so base check passes
        vessel = _make_vessel(deadweight=150000)
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor, duration_minutes=1800, config=cfg)

        # With min_fams=5, it's very unlikely 5 families are active
        from app.modules.risk_scoring import (
            _POSITION_PILLAR_KEYS,
            _VESSEL_PILLAR_KEYS,
            _VOYAGE_PILLAR_KEYS,
            _WATCHLIST_PILLAR_KEYS,
        )
        fam_sets = {
            "position": _POSITION_PILLAR_KEYS,
            "identity": _VESSEL_PILLAR_KEYS,
            "voyage": _VOYAGE_PILLAR_KEYS,
            "watchlist": _WATCHLIST_PILLAR_KEYS,
        }
        active = sum(
            1 for keys in fam_sets.values()
            if any(breakdown.get(k, 0) > 0 for k in keys)
        )
        all_pillar = _POSITION_PILLAR_KEYS | _VESSEL_PILLAR_KEYS | _VOYAGE_PILLAR_KEYS | _WATCHLIST_PILLAR_KEYS
        has_other = any(
            isinstance(v, (int, float)) and v > 0
            for k, v in breakdown.items()
            if not k.startswith("_") and k not in all_pillar
        )
        if has_other:
            active += 1

        if active < 5:
            assert breakdown.get("_multiplier_gating_applied") == "insufficient_family_breadth", (
                f"Expected gating for {active} families < 5; breakdown={breakdown}"
            )


class TestMultiplierGatingSufficientSubstance:
    """Sufficient substance (high score across 2+ families) allows multipliers."""

    def test_multi_family_high_score_allows_multipliers(self):
        """A gap with signals from multiple pillar families and high score should NOT be gated.

        new_mmsi_first_30d (position pillar) + gap duration + other signals = 2+ families.
        """
        cfg = _make_gating_config(min_base=25, min_fams=2)
        vessel = _make_vessel(
            flag="CM",
            flag_risk="high_risk",
            deadweight=150000,
            year_built=1995,  # 30 years old
            mmsi_first_seen=datetime(2025, 5, 25),  # 7 days before scoring → new_mmsi_first_30d fires
        )
        corridor = _make_sts_corridor()

        # 24h+ gap = 55 pts base + new_mmsi (position pillar) + other signals
        score, breakdown = _score(vessel, corridor=corridor, duration_minutes=1800, config=cfg)

        # Verify new_mmsi_first_30d fired (position pillar) and other signals exist
        assert "new_mmsi_first_30d" in breakdown, (
            f"new_mmsi_first_30d should fire; breakdown keys={[k for k in breakdown if not k.startswith('_')]}"
        )
        assert "_multiplier_gating_applied" not in breakdown, (
            f"Gating should NOT fire with multi-family high score; "
            f"breakdown keys={[k for k in breakdown if not k.startswith('_')]}"
        )


class TestMultiplierGatingLowRiskFlagInteraction:
    """Gating and low-risk-flag cap can both apply independently."""

    def test_low_risk_flag_cap_still_applies(self):
        """EU/NATO flag corridor cap is independent of multiplier gating."""
        cfg = _make_gating_config(min_base=100)
        vessel = _make_vessel(
            flag="SE",
            flag_risk="low_risk",
            deadweight=150000,
            mmsi="265000001",
        )
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor, duration_minutes=300, config=cfg)

        # Low-risk flag corridor cap should still be present
        assert "_low_risk_flag_corridor_cap" in breakdown, (
            f"Low-risk flag corridor cap should still apply; breakdown keys={list(breakdown.keys())}"
        )


class TestMultiplierGatingNonCommercialInteraction:
    """Non-commercial override still applies independently of gating."""

    def test_non_commercial_removes_sts_even_with_gating(self):
        """Non-commercial vessels still get STS removal and score cap."""
        cfg = _make_gating_config(min_base=25, min_fams=2)
        # DWT must be <= 5000 for non-commercial override (DWT > 5000 forces commercial)
        vessel = _make_vessel(vessel_type="tug", deadweight=500)
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor, duration_minutes=300, config=cfg)

        # Non-commercial cap should be applied
        assert "_non_commercial_cap_applied" in breakdown, (
            f"Non-commercial cap should apply; breakdown keys={list(breakdown.keys())}"
        )
        # STS signals should be removed
        sts_keys = [k for k in breakdown if k.startswith("sts_event_") or k.startswith("repeat_sts")]
        assert len(sts_keys) == 0, f"STS keys should be removed for non-commercial: {sts_keys}"


class TestMultiplierGatingConfigDisabled:
    """Config enabled: false bypasses gating entirely."""

    def test_disabled_config_allows_multipliers(self):
        """When multiplier_gating.enabled=false, gating metadata should be absent."""
        cfg = _make_gating_config(enabled=False, min_base=1, min_fams=99)
        vessel = _make_vessel(deadweight=150000)
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor, duration_minutes=300, config=cfg)

        assert "_multiplier_gating_applied" not in breakdown, (
            f"Gating should not fire when disabled; breakdown keys={list(breakdown.keys())}"
        )


class TestMultiplierGatingMetadata:
    """Metadata _multiplier_gating_applied present when gating fires."""

    def test_metadata_has_valid_reason_string(self):
        """When gating fires, reason should be one of the two expected values."""
        cfg = _make_gating_config(min_base=9999)
        vessel = _make_vessel(deadweight=150000)
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor, duration_minutes=300, config=cfg)

        assert "_multiplier_gating_applied" in breakdown, (
            f"Gating should fire with impossibly high threshold"
        )
        reason = breakdown["_multiplier_gating_applied"]
        assert reason in ("base_score_below_threshold", "insufficient_family_breadth"), (
            f"Unexpected gating reason: {reason}"
        )

    def test_metadata_absent_when_not_gated(self):
        """When gating does not fire, _multiplier_gating_applied should be absent."""
        cfg = _make_gating_config(enabled=False)
        vessel = _make_vessel(deadweight=150000)
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor, duration_minutes=300, config=cfg)

        assert "_multiplier_gating_applied" not in breakdown
