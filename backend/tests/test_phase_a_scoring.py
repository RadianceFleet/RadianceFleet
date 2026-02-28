"""Phase A6 + B11 scoring signal tests.

Tests cover:
  - Repeat STS partnership (3+ events with same partner)
  - Flag + corridor coupling (high-risk flag in suspicious corridor)
  - Invalid AIS metadata (generic names, impossible DWT)
  - Voyage cycle pattern (Russian port + STS + repeated gaps)
  - Selective dark zone evasion (only this vessel dark)
  - Ambient dark zone jamming (many vessels dark)

All tests are unit-level: no database required (mock DB queries via MagicMock).
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.modules.risk_scoring import compute_gap_score, load_scoring_config


# ── Mock gap factory ──────────────────────────────────────────────────────────

def _make_gap(
    duration_minutes=360,
    corridor_type=None,
    corridor_tags=None,
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
    vessel_name=None,
    vessel_type=None,
    vessel_id=1,
    mmsi=None,
    pi_coverage_status="active",
    psc_detained=False,
    psc_major_deficiencies=0,
):
    """Build a mock AISGapEvent for Phase A6/B11 scoring tests."""
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk
    vessel.year_built = year_built
    vessel.ais_class = ais_class
    vessel.flag = flag
    vessel.mmsi = mmsi
    vessel.mmsi_first_seen_utc = mmsi_first_seen_utc
    vessel.name = vessel_name
    vessel.vessel_type = vessel_type
    vessel.vessel_id = vessel_id
    vessel.vessel_laid_up_30d = False
    vessel.vessel_laid_up_60d = False
    vessel.vessel_laid_up_in_sts_zone = False
    vessel.pi_coverage_status = pi_coverage_status
    vessel.psc_detained_last_12m = psc_detained
    vessel.psc_major_deficiencies_last_12m = psc_major_deficiencies
    vessel.imo = None

    corridor = None
    if corridor_type is not None:
        corridor = MagicMock()
        corridor.corridor_type = corridor_type
        corridor.tags = corridor_tags

    gap = MagicMock()
    gap.gap_event_id = 1
    gap.vessel_id = vessel_id
    gap.duration_minutes = duration_minutes
    gap.impossible_speed_flag = impossible_speed_flag
    gap.velocity_plausibility_ratio = velocity_ratio
    gap.in_dark_zone = in_dark_zone
    gap.dark_zone_id = dark_zone_id
    gap.vessel = vessel
    gap.corridor = corridor
    gap.corridor_id = 10 if corridor_type else None
    gap.gap_start_utc = datetime(2026, 1, 15, 12, 0)
    gap.gap_end_utc = datetime(2026, 1, 15, 18, 0)
    gap.start_point = None
    gap.gap_off_lat = None
    gap.gap_off_lon = None
    gap.max_plausible_distance_nm = None
    gap.pre_gap_sog = None
    return gap


# ── Repeat STS partnership tests ──────────────────────────────────────────────

def test_repeat_sts_partnership():
    """Vessel with 3+ STS events with same partner fires repeat_sts_partnership (+30)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=360, vessel_id=1)

    db = MagicMock()

    # Build 3 STS events with the same partner (vessel_id=2)
    sts1 = MagicMock()
    sts1.vessel_1_id = 1
    sts1.vessel_2_id = 2
    sts1.risk_score_component = 10
    sts2 = MagicMock()
    sts2.vessel_1_id = 1
    sts2.vessel_2_id = 2
    sts2.risk_score_component = 10
    sts3 = MagicMock()
    sts3.vessel_1_id = 2
    sts3.vessel_2_id = 1  # reversed order — same pair
    sts3.risk_score_component = 10

    # First db.query call for STS near gap returns no events (Phase 6.6)
    # Second db.query call for repeat STS returns all 3
    # We need to handle multiple query() calls, so use side_effect chain
    # However, compute_gap_score calls db.query(...) many times for different models.
    # We use a single mock that returns different things based on filter chain.

    # Simpler approach: make all queries return the 3 STS events for STS queries
    # and empty/default for everything else.
    _sts_query = MagicMock()
    _sts_query.filter.return_value = _sts_query
    _sts_query.all.return_value = [sts1, sts2, sts3]
    _sts_query.count.return_value = 0
    _sts_query.first.return_value = None

    _empty_query = MagicMock()
    _empty_query.filter.return_value = _empty_query
    _empty_query.join.return_value = _empty_query
    _empty_query.order_by.return_value = _empty_query
    _empty_query.all.return_value = []
    _empty_query.count.return_value = 0
    _empty_query.first.return_value = None
    _empty_query.get.return_value = None

    def _query_router(model):
        model_name = getattr(model, '__name__', '') or getattr(model, '__tablename__', '')
        if 'StsTransferEvent' in str(model_name) or 'sts_transfer' in str(model_name):
            # Return fresh mock each time to avoid shared filter state
            q = MagicMock()
            q.filter.return_value = q
            q.all.return_value = [sts1, sts2, sts3]
            q.count.return_value = 0
            q.first.return_value = None
            return q
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []
        q.count.return_value = 0
        q.first.return_value = None
        q.get.return_value = None
        return q

    db.query.side_effect = _query_router

    score, breakdown = compute_gap_score(gap, config, db=db)
    assert "repeat_sts_partnership" in breakdown, \
        f"Expected repeat_sts_partnership in breakdown, got: {list(breakdown.keys())}"
    assert breakdown["repeat_sts_partnership"] == 30


def test_repeat_sts_no_repeat():
    """Vessel with only 2 STS events with same partner does NOT fire repeat_sts_partnership."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=360, vessel_id=1)

    db = MagicMock()

    sts1 = MagicMock()
    sts1.vessel_1_id = 1
    sts1.vessel_2_id = 2
    sts1.risk_score_component = 10
    sts2 = MagicMock()
    sts2.vessel_1_id = 1
    sts2.vessel_2_id = 2
    sts2.risk_score_component = 10

    def _query_router(model):
        model_name = getattr(model, '__name__', '') or getattr(model, '__tablename__', '')
        if 'StsTransferEvent' in str(model_name) or 'sts_transfer' in str(model_name):
            q = MagicMock()
            q.filter.return_value = q
            q.all.return_value = [sts1, sts2]
            q.count.return_value = 0
            q.first.return_value = None
            return q
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []
        q.count.return_value = 0
        q.first.return_value = None
        q.get.return_value = None
        return q

    db.query.side_effect = _query_router

    score, breakdown = compute_gap_score(gap, config, db=db)
    assert "repeat_sts_partnership" not in breakdown, \
        f"repeat_sts_partnership should NOT fire with only 2 events, got: {breakdown.get('repeat_sts_partnership')}"


# ── Flag + corridor coupling tests ────────────────────────────────────────────

def test_flag_corridor_coupling():
    """High-risk flag (Russian origin) vessel in export_route corridor fires flag_corridor_coupling (+20)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        flag_risk="high_risk",
        flag="PW",  # Palau — in RUSSIAN_ORIGIN_FLAGS
        corridor_type="export_route",
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "flag_corridor_coupling" in breakdown, \
        f"Expected flag_corridor_coupling in breakdown, got: {list(breakdown.keys())}"
    assert breakdown["flag_corridor_coupling"] == 20


def test_flag_corridor_coupling_low_risk_flag():
    """Low-risk flag vessel does NOT fire flag_corridor_coupling even in export_route."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        flag_risk="low_risk",
        flag="NO",  # Norway — NOT in RUSSIAN_ORIGIN_FLAGS
        corridor_type="export_route",
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "flag_corridor_coupling" not in breakdown, \
        "flag_corridor_coupling should NOT fire for low-risk flags"


def test_flag_corridor_coupling_high_risk_non_russian_origin():
    """High-risk flag NOT in RUSSIAN_ORIGIN_FLAGS does NOT fire flag_corridor_coupling."""
    config = load_scoring_config()
    # Use a flag that's high_risk but NOT in RUSSIAN_ORIGIN_FLAGS
    gap = _make_gap(
        duration_minutes=360,
        flag_risk="high_risk",
        flag="XX",  # Not in RUSSIAN_ORIGIN_FLAGS
        corridor_type="export_route",
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "flag_corridor_coupling" not in breakdown, \
        "flag_corridor_coupling should NOT fire for high-risk flags not in RUSSIAN_ORIGIN_FLAGS"


def test_flag_corridor_coupling_sts_zone():
    """Russian-origin flag vessel in sts_zone fires flag_corridor_coupling (+20)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        flag_risk="high_risk",
        flag="KM",  # Comoros — in RUSSIAN_ORIGIN_FLAGS
        corridor_type="sts_zone",
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "flag_corridor_coupling" in breakdown
    assert breakdown["flag_corridor_coupling"] == 20


# ── Invalid AIS metadata tests ───────────────────────────────────────────────

def test_invalid_metadata_generic_name():
    """Vessel with generic name 'TANKER' fires invalid_metadata_generic_name (+10)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        vessel_name="TANKER",
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "invalid_metadata_generic_name" in breakdown, \
        f"Expected invalid_metadata_generic_name, got: {list(breakdown.keys())}"
    assert breakdown["invalid_metadata_generic_name"] == 10


def test_invalid_metadata_normal_name():
    """Vessel with normal name 'EAGLE S' does NOT fire invalid_metadata_generic_name."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        vessel_name="EAGLE S",
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "invalid_metadata_generic_name" not in breakdown, \
        "Normal vessel names should not trigger generic name signal"


def test_invalid_metadata_single_letter_name():
    """Vessel with single-letter name 'X' fires invalid_metadata_generic_name (+10)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        vessel_name="X",
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "invalid_metadata_generic_name" in breakdown
    assert breakdown["invalid_metadata_generic_name"] == 10


def test_invalid_metadata_impossible_dwt():
    """Vessel with DWT 600000 fires invalid_metadata_impossible_dwt (+15)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        deadweight=600_000,
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "invalid_metadata_impossible_dwt" in breakdown, \
        f"Expected invalid_metadata_impossible_dwt, got: {list(breakdown.keys())}"
    assert breakdown["invalid_metadata_impossible_dwt"] == 15


def test_invalid_metadata_impossible_dwt_tanker_too_small():
    """Tanker with DWT 50 fires invalid_metadata_impossible_dwt (+15)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        deadweight=50,
        vessel_type="crude_oil_tanker",
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "invalid_metadata_impossible_dwt" in breakdown
    assert breakdown["invalid_metadata_impossible_dwt"] == 15


def test_invalid_metadata_valid_dwt():
    """Vessel with DWT 120000 does NOT fire impossible_dwt."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        deadweight=120_000,
    )

    score, breakdown = compute_gap_score(gap, config)
    assert "invalid_metadata_impossible_dwt" not in breakdown, \
        "Valid DWT should not trigger impossible_dwt signal"


# ── Voyage cycle pattern tests ────────────────────────────────────────────────

def test_voyage_cycle_pattern():
    """Breakdown with russian_port_recent + sts_event + gap_frequency fires voyage_cycle_pattern (+30)."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=360, vessel_id=1)

    db = MagicMock()

    # We need the breakdown to contain:
    # 1. russian_port_recent or russian_port_gap_sts
    # 2. an sts_event_* key
    # 3. a gap_frequency_* key

    # Easiest approach: mock at a higher level by pre-populating breakdown
    # But compute_gap_score creates its own breakdown dict internally.
    # Instead, we need to trigger all three signals organically:
    # - gap_frequency: pass gaps_in_7d=2
    # - sts_event: mock STS query to return an event
    # - russian_port: mock _had_russian_port_call to return True

    sts_event = MagicMock()
    sts_event.vessel_1_id = 1
    sts_event.vessel_2_id = 2
    sts_event.sts_id = 99
    sts_event.risk_score_component = 25
    sts_event.detection_type = None

    def _query_router(model):
        model_name = str(getattr(model, '__name__', '') or getattr(model, '__tablename__', ''))
        if 'StsTransferEvent' in model_name or 'sts_transfer' in model_name:
            q = MagicMock()
            q.filter.return_value = q
            q.all.return_value = [sts_event]
            q.count.return_value = 0
            q.first.return_value = None
            return q
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []
        q.count.return_value = 0
        q.first.return_value = None
        q.get.return_value = None
        return q

    db.query.side_effect = _query_router

    with patch("app.modules.risk_scoring._had_russian_port_call", return_value=True):
        score, breakdown = compute_gap_score(
            gap, config,
            gaps_in_7d=2,  # triggers gap_frequency_2_in_7d
            db=db,
        )

    assert "russian_port_recent" in breakdown or "russian_port_gap_sts" in breakdown, \
        f"Expected russian_port signal, got: {list(breakdown.keys())}"
    assert any(k.startswith("sts_event_") for k in breakdown), \
        f"Expected sts_event_ signal, got: {list(breakdown.keys())}"
    assert any(k.startswith("gap_frequency_") for k in breakdown), \
        f"Expected gap_frequency_ signal, got: {list(breakdown.keys())}"
    assert "voyage_cycle_pattern" in breakdown, \
        f"Expected voyage_cycle_pattern in breakdown, got: {list(breakdown.keys())}"
    assert breakdown["voyage_cycle_pattern"] == 30


def test_voyage_cycle_pattern_missing_sts():
    """Without STS events, voyage_cycle_pattern does NOT fire even with port + frequency."""
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=360, vessel_id=1)

    db = MagicMock()

    def _query_router(model):
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []
        q.count.return_value = 0
        q.first.return_value = None
        q.get.return_value = None
        return q

    db.query.side_effect = _query_router

    with patch("app.modules.risk_scoring._had_russian_port_call", return_value=True):
        score, breakdown = compute_gap_score(
            gap, config,
            gaps_in_7d=2,
            db=db,
        )

    assert "voyage_cycle_pattern" not in breakdown, \
        "voyage_cycle_pattern should NOT fire without STS events"


# ── Selective dark zone evasion tests ─────────────────────────────────────────

def test_selective_dark_zone_evasion():
    """Gap in dark zone with <=2 other vessels dark fires selective_dark_zone_evasion (+20)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        in_dark_zone=True,
        dark_zone_id=5,
    )

    db = MagicMock()

    def _query_router(model):
        model_name = str(getattr(model, '__name__', '') or getattr(model, '__tablename__', ''))
        if 'AISGapEvent' in model_name or 'ais_gap_events' in model_name:
            q = MagicMock()
            q.filter.return_value = q
            # Only 1 other vessel dark (<=2 threshold)
            q.count.return_value = 1
            q.all.return_value = []
            q.first.return_value = None
            return q
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []
        q.count.return_value = 0
        q.first.return_value = None
        q.get.return_value = None
        return q

    db.query.side_effect = _query_router

    score, breakdown = compute_gap_score(gap, config, db=db)
    assert "selective_dark_zone_evasion" in breakdown, \
        f"Expected selective_dark_zone_evasion, got: {list(breakdown.keys())}"
    assert breakdown["selective_dark_zone_evasion"] == 20
    assert "dark_zone_deduction" not in breakdown, \
        "dark_zone_deduction should NOT be present when selective evasion fires"


def test_ambient_dark_zone_jamming():
    """Gap in dark zone with >2 other vessels dark fires dark_zone_deduction (-10)."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        in_dark_zone=True,
        dark_zone_id=5,
    )

    db = MagicMock()

    def _query_router(model):
        model_name = str(getattr(model, '__name__', '') or getattr(model, '__tablename__', ''))
        if 'AISGapEvent' in model_name or 'ais_gap_events' in model_name:
            q = MagicMock()
            q.filter.return_value = q
            # 8 other vessels also dark (>2 threshold = ambient jamming)
            q.count.return_value = 8
            q.all.return_value = []
            q.first.return_value = None
            return q
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []
        q.count.return_value = 0
        q.first.return_value = None
        q.get.return_value = None
        return q

    db.query.side_effect = _query_router

    score, breakdown = compute_gap_score(gap, config, db=db)
    assert "dark_zone_deduction" in breakdown, \
        f"Expected dark_zone_deduction for ambient jamming, got: {list(breakdown.keys())}"
    assert breakdown["dark_zone_deduction"] == -10
    assert "selective_dark_zone_evasion" not in breakdown, \
        "selective_dark_zone_evasion should NOT fire when many vessels are dark"


def test_dark_zone_no_db_falls_back_to_deduction():
    """Without DB, dark zone gap falls back to standard deduction (-10) — no selective check."""
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=360,
        in_dark_zone=True,
        dark_zone_id=5,
    )

    # No db — db=None
    score, breakdown = compute_gap_score(gap, config, db=None)
    assert "dark_zone_deduction" in breakdown, \
        f"Expected dark_zone_deduction without DB, got: {list(breakdown.keys())}"
    assert breakdown["dark_zone_deduction"] == -10
    assert "selective_dark_zone_evasion" not in breakdown
