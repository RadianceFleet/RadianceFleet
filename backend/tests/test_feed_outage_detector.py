"""Tests for feed outage detection."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# ── Helpers ──────────────────────────────────────────────────────────


def _make_gap(
    vessel_id, gap_start, corridor_id=None, risk_score=0, is_feed_outage=False, source=None
):
    g = MagicMock()
    g.vessel_id = vessel_id
    g.gap_start_utc = gap_start
    g.gap_end_utc = gap_start + timedelta(hours=4)
    g.corridor_id = corridor_id
    g.risk_score = risk_score
    g.is_feed_outage = is_feed_outage
    g.source = source
    g.coverage_quality = None
    return g


# ── Tests: _cluster_gaps ────────────────────────────────────────────


class TestClusterGaps:
    def test_groups_by_corridor_and_window(self):
        from app.modules.feed_outage_detector import _cluster_gaps

        base = datetime(2026, 1, 15, 10, 30)
        gaps = [
            _make_gap(1, base, corridor_id=1),
            _make_gap(2, base + timedelta(minutes=30), corridor_id=1),
            _make_gap(3, base + timedelta(hours=4), corridor_id=1),
        ]
        clusters = _cluster_gaps(gaps)
        assert len(clusters) == 2

    def test_separate_corridors_separate_clusters(self):
        from app.modules.feed_outage_detector import _cluster_gaps

        base = datetime(2026, 1, 15, 10, 0)
        gaps = [
            _make_gap(1, base, corridor_id=1),
            _make_gap(2, base, corridor_id=2),
        ]
        clusters = _cluster_gaps(gaps)
        assert len(clusters) == 2

    def test_empty_gaps(self):
        from app.modules.feed_outage_detector import _cluster_gaps

        assert _cluster_gaps([]) == []


# ── Tests: _detect_dominant_source ───────────────────────────────────


class TestDetectDominantSource:
    def test_returns_dominant_source(self):
        from app.modules.feed_outage_detector import _detect_dominant_source

        gaps = [_make_gap(i, datetime(2026, 1, 15), source="kystverket") for i in range(10)]
        assert _detect_dominant_source(gaps) == "kystverket"

    def test_returns_none_mixed_sources(self):
        from app.modules.feed_outage_detector import _detect_dominant_source

        gaps = [
            _make_gap(1, datetime(2026, 1, 15), source="kystverket"),
            _make_gap(2, datetime(2026, 1, 15), source="digitraffic"),
            _make_gap(3, datetime(2026, 1, 15), source="aisstream"),
        ]
        assert _detect_dominant_source(gaps) is None

    def test_returns_none_no_sources(self):
        from app.modules.feed_outage_detector import _detect_dominant_source

        gaps = [_make_gap(1, datetime(2026, 1, 15), source=None)]
        assert _detect_dominant_source(gaps) is None

    def test_returns_none_empty_list(self):
        from app.modules.feed_outage_detector import _detect_dominant_source

        assert _detect_dominant_source([]) is None


# ── Tests: _get_threshold ────────────────────────────────────────────


class TestGetThreshold:
    def test_uses_p95_baseline_when_available(self):
        from app.modules.feed_outage_detector import _get_threshold

        db = MagicMock()
        baseline = MagicMock()
        baseline.p95_threshold = 10.0

        db.query.return_value.filter.return_value.first.return_value = baseline

        result = _get_threshold(db, corridor_id=1, reference_time=datetime(2026, 1, 15))
        assert result == 30  # 10 * 3.0

    def test_min_threshold_from_p95(self):
        from app.modules.feed_outage_detector import _get_threshold

        db = MagicMock()
        baseline = MagicMock()
        baseline.p95_threshold = 1.0

        db.query.return_value.filter.return_value.first.return_value = baseline

        result = _get_threshold(db, corridor_id=1, reference_time=datetime(2026, 1, 15))
        assert result >= 8

    def test_null_corridor_fallback(self):
        from app.modules.feed_outage_detector import _get_threshold

        db = MagicMock()
        db.query.return_value.filter.return_value.distinct.return_value.count.return_value = 200

        result = _get_threshold(db, corridor_id=None, reference_time=datetime(2026, 1, 15))
        assert result == max(int(200 * 0.15), 25)


# ── Tests: _has_evasion_signals ──────────────────────────────────────


class TestHasEvasionSignals:
    def test_returns_true_with_spoofing(self):
        from app.modules.feed_outage_detector import _has_evasion_signals

        db = MagicMock()
        gap = _make_gap(1, datetime(2026, 1, 15), corridor_id=1)

        db.query.return_value.filter.return_value.count.return_value = 1

        assert _has_evasion_signals(db, gap) is True

    def test_returns_false_no_signals(self):
        from app.modules.feed_outage_detector import _has_evasion_signals

        db = MagicMock()
        gap = _make_gap(1, datetime(2026, 1, 15), corridor_id=1)

        db.query.return_value.filter.return_value.count.return_value = 0

        assert _has_evasion_signals(db, gap) is False


# ── Tests: detect_feed_outages ───────────────────────────────────────


class TestDetectFeedOutages:
    def test_disabled_returns_zeros(self):
        from app.modules.feed_outage_detector import detect_feed_outages

        db = MagicMock()
        with patch("app.modules.feed_outage_detector.settings") as mock_settings:
            mock_settings.FEED_OUTAGE_DETECTION_ENABLED = False
            result = detect_feed_outages(db)
            assert result["gaps_checked"] == 0
            assert result["outages_detected"] == 0

    def test_no_unscored_gaps(self):
        from app.modules.feed_outage_detector import detect_feed_outages

        db = MagicMock()
        with patch("app.modules.feed_outage_detector.settings") as mock_settings:
            mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
            db.query.return_value.first.return_value = MagicMock()
            db.query.return_value.filter.return_value.all.return_value = []

            result = detect_feed_outages(db)
            assert result["gaps_checked"] == 0


# ── Tests: tag_coverage_quality ──────────────────────────────────────


class TestTagCoverageQuality:
    def test_disabled_returns_zero(self):
        from app.modules.feed_outage_detector import tag_coverage_quality

        db = MagicMock()
        with patch("app.modules.feed_outage_detector.settings") as mock_settings:
            mock_settings.COVERAGE_QUALITY_TAGGING_ENABLED = False
            result = tag_coverage_quality(db)
            assert result["gaps_tagged"] == 0

    def test_no_untagged_gaps(self):
        from app.modules.feed_outage_detector import tag_coverage_quality

        db = MagicMock()
        with patch("app.modules.feed_outage_detector.settings") as mock_settings:
            mock_settings.COVERAGE_QUALITY_TAGGING_ENABLED = True
            db.query.return_value.filter.return_value.all.return_value = []
            result = tag_coverage_quality(db)
            assert result["gaps_tagged"] == 0
