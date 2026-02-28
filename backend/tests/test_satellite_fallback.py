"""Tests for satellite query fallback to last known AIS position (G6).

Validates _get_gap_center fallback chain:
1. Midpoint of start/end gap points
2. Start point only
3. End point only
4. Last known AIS position for vessel (NEW)
5. North Sea fallback (55.0, 15.0)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, PropertyMock

import pytest


def _make_ais_point(lat: float, lon: float, point_id: int = 1):
    """Create a mock AISPoint."""
    point = MagicMock()
    point.lat = lat
    point.lon = lon
    point.ais_point_id = point_id
    return point


class TestGapCenterWithStartEnd:
    """When gap has both start and end points, returns midpoint."""

    def test_midpoint_of_start_and_end(self):
        from app.modules.satellite_query import _get_gap_center

        gap = MagicMock()
        gap.start_point_id = 1
        gap.end_point_id = 2
        gap.vessel_id = 100

        start_pt = _make_ais_point(lat=40.0, lon=20.0, point_id=1)
        end_pt = _make_ais_point(lat=50.0, lon=30.0, point_id=2)

        db = MagicMock()
        db.get.side_effect = lambda model, pid: start_pt if pid == 1 else end_pt

        lat, lon = _get_gap_center(gap, db)
        assert lat == pytest.approx(45.0)
        assert lon == pytest.approx(25.0)


class TestGapCenterStartOnly:
    """When gap has only start point, returns start location."""

    def test_start_only(self):
        from app.modules.satellite_query import _get_gap_center

        gap = MagicMock()
        gap.start_point_id = 1
        gap.end_point_id = None
        gap.vessel_id = 100

        start_pt = _make_ais_point(lat=42.0, lon=22.0, point_id=1)

        db = MagicMock()
        db.get.side_effect = lambda model, pid: start_pt if pid == 1 else None

        lat, lon = _get_gap_center(gap, db)
        assert lat == pytest.approx(42.0)
        assert lon == pytest.approx(22.0)


class TestGapCenterEndOnly:
    """When gap has only end point, returns end location."""

    def test_end_only(self):
        from app.modules.satellite_query import _get_gap_center

        gap = MagicMock()
        gap.start_point_id = None
        gap.end_point_id = 2
        gap.vessel_id = 100

        end_pt = _make_ais_point(lat=48.0, lon=28.0, point_id=2)

        db = MagicMock()
        db.get.side_effect = lambda model, pid: end_pt if pid == 2 else None

        lat, lon = _get_gap_center(gap, db)
        assert lat == pytest.approx(48.0)
        assert lon == pytest.approx(28.0)


class TestGapCenterAISFallback:
    """When gap has no points but vessel has AIS history, uses last AIS point."""

    def test_fallback_to_last_ais_position(self):
        from app.modules.satellite_query import _get_gap_center

        gap = MagicMock()
        gap.start_point_id = None
        gap.end_point_id = None
        gap.vessel_id = 100

        last_ais = _make_ais_point(lat=35.0, lon=25.0, point_id=99)

        db = MagicMock()
        db.get.return_value = None

        # Mock the AIS query chain: db.query(AISPoint).filter(...).order_by(...).first()
        query_mock = MagicMock()
        filter_mock = MagicMock()
        order_mock = MagicMock()

        db.query.return_value = query_mock
        query_mock.filter.return_value = filter_mock
        filter_mock.order_by.return_value = order_mock
        order_mock.first.return_value = last_ais

        lat, lon = _get_gap_center(gap, db)
        assert lat == pytest.approx(35.0)
        assert lon == pytest.approx(25.0)


class TestGapCenterNorthSeaFallback:
    """When gap has no points AND vessel has no AIS history, returns default."""

    def test_north_sea_fallback(self):
        from app.modules.satellite_query import _get_gap_center

        gap = MagicMock()
        gap.start_point_id = None
        gap.end_point_id = None
        gap.vessel_id = 100

        db = MagicMock()
        db.get.return_value = None

        # Mock the AIS query chain to return None
        query_mock = MagicMock()
        filter_mock = MagicMock()
        order_mock = MagicMock()

        db.query.return_value = query_mock
        query_mock.filter.return_value = filter_mock
        filter_mock.order_by.return_value = order_mock
        order_mock.first.return_value = None

        lat, lon = _get_gap_center(gap, db)
        assert lat == pytest.approx(55.0)
        assert lon == pytest.approx(15.0)


class TestGapCenterStartPointNotFound:
    """When start_point_id is set but db.get returns None, falls through."""

    def test_start_id_set_but_not_found(self):
        from app.modules.satellite_query import _get_gap_center

        gap = MagicMock()
        gap.start_point_id = 999
        gap.end_point_id = None
        gap.vessel_id = 100

        db = MagicMock()
        db.get.return_value = None

        # AIS fallback returns a point
        last_ais = _make_ais_point(lat=33.0, lon=33.0)
        query_mock = MagicMock()
        filter_mock = MagicMock()
        order_mock = MagicMock()

        db.query.return_value = query_mock
        query_mock.filter.return_value = filter_mock
        filter_mock.order_by.return_value = order_mock
        order_mock.first.return_value = last_ais

        lat, lon = _get_gap_center(gap, db)
        assert lat == pytest.approx(33.0)
        assert lon == pytest.approx(33.0)
