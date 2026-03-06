"""Tests for loitering detection engine."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import make_mock_vessel


# ── Helpers ──────────────────────────────────────────────────────────

def _make_ais_point(vessel_id, ts, lat, lon, sog=0.1):
    p = MagicMock()
    p.vessel_id = vessel_id
    p.timestamp_utc = ts
    p.lat = lat
    p.lon = lon
    p.sog = sog
    return p


def _make_vessel(vessel_id=1, mmsi="123456789"):
    return make_mock_vessel(
        vessel_id=vessel_id, mmsi=mmsi,
        vessel_laid_up_30d=False, vessel_laid_up_60d=False,
        vessel_laid_up_in_sts_zone=False,
    )


def _make_corridor(corridor_id=1, name="Test STS", corridor_type="sts_zone", geometry_wkt=None):
    c = MagicMock()
    c.corridor_id = corridor_id
    c.name = name
    c.corridor_type = MagicMock()
    c.corridor_type.value = corridor_type
    c.geometry = geometry_wkt
    return c


def _generate_loitering_track(vessel_id, hours=6, sog=0.1):
    """Generate AIS points with low SOG over several hours (loitering)."""
    base = datetime(2026, 1, 15, 10, 0, 0)
    points = []
    for i in range(hours * 6):  # 6 points per hour (every 10 min)
        ts = base + timedelta(minutes=i * 10)
        points.append(_make_ais_point(vessel_id, ts, 25.0, 55.0, sog))
    return points


def _generate_moving_track(vessel_id, hours=6, sog=12.0):
    """Generate AIS points with high SOG (normal transit)."""
    base = datetime(2026, 1, 15, 10, 0, 0)
    points = []
    for i in range(hours * 6):
        ts = base + timedelta(minutes=i * 10)
        lat = 25.0 + i * 0.01
        lon = 55.0 + i * 0.01
        points.append(_make_ais_point(vessel_id, ts, lat, lon, sog))
    return points


# ── Tests ────────────────────────────────────────────────────────────

class TestDetectLoiteringForVessel:
    """Tests for detect_loitering_for_vessel()."""

    @patch("app.modules.loitering_detector._load_loiter_thresholds", return_value={})
    def test_skip_when_too_few_points(self, _mock_cfg):
        from app.modules.loitering_detector import detect_loitering_for_vessel

        db = MagicMock()
        vessel = _make_vessel()

        # Return only 2 points (below _MIN_POINTS=4)
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _make_ais_point(1, datetime(2026, 1, 15, 10), 25.0, 55.0),
            _make_ais_point(1, datetime(2026, 1, 15, 11), 25.0, 55.0),
        ]

        result = detect_loitering_for_vessel(db, vessel)
        assert result == 0

    @patch("app.modules.loitering_detector._load_loiter_thresholds", return_value={})
    def test_no_loitering_for_moving_vessel(self, _mock_cfg):
        from app.modules.loitering_detector import detect_loitering_for_vessel

        db = MagicMock()
        vessel = _make_vessel()

        points = _generate_moving_track(1, hours=8)
        query_mock = MagicMock()
        query_mock.all.return_value = points
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        db.query.return_value = query_mock

        result = detect_loitering_for_vessel(db, vessel)
        assert result == 0

    @patch("app.modules.loitering_detector._load_loiter_thresholds", return_value={})
    def test_detects_loitering_event(self, _mock_cfg):
        from app.modules.loitering_detector import detect_loitering_for_vessel

        db = MagicMock()
        vessel = _make_vessel()

        points = _generate_loitering_track(1, hours=6, sog=0.1)
        query_mock = MagicMock()
        query_mock.all.return_value = points
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        # Dedup check returns None (no existing event)
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = detect_loitering_for_vessel(db, vessel)
        assert result >= 1
        assert db.add.called
        assert db.commit.called

    @patch("app.modules.loitering_detector._load_loiter_thresholds", return_value={})
    def test_skip_duplicate_event(self, _mock_cfg):
        from app.modules.loitering_detector import detect_loitering_for_vessel

        db = MagicMock()
        vessel = _make_vessel()

        points = _generate_loitering_track(1, hours=6)
        query_mock = MagicMock()
        query_mock.all.return_value = points
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        # Dedup check returns existing event
        query_mock.first.return_value = MagicMock()
        db.query.return_value = query_mock

        result = detect_loitering_for_vessel(db, vessel)
        assert result == 0

    @patch("app.modules.loitering_detector._load_loiter_thresholds", return_value={})
    def test_no_loitering_below_min_hours(self, _mock_cfg):
        from app.modules.loitering_detector import detect_loitering_for_vessel

        db = MagicMock()
        vessel = _make_vessel()

        # Only 2 hours of low SOG (below default _MIN_LOITER_HOURS=4)
        points = _generate_loitering_track(1, hours=2)
        query_mock = MagicMock()
        query_mock.all.return_value = points
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        result = detect_loitering_for_vessel(db, vessel)
        assert result == 0


class TestRunLoiteringDetection:
    """Tests for run_loitering_detection() batch runner."""

    @patch("app.modules.loitering_detector.detect_loitering_for_vessel", return_value=2)
    def test_processes_all_vessels(self, mock_detect):
        from app.modules.loitering_detector import run_loitering_detection

        db = MagicMock()
        vessels = [_make_vessel(i) for i in range(3)]
        db.query.return_value.all.return_value = vessels

        result = run_loitering_detection(db)
        assert result["vessels_processed"] == 3
        assert result["loitering_events_created"] == 6
        assert mock_detect.call_count == 3

    @patch("app.modules.loitering_detector.detect_loitering_for_vessel", side_effect=Exception("boom"))
    def test_handles_per_vessel_errors(self, mock_detect):
        from app.modules.loitering_detector import run_loitering_detection

        db = MagicMock()
        db.query.return_value.all.return_value = [_make_vessel()]

        result = run_loitering_detection(db)
        assert result["loitering_events_created"] == 0
        assert result["vessels_processed"] == 1


class TestPointInCorridor:
    """Tests for internal _point_in_corridor helper."""

    def test_point_in_corridor_returns_false_no_geometry(self):
        from app.modules.loitering_detector import _point_in_corridor

        corridor = _make_corridor()
        corridor.geometry = None

        with patch("app.modules.loitering_detector._parse_corridor_bbox", return_value=None):
            assert _point_in_corridor(25.0, 55.0, corridor) is False

    def test_point_in_corridor_returns_true_within_bbox(self):
        from app.modules.loitering_detector import _point_in_corridor

        corridor = _make_corridor()
        # bbox: (24, 26, 54, 56) = (min_lat, max_lat, min_lon, max_lon)
        with patch("app.modules.loitering_detector._parse_corridor_bbox", return_value=(24.0, 26.0, 54.0, 56.0)):
            assert _point_in_corridor(25.0, 55.0, corridor) is True

    def test_point_outside_corridor(self):
        from app.modules.loitering_detector import _point_in_corridor

        corridor = _make_corridor()
        with patch("app.modules.loitering_detector._parse_corridor_bbox", return_value=(24.0, 26.0, 54.0, 56.0)):
            assert _point_in_corridor(30.0, 60.0, corridor) is False


class TestFindCorridorForPosition:
    """Tests for _find_corridor_for_position."""

    def test_finds_matching_corridor(self):
        from app.modules.loitering_detector import _find_corridor_for_position

        c1 = _make_corridor(1, "C1")
        c2 = _make_corridor(2, "C2")

        with patch("app.modules.loitering_detector._point_in_corridor", side_effect=[False, True]):
            result = _find_corridor_for_position(25.0, 55.0, [c1, c2])
            assert result == c2

    def test_returns_none_when_no_match(self):
        from app.modules.loitering_detector import _find_corridor_for_position

        c1 = _make_corridor(1, "C1")

        with patch("app.modules.loitering_detector._point_in_corridor", return_value=False):
            result = _find_corridor_for_position(25.0, 55.0, [c1])
            assert result is None

    def test_empty_corridors_returns_none(self):
        from app.modules.loitering_detector import _find_corridor_for_position

        result = _find_corridor_for_position(25.0, 55.0, [])
        assert result is None


class TestDetectLaidUpVessels:
    """Tests for detect_laid_up_vessels."""

    def test_returns_zero_when_no_vessels(self):
        from app.modules.loitering_detector import detect_laid_up_vessels

        db = MagicMock()
        db.query.return_value.all.return_value = []

        result = detect_laid_up_vessels(db)
        assert result["laid_up_updated"] == 0

    def test_skips_vessel_with_no_ais_points(self):
        from app.modules.loitering_detector import detect_laid_up_vessels

        db = MagicMock()
        vessel = _make_vessel()

        # First .all() returns vessels, corridors
        call_count = [0]
        def side_effect_all():
            call_count[0] += 1
            if call_count[0] <= 2:
                return [vessel] if call_count[0] == 2 else []
            return []

        query_mock = MagicMock()
        query_mock.all.side_effect = side_effect_all
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        db.query.return_value = query_mock

        result = detect_laid_up_vessels(db)
        assert result["laid_up_updated"] == 0
