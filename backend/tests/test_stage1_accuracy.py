"""Tests for Stage 1: Accuracy Foundation.

Covers:
  1-A: Feed outage detection
  1-B: Multi-signal confidence classifier
  1-C: Coverage quality tagging
  PipelineRun model + drift detection
  CLI commands (rescore, evaluate-detector, confirm-detector)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _gap(vessel_id=1, corridor_id=1, gap_start=None, risk_score=0,
         is_feed_outage=False, source=None, in_dark_zone=False,
         dark_zone_id=None, gap_event_id=None, original_vessel_id=None,
         duration_minutes=120, coverage_quality=None):
    """Create a mock AISGapEvent."""
    m = MagicMock()
    m.gap_event_id = gap_event_id or vessel_id * 100
    m.vessel_id = vessel_id
    m.corridor_id = corridor_id
    m.gap_start_utc = gap_start or datetime(2025, 6, 15, 10, 0)
    m.gap_end_utc = m.gap_start_utc + timedelta(minutes=duration_minutes)
    m.duration_minutes = duration_minutes
    m.risk_score = risk_score
    m.is_feed_outage = is_feed_outage
    m.source = source
    m.in_dark_zone = in_dark_zone
    m.dark_zone_id = dark_zone_id
    m.original_vessel_id = original_vessel_id
    m.risk_breakdown_json = None
    m.coverage_quality = coverage_quality
    m.impossible_speed_flag = False
    m.velocity_plausibility_ratio = None
    m.pre_gap_sog = None
    m.corridor = None
    m.start_point = None
    m.end_point = None
    return m


def _vessel(vessel_id=1, mmsi="123456789"):
    """Create a mock Vessel."""
    m = MagicMock()
    m.vessel_id = vessel_id
    m.mmsi = mmsi
    m.dark_fleet_confidence = None
    m.confidence_evidence_json = None
    return m


# ══════════════════════════════════════════════════════════════════════════════
# 1-A: Feed Outage Detection
# ══════════════════════════════════════════════════════════════════════════════


class TestFeedOutageDetection:
    """Tests for feed_outage_detector.detect_feed_outages()."""

    @patch("app.modules.feed_outage_detector.settings")
    def test_disabled_returns_zeros(self, mock_settings):
        mock_settings.FEED_OUTAGE_DETECTION_ENABLED = False
        from app.modules.feed_outage_detector import detect_feed_outages
        result = detect_feed_outages(MagicMock())
        assert result == {"gaps_checked": 0, "outages_detected": 0, "gaps_marked": 0}

    @patch("app.modules.feed_outage_detector.settings")
    def test_no_gaps_returns_zeros(self, mock_settings):
        mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        from app.modules.feed_outage_detector import detect_feed_outages
        result = detect_feed_outages(db)
        assert result["gaps_checked"] == 0

    @patch("app.modules.feed_outage_detector.settings")
    def test_single_vessel_not_outage(self, mock_settings):
        """A single vessel going dark is not a feed outage."""
        mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
        db = MagicMock()
        gaps = [_gap(vessel_id=1, corridor_id=10)]
        db.query.return_value.filter.return_value.all.return_value = gaps
        from app.modules.feed_outage_detector import detect_feed_outages
        result = detect_feed_outages(db)
        assert result["outages_detected"] == 0
        assert result["gaps_marked"] == 0

    @patch("app.modules.feed_outage_detector.settings")
    def test_five_vessels_triggers_fallback(self, mock_settings):
        """5+ unrelated vessels in same corridor + 2h window = outage (fallback)."""
        mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
        db = MagicMock()
        base_time = datetime(2025, 6, 15, 10, 30)
        gaps = [
            _gap(vessel_id=i, corridor_id=10, gap_start=base_time + timedelta(minutes=i * 5))
            for i in range(1, 7)  # 6 unique vessels
        ]
        db.query.return_value.filter.return_value.all.return_value = gaps
        # No baseline → fallback threshold of 5
        from app.modules.feed_outage_detector import detect_feed_outages
        with patch("app.modules.feed_outage_detector._get_threshold", return_value=5):
            result = detect_feed_outages(db)
        assert result["outages_detected"] == 1
        assert result["gaps_marked"] == 6
        # All gaps should be marked
        for g in gaps:
            assert g.is_feed_outage is True

    @patch("app.modules.feed_outage_detector.settings")
    def test_adaptive_threshold_with_baseline(self, mock_settings):
        """When P95 baseline exists, threshold = 3 × P95."""
        mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
        from app.modules.feed_outage_detector import _get_threshold
        db = MagicMock()
        baseline = MagicMock()
        baseline.p95_threshold = 2.0  # 3 × 2 = 6
        db.query.return_value.filter.return_value.first.return_value = baseline
        threshold = _get_threshold(db, corridor_id=10, reference_time=datetime(2025, 6, 15))
        assert threshold == 6

    @patch("app.modules.feed_outage_detector.settings")
    def test_threshold_floor_at_3(self, mock_settings):
        """Even with very low baseline, threshold never goes below 3."""
        mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
        from app.modules.feed_outage_detector import _get_threshold
        db = MagicMock()
        baseline = MagicMock()
        baseline.p95_threshold = 0.5  # 3 × 0.5 = 1.5, floored to 3
        db.query.return_value.filter.return_value.first.return_value = baseline
        threshold = _get_threshold(db, corridor_id=10, reference_time=datetime(2025, 6, 15))
        assert threshold == 3

    def test_no_corridor_uses_fallback(self):
        from app.modules.feed_outage_detector import _get_threshold
        threshold = _get_threshold(MagicMock(), corridor_id=None, reference_time=datetime(2025, 6, 15))
        assert threshold == 5

    @patch("app.modules.feed_outage_detector.settings")
    def test_gaps_in_different_corridors_separate(self, mock_settings):
        """Gaps in different corridors are not clustered together."""
        mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
        db = MagicMock()
        base_time = datetime(2025, 6, 15, 10, 30)
        # 3 vessels in corridor 10, 3 in corridor 20 — neither hits threshold of 5
        gaps = [
            _gap(vessel_id=i, corridor_id=10, gap_start=base_time) for i in range(1, 4)
        ] + [
            _gap(vessel_id=i, corridor_id=20, gap_start=base_time) for i in range(4, 7)
        ]
        db.query.return_value.filter.return_value.all.return_value = gaps
        from app.modules.feed_outage_detector import detect_feed_outages
        with patch("app.modules.feed_outage_detector._get_threshold", return_value=5):
            result = detect_feed_outages(db)
        assert result["outages_detected"] == 0


class TestFeedOutageScoringSkip:
    """Verify scoring skips is_feed_outage=True gaps."""

    def test_score_all_skips_feed_outage(self):
        from app.modules.risk_scoring import score_all_alerts
        db = MagicMock()
        outage_gap = _gap(vessel_id=1, is_feed_outage=True, risk_score=0)
        normal_gap = _gap(vessel_id=2, is_feed_outage=False, risk_score=0)
        db.query.return_value.filter.return_value.all.return_value = [outage_gap, normal_gap]
        db.commit = MagicMock()

        with patch("app.modules.risk_scoring.load_scoring_config", return_value={}), \
             patch("app.modules.risk_scoring._count_gaps_in_window", return_value=0), \
             patch("app.modules.risk_scoring.compute_gap_score", return_value=(42, {"test": 42})):
            result = score_all_alerts(db)

        assert result["feed_outage_skipped"] == 1
        assert result["scored"] == 1
        # The outage gap should NOT have been scored
        assert outage_gap.risk_score == 0
        # The normal gap should have been scored
        assert normal_gap.risk_score == 42


# ══════════════════════════════════════════════════════════════════════════════
# 1-B: Multi-Signal Confidence Classifier
# ══════════════════════════════════════════════════════════════════════════════


class TestConfidenceClassifier:
    """Tests for confidence_classifier.classify_vessel_confidence()."""

    def test_confirmed_watchlist(self):
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        conf, evidence = classify_vessel_confidence(
            v, total_score=30, breakdown={"watchlist_OFAC": 50},
            has_watchlist_match=True,
        )
        assert conf == "CONFIRMED"

    def test_confirmed_analyst_verified(self):
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        conf, _ = classify_vessel_confidence(
            v, total_score=10, breakdown={},
            analyst_verified=True,
        )
        assert conf == "CONFIRMED"

    def test_high_two_categories(self):
        """score ≥ 76 AND ≥2 categories → HIGH."""
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        breakdown = {
            "gap_duration_8h_12h": 40,
            "spoofing_erratic_nav_status": 40,
        }
        conf, evidence = classify_vessel_confidence(v, total_score=80, breakdown=breakdown)
        assert conf == "HIGH"
        assert "AIS_GAP" in evidence
        assert "SPOOFING" in evidence

    def test_high_single_category_80_plus(self):
        """score ≥ 76 AND single category ≥80 pts → HIGH."""
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        breakdown = {
            "spoofing_erratic_nav_status": 50,
            "spoofing_fake_position": 35,
        }
        conf, _ = classify_vessel_confidence(v, total_score=85, breakdown=breakdown)
        assert conf == "HIGH"

    def test_score_76_single_category_below_80_not_high(self):
        """score ≥ 76 but only 1 category <80 → falls to MEDIUM."""
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        breakdown = {"gap_duration_24h_plus": 76}
        conf, _ = classify_vessel_confidence(v, total_score=76, breakdown=breakdown)
        # Single category with 76 pts: <80 so not HIGH, but ≥51 and ≥30pts → MEDIUM
        assert conf == "MEDIUM"

    def test_medium(self):
        """score ≥ 51 AND ≥1 category ≥30 pts → MEDIUM."""
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        breakdown = {"gap_duration_8h_12h": 35}
        conf, _ = classify_vessel_confidence(v, total_score=55, breakdown=breakdown)
        assert conf == "MEDIUM"

    def test_low(self):
        """score 21-50 → LOW."""
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        breakdown = {"gap_duration_2h_4h": 25}
        conf, _ = classify_vessel_confidence(v, total_score=25, breakdown=breakdown)
        assert conf == "LOW"

    def test_none(self):
        """score < 21 → NONE."""
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        conf, _ = classify_vessel_confidence(v, total_score=15, breakdown={"gap_duration_2h_4h": 15})
        assert conf == "NONE"

    def test_negative_scores_excluded_from_evidence(self):
        """Deductions (negative values) don't count as evidence."""
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        breakdown = {
            "gap_duration_8h_12h": 60,
            "dark_zone_deduction": -10,
        }
        conf, evidence = classify_vessel_confidence(v, total_score=50, breakdown=breakdown)
        # Only positive signals in evidence
        assert evidence.get("AIS_GAP", 0) == 60

    def test_categorize_key_spoofing(self):
        from app.modules.confidence_classifier import _categorize_key
        assert _categorize_key("spoofing_erratic_nav_status") == "SPOOFING"
        assert _categorize_key("track_naturalness_3of5") == "SPOOFING"

    def test_categorize_key_sts(self):
        from app.modules.confidence_classifier import _categorize_key
        assert _categorize_key("sts_event_42") == "STS_TRANSFER"
        assert _categorize_key("repeat_sts_partnership") == "STS_TRANSFER"

    def test_categorize_key_identity(self):
        from app.modules.confidence_classifier import _categorize_key
        assert _categorize_key("flag_change_7d") == "IDENTITY_CHANGE"
        assert _categorize_key("callsign_change") == "IDENTITY_CHANGE"

    def test_categorize_key_watchlist(self):
        from app.modules.confidence_classifier import _categorize_key
        assert _categorize_key("watchlist_OFAC") == "WATCHLIST"
        assert _categorize_key("owner_or_manager_on_sanctions_list") == "WATCHLIST"

    def test_107pt_spoofing_is_high(self):
        """The bug from v1: 107 pts all spoofing = HIGH (was LOW)."""
        from app.modules.confidence_classifier import classify_vessel_confidence
        v = _vessel()
        breakdown = {
            "spoofing_erratic_nav_status": 57,
            "spoofing_impossible_position": 50,
        }
        conf, _ = classify_vessel_confidence(v, total_score=107, breakdown=breakdown)
        assert conf == "HIGH"  # single category ≥80 pts (107 > 80)


# ══════════════════════════════════════════════════════════════════════════════
# 1-C: Coverage Quality Tagging
# ══════════════════════════════════════════════════════════════════════════════


class TestCoverageQualityTagging:
    @patch("app.modules.feed_outage_detector.settings")
    def test_disabled_returns_zero(self, mock_settings):
        mock_settings.COVERAGE_QUALITY_TAGGING_ENABLED = False
        from app.modules.feed_outage_detector import tag_coverage_quality
        result = tag_coverage_quality(MagicMock())
        assert result == {"gaps_tagged": 0}

    @patch("app.modules.feed_outage_detector.settings")
    def test_tags_gaps_with_corridor_quality(self, mock_settings):
        mock_settings.COVERAGE_QUALITY_TAGGING_ENABLED = True
        db = MagicMock()
        gap = _gap(coverage_quality=None)
        corridor = MagicMock()
        corridor.name = "Baltic Export Gate"
        gap.corridor = corridor
        db.query.return_value.filter.return_value.all.return_value = [gap]

        from app.modules.feed_outage_detector import tag_coverage_quality
        with patch("app.api.routes._get_coverage_quality", return_value="GOOD"):
            result = tag_coverage_quality(db)
        assert result["gaps_tagged"] == 1
        assert gap.coverage_quality == "GOOD"

    @patch("app.modules.feed_outage_detector.settings")
    def test_no_corridor_gets_unknown(self, mock_settings):
        mock_settings.COVERAGE_QUALITY_TAGGING_ENABLED = True
        db = MagicMock()
        gap = _gap(coverage_quality=None)
        gap.corridor = None
        db.query.return_value.filter.return_value.all.return_value = [gap]

        from app.modules.feed_outage_detector import tag_coverage_quality
        with patch("app.api.routes._get_coverage_quality", return_value="UNKNOWN"):
            result = tag_coverage_quality(db)
        assert result["gaps_tagged"] == 1
        assert gap.coverage_quality == "UNKNOWN"


# ══════════════════════════════════════════════════════════════════════════════
# PipelineRun Model + Drift Detection
# ══════════════════════════════════════════════════════════════════════════════


class TestPipelineRunModel:
    def test_model_attributes(self):
        from app.models.pipeline_run import PipelineRun
        run = PipelineRun(status="running")
        assert run.status == "running"
        assert run.drift_disabled_detectors_json is None

    def test_model_in_registry(self):
        from app.models import PipelineRun
        assert PipelineRun.__tablename__ == "pipeline_runs"


class TestDriftDetection:
    def test_no_drift_when_counts_stable(self):
        """No drift when anomaly counts are similar between runs."""
        from app.modules.dark_vessel_discovery import _finalize_pipeline_run
        from app.models.pipeline_run import PipelineRun

        db = MagicMock()
        pipeline_run = MagicMock(spec=PipelineRun)
        pipeline_run.run_id = 2

        # Mock queries
        from sqlalchemy import func
        db.query.return_value.group_by.return_value.all.return_value = []
        db.query.return_value.count.return_value = 10
        db.query.return_value.filter.return_value.count.return_value = 5

        # Previous run with similar counts
        prev_run = MagicMock()
        prev_run.detector_anomaly_counts_json = {"gap_events": 10}
        prev_run.data_volume_json = {"ais_points_count": 1000, "vessels_count": 50}
        prev_run.drift_disabled_detectors_json = None
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = prev_run

        _finalize_pipeline_run(db, pipeline_run, {"run_status": "complete"})
        # Should not have drift-disabled detectors
        assert pipeline_run.drift_disabled_detectors_json is None or \
               pipeline_run.drift_disabled_detectors_json == []

    def test_drift_carries_forward_disabled(self):
        """Previously disabled detectors carry forward until confirmed."""
        from app.modules.dark_vessel_discovery import _finalize_pipeline_run

        db = MagicMock()
        pipeline_run = MagicMock()
        pipeline_run.run_id = 3

        db.query.return_value.group_by.return_value.all.return_value = []
        db.query.return_value.count.return_value = 10
        db.query.return_value.filter.return_value.count.return_value = 5

        prev_run = MagicMock()
        prev_run.detector_anomaly_counts_json = {"gap_events": 10}
        prev_run.data_volume_json = {"ais_points_count": 1000, "vessels_count": 50}
        prev_run.drift_disabled_detectors_json = ["spoofing_detector"]
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = prev_run

        _finalize_pipeline_run(db, pipeline_run, {"run_status": "complete"})
        disabled = pipeline_run.drift_disabled_detectors_json
        assert "spoofing_detector" in (disabled or [])


# ══════════════════════════════════════════════════════════════════════════════
# CLI Commands
# ══════════════════════════════════════════════════════════════════════════════


class TestCLIRescore:
    def test_rescore_command_exists(self):
        """Verify rescore command is registered."""
        from app.cli import app
        command_names = [cmd.name for cmd in app.registered_commands]
        assert "rescore" in command_names


class TestCLIEvaluateDetector:
    def test_evaluate_detector_command_exists(self):
        from app.cli import app
        command_names = [cmd.name for cmd in app.registered_commands]
        assert "evaluate-detector" in command_names


class TestCLIConfirmDetector:
    def test_confirm_detector_command_exists(self):
        from app.cli import app
        command_names = [cmd.name for cmd in app.registered_commands]
        assert "confirm-detector" in command_names


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline Integration
# ══════════════════════════════════════════════════════════════════════════════


class TestPipelineWiring:
    def test_feed_outage_in_pipeline(self):
        """Feed outage detection is wired between gap detection and scoring."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        src = inspect.getsource(discover_dark_vessels)
        # Feed outage must appear BEFORE scoring
        assert src.index("feed_outage_detection") < src.index('"scoring"')

    def test_confidence_classification_in_pipeline(self):
        """Confidence classification runs after scoring."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        src = inspect.getsource(discover_dark_vessels)
        assert src.index("confidence_classification") > src.index('"scoring"')

    def test_coverage_quality_in_pipeline(self):
        """Coverage quality tagging is wired before scoring."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        src = inspect.getsource(discover_dark_vessels)
        assert src.index("coverage_quality_tagging") < src.index('"scoring"')

    def test_pipeline_run_created(self):
        """PipelineRun is created at pipeline start."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        src = inspect.getsource(discover_dark_vessels)
        assert "PipelineRun" in src
        assert "_finalize_pipeline_run" in src


class TestStage1Integration:
    def test_all_imports_compile(self):
        """All Stage 1 modules import without errors."""
        from app.modules import feed_outage_detector
        from app.modules import confidence_classifier
        from app.models import pipeline_run

    def test_vessel_model_has_confidence_fields(self):
        from app.models.vessel import Vessel
        assert hasattr(Vessel, "dark_fleet_confidence")
        assert hasattr(Vessel, "confidence_evidence_json")

    def test_gap_model_has_feed_outage_field(self):
        from app.models.gap_event import AISGapEvent
        assert hasattr(AISGapEvent, "is_feed_outage")
        assert hasattr(AISGapEvent, "coverage_quality")

    def test_config_has_feature_flags(self):
        from app.config import Settings
        s = Settings()
        assert hasattr(s, "FEED_OUTAGE_DETECTION_ENABLED")
        assert hasattr(s, "COVERAGE_QUALITY_TAGGING_ENABLED")
        assert s.FEED_OUTAGE_DETECTION_ENABLED is False
        assert s.COVERAGE_QUALITY_TAGGING_ENABLED is False


class TestPartialOutageGuard:
    """Test the partial outage guard in selective dark zone evasion."""

    def test_same_source_blocks_evasion_bonus(self):
        """When ≤2 other-dark vessels share same source, no evasion bonus."""
        # This test verifies the logic exists in risk_scoring.py
        import inspect
        from app.modules.risk_scoring import compute_gap_score
        src = inspect.getsource(compute_gap_score)
        assert "_same_source" in src
        assert "_vessel_source" in src
