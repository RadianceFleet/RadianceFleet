"""Ground truth regression tests for shadow fleet scoring.

Validates that known shadow fleet archetypes score HIGH or CRITICAL,
and known clean vessels score LOW.  All tests are unit-level (db=None).

Uses the same _make_gap() factory from test_risk_scoring_complete.py.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from app.modules.risk_scoring import compute_gap_score, load_scoring_config, _score_band


# ── Shared config (loaded once) ─────────────────────────────────────────────
CONFIG = load_scoring_config()
SCORING_DATE = datetime(2026, 3, 1, 12, 0)


# ── Mock gap factory ────────────────────────────────────────────────────────

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
    vessel_name="TEST VESSEL",
    vessel_type="tanker",
    mmsi=None,
):
    """Build a fully-featured mock AISGapEvent for scoring tests."""
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk
    vessel.year_built = year_built
    vessel.ais_class = ais_class
    vessel.flag = flag
    vessel.mmsi = mmsi
    vessel.mmsi_first_seen_utc = mmsi_first_seen_utc
    vessel.vessel_laid_up_30d = vessel_laid_up_30d
    vessel.vessel_laid_up_60d = vessel_laid_up_60d
    vessel.vessel_laid_up_in_sts_zone = vessel_laid_up_in_sts_zone
    vessel.pi_coverage_status = pi_coverage_status
    vessel.psc_detained_last_12m = psc_detained
    vessel.psc_major_deficiencies_last_12m = psc_major_deficiencies
    vessel.vessel_id = 1
    vessel.name = vessel_name
    vessel.vessel_type = vessel_type

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
    gap.gap_end_utc = datetime(2026, 1, 15, 12, 0) + timedelta(minutes=duration_minutes)
    return gap


# ── Shadow fleet archetypes — must score >= HIGH (51+) ──────────────────────

SHADOW_FLEET_CASES = [
    pytest.param(
        # 1. Old tanker, high-risk flag, long gap, STS corridor
        dict(
            duration_minutes=24 * 60,
            corridor_type="sts_zone",
            deadweight=100_000,
            flag_risk="high_risk",
            year_built=1998,
            ais_class="B",
            flag="CM",
            vessel_name="TANKER ONE",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        "critical",
        id="1-old-tanker-high-risk-sts",
    ),
    pytest.param(
        # 2. Suezmax, flag change signal approximated by high_risk flag, circle spoofing via speed spoof
        dict(
            duration_minutes=10 * 60,
            corridor_type="sts_zone",
            deadweight=150_000,
            flag_risk="high_risk",
            flag="PW",
            year_built=2005,
            vessel_name="BULKER STAR",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0, pre_gap_sog=24.0),
        "high",
        id="2-suezmax-flag-change-spoof",
    ),
    pytest.param(
        # 3. VLCC, high frequency gaps (simulates watchlist-level concern)
        dict(
            duration_minutes=12 * 60,
            corridor_type="sts_zone",
            deadweight=300_000,
            flag_risk="high_risk",
            year_built=2000,
            flag="CM",
            vessel_name="CRUDE MOVER",
        ),
        dict(gaps_in_7d=2, gaps_in_14d=3, gaps_in_30d=5),
        "critical",
        id="3-vlcc-gap-frequency",
    ),
    pytest.param(
        # 4. Aframax, Russian-origin flag + STS + 12h gap
        dict(
            duration_minutes=12 * 60,
            corridor_type="sts_zone",
            deadweight=100_000,
            flag_risk="high_risk",
            year_built=2004,
            flag="RU",
            vessel_name="VOLGA SPIRIT",
        ),
        dict(gaps_in_7d=1, gaps_in_14d=2, gaps_in_30d=3),
        "high",
        id="4-aframax-russian-sts",
    ),
    pytest.param(
        # 5. Impossible speed + dark zone exit
        dict(
            duration_minutes=8 * 60,
            corridor_type="export_route",
            deadweight=90_000,
            flag_risk="high_risk",
            year_built=2001,
            impossible_speed_flag=True,
            in_dark_zone=True,
            dark_zone_id=42,
            flag="KM",
            vessel_name="DARK RUNNER",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        "high",
        id="5-impossible-speed-dark-zone-exit",
    ),
    pytest.param(
        # 6. Old vessel, lapsed P&I, Class B mismatch
        dict(
            duration_minutes=14 * 60,
            corridor_type="sts_zone",
            deadweight=85_000,
            flag_risk="high_risk",
            year_built=1995,
            ais_class="B",
            pi_coverage_status="lapsed",
            flag="TG",
            vessel_name="OLD FAITHFUL",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        "critical",
        id="6-old-lapsed-pi-stale",
    ),
    pytest.param(
        # 7. High-risk flag + STS + gap frequency + generic name
        dict(
            duration_minutes=10 * 60,
            corridor_type="sts_zone",
            deadweight=100_000,
            flag_risk="high_risk",
            year_built=2003,
            flag="PW",
            vessel_name="TANKER",
        ),
        dict(gaps_in_7d=2, gaps_in_14d=3, gaps_in_30d=4),
        "high",
        id="7-flag-hop-rename-generic",
    ),
    pytest.param(
        # 8. PSC detained + high risk + STS + large gap (KSE-like profile)
        dict(
            duration_minutes=16 * 60,
            corridor_type="sts_zone",
            deadweight=120_000,
            flag_risk="high_risk",
            year_built=2000,
            ais_class="B",
            psc_detained=True,
            psc_major_deficiencies=4,
            flag="CM",
            vessel_name="SHADOW PRIME",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        "critical",
        id="8-kse-shadow-profile",
    ),
    pytest.param(
        # 9. High-risk flag in export corridor + large gap + old vessel
        dict(
            duration_minutes=18 * 60,
            corridor_type="export_route",
            deadweight=150_000,
            flag_risk="high_risk",
            year_built=1999,
            flag="GA",
            vessel_name="LADEN CARRIER",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=2),
        "high",
        id="9-sanctioned-port-laden",
    ),
    pytest.param(
        # 10. Dark vessel in corridor + high risk flag + impossible speed
        dict(
            duration_minutes=10 * 60,
            corridor_type="export_route",
            deadweight=100_000,
            flag_risk="high_risk",
            year_built=2002,
            impossible_speed_flag=True,
            in_dark_zone=True,
            dark_zone_id=99,
            flag="PW",
            vessel_name="GHOST TANKER",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        "high",
        id="10-dark-corridor-eez",
    ),
]


@pytest.mark.parametrize("gap_kwargs,score_kwargs,expected_min_band", SHADOW_FLEET_CASES)
def test_shadow_fleet_archetype(gap_kwargs, score_kwargs, expected_min_band):
    """Shadow fleet archetypes must score at or above the expected band."""
    gap = _make_gap(**gap_kwargs)
    score, breakdown = compute_gap_score(
        gap, CONFIG, scoring_date=SCORING_DATE, db=None, **score_kwargs
    )
    band = _score_band(score)

    band_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    assert band_order[band] >= band_order[expected_min_band], (
        f"Expected >= {expected_min_band} but got {band} (score={score}). "
        f"Breakdown: {breakdown}"
    )


# ── Clean vessel cases — must score <= LOW (20) ────────────────────────────

CLEAN_VESSEL_CASES = [
    pytest.param(
        # 1. Young vessel, white flag, short gap, clean PSC
        dict(
            duration_minutes=2 * 60,
            deadweight=80_000,
            flag_risk="low_risk",
            year_built=2021,
            ais_class="A",
            flag="NO",
            vessel_name="NORDIC CARRIER",
            vessel_type="tanker",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        id="clean-1-young-white-flag",
    ),
    pytest.param(
        # 2. EU-flagged Panamax, no gap frequency
        dict(
            duration_minutes=3 * 60,
            deadweight=70_000,
            flag_risk="low_risk",
            year_built=2018,
            ais_class="A",
            flag="DE",
            vessel_name="HAMBURG EXPRESS",
            vessel_type="tanker",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        id="clean-2-eu-panamax",
    ),
    pytest.param(
        # 3. Modern tanker, Class A, legitimate trade route corridor
        dict(
            duration_minutes=2.5 * 60,
            corridor_type="legitimate_trade_route",
            deadweight=90_000,
            flag_risk="low_risk",
            year_built=2020,
            ais_class="A",
            flag="NL",
            vessel_name="DUTCH ENTERPRISE",
            vessel_type="tanker",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        id="clean-3-modern-legit-route",
    ),
    pytest.param(
        # 4. Norway-flagged, long track record
        dict(
            duration_minutes=2 * 60,
            deadweight=85_000,
            flag_risk="low_risk",
            year_built=2015,
            ais_class="A",
            flag="NO",
            vessel_name="FJORD TANKER",
            vessel_type="tanker",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        id="clean-4-norway-flagged",
    ),
    pytest.param(
        # 5. Small tanker, white flag, PI active
        dict(
            duration_minutes=2 * 60,
            deadweight=30_000,
            flag_risk="low_risk",
            year_built=2019,
            ais_class="A",
            flag="JP",
            pi_coverage_status="active",
            vessel_name="SAKURA MARU",
            vessel_type="tanker",
        ),
        dict(gaps_in_7d=0, gaps_in_14d=0, gaps_in_30d=0),
        id="clean-5-small-white-pi-active",
    ),
]


@pytest.mark.parametrize("gap_kwargs,score_kwargs", CLEAN_VESSEL_CASES)
def test_clean_vessel(gap_kwargs, score_kwargs):
    """Clean vessels must score LOW (<=20)."""
    gap = _make_gap(**gap_kwargs)
    score, breakdown = compute_gap_score(
        gap, CONFIG, scoring_date=SCORING_DATE, db=None, **score_kwargs
    )
    band = _score_band(score)

    assert band == "low", (
        f"Expected low but got {band} (score={score}). "
        f"Breakdown: {breakdown}"
    )
