"""Tests for Mediterranean STS false positive reduction.

Covers:
  - Anchorage exclusion zones in corridors.yaml (Ceuta, Laconian Gulf, Hurd Bank, Cyprus)
  - _build_anchorage_exclusion_bboxes loads tagged corridors
  - _in_any_anchorage_exclusion correctly identifies positions in exclusion zones
  - Phase A applies stricter thresholds (12 windows, SOG < 0.5kn) in exclusion zones
  - Normal STS detection is NOT suppressed (events still created with enough windows)
"""

import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import yaml

from app.modules.sts_detector import (
    _MIN_CONSECUTIVE_WINDOWS_STRICT,
    _SOG_STATIONARY_STRICT,
    _build_anchorage_exclusion_bboxes,
    _in_any_anchorage_exclusion,
)

CORRIDORS_YAML = pathlib.Path(__file__).resolve().parents[2] / "config" / "corridors.yaml"


def _load_corridors() -> list[dict]:
    with open(CORRIDORS_YAML) as f:
        data = yaml.safe_load(f) or {}
    return data.get("corridors", [])


# ── YAML config tests ────────────────────────────────────────────────────


class TestAnchorageExclusionYAML:
    """Verify anchorage exclusion zones are properly configured in corridors.yaml."""

    def test_exclusion_zones_exist(self):
        corridors = _load_corridors()
        exclusion_names = [
            c["name"] for c in corridors if "anchorage_exclusion" in (c.get("tags") or [])
        ]
        assert "Ceuta Anchorage Exclusion" in exclusion_names
        assert "Laconian Gulf Anchorage Exclusion" in exclusion_names
        assert "Hurd Bank Anchorage Exclusion" in exclusion_names
        assert "Cyprus Anchorage Exclusion" in exclusion_names

    def test_exclusion_zones_are_anchorage_holding_type(self):
        corridors = _load_corridors()
        for c in corridors:
            if "anchorage_exclusion" in (c.get("tags") or []):
                assert c["corridor_type"] == "anchorage_holding", (
                    f"{c['name']} should be anchorage_holding"
                )

    def test_exclusion_zones_have_med_fp_reduction_tag(self):
        corridors = _load_corridors()
        for c in corridors:
            if "anchorage_exclusion" in (c.get("tags") or []):
                assert "med_fp_reduction" in c["tags"], f"{c['name']} missing med_fp_reduction tag"

    def test_laconian_gulf_has_sts_waiting_zone_tag(self):
        """Laconian Gulf is both an anchorage and a real STS waiting zone."""
        corridors = _load_corridors()
        laconian = [c for c in corridors if c["name"] == "Laconian Gulf Anchorage Exclusion"]
        assert len(laconian) == 1
        assert "sts_waiting_zone" in laconian[0]["tags"]

    def test_exclusion_zones_inside_parent_sts_zones(self):
        """Verify each exclusion zone bbox falls within its parent STS zone bbox."""
        corridors = _load_corridors()
        import re

        def parse_bbox(geom):
            pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", geom)
            lons = [float(p[0]) for p in pairs]
            lats = [float(p[1]) for p in pairs]
            return min(lons), min(lats), max(lons), max(lats)

        parent_zones = {
            "Ceuta": "Ceuta / Gibraltar STS Anchorage",
            "Laconian Gulf": "Laconian Gulf (Greece) — STS Zone",
            "Hurd Bank": "Hurd's Bank — Malta STS Anchorage",
            "Cyprus": "Cyprus offshore STS",
        }

        for exclusion_prefix, parent_name in parent_zones.items():
            exclusion = [
                c
                for c in corridors
                if c["name"].startswith(exclusion_prefix)
                and "anchorage_exclusion" in (c.get("tags") or [])
            ]
            parent = [c for c in corridors if c["name"] == parent_name]
            assert len(exclusion) == 1, f"Expected 1 exclusion zone for {exclusion_prefix}"
            assert len(parent) == 1, f"Expected parent zone {parent_name}"

            ex_bbox = parse_bbox(exclusion[0]["geometry"])
            pa_bbox = parse_bbox(parent[0]["geometry"])
            # Exclusion min_lon >= parent min_lon, etc.
            assert ex_bbox[0] >= pa_bbox[0], f"{exclusion_prefix} exclusion min_lon outside parent"
            assert ex_bbox[1] >= pa_bbox[1], f"{exclusion_prefix} exclusion min_lat outside parent"
            assert ex_bbox[2] <= pa_bbox[2], f"{exclusion_prefix} exclusion max_lon outside parent"
            assert ex_bbox[3] <= pa_bbox[3], f"{exclusion_prefix} exclusion max_lat outside parent"


# ── Detector helper tests ────────────────────────────────────────────────


def _make_corridor_mock(tags, geometry_wkt):
    """Create a mock Corridor with tags and geometry."""
    c = MagicMock()
    c.tags = tags
    c.geometry = geometry_wkt
    c.corridor_type = MagicMock()
    c.corridor_type.value = "anchorage_holding"
    return c


class TestBuildAnchorageExclusionBboxes:
    def test_filters_by_tag(self):
        corridors = [
            _make_corridor_mock(
                ["anchorage_exclusion", "med_fp_reduction"],
                "POLYGON((10 20, 11 20, 11 21, 10 21, 10 20))",
            ),
            _make_corridor_mock(["ship_to_ship"], "POLYGON((30 40, 31 40, 31 41, 30 41, 30 40))"),
        ]
        bboxes = _build_anchorage_exclusion_bboxes(corridors)
        assert len(bboxes) == 1
        assert bboxes[0] == (10.0, 20.0, 11.0, 21.0)

    def test_empty_without_tag(self):
        corridors = [
            _make_corridor_mock(["ship_to_ship"], "POLYGON((10 20, 11 20, 11 21, 10 21, 10 20))"),
        ]
        bboxes = _build_anchorage_exclusion_bboxes(corridors)
        assert len(bboxes) == 0

    def test_handles_none_tags(self):
        corridors = [_make_corridor_mock(None, "POLYGON((10 20, 11 20, 11 21, 10 21, 10 20))")]
        bboxes = _build_anchorage_exclusion_bboxes(corridors)
        assert len(bboxes) == 0

    def test_handles_string_tags(self):
        """Tags stored as comma-separated string should still work."""
        corridors = [
            _make_corridor_mock(
                "anchorage_exclusion, med_fp_reduction",
                "POLYGON((10 20, 11 20, 11 21, 10 21, 10 20))",
            ),
        ]
        bboxes = _build_anchorage_exclusion_bboxes(corridors)
        assert len(bboxes) == 1


class TestInAnyAnchorageExclusion:
    def test_inside_zone(self):
        bboxes = [(10.0, 20.0, 11.0, 21.0)]
        assert _in_any_anchorage_exclusion(20.5, 10.5, bboxes) is True

    def test_outside_zone(self):
        bboxes = [(10.0, 20.0, 11.0, 21.0)]
        assert _in_any_anchorage_exclusion(25.0, 15.0, bboxes) is False

    def test_empty_bboxes(self):
        assert _in_any_anchorage_exclusion(20.5, 10.5, []) is False

    def test_on_boundary(self):
        bboxes = [(10.0, 20.0, 11.0, 21.0)]
        assert _in_any_anchorage_exclusion(20.0, 10.0, bboxes) is True

    def test_multiple_zones(self):
        bboxes = [
            (10.0, 20.0, 11.0, 21.0),
            (30.0, 40.0, 31.0, 41.0),
        ]
        assert _in_any_anchorage_exclusion(40.5, 30.5, bboxes) is True
        assert _in_any_anchorage_exclusion(50.0, 50.0, bboxes) is False


# ── Constants tests ──────────────────────────────────────────────────────


class TestStricterThresholdConstants:
    def test_strict_window_count_higher_than_default(self):
        from app.modules.sts_detector import _MIN_CONSECUTIVE_WINDOWS

        assert _MIN_CONSECUTIVE_WINDOWS_STRICT > _MIN_CONSECUTIVE_WINDOWS
        assert _MIN_CONSECUTIVE_WINDOWS_STRICT == 12

    def test_strict_sog_lower_than_default(self):
        from app.modules.sts_detector import _SOG_STATIONARY

        assert _SOG_STATIONARY_STRICT < _SOG_STATIONARY
        assert _SOG_STATIONARY_STRICT == 0.5


# ── Integration-level tests for Phase A exclusion logic ──────────────────


class TestPhaseAExclusionIntegration:
    """Test that _phase_a applies stricter thresholds in anchorage exclusion zones."""

    def _make_ais_point(self, vessel_id, lat, lon, sog, cog, timestamp):
        pt = MagicMock()
        pt.vessel_id = vessel_id
        pt.lat = lat
        pt.lon = lon
        pt.sog = sog
        pt.cog = cog
        pt.heading = cog
        pt.timestamp_utc = timestamp
        return pt

    def _make_points_for_pair(self, vid1, vid2, lat, lon, n_windows, sog=0.3, start=None):
        """Generate AIS points for two vessels near each other for n_windows 15-min buckets."""
        if start is None:
            start = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)
        points = []
        for i in range(n_windows):
            ts = start + timedelta(minutes=15 * i)
            # Vessels 50m apart (well within 200m threshold)
            points.append(self._make_ais_point(vid1, lat, lon, sog, 90.0, ts))
            points.append(self._make_ais_point(vid2, lat + 0.0004, lon, sog, 90.0, ts))
        return points

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_normal_zone_8_windows_creates_event(self, mock_overlap, mock_bunker):
        """8 windows in a non-exclusion zone should create an STS event."""
        from app.modules.sts_detector import _phase_a

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []  # no ports

        # Position outside any exclusion zone
        points = self._make_points_for_pair(1, 2, 50.0, 10.0, n_windows=8, sog=0.3)

        corridors = []  # no corridors — no exclusion zones
        sts_bboxes = []

        created = _phase_a(db, points, sts_bboxes, corridors, config={})
        assert created == 1

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_exclusion_zone_8_windows_rejected(self, mock_overlap, mock_bunker):
        """8 windows inside an exclusion zone should NOT create an event (needs 12)."""
        from app.modules.sts_detector import _phase_a

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        # Ceuta anchorage exclusion zone center: ~(-5.315, 35.90)
        lat, lon = 35.90, -5.31
        points = self._make_points_for_pair(1, 2, lat, lon, n_windows=8, sog=0.3)

        # Create a corridor that matches the Ceuta exclusion zone
        ceuta_exclusion = _make_corridor_mock(
            ["anchorage_exclusion", "med_fp_reduction"],
            "POLYGON((-5.35 35.88, -5.28 35.88, -5.28 35.92, -5.35 35.92, -5.35 35.88))",
        )
        corridors = [ceuta_exclusion]
        sts_bboxes = []

        created = _phase_a(db, points, sts_bboxes, corridors, config={})
        assert created == 0

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_exclusion_zone_12_windows_low_sog_creates_event(self, mock_overlap, mock_bunker):
        """12 windows with SOG < 0.5kn inside an exclusion zone should create an event."""
        from app.modules.sts_detector import _phase_a

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        lat, lon = 35.90, -5.31
        points = self._make_points_for_pair(1, 2, lat, lon, n_windows=12, sog=0.3)

        ceuta_exclusion = _make_corridor_mock(
            ["anchorage_exclusion", "med_fp_reduction"],
            "POLYGON((-5.35 35.88, -5.28 35.88, -5.28 35.92, -5.35 35.92, -5.35 35.88))",
        )
        corridors = [ceuta_exclusion]
        sts_bboxes = []

        created = _phase_a(db, points, sts_bboxes, corridors, config={})
        assert created == 1

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_exclusion_zone_12_windows_high_sog_rejected(self, mock_overlap, mock_bunker):
        """12 windows but SOG >= 0.5kn in exclusion zone should be rejected."""
        from app.modules.sts_detector import _phase_a

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        lat, lon = 35.90, -5.31
        # SOG = 0.7 — above the strict 0.5 threshold but below normal 1.0
        points = self._make_points_for_pair(1, 2, lat, lon, n_windows=12, sog=0.7)

        ceuta_exclusion = _make_corridor_mock(
            ["anchorage_exclusion", "med_fp_reduction"],
            "POLYGON((-5.35 35.88, -5.28 35.88, -5.28 35.92, -5.35 35.92, -5.35 35.88))",
        )
        corridors = [ceuta_exclusion]
        sts_bboxes = []

        created = _phase_a(db, points, sts_bboxes, corridors, config={})
        assert created == 0

    @patch("app.modules.sts_detector._is_bunkering_vessel", return_value=False)
    @patch("app.modules.sts_detector._overlap_exists", return_value=False)
    def test_normal_zone_sog_07_still_creates_event(self, mock_overlap, mock_bunker):
        """SOG 0.7 outside exclusion zone should still create an event (< 1.0 threshold)."""
        from app.modules.sts_detector import _phase_a

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        points = self._make_points_for_pair(1, 2, 50.0, 10.0, n_windows=8, sog=0.7)

        corridors = []
        sts_bboxes = []

        created = _phase_a(db, points, sts_bboxes, corridors, config={})
        assert created == 1
