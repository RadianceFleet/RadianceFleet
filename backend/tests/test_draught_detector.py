"""Tests for Phase L: Draught intelligence detector.

Uses pytest + MagicMock to test draught change detection logic.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

from app.models.draught_event import DraughtChangeEvent
from app.modules.draught_detector import (
    run_draught_detection,
    _get_class_threshold,
    _haversine_nm,
    _is_valid_draught,
    _parse_port_coords,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_vessel(vessel_id=1, mmsi="123456789", deadweight=100000.0, name="TEST"):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi
    v.deadweight = deadweight
    v.name = name
    return v


def _mock_point(vessel_id, lat, lon, ts, draught=None):
    p = MagicMock()
    p.vessel_id = vessel_id
    p.lat = lat
    p.lon = lon
    p.timestamp_utc = ts
    p.draught = draught
    return p


def _mock_port(port_id=1, name="Test Port", geometry="POINT(55.0 25.0)",
               is_offshore_terminal=False):
    p = MagicMock()
    p.port_id = port_id
    p.name = name
    p.geometry = geometry
    p.is_offshore_terminal = is_offshore_terminal
    return p


def _mock_gap(gap_event_id=1, vessel_id=1, gap_start=None, gap_end=None):
    g = MagicMock()
    g.gap_event_id = gap_event_id
    g.vessel_id = vessel_id
    g.gap_start_utc = gap_start or datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    g.gap_end_utc = gap_end or datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    return g


def _setup_db_mock(vessels=None, ports=None, points_by_vessel=None,
                   gaps_by_vessel=None, pre_gap_point=None, post_gap_point=None,
                   sts_result=None):
    """Build a MagicMock db session with chained query support."""
    db = MagicMock()

    if vessels is None:
        vessels = []
    if ports is None:
        ports = []

    vessel_mock = MagicMock()
    vessel_mock.all.return_value = vessels

    port_mock = MagicMock()
    port_mock.all.return_value = ports

    # Shared counter for AISPoint first() calls across all query chains
    ap_first_counter = {"count": 0}

    def query_side_effect(model_class):
        from app.models.vessel import Vessel as V
        from app.models.port import Port as P
        from app.models.ais_point import AISPoint as AP
        from app.models.gap_event import AISGapEvent as GE
        from app.models.sts_transfer import StsTransferEvent as STS

        if model_class is V:
            return vessel_mock
        elif model_class is P:
            return port_mock
        elif model_class is AP:
            chain = MagicMock()
            filter_chain = MagicMock()
            chain.filter.return_value = filter_chain
            order_chain = MagicMock()
            filter_chain.order_by.return_value = order_chain
            # For the main detection loop: .all() returns points
            if points_by_vessel:
                pts = list(points_by_vessel.values())[0] if points_by_vessel else []
                order_chain.all.return_value = pts
            else:
                order_chain.all.return_value = []
            # For gap analysis: .first() returns pre/post gap points
            def first_fn():
                ap_first_counter["count"] += 1
                if ap_first_counter["count"] % 2 == 1:
                    return pre_gap_point
                return post_gap_point
            order_chain.first.side_effect = first_fn
            return chain
        elif model_class is GE:
            chain = MagicMock()
            filter_chain = MagicMock()
            chain.filter.return_value = filter_chain
            if gaps_by_vessel:
                gaps = list(gaps_by_vessel.values())[0] if gaps_by_vessel else []
                filter_chain.all.return_value = gaps
            else:
                filter_chain.all.return_value = []
            return chain
        elif model_class is STS:
            chain = MagicMock()
            filter_chain = MagicMock()
            chain.filter.return_value = filter_chain
            filter_chain.first.return_value = sts_result
            return chain

        return MagicMock()

    db.query.side_effect = query_side_effect
    return db


# ── Test: _get_class_threshold ───────────────────────────────────────────────

def test_class_threshold_vlcc():
    """VLCC (>200k DWT) should have 3.0m threshold."""
    assert _get_class_threshold(250000) == 3.0


def test_class_threshold_suezmax():
    """Suezmax (120-200k DWT) should have 2.0m threshold."""
    assert _get_class_threshold(150000) == 2.0


def test_class_threshold_aframax():
    """Aframax (80-120k DWT) should have 1.5m threshold."""
    assert _get_class_threshold(100000) == 1.5


def test_class_threshold_panamax():
    """Panamax (<80k DWT) should have 1.0m threshold."""
    assert _get_class_threshold(50000) == 1.0


def test_class_threshold_none_dwt():
    """Unknown DWT should default to 1.0m (strictest)."""
    assert _get_class_threshold(None) == 1.0


# ── Test: disabled flag ──────────────────────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_disabled_returns_status(mock_settings):
    """DRAUGHT_DETECTION_ENABLED=False should return disabled status."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = False
    db = MagicMock()
    result = run_draught_detection(db)
    assert result == {"status": "disabled"}
    # db should not be queried
    db.query.assert_not_called()


# ── Test: VLCC below threshold not flagged ───────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_vlcc_below_threshold_not_flagged(mock_settings):
    """VLCC with 2.5m change (below 3.0m threshold) should NOT be flagged."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=250000.0)  # VLCC
    points = [
        _mock_point(1, 25.0, 55.0, ts_base, draught=15.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=2), draught=17.5),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=3), draught=17.5),
    ]

    db = _setup_db_mock(
        vessels=[vessel],
        ports=[_mock_port(geometry="POINT(100.0 50.0)")],  # far away
        points_by_vessel={1: points},
    )

    result = run_draught_detection(db)
    assert result["events_created"] == 0
    assert result["vessels_processed"] == 1


# ── Test: Aframax offshore flagged ───────────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_aframax_offshore_flagged(mock_settings):
    """Aframax with 2.0m change offshore (above 1.5m) should be flagged."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=100000.0)  # Aframax
    points = [
        _mock_point(1, 25.0, 55.0, ts_base, draught=10.0),
        _mock_point(1, 25.1, 55.1, ts_base + timedelta(hours=2), draught=12.0),
        _mock_point(1, 25.1, 55.1, ts_base + timedelta(hours=3), draught=12.0),  # confirming
    ]

    # Port far away (>10nm) and not offshore terminal
    db = _setup_db_mock(
        vessels=[vessel],
        ports=[_mock_port(geometry="POINT(100.0 50.0)")],  # very far
        points_by_vessel={1: points},
    )

    result = run_draught_detection(db)
    assert result["events_created"] >= 1
    assert result["vessels_processed"] == 1
    db.add.assert_called()


# ── Test: near offshore terminal not flagged ─────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_near_offshore_terminal_not_flagged(mock_settings):
    """Change near offshore terminal (is_offshore_terminal=True, <25nm) should NOT be flagged."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=100000.0)  # Aframax, 1.5m threshold
    # Position very close to port: 25.0, 55.0
    points = [
        _mock_point(1, 25.0, 55.0, ts_base, draught=10.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=2), draught=12.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=3), draught=12.0),  # confirming
    ]

    # Offshore terminal at same location
    port = _mock_port(
        geometry="POINT(55.0 25.0)",  # WKT: POINT(lon lat)
        is_offshore_terminal=True,
    )

    db = _setup_db_mock(
        vessels=[vessel],
        ports=[port],
        points_by_vessel={1: points},
    )

    result = run_draught_detection(db)
    assert result["events_created"] == 0


# ── Test: near regular port not flagged ──────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_near_regular_port_not_flagged(mock_settings):
    """Change near regular port (<10nm) should NOT be flagged."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=50000.0)  # Panamax, 1.0m threshold
    points = [
        _mock_point(1, 25.0, 55.0, ts_base, draught=8.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=2), draught=10.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=3), draught=10.0),  # confirming
    ]

    # Regular port at same location
    port = _mock_port(
        geometry="POINT(55.0 25.0)",  # WKT: POINT(lon lat)
        is_offshore_terminal=False,
    )

    db = _setup_db_mock(
        vessels=[vessel],
        ports=[port],
        points_by_vessel={1: points},
    )

    result = run_draught_detection(db)
    assert result["events_created"] == 0


# ── Test: draught delta across gap ───────────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_draught_delta_across_gap(mock_settings):
    """Draught change across AIS gap should create corroborating event with linked_gap_id."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=100000.0)  # Aframax, 1.5m threshold

    gap = _mock_gap(
        gap_event_id=42,
        vessel_id=1,
        gap_start=ts_base,
        gap_end=ts_base + timedelta(hours=12),
    )

    pre_point = _mock_point(1, 25.0, 55.0, ts_base - timedelta(hours=1), draught=10.0)
    post_point = _mock_point(1, 30.0, 60.0, ts_base + timedelta(hours=13), draught=12.0)

    # Use setup helper: 1 point in main loop (skipped), but gap analysis still runs
    db = _setup_db_mock(
        vessels=[vessel],
        ports=[_mock_port(geometry="POINT(100.0 50.0)")],  # far away
        points_by_vessel={1: [pre_point]},  # <2 points → skip main loop
        gaps_by_vessel={1: [gap]},
        pre_gap_point=pre_point,
        post_gap_point=post_point,
    )

    result = run_draught_detection(db)
    # Should have created a gap-linked event
    assert result["events_created"] >= 1

    # Verify the add call contains a DraughtChangeEvent with linked_gap_id
    added_objects = [c.args[0] for c in db.add.call_args_list
                     if hasattr(c.args[0], 'linked_gap_id')]
    assert len(added_objects) >= 1
    gap_event = added_objects[0]
    assert gap_event.linked_gap_id == 42
    assert gap_event.risk_score_component == 20  # draught_delta_across_gap


# ── Test: stale draught no alert ─────────────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_stale_draught_no_alert(mock_settings):
    """Same draught value for 30 days should NOT create an alert."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=50000.0)

    # All points have same draught
    points = [
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(days=d), draught=10.0)
        for d in range(30)
    ]

    db = _setup_db_mock(
        vessels=[vessel],
        ports=[_mock_port(geometry="POINT(100.0 50.0)")],
        points_by_vessel={1: points},
    )

    result = run_draught_detection(db)
    assert result["events_created"] == 0


# ── Test: draught out of bounds rejected ─────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_draught_out_of_bounds_rejected(mock_settings):
    """Draught >25m should be rejected as physically impossible."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=50000.0)
    points = [
        _mock_point(1, 25.0, 55.0, ts_base, draught=10.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=2), draught=30.0),  # invalid
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=3), draught=30.0),
    ]

    db = _setup_db_mock(
        vessels=[vessel],
        ports=[_mock_port(geometry="POINT(100.0 50.0)")],
        points_by_vessel={1: points},
    )

    result = run_draught_detection(db)
    assert result["events_created"] == 0


# ── Test: negative draught rejected ──────────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_negative_draught_rejected(mock_settings):
    """Draught <0 should be rejected."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=50000.0)
    points = [
        _mock_point(1, 25.0, 55.0, ts_base, draught=10.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=2), draught=-5.0),  # invalid
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=3), draught=-5.0),
    ]

    db = _setup_db_mock(
        vessels=[vessel],
        ports=[_mock_port(geometry="POINT(100.0 50.0)")],
        points_by_vessel={1: points},
    )

    result = run_draught_detection(db)
    assert result["events_created"] == 0


# ── Test: single reading not confirmed ───────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_single_reading_not_confirmed(mock_settings):
    """Only 1 reading at new draught (needs >=2) should NOT be flagged."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True
    ts_base = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)

    vessel = _mock_vessel(deadweight=50000.0)  # Panamax, 1.0m threshold
    points = [
        _mock_point(1, 25.0, 55.0, ts_base, draught=10.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=1), draught=10.0),
        _mock_point(1, 25.0, 55.0, ts_base + timedelta(hours=2), draught=12.0),  # single change
        # No confirming reading follows
    ]

    db = _setup_db_mock(
        vessels=[vessel],
        ports=[_mock_port(geometry="POINT(100.0 50.0)")],
        points_by_vessel={1: points},
    )

    result = run_draught_detection(db)
    assert result["events_created"] == 0


# ── Test: event record structure ─────────────────────────────────────────────

def test_event_record_structure():
    """DraughtChangeEvent should have all expected fields."""
    expected_fields = [
        "event_id", "vessel_id", "timestamp_utc",
        "old_draught_m", "new_draught_m", "delta_m",
        "nearest_port_id", "distance_to_port_nm", "is_offshore",
        "linked_gap_id", "linked_sts_id", "risk_score_component",
    ]
    # Check that the model class has these as mapped columns
    mapper = DraughtChangeEvent.__table__
    column_names = {c.name for c in mapper.columns}
    for field in expected_fields:
        assert field in column_names, f"Missing field: {field}"


# ── Test: no draught points skipped ──────────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_no_draught_points_skipped(mock_settings):
    """Vessel with no draught-populated points should be skipped."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True

    vessel = _mock_vessel(deadweight=50000.0)

    db = _setup_db_mock(
        vessels=[vessel],
        ports=[],
        points_by_vessel={1: []},  # no points
    )

    result = run_draught_detection(db)
    assert result["vessels_skipped"] == 1
    assert result["vessels_processed"] == 0
    assert result["events_created"] == 0


# ── Test: return dict structure ──────────────────────────────────────────────

@patch("app.modules.draught_detector.settings")
def test_return_dict_structure(mock_settings):
    """Return dict should have keys: events_created, vessels_processed, vessels_skipped."""
    mock_settings.DRAUGHT_DETECTION_ENABLED = True

    db = _setup_db_mock(vessels=[], ports=[])

    result = run_draught_detection(db)
    assert "events_created" in result
    assert "vessels_processed" in result
    assert "vessels_skipped" in result


# ── Test: _is_valid_draught helper ───────────────────────────────────────────

def test_valid_draught_bounds():
    """_is_valid_draught should reject out-of-bounds values."""
    assert _is_valid_draught(10.0) is True
    assert _is_valid_draught(0.0) is False    # exactly 0 is invalid (no draught)
    assert _is_valid_draught(25.0) is True    # exactly 25 is valid
    assert _is_valid_draught(25.1) is False   # >25 invalid
    assert _is_valid_draught(-1.0) is False
    assert _is_valid_draught(None) is False


# ── Test: _parse_port_coords helper ──────────────────────────────────────────

def test_parse_port_coords():
    """_parse_port_coords should extract (lat, lon) from WKT POINT."""
    result = _parse_port_coords("POINT(55.0 25.0)")
    assert result == (25.0, 55.0)  # WKT is POINT(lon lat), returns (lat, lon)
    assert _parse_port_coords(None) is None
    assert _parse_port_coords("INVALID") is None
