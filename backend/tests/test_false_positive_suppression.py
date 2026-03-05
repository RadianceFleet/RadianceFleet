"""Tests for Phase 2 false positive suppression fixes.

Covers:
- Fix 1: _is_low_risk_flag / _suppress_data_absence booleans
- Fix 2: data-absence signals suppressed for low-risk flag vessels
- Fix 3: corridor multiplier capped at 1.0 for EU/NATO flags
- Fix 4: ambiguous AIS type codes (90/96/99) soft-capped at 50 for low-risk flags
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.modules.risk_scoring import compute_gap_score, load_scoring_config

_CONFIG = load_scoring_config()

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_vessel(
    flag: str | None = "SE",
    flag_risk: str = "low_risk",
    vessel_type: str | None = None,
    deadweight: float | None = None,
    mmsi: str = "265000001",
    mmsi_first_seen: datetime | None = None,
    year_built: int | None = 2005,
) -> MagicMock:
    v = MagicMock()
    v.vessel_id = 1
    v.flag = flag
    v.flag_risk_category = MagicMock()
    v.flag_risk_category.value = flag_risk
    v.vessel_type = vessel_type
    v.deadweight = deadweight
    v.mmsi = mmsi
    v.mmsi_first_seen_utc = mmsi_first_seen
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
    duration_minutes: float = 1800,  # 30 hours
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


def _make_sts_corridor(corridor_type: str = "sts_zone") -> MagicMock:
    c = MagicMock()
    c.corridor_type = MagicMock()
    c.corridor_type.value = corridor_type
    c.tags = "ship_to_ship,documented"
    c.risk_weight = 1.5
    c.name = "Mediterranean STS — Western"
    return c


def _make_export_corridor() -> MagicMock:
    c = MagicMock()
    c.corridor_type = MagicMock()
    c.corridor_type.value = "export_route"
    c.tags = "transit,chokepoint"
    c.risk_weight = 1.5
    c.name = "Baltic Exit — Oresund"
    return c


def _minimal_db() -> MagicMock:
    """DB mock that returns nothing for all queries."""
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
    """Patch scoring settings, disabling expensive optional modules by default."""
    defaults = {
        "AT_SEA_OPERATIONS_SCORING_ENABLED": True,
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
        "PI_VALIDATION_SCORING_ENABLED": True,
        "AT_SEA_EXTENDED_OPS_SCORING_ENABLED": True,
        "WATCHLIST_STUB_SCORING_ENABLED": False,
    }
    defaults.update(overrides)
    with patch("app.modules.risk_scoring.settings") as mock_s, \
         patch("app.config.settings", mock_s):
        for k, v in defaults.items():
            setattr(mock_s, k, v)
        yield mock_s


def _score(vessel, corridor=None, duration_minutes=1800, gap_start=None, db=None, **settings_overrides):
    gap = _make_gap(vessel, corridor=corridor, duration_minutes=duration_minutes, gap_start=gap_start)
    if db is None:
        db = _minimal_db()
    with _settings_ctx(**settings_overrides):
        return compute_gap_score(gap, _CONFIG, db=db,
                                 scoring_date=datetime(2025, 6, 1, 12, 0))


# ── Fix 2a: new_mmsi_first_30d suppressed for low-risk flags ──────────────────

class TestFix2NewMmsiSuppression:
    def test_low_risk_flag_suppresses_new_mmsi(self):
        """SE flag + MMSI < 30d old → new_mmsi_first_30d must NOT be in breakdown."""
        recent = datetime(2025, 5, 25, 0, 0)  # 7 days before scoring_date 2025-06-01
        vessel = _make_vessel(flag="SE", flag_risk="low_risk", mmsi_first_seen=recent)

        score, breakdown = _score(vessel)

        assert "new_mmsi_first_30d" not in breakdown, (
            f"new_mmsi_first_30d should be suppressed for SE (low_risk) flag; "
            f"breakdown keys={[k for k in breakdown if not k.startswith('_')]}"
        )

    def test_high_risk_flag_fires_new_mmsi(self):
        """RU flag + MMSI < 30d old → new_mmsi_first_30d MUST fire."""
        recent = datetime(2025, 5, 25, 0, 0)  # 7 days before scoring_date
        vessel = _make_vessel(flag="RU", flag_risk="high_risk", mmsi_first_seen=recent)

        score, breakdown = _score(vessel)

        assert "new_mmsi_first_30d" in breakdown, (
            f"new_mmsi_first_30d should fire for RU (high_risk) flag; "
            f"breakdown keys={[k for k in breakdown if not k.startswith('_')]}"
        )

    def test_no_mmsi_seen_date_no_signal(self):
        """No mmsi_first_seen_utc → no new_mmsi signal regardless of flag."""
        vessel = _make_vessel(flag="SE", flag_risk="low_risk", mmsi_first_seen=None)

        score, breakdown = _score(vessel)

        assert "new_mmsi_first_30d" not in breakdown

    def test_old_mmsi_no_signal(self):
        """MMSI seen 90d ago → no new_mmsi_first_30d for any flag."""
        old = datetime(2025, 3, 1, 0, 0)  # 92 days before scoring_date
        vessel = _make_vessel(flag="RU", flag_risk="high_risk", mmsi_first_seen=old)

        score, breakdown = _score(vessel)

        assert "new_mmsi_first_30d" not in breakdown


# ── Fix 2b: pi_no_insurer suppressed for low-risk flags ──────────────────────

class TestFix2PiNoInsurer:
    def test_low_risk_flag_suppresses_pi_no_insurer(self):
        """NO flag, no VesselOwner → pi_no_insurer must NOT be in breakdown."""
        vessel = _make_vessel(flag="NO", flag_risk="low_risk")

        score, breakdown = _score(vessel, PI_VALIDATION_SCORING_ENABLED=True)

        assert "pi_no_insurer" not in breakdown, (
            f"pi_no_insurer should be suppressed for NO (low_risk) flag; "
            f"breakdown keys={[k for k in breakdown if not k.startswith('_')]}"
        )

    def test_high_risk_flag_fires_pi_no_insurer(self):
        """KM flag, no VesselOwner → pi_no_insurer MUST be in breakdown."""
        vessel = _make_vessel(flag="KM", flag_risk="high_risk")

        score, breakdown = _score(vessel, PI_VALIDATION_SCORING_ENABLED=True)

        assert "pi_no_insurer" in breakdown, (
            f"pi_no_insurer should fire for KM (high_risk) flag; "
            f"breakdown keys={[k for k in breakdown if not k.startswith('_')]}"
        )

    def test_medium_risk_flag_fires_pi_no_insurer(self):
        """ZA flag (medium_risk), no VesselOwner → pi_no_insurer fires (not suppressed)."""
        vessel = _make_vessel(flag="ZA", flag_risk="medium_risk")

        score, breakdown = _score(vessel, PI_VALIDATION_SCORING_ENABLED=True)

        # ZA is MEDIUM_RISK — suppress_data_absence only gates LOW_RISK
        assert "pi_no_insurer" in breakdown


# ── Fix 2c: at_sea_no_port_call suppressed for low-risk flags ─────────────────

class TestFix2AtSeaNoPortCall:
    def test_low_risk_flag_suppresses_at_sea_365d(self):
        """GB flag, no port call record → at_sea_no_port_call_365d must NOT fire."""
        vessel = _make_vessel(flag="GB", flag_risk="low_risk")

        score, breakdown = _score(vessel)

        assert "at_sea_no_port_call_365d" not in breakdown, (
            f"at_sea_no_port_call_365d should be suppressed for GB (low_risk); "
            f"breakdown keys={[k for k in breakdown if not k.startswith('_')]}"
        )
        assert "at_sea_no_port_call_180d" not in breakdown
        assert "at_sea_no_port_call_90d" not in breakdown

    def test_high_risk_flag_fires_at_sea_365d(self):
        """RU flag, no port call record → at_sea_no_port_call_365d MUST fire."""
        vessel = _make_vessel(flag="RU", flag_risk="high_risk")

        score, breakdown = _score(vessel)

        assert "at_sea_no_port_call_365d" in breakdown, (
            f"at_sea_no_port_call_365d should fire for RU (high_risk); "
            f"breakdown keys={[k for k in breakdown if not k.startswith('_')]}"
        )

    def test_low_risk_flag_real_port_call_no_signal(self):
        """SE flag with a recent port call → at_sea signals don't fire for any flag."""
        vessel = _make_vessel(flag="SE", flag_risk="low_risk")

        db = _minimal_db()
        mock_port_call = MagicMock()
        mock_port_call.departure_utc = datetime(2025, 5, 25, 0, 0)  # 7 days ago

        # Override the PortCall query to return the port call
        def query_side_effect(model, *args):
            mock_q = MagicMock()
            mock_q.filter.return_value = mock_q
            mock_q.order_by.return_value = mock_q
            mock_q.all.return_value = []
            mock_q.first.return_value = None
            mock_q.count.return_value = 0
            if "PortCall" in str(model):
                mock_q.filter.return_value.order_by.return_value.first.return_value = mock_port_call
            return mock_q

        db.query.side_effect = query_side_effect

        score, breakdown = _score(vessel, db=db)

        assert "at_sea_no_port_call_365d" not in breakdown
        assert "at_sea_no_port_call_180d" not in breakdown
        assert "at_sea_no_port_call_90d" not in breakdown


# ── Fix 3: corridor multiplier capped at 1.0 for low-risk flags ───────────────

class TestFix3CorridorMultiplierCap:
    def test_low_risk_flag_sts_corridor_cap(self):
        """SE flag in STS corridor (×1.5) → corridor multiplier capped at 1.0."""
        vessel = _make_vessel(flag="SE", flag_risk="low_risk")
        corridor = _make_sts_corridor("sts_zone")

        score, breakdown = _score(vessel, corridor=corridor)

        assert "_low_risk_flag_corridor_cap" in breakdown, (
            "Expected _low_risk_flag_corridor_cap in breakdown for SE + STS corridor"
        )
        assert breakdown["_corridor_multiplier"] <= 1.0, (
            f"Corridor multiplier should be capped at 1.0 for SE flag; "
            f"got {breakdown['_corridor_multiplier']}"
        )

    def test_low_risk_flag_export_corridor_cap(self):
        """DK flag in export corridor (×1.5) → corridor multiplier capped at 1.0."""
        vessel = _make_vessel(flag="DK", flag_risk="low_risk")
        corridor = _make_export_corridor()

        score, breakdown = _score(vessel, corridor=corridor)

        assert breakdown.get("_corridor_multiplier", 1.5) <= 1.0

    def test_high_risk_flag_corridor_not_capped(self):
        """KM flag in STS corridor → corridor multiplier stays at 1.5 (uncapped)."""
        vessel = _make_vessel(flag="KM", flag_risk="high_risk")
        corridor = _make_sts_corridor("sts_zone")

        score, breakdown = _score(vessel, corridor=corridor)

        assert "_low_risk_flag_corridor_cap" not in breakdown, (
            "low_risk_flag_corridor_cap must NOT appear for KM (high_risk) flag"
        )
        assert breakdown.get("_corridor_multiplier", 1.0) > 1.0, (
            f"KM flag in STS corridor should use 1.5× multiplier; "
            f"got {breakdown.get('_corridor_multiplier')}"
        )

    def test_medium_risk_flag_corridor_not_capped(self):
        """ZA flag (medium_risk) in STS corridor → corridor multiplier NOT capped."""
        vessel = _make_vessel(flag="ZA", flag_risk="medium_risk")
        corridor = _make_sts_corridor("sts_zone")

        score, breakdown = _score(vessel, corridor=corridor)

        assert "_low_risk_flag_corridor_cap" not in breakdown

    def test_no_corridor_multiplier_already_one(self):
        """SE flag with no corridor → corridor_mult already 1.0, cap has no effect on score."""
        vessel = _make_vessel(flag="SE", flag_risk="low_risk")

        score, breakdown = _score(vessel, corridor=None)

        assert breakdown.get("_corridor_multiplier", 1.0) <= 1.0


# ── Fix 4: ambiguous AIS type cap for low-risk flags ──────────────────────────

class TestFix4AmbiguousTypeCapLowRisk:
    def test_type_90_eu_flag_capped_at_50(self):
        """SE flag (low_risk) + Type 90 → score capped at 50."""
        vessel = _make_vessel(flag="SE", flag_risk="low_risk", vessel_type="Type 90")
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor)

        assert score <= 50, (
            f"SE + Type 90 should be capped at 50 by ambiguous_type_low_risk_cap; "
            f"score={score}"
        )
        assert "_ambiguous_type_low_risk_cap_applied" in breakdown

    def test_type_99_eu_flag_capped(self):
        """GB flag (low_risk) + Type 99 → score capped at 50."""
        vessel = _make_vessel(flag="GB", flag_risk="low_risk", vessel_type="Type 99")
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor)

        assert score <= 50
        assert "_ambiguous_type_low_risk_cap_applied" in breakdown

    def test_type_96_eu_flag_capped(self):
        """NO flag (low_risk) + Type 96 → score capped at 50."""
        vessel = _make_vessel(flag="NO", flag_risk="low_risk", vessel_type="Type 96")
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor)

        assert score <= 50
        assert "_ambiguous_type_low_risk_cap_applied" in breakdown

    def test_type_90_high_risk_not_capped(self):
        """RU flag (high_risk) + Type 90 → Fix 4 does NOT apply."""
        vessel = _make_vessel(flag="RU", flag_risk="high_risk", vessel_type="Type 90")
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor)

        assert "_ambiguous_type_low_risk_cap_applied" not in breakdown, (
            "RU flag should NOT receive ambiguous_type_low_risk_cap"
        )

    def test_type_90_medium_risk_not_capped(self):
        """ZA flag (medium_risk) + Type 90 → Fix 4 does NOT apply."""
        vessel = _make_vessel(flag="ZA", flag_risk="medium_risk", vessel_type="Type 90")
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor)

        assert "_ambiguous_type_low_risk_cap_applied" not in breakdown

    def test_type_50_eu_flag_uses_non_commercial_cap(self):
        """DK flag + Type 50 → existing non_commercial_score_cap (30) applies, not Fix 4."""
        vessel = _make_vessel(flag="DK", flag_risk="low_risk", vessel_type="Type 50")
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor)

        # Type 50 is definitively non-commercial → capped at 30
        assert score <= 30, f"Type 50 should be capped at 30 by non_commercial_score_cap; score={score}"
        assert "_non_commercial_cap_applied" in breakdown

    def test_type_none_eu_flag_no_ambiguous_cap(self):
        """SE flag + vessel_type=None → _vessel_type_raw='', not in ambiguous set."""
        vessel = _make_vessel(flag="SE", flag_risk="low_risk", vessel_type=None)
        corridor = _make_sts_corridor()

        score, breakdown = _score(vessel, corridor=corridor)

        assert "_ambiguous_type_low_risk_cap_applied" not in breakdown, (
            "vessel_type=None should not trigger the ambiguous type cap "
            "(raw string is '', not in {'type 90', 'type 96', 'type 99'})"
        )


# ── Combined: multi-fix score reduction for realistic vessel ──────────────────

class TestCombinedFixImpact:
    def test_eu_research_vessel_score_reduced(self):
        """SE + Type None + STS corridor + no port call + new MMSI:
        Fixes 2+3 should keep score well below 200 (CRITICAL threshold)."""
        recent_mmsi = datetime(2025, 5, 25, 0, 0)  # 7 days before scoring_date
        vessel = _make_vessel(
            flag="SE",
            flag_risk="low_risk",
            vessel_type=None,
            deadweight=None,
            mmsi_first_seen=recent_mmsi,
        )
        corridor = _make_sts_corridor("sts_zone")

        score, breakdown = _score(vessel, corridor=corridor)

        assert score < 150, (
            f"SE research vessel should not be CRITICAL after suppression fixes; "
            f"score={score}, positive signals={[k for k, v in breakdown.items() if isinstance(v, (int,float)) and v > 0 and not k.startswith('_')]}"
        )
        # Data-absence signals must be suppressed
        assert "new_mmsi_first_30d" not in breakdown
        assert "pi_no_insurer" not in breakdown
        assert "at_sea_no_port_call_365d" not in breakdown
        # Corridor multiplier must be capped
        assert breakdown.get("_corridor_multiplier", 1.5) <= 1.0

    def test_ru_vessel_same_conditions_still_high(self):
        """RU + Type None + STS corridor + no port call + new MMSI:
        All risk signals fire; score must remain elevated."""
        recent_mmsi = datetime(2025, 5, 25, 0, 0)  # 7 days before scoring_date
        vessel = _make_vessel(
            flag="RU",
            flag_risk="high_risk",
            vessel_type=None,
            deadweight=None,
            mmsi_first_seen=recent_mmsi,
        )
        corridor = _make_sts_corridor("sts_zone")

        score, breakdown = _score(vessel, corridor=corridor)

        # Data-absence signals must still fire for RU
        assert "new_mmsi_first_30d" in breakdown
        assert "at_sea_no_port_call_365d" in breakdown
        # Corridor multiplier must NOT be capped
        assert "_low_risk_flag_corridor_cap" not in breakdown
        # Score must remain elevated (above what a fully-suppressed EU vessel gets)
        assert score >= 100, (
            f"RU vessel with full signals should score ≥100; score={score}"
        )
