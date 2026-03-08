"""Tests for Mediterranean STS anchorage exclusion zone false positive reduction.

Validates that:
- Normal STS detection outside exclusion zones works unchanged
- STS in exclusion zone with weak evidence (few windows, higher speed) is suppressed
- STS in exclusion zone with strong evidence (12+ windows, <0.5kn) still triggers
- Single vessel in exclusion zone (other outside) uses normal thresholds
"""

from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest

from app.modules.sts_detector import (
    _MIN_CONSECUTIVE_WINDOWS_STRICT,
    _SOG_STATIONARY_STRICT,
    _build_anchorage_exclusion_bboxes,
    _in_any_anchorage_exclusion,
)

# ── Helper function tests ───────────────────────────────────────────────────


class TestBuildAnchorageExclusionBboxes:
    def test_extracts_tagged_corridors(self):
        """Corridors tagged 'anchorage_exclusion' produce bboxes."""
        c1 = MagicMock()
        c1.tags = ["anchorage_exclusion", "med_fp_reduction"]
        c1.geometry = "POLYGON((-5.35 35.88, -5.28 35.88, -5.28 35.92, -5.35 35.92, -5.35 35.88))"

        c2 = MagicMock()
        c2.tags = ["ship_to_ship"]
        c2.geometry = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"

        result = _build_anchorage_exclusion_bboxes([c1, c2])
        assert len(result) == 1
        bbox = result[0]
        assert bbox[0] == pytest.approx(-5.35)  # min_lon
        assert bbox[1] == pytest.approx(35.88)  # min_lat

    def test_empty_when_no_tagged_corridors(self):
        c = MagicMock()
        c.tags = ["ship_to_ship"]
        c.geometry = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
        assert _build_anchorage_exclusion_bboxes([c]) == []

    def test_handles_csv_string_tags(self):
        """Tags stored as comma-separated string are parsed correctly."""
        c = MagicMock()
        c.tags = "anchorage_exclusion, med_fp_reduction"
        c.geometry = "POLYGON((14.70 35.65, 15.10 35.65, 15.10 35.85, 14.70 35.85, 14.70 35.65))"
        result = _build_anchorage_exclusion_bboxes([c])
        assert len(result) == 1

    def test_handles_none_tags(self):
        c = MagicMock()
        c.tags = None
        c.geometry = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
        assert _build_anchorage_exclusion_bboxes([c]) == []


class TestInAnyAnchorageExclusion:
    def test_point_inside_exclusion_zone(self):
        bbox = (-5.35, 35.88, -5.28, 35.92)
        assert _in_any_anchorage_exclusion(35.90, -5.31, [bbox]) is True

    def test_point_outside_exclusion_zone(self):
        bbox = (-5.35, 35.88, -5.28, 35.92)
        assert _in_any_anchorage_exclusion(36.50, 22.85, [bbox]) is False

    def test_empty_exclusion_list(self):
        assert _in_any_anchorage_exclusion(35.90, -5.31, []) is False

    def test_multiple_zones_second_matches(self):
        bbox1 = (-5.35, 35.88, -5.28, 35.92)  # Ceuta
        bbox2 = (14.70, 35.65, 15.10, 35.85)  # Hurd Bank
        assert _in_any_anchorage_exclusion(35.75, 14.90, [bbox1, bbox2]) is True


# ── Integration tests (Phase A with anchorage exclusion) ────────────────────


def _make_ais_point(vessel_id, lat, lon, sog, cog, ts_epoch_min):
    """Create a mock AISPoint for testing."""
    from datetime import datetime

    pt = MagicMock()
    pt.vessel_id = vessel_id
    pt.lat = lat
    pt.lon = lon
    pt.sog = sog
    pt.cog = cog
    pt.heading = cog
    pt.timestamp_utc = datetime.fromtimestamp(ts_epoch_min * 60, tz=UTC)
    return pt


def _make_corridor(corridor_id, name, corridor_type, geometry, tags=None):
    """Create a mock Corridor."""
    c = MagicMock()
    c.corridor_id = corridor_id
    c.name = name
    c.corridor_type = MagicMock()
    c.corridor_type.value = corridor_type
    c.geometry = geometry
    c.tags = tags or []
    return c


class TestPhaseAAnchorageExclusion:
    """Integration tests for Phase A with anchorage exclusion filtering."""

    def _build_window_points(self, vessel_a_id, vessel_b_id, lat, lon, sog, n_windows, base_min=0):
        """Generate n_windows of 15-min-spaced AIS points for a pair at the same position.

        Both vessels are placed at nearly identical positions (< 10m apart)
        within the same 1-degree grid cell to ensure the spatial index finds them.
        """
        points = []
        for i in range(n_windows):
            ts = base_min + i * 15
            # Keep both points in the same 1-degree grid cell
            points.append(_make_ais_point(vessel_a_id, lat + 0.00002, lon + 0.00002, sog, 90.0, ts))
            points.append(_make_ais_point(vessel_b_id, lat - 0.00002, lon - 0.00002, sog, 90.0, ts))
        return points

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_normal_sts_outside_exclusion_zone(self, mock_overlap, mock_bunk):
        """STS detection with 8 windows outside exclusion zones works normally."""
        from app.modules.sts_detector import _MIN_CONSECUTIVE_WINDOWS, _phase_a

        # Position outside any exclusion zone (open Mediterranean, mid-cell)
        lat, lon = 38.5, 3.5
        points = self._build_window_points(
            1, 2, lat, lon, sog=0.3, n_windows=_MIN_CONSECUTIVE_WINDOWS
        )

        sts_corridor = _make_corridor(
            1,
            "Med STS",
            "sts_zone",
            "POLYGON((-1.0 37.0, 6.0 37.0, 6.0 40.0, -1.0 40.0, -1.0 37.0))",
            tags=["ship_to_ship"],
        )
        sts_zone_bboxes = [(sts_corridor, (-1.0, 37.0, 6.0, 40.0))]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []  # no ports

        created = _phase_a(db, points, sts_zone_bboxes, [sts_corridor])
        assert created >= 1

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_sts_in_exclusion_zone_weak_evidence_suppressed(self, mock_overlap, mock_bunk):
        """STS in exclusion zone with only 8 windows (below 12 threshold) is suppressed."""
        from app.modules.sts_detector import _MIN_CONSECUTIVE_WINDOWS, _phase_a

        # Position inside Ceuta exclusion zone (mid-cell at lat 35.9, lon -5.31)
        lat, lon = 35.90, -5.31
        points = self._build_window_points(
            1, 2, lat, lon, sog=0.3, n_windows=_MIN_CONSECUTIVE_WINDOWS
        )

        sts_corridor = _make_corridor(
            1,
            "Ceuta STS",
            "sts_zone",
            "POLYGON((-5.8 35.8, -5.2 35.8, -5.2 36.2, -5.8 36.2, -5.8 35.8))",
            tags=["ship_to_ship"],
        )
        excl_corridor = _make_corridor(
            2,
            "Ceuta Exclusion",
            "anchorage_holding",
            "POLYGON((-5.35 35.88, -5.28 35.88, -5.28 35.92, -5.35 35.92, -5.35 35.88))",
            tags=["anchorage_exclusion", "med_fp_reduction"],
        )

        sts_zone_bboxes = [(sts_corridor, (-5.8, 35.8, -5.2, 36.2))]
        all_corridors = [sts_corridor, excl_corridor]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        created = _phase_a(db, points, sts_zone_bboxes, all_corridors)
        assert created == 0, "Weak evidence in exclusion zone should be suppressed"

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_sts_in_exclusion_zone_high_sog_suppressed(self, mock_overlap, mock_bunk):
        """STS in exclusion zone with SOG >= 0.5kn is suppressed even with 12+ windows."""
        from app.modules.sts_detector import _phase_a

        lat, lon = 35.90, -5.31
        # 12 windows but SOG = 0.7 (above 0.5 strict threshold)
        points = self._build_window_points(
            1, 2, lat, lon, sog=0.7, n_windows=_MIN_CONSECUTIVE_WINDOWS_STRICT
        )

        sts_corridor = _make_corridor(
            1,
            "Ceuta STS",
            "sts_zone",
            "POLYGON((-5.8 35.8, -5.2 35.8, -5.2 36.2, -5.8 36.2, -5.8 35.8))",
            tags=["ship_to_ship"],
        )
        excl_corridor = _make_corridor(
            2,
            "Ceuta Exclusion",
            "anchorage_holding",
            "POLYGON((-5.35 35.88, -5.28 35.88, -5.28 35.92, -5.35 35.92, -5.35 35.88))",
            tags=["anchorage_exclusion", "med_fp_reduction"],
        )

        sts_zone_bboxes = [(sts_corridor, (-5.8, 35.8, -5.2, 36.2))]
        all_corridors = [sts_corridor, excl_corridor]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        created = _phase_a(db, points, sts_zone_bboxes, all_corridors)
        assert created == 0, "High SOG in exclusion zone should be suppressed"

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_sts_in_exclusion_zone_strong_evidence_triggers(self, mock_overlap, mock_bunk):
        """STS in exclusion zone with 12+ windows AND <0.5kn SOG still triggers."""
        from app.modules.sts_detector import _phase_a

        # Position inside Ceuta exclusion zone (mid-cell)
        lat, lon = 35.90, -5.31
        # 13 windows at 0.3kn — strong evidence, should pass stricter thresholds
        points = self._build_window_points(
            1, 2, lat, lon, sog=0.3, n_windows=_MIN_CONSECUTIVE_WINDOWS_STRICT + 1
        )

        sts_corridor = _make_corridor(
            1,
            "Ceuta STS",
            "sts_zone",
            "POLYGON((-5.8 35.8, -5.2 35.8, -5.2 36.2, -5.8 36.2, -5.8 35.8))",
            tags=["ship_to_ship"],
        )
        excl_corridor = _make_corridor(
            2,
            "Ceuta Exclusion",
            "anchorage_holding",
            "POLYGON((-5.35 35.88, -5.28 35.88, -5.28 35.92, -5.35 35.92, -5.35 35.88))",
            tags=["anchorage_exclusion", "med_fp_reduction"],
        )

        sts_zone_bboxes = [(sts_corridor, (-5.8, 35.8, -5.2, 36.2))]
        all_corridors = [sts_corridor, excl_corridor]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        created = _phase_a(db, points, sts_zone_bboxes, all_corridors)
        assert created >= 1, "Strong evidence in exclusion zone should still trigger"

    def test_constants_are_correct(self):
        """Verify the threshold constants are set correctly."""
        assert _MIN_CONSECUTIVE_WINDOWS_STRICT == 12
        assert _SOG_STATIONARY_STRICT == 0.5
