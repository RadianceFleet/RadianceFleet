"""Unit tests for AIS gap detection and risk scoring.

Tests are calibrated against known shadow fleet behavior patterns (PRD §7.4, §7.5).
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.modules.gap_detector import _haversine_nm, detect_gaps_for_vessel
from app.modules.normalize import validate_ais_row
from app.modules.risk_scoring import compute_gap_score, _score_band, load_scoring_config


# --- Haversine distance tests ---

def test_haversine_zero_distance():
    assert _haversine_nm(0, 0, 0, 0) == 0.0


def test_haversine_known_distance():
    # London to Paris ~186nm (344km great-circle)
    dist = _haversine_nm(51.5, -0.1, 48.8, 2.3)
    assert 180 < dist < 200


# --- AIS validation tests ---

def test_validate_rejects_short_mmsi():
    row = {"mmsi": "12345", "lat": 55.0, "lon": 25.0, "timestamp_utc": "2025-01-01T00:00:00Z"}
    assert validate_ais_row(row) is not None


def test_validate_rejects_invalid_lat():
    row = {"mmsi": "241234567", "lat": 91.0, "lon": 25.0, "timestamp_utc": "2025-01-01T00:00:00Z"}
    assert validate_ais_row(row) is not None


def test_validate_rejects_future_timestamp():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    row = {"mmsi": "241234567", "lat": 55.0, "lon": 25.0, "timestamp_utc": future}
    assert validate_ais_row(row) is not None


def test_validate_rejects_excessive_sog():
    row = {"mmsi": "241234567", "lat": 55.0, "lon": 25.0, "sog": 40.0,
           "timestamp_utc": "2025-01-01T00:00:00Z"}
    assert validate_ais_row(row) is not None


def test_validate_accepts_valid_row():
    row = {
        "mmsi": "241234567",
        "lat": 55.5,
        "lon": 24.7,
        "sog": 12.0,
        "timestamp_utc": "2025-06-01T12:00:00Z",
    }
    assert validate_ais_row(row) is None


# --- Score band boundary tests ---

def test_score_band_low_boundary():
    assert _score_band(0) == "low"
    assert _score_band(20) == "low"


def test_score_band_medium_boundary():
    assert _score_band(21) == "medium"
    assert _score_band(50) == "medium"


def test_score_band_high_boundary():
    assert _score_band(51) == "high"
    assert _score_band(75) == "high"


def test_score_band_critical_boundary():
    assert _score_band(76) == "critical"
    assert _score_band(500) == "critical"


# --- Risk scoring integration tests ---

def _make_mock_gap(
    duration_minutes=0,
    corridor_type=None,
    deadweight=None,
    flag_risk="unknown",
    year_built=None,
    ais_class="unknown",
    impossible_speed_flag=False,
    velocity_ratio=None,
    in_dark_zone=False,
):
    """Build a minimal mock AISGapEvent for scoring tests.

    Uses plain strings for enum fields so _corridor_multiplier / _vessel_size_multiplier
    comparisons work without SQLAlchemy infrastructure.
    """
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk   # plain string, not enum
    vessel.year_built = year_built
    vessel.ais_class = ais_class            # plain string, not enum
    vessel.pi_coverage_status = "active"     # no P&I signal by default
    vessel.psc_detained_last_12m = False
    vessel.psc_major_deficiencies_last_12m = 0

    corridor = None
    if corridor_type is not None:
        corridor = MagicMock()
        corridor.corridor_type = corridor_type  # plain string, e.g. "sts_zone"

    gap = MagicMock()
    gap.duration_minutes = duration_minutes
    gap.impossible_speed_flag = impossible_speed_flag
    gap.velocity_plausibility_ratio = velocity_ratio
    gap.in_dark_zone = in_dark_zone
    gap.vessel = vessel
    gap.corridor = corridor
    return gap


def test_compute_gap_score_critical_sts_vlcc():
    """24h+ gap in STS zone with VLCC → score > 76 (critical band).

    Hand calculation: 55 × 2.0 × 1.5 = 165
    """
    config = load_scoring_config()
    gap = _make_mock_gap(
        duration_minutes=25 * 60,   # 25 hours
        corridor_type="sts_zone",
        deadweight=250_000,         # VLCC
    )
    score, breakdown = compute_gap_score(gap, config)

    assert score > 76, f"Expected critical score, got {score}"
    assert _score_band(score) == "critical"
    assert breakdown["_corridor_multiplier"] == 2.0
    assert breakdown["_vessel_size_multiplier"] == 1.5
    assert "gap_duration_24h_plus" in breakdown


def test_compute_gap_score_low_short_gap():
    """6h gap, no corridor, aframax DWT → stays in low-medium range."""
    config = load_scoring_config()
    gap = _make_mock_gap(
        duration_minutes=6 * 60,    # 6 hours (4h–8h band = 12pts)
        corridor_type=None,
        deadweight=100_000,         # aframax — 1.0x multiplier
    )
    score, _ = compute_gap_score(gap, config)

    # 12pts × 1.0 × 1.0 = 12 → low band
    assert score <= 20
    assert _score_band(score) == "low"


def test_compute_gap_score_dark_zone_reduces_score():
    """Gap in known jamming zone gets -10pt deduction."""
    config = load_scoring_config()

    gap_normal = _make_mock_gap(duration_minutes=8 * 60, in_dark_zone=False)
    gap_dark = _make_mock_gap(duration_minutes=8 * 60, in_dark_zone=True)

    score_normal, _ = compute_gap_score(gap_normal, config)
    score_dark, breakdown = compute_gap_score(gap_dark, config)

    assert score_dark < score_normal
    assert "dark_zone_deduction" in breakdown
    assert breakdown["dark_zone_deduction"] == -10


def test_compute_gap_score_speed_spike_bonus():
    """Speed spike preceding gap increases gap_duration sub-score by 40%."""
    config = load_scoring_config()

    gap_no_spike = _make_mock_gap(duration_minutes=25 * 60)
    gap_with_spike = _make_mock_gap(duration_minutes=25 * 60)

    score_base, _ = compute_gap_score(gap_no_spike, config)
    score_spike, breakdown = compute_gap_score(gap_with_spike, config, speed_spike_precedes=True)

    assert score_spike > score_base
    assert "gap_duration_speed_spike_bonus" in breakdown
