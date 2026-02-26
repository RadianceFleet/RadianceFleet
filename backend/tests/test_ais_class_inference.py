"""Tests for AIS class inference from transmission intervals."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.modules.vessel_enrichment import infer_ais_class


def _make_points(intervals_seconds: list[float], base_time=None):
    """Create mock AIS points with given inter-point intervals (descending order)."""
    if base_time is None:
        base_time = datetime(2026, 2, 20, 12, 0, 0)

    points = []
    current = base_time
    for i in range(len(intervals_seconds) + 1):
        pt = MagicMock()
        pt.timestamp_utc = current
        points.append(pt)
        if i < len(intervals_seconds):
            current = current - timedelta(seconds=intervals_seconds[i])
    return points


def _make_db_with_points(points):
    """Build a mock DB that returns the given points from the query chain."""
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = points
    return db


class TestInferAISClass:
    def test_infer_class_b_from_slow_intervals(self):
        """Median interval >25s → Class B."""
        # 10 intervals of 35 seconds each (typical Class B SOTDMA)
        points = _make_points([35] * 10)
        vessel = MagicMock()
        vessel.vessel_id = 1
        db = _make_db_with_points(points)

        result = infer_ais_class(db, vessel)
        assert result == "B"

    def test_infer_class_a_from_fast_intervals(self):
        """Median interval ≤10s → Class A."""
        # 10 intervals of 5 seconds each (typical Class A at speed)
        points = _make_points([5] * 10)
        vessel = MagicMock()
        vessel.vessel_id = 1
        db = _make_db_with_points(points)

        result = infer_ais_class(db, vessel)
        assert result == "A"

    def test_insufficient_points_returns_none(self):
        """Fewer than 5 points → cannot infer."""
        points = _make_points([5, 5, 5])  # Only 4 points
        vessel = MagicMock()
        vessel.vessel_id = 1
        db = _make_db_with_points(points)

        result = infer_ais_class(db, vessel)
        assert result is None

    def test_ambiguous_interval_returns_none(self):
        """Median interval between 10-25s → ambiguous, returns None."""
        points = _make_points([15] * 10)
        vessel = MagicMock()
        vessel.vessel_id = 1
        db = _make_db_with_points(points)

        result = infer_ais_class(db, vessel)
        assert result is None
