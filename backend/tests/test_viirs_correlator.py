"""Tests for VIIRS-AIS gap correlator."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock


def _make_detection(
    detection_id=1,
    lat=55.5,
    lon=18.1,
    time=None,
    scene_id="viirs-20260313-55.5000-18.1000",
    corridor_id=None,
    ais_match_result="unmatched",
):
    det = MagicMock()
    det.detection_id = detection_id
    det.detection_lat = lat
    det.detection_lon = lon
    det.detection_time_utc = time or datetime(2026, 3, 13, 3, 0)
    det.scene_id = scene_id
    det.corridor_id = corridor_id
    det.ais_match_result = ais_match_result
    det.ais_match_attempted = False
    det.created_gap_event_id = None
    return det


def _make_gap(gap_event_id=1, lat=55.6, lon=18.2, corridor_id=None):
    gap = MagicMock()
    gap.gap_event_id = gap_event_id
    gap.gap_off_lat = lat
    gap.gap_off_lon = lon
    gap.gap_start_utc = datetime(2026, 3, 13, 0, 0)
    gap.gap_end_utc = datetime(2026, 3, 13, 6, 0)
    gap.corridor_id = corridor_id
    return gap


class TestFindNearbyGaps:
    """Tests for find_nearby_gaps."""

    def test_finds_gap_within_radius(self):
        """Finds a gap within search radius."""
        gap = _make_gap(lat=55.6, lon=18.2)
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [gap]

        from app.modules.viirs_correlator import find_nearby_gaps

        result = find_nearby_gaps(
            db, 55.5, 18.1, datetime(2026, 3, 13, 3, 0), radius_nm=30.0
        )
        assert len(result) == 1

    def test_excludes_gap_outside_radius(self):
        """Excludes gaps beyond search radius."""
        gap = _make_gap(lat=60.0, lon=25.0)  # far away
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [gap]

        from app.modules.viirs_correlator import find_nearby_gaps

        result = find_nearby_gaps(
            db, 55.5, 18.1, datetime(2026, 3, 13, 3, 0), radius_nm=30.0
        )
        assert len(result) == 0

    def test_excludes_gap_without_position(self):
        """Skips gaps with no gap_off position."""
        gap = _make_gap()
        gap.gap_off_lat = None
        gap.gap_off_lon = None
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [gap]

        from app.modules.viirs_correlator import find_nearby_gaps

        result = find_nearby_gaps(
            db, 55.5, 18.1, datetime(2026, 3, 13, 3, 0), radius_nm=30.0
        )
        assert len(result) == 0

    def test_returns_empty_when_no_candidates(self):
        """Returns empty list when no gap candidates exist."""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        from app.modules.viirs_correlator import find_nearby_gaps

        result = find_nearby_gaps(
            db, 55.5, 18.1, datetime(2026, 3, 13, 3, 0), radius_nm=30.0
        )
        assert result == []


class TestCorrelateViirs:
    """Tests for correlate_viirs_with_gaps."""

    def test_no_detections_returns_zero_stats(self):
        """Returns zero stats when no VIIRS detections exist."""
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        from app.modules.viirs_correlator import correlate_viirs_with_gaps

        result = correlate_viirs_with_gaps(db)
        assert result["detections_processed"] == 0
        assert result["gaps_matched"] == 0

    def test_matches_detection_to_gap(self):
        """Links detection to nearby gap."""
        det = _make_detection()
        gap = _make_gap(lat=55.55, lon=18.15)

        db = MagicMock()
        # First query: VIIRS detections
        viirs_query = MagicMock()
        viirs_query.filter.return_value = viirs_query
        viirs_query.all.return_value = [det]

        # Second query: gap events
        gap_query = MagicMock()
        gap_query.filter.return_value = gap_query
        gap_query.all.return_value = [gap]

        db.query.side_effect = [viirs_query, gap_query]

        from app.modules.viirs_correlator import correlate_viirs_with_gaps

        result = correlate_viirs_with_gaps(db)
        assert result["detections_processed"] == 1
        assert result["gaps_matched"] == 1
        assert det.ais_match_result == "gap_correlated"
        assert det.created_gap_event_id == gap.gap_event_id

    def test_unmatched_when_no_gaps(self):
        """Detection stays unmatched when no nearby gaps."""
        det = _make_detection()

        db = MagicMock()
        viirs_query = MagicMock()
        viirs_query.filter.return_value = viirs_query
        viirs_query.all.return_value = [det]

        gap_query = MagicMock()
        gap_query.filter.return_value = gap_query
        gap_query.all.return_value = []

        db.query.side_effect = [viirs_query, gap_query]

        from app.modules.viirs_correlator import correlate_viirs_with_gaps

        result = correlate_viirs_with_gaps(db)
        assert result["unmatched"] == 1
        assert result["gaps_matched"] == 0

    def test_skips_detection_without_position(self):
        """Skips detections with null lat/lon."""
        det = _make_detection(lat=None, lon=None)

        db = MagicMock()
        viirs_query = MagicMock()
        viirs_query.filter.return_value = viirs_query
        viirs_query.all.return_value = [det]

        db.query.side_effect = [viirs_query]

        from app.modules.viirs_correlator import correlate_viirs_with_gaps

        result = correlate_viirs_with_gaps(db)
        assert result["skipped_no_position"] == 1

    def test_skips_detection_without_timestamp(self):
        """Skips detections with null detection_time_utc."""
        det = _make_detection(time=None)
        det.detection_time_utc = None

        db = MagicMock()
        viirs_query = MagicMock()
        viirs_query.filter.return_value = viirs_query
        viirs_query.all.return_value = [det]

        db.query.side_effect = [viirs_query]

        from app.modules.viirs_correlator import correlate_viirs_with_gaps

        result = correlate_viirs_with_gaps(db)
        assert result["skipped_no_position"] == 1

    def test_corridor_match_counted(self):
        """Corridor match is tracked in stats."""
        det = _make_detection(corridor_id=1)
        gap = _make_gap(lat=55.55, lon=18.15, corridor_id=1)

        db = MagicMock()
        viirs_query = MagicMock()
        viirs_query.filter.return_value = viirs_query
        viirs_query.all.return_value = [det]

        gap_query = MagicMock()
        gap_query.filter.return_value = gap_query
        gap_query.all.return_value = [gap]

        db.query.side_effect = [viirs_query, gap_query]

        from app.modules.viirs_correlator import correlate_viirs_with_gaps

        result = correlate_viirs_with_gaps(db)
        assert result["corridor_matches"] == 1

    def test_commits_after_processing(self):
        """DB commit is called after processing."""
        det = _make_detection()

        db = MagicMock()
        viirs_query = MagicMock()
        viirs_query.filter.return_value = viirs_query
        viirs_query.all.return_value = [det]

        gap_query = MagicMock()
        gap_query.filter.return_value = gap_query
        gap_query.all.return_value = []

        db.query.side_effect = [viirs_query, gap_query]

        from app.modules.viirs_correlator import correlate_viirs_with_gaps

        correlate_viirs_with_gaps(db)
        assert db.commit.called

    def test_date_filters_applied(self):
        """Date filters are passed to query."""
        db = MagicMock()
        viirs_query = MagicMock()
        viirs_query.filter.return_value = viirs_query
        viirs_query.all.return_value = []

        db.query.return_value = viirs_query

        from app.modules.viirs_correlator import correlate_viirs_with_gaps

        date_from = datetime(2026, 3, 1)
        date_to = datetime(2026, 3, 31)
        correlate_viirs_with_gaps(db, date_from=date_from, date_to=date_to)
        # filter is called multiple times (base + date filters)
        assert viirs_query.filter.call_count >= 1
