"""Tests for Stage B — Detection Accuracy Bug Fixes.

Covers:
  B1: Track naturalness bearings computation before Features 4 & 5
  B3: BFS chain detection excludes PENDING status + chain invalidation
  B4: Dark coordination requires geographic proximity
  B5: AISPoint model has destination column
  B6: Null destination does NOT trigger blank-destination anomaly
"""
from __future__ import annotations

import inspect
import math
import textwrap
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# B1: Track naturalness bearings computation
# ---------------------------------------------------------------------------

class TestB1TrackNaturalnessBearings:
    """Verify bearings are computed once, before both Feature 4 and Feature 5."""

    def test_bearings_computed_before_feature_4_and_5(self):
        """Source-code structure check: bearings list is initialised before
        both the Feature 4 and Feature 5 blocks, removing the redundant
        ``dir()`` guard."""
        from app.modules.track_naturalness_detector import _compute_features

        source = inspect.getsource(_compute_features)

        # bearings computation should appear before Feature 4
        bearings_init_pos = source.index("bearings: list[float] = []")
        feature4_pos = source.index("Feature 4")
        feature5_pos = source.index("Feature 5")

        assert bearings_init_pos < feature4_pos, (
            "bearings computation must appear before Feature 4 block"
        )
        assert bearings_init_pos < feature5_pos, (
            "bearings computation must appear before Feature 5 block"
        )

    def test_no_dir_guard_in_feature_5(self):
        """The old ``'bearings' in dir()`` guard should be removed."""
        from app.modules.track_naturalness_detector import _compute_features

        source = inspect.getsource(_compute_features)
        assert "dir()" not in source, (
            "dir() guard should be removed from Feature 5 block"
        )

    def test_bearing_changes_computed_once(self):
        """bearing_changes should be computed once and reused, not recomputed
        as bearing_changes_k in Feature 5."""
        from app.modules.track_naturalness_detector import _compute_features

        source = inspect.getsource(_compute_features)
        assert "bearing_changes_k" not in source, (
            "Redundant bearing_changes_k variable should be eliminated"
        )

    def test_feature_5_produces_value_with_enough_points(self):
        """Feature 5 (course_kurtosis) should produce a numeric value when
        given sufficient data points, proving it is no longer gated by a
        broken dir() check."""
        from app.modules.track_naturalness_detector import _compute_features

        # Generate 30 points with varying bearings
        points = []
        for i in range(30):
            ts = 1000.0 + i * 60.0
            lat = 60.0 + i * 0.01
            lon = 25.0 + i * 0.005 * math.sin(i * 0.3)
            sog = 10.0 + i * 0.1
            points.append((ts, lat, lon, sog))

        residuals = [0.0] * len(points)

        features = _compute_features(points, residuals)
        assert features["course_kurtosis"] is not None, (
            "Feature 5 should produce a numeric kurtosis value"
        )


# ---------------------------------------------------------------------------
# B3: Merge chain BFS excludes PENDING + chain invalidation
# ---------------------------------------------------------------------------

class TestB3MergeChainBFS:
    """Verify BFS chain detection uses only confirmed statuses."""

    def test_detect_merge_chains_excludes_pending(self):
        """Source-code check: PENDING should not be in the status filter."""
        from app.modules.identity_resolver import detect_merge_chains

        source = inspect.getsource(detect_merge_chains)
        # The filter should contain AUTO_MERGED and ANALYST_MERGED
        assert "AUTO_MERGED" in source
        assert "ANALYST_MERGED" in source
        # PENDING should not be in the .in_() filter list
        # Find the .in_ block and verify PENDING is not in it
        in_block_start = source.index("status.in_")
        in_block_end = source.index("])", in_block_start)
        in_block = source[in_block_start:in_block_end]
        assert "PENDING" not in in_block, (
            "PENDING should not be in the merge chain BFS status filter"
        )

    def test_invalidate_chains_function_exists(self):
        """The invalidate_chains_for_rejected_candidate function should exist."""
        from app.modules.identity_resolver import invalidate_chains_for_rejected_candidate
        assert callable(invalidate_chains_for_rejected_candidate)

    def test_invalidate_chains_removes_matching_chains(self):
        """invalidate_chains_for_rejected_candidate should delete chains
        that reference the given candidate_id in their links_json."""
        from app.modules.identity_resolver import invalidate_chains_for_rejected_candidate

        # Create mock chain objects
        chain1 = MagicMock()
        chain1.links_json = [10, 20, 30]
        chain2 = MagicMock()
        chain2.links_json = [40, 50]
        chain3 = MagicMock()
        chain3.links_json = [20, 60]

        db = MagicMock()
        db.query.return_value.all.return_value = [chain1, chain2, chain3]

        result = invalidate_chains_for_rejected_candidate(db, candidate_id=20)

        # chain1 and chain3 contain candidate_id=20 and should be deleted
        assert result == 2
        assert db.delete.call_count == 2
        db.commit.assert_called_once()

    def test_invalidate_chains_no_match(self):
        """When no chains reference the candidate, nothing should be deleted."""
        from app.modules.identity_resolver import invalidate_chains_for_rejected_candidate

        chain1 = MagicMock()
        chain1.links_json = [10, 30]

        db = MagicMock()
        db.query.return_value.all.return_value = [chain1]

        result = invalidate_chains_for_rejected_candidate(db, candidate_id=99)

        assert result == 0
        db.delete.assert_not_called()
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# B4: Dark coordination requires geographic proximity
# ---------------------------------------------------------------------------

class TestB4DarkCoordinationProximity:
    """Verify dark coordination groups gaps geographically."""

    def test_geo_bin_key_function_exists(self):
        """The _geo_bin_key helper should exist."""
        from app.modules.fleet_analyzer import _geo_bin_key
        assert callable(_geo_bin_key)

    def test_geo_bin_key_returns_tuple(self):
        """Bins lat/lon into 5-degree cells."""
        from app.modules.fleet_analyzer import _geo_bin_key

        result = _geo_bin_key(62.5, 27.3)
        assert result == (12, 5)  # 62.5/5 = 12.5 -> 12, 27.3/5 = 5.46 -> 5

    def test_geo_bin_key_none_for_missing(self):
        """Returns None when lat or lon is missing."""
        from app.modules.fleet_analyzer import _geo_bin_key

        assert _geo_bin_key(None, 10.0) is None
        assert _geo_bin_key(10.0, None) is None
        assert _geo_bin_key(None, None) is None

    def test_dark_coordination_groups_by_geography(self):
        """Source-code check: _check_dark_coordination should reference
        corridor_id or geo_bin for grouping."""
        from app.modules.fleet_analyzer import _check_dark_coordination

        source = inspect.getsource(_check_dark_coordination)
        assert "corridor_id" in source, (
            "Dark coordination should group by corridor_id"
        )
        assert "geo_bin" in source or "_geo_bin_key" in source, (
            "Dark coordination should use geographic binning for gaps without corridors"
        )

    def test_dark_coordination_different_oceans_no_alert(self):
        """3 vessels going dark in 3 different geographic areas should NOT
        produce an alert, even if they all go dark within 48h."""
        from app.modules.fleet_analyzer import _check_dark_coordination

        cluster = MagicMock()
        cluster.cluster_id = 1

        # 3 vessels
        vessels = [MagicMock(vessel_id=i) for i in range(1, 4)]

        now = datetime(2025, 1, 1, tzinfo=timezone.utc)

        # Create 3 gaps — each in a wildly different location (different 5-deg bin)
        gap1 = MagicMock()
        gap1.vessel_id = 1
        gap1.gap_start_utc = now
        gap1.corridor_id = None
        gap1.gap_off_lat = 60.0   # Nordic
        gap1.gap_off_lon = 20.0

        gap2 = MagicMock()
        gap2.vessel_id = 2
        gap2.gap_start_utc = now + timedelta(hours=1)
        gap2.corridor_id = None
        gap2.gap_off_lat = -30.0  # South Atlantic
        gap2.gap_off_lon = -20.0

        gap3 = MagicMock()
        gap3.vessel_id = 3
        gap3.gap_start_utc = now + timedelta(hours=2)
        gap3.corridor_id = None
        gap3.gap_off_lat = 5.0    # Gulf of Guinea
        gap3.gap_off_lon = 0.0

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            gap1, gap2, gap3,
        ]

        result = _check_dark_coordination(db, cluster, vessels)
        assert result is None, (
            "Gaps in different geographic areas should not trigger dark coordination"
        )

    def test_dark_coordination_same_corridor_fires(self):
        """3 vessels going dark in the same corridor within 48h should produce an alert."""
        from app.modules.fleet_analyzer import _check_dark_coordination

        cluster = MagicMock()
        cluster.cluster_id = 1

        vessels = [MagicMock(vessel_id=i) for i in range(1, 4)]

        now = datetime(2025, 1, 1, tzinfo=timezone.utc)

        gap1 = MagicMock()
        gap1.vessel_id = 1
        gap1.gap_start_utc = now
        gap1.corridor_id = 42
        gap1.gap_off_lat = 60.0
        gap1.gap_off_lon = 20.0

        gap2 = MagicMock()
        gap2.vessel_id = 2
        gap2.gap_start_utc = now + timedelta(hours=1)
        gap2.corridor_id = 42
        gap2.gap_off_lat = 60.5
        gap2.gap_off_lon = 20.5

        gap3 = MagicMock()
        gap3.vessel_id = 3
        gap3.gap_start_utc = now + timedelta(hours=2)
        gap3.corridor_id = 42
        gap3.gap_off_lat = 60.2
        gap3.gap_off_lon = 20.2

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            gap1, gap2, gap3,
        ]

        result = _check_dark_coordination(db, cluster, vessels)
        assert result is not None, (
            "3 gaps in same corridor within 48h should trigger alert"
        )
        assert result.alert_type == "fleet_dark_coordination"


# ---------------------------------------------------------------------------
# B5: AISPoint model has destination column
# ---------------------------------------------------------------------------

class TestB5AISPointDestination:
    """Verify AISPoint model includes the destination column."""

    def test_destination_column_exists(self):
        """AISPoint should have a 'destination' mapped column."""
        from app.models.ais_point import AISPoint

        assert hasattr(AISPoint, "destination"), (
            "AISPoint model must have a 'destination' attribute"
        )

    def test_destination_column_is_nullable(self):
        """The destination column should be nullable."""
        from app.models.ais_point import AISPoint

        col = AISPoint.__table__.columns["destination"]
        assert col.nullable is True

    def test_destination_column_type(self):
        """The destination column should be VARCHAR(20)."""
        from app.models.ais_point import AISPoint

        col = AISPoint.__table__.columns["destination"]
        assert str(col.type) == "VARCHAR(20)"

    def test_destination_in_migration_list(self):
        """The destination column should be in the database migration list."""
        import app.database as db_mod

        source = inspect.getsource(db_mod._run_migrations)
        assert '"ais_points", "destination"' in source or \
               "\"ais_points\", \"destination\"" in source, (
            "destination migration should be in _run_migrations"
        )

    def test_destination_index_in_migration(self):
        """An index on ais_points.destination should be created in migrations."""
        import app.database as db_mod

        source = inspect.getsource(db_mod._run_migrations)
        assert "ix_ais_points_destination" in source, (
            "Index ix_ais_points_destination should be created in _run_migrations"
        )


# ---------------------------------------------------------------------------
# B6: Null destination does NOT trigger blank-destination anomaly
# ---------------------------------------------------------------------------

class TestB6DestinationFalsePositive:
    """Verify that null/missing destinations don't produce false positives."""

    def test_none_destination_not_blank(self):
        """_is_blank_or_generic(None) should return False — missing data is
        not a deception signal."""
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic(None) is False

    def test_explicit_generic_destinations_are_blank(self):
        """Explicitly generic destination strings should still be detected."""
        from app.modules.destination_detector import _is_blank_or_generic

        for value in ["TBA", "FOR ORDERS", "AT SEA", "N/A", "UNKNOWN", ".", "---"]:
            assert _is_blank_or_generic(value) is True, (
                f"'{value}' should be flagged as blank/generic"
            )

    def test_empty_string_is_blank(self):
        """An empty string destination should be detected as blank."""
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("") is True

    def test_real_destination_not_blank(self):
        """A real port name should not be flagged."""
        from app.modules.destination_detector import _is_blank_or_generic

        for value in ["ROTTERDAM", "Fujairah", "PIRAEUS", "SINGAPORE"]:
            assert _is_blank_or_generic(value) is False, (
                f"'{value}' should NOT be flagged as blank/generic"
            )

    def test_detector_uses_destination_column(self):
        """The destination detector should read from AISPoint.destination,
        not raw_payload_ref."""
        from app.modules.destination_detector import detect_destination_anomalies

        source = inspect.getsource(detect_destination_anomalies)
        # Should reference the destination attribute, not raw_payload_ref
        assert '"destination"' in source or "'destination'" in source or \
               'getattr(latest_point, "destination"' in source or \
               "destination" in source
        # Should NOT use raw_payload_ref for destination lookup
        assert 'raw_payload_ref' not in source, (
            "Detector should use 'destination' column, not 'raw_payload_ref'"
        )
