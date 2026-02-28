"""Tests for dark vessel discovery — auto-hunt, clustering, and orchestrator."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# --- Helpers ---

def _make_gap(gap_id, vessel_id, risk_score=60, off_lat=60.0, off_lon=25.0):
    gap = MagicMock()
    gap.gap_event_id = gap_id
    gap.vessel_id = vessel_id
    gap.risk_score = risk_score
    gap.gap_start_utc = datetime(2025, 12, 1, 0, 0)
    gap.gap_end_utc = datetime(2025, 12, 2, 0, 0)
    gap.gap_off_lat = off_lat
    gap.gap_off_lon = off_lon
    gap.start_point = None
    gap.corridor_id = 1
    gap.duration_minutes = 1440
    return gap


def _make_detection(det_id, lat, lon, time_utc, corridor_id=None, vessel_type="unknown"):
    det = MagicMock()
    det.detection_id = det_id
    det.detection_lat = lat
    det.detection_lon = lon
    det.detection_time_utc = time_utc
    det.corridor_id = corridor_id
    det.ais_match_result = "unmatched"
    det.vessel_type_inferred = vessel_type
    return det


# --- Auto-hunt ---

class TestAutoHunt:
    @patch("app.modules.vessel_hunt.find_hunt_candidates")
    @patch("app.modules.vessel_hunt.create_search_mission")
    @patch("app.modules.vessel_hunt.create_target_profile")
    def test_auto_hunt_creates_missions(self, mock_profile, mock_mission, mock_candidates):
        """Auto-hunt creates missions for high-risk gaps with position."""
        from app.modules.dark_vessel_discovery import auto_hunt_dark_vessels

        gap = _make_gap(1, 100, risk_score=80)

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [gap]

        mock_profile.return_value = MagicMock(profile_id=1, vessel_id=100)
        mock_mission.return_value = MagicMock(mission_id=1)

        candidate = MagicMock()
        candidate.score_breakdown_json = {"band": "HIGH"}
        mock_candidates.return_value = [candidate]

        # Mock STS query
        db.query.return_value.all.return_value = []

        result = auto_hunt_dark_vessels(db, min_gap_score=50)
        assert result["missions_created"] >= 1
        assert result["high"] >= 1

    def test_auto_hunt_skips_gaps_without_position(self):
        """Gaps without position data are skipped."""
        from app.modules.dark_vessel_discovery import auto_hunt_dark_vessels

        gap = _make_gap(1, 100, risk_score=80, off_lat=None, off_lon=None)
        gap.start_point = None

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [gap]
        db.query.return_value.all.return_value = []

        result = auto_hunt_dark_vessels(db, min_gap_score=50)
        assert result["missions_created"] == 0


# --- Clustering ---

class TestClusterDarkDetections:
    def test_clusters_nearby_detections(self):
        """Detections within radius_nm are clustered together."""
        from app.modules.dark_vessel_discovery import cluster_dark_detections

        base_time = datetime(2025, 12, 1, 12, 0)
        dets = [
            _make_detection(1, 60.0, 25.0, base_time),
            _make_detection(2, 60.01, 25.01, base_time + timedelta(hours=1)),
            _make_detection(3, 60.02, 25.02, base_time + timedelta(hours=2)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = dets

        clusters = cluster_dark_detections(db, radius_nm=5.0, min_detections=3)
        assert len(clusters) >= 1
        assert clusters[0]["count"] == 3

    def test_no_cluster_if_too_few_detections(self):
        """Fewer than min_detections → no clusters."""
        from app.modules.dark_vessel_discovery import cluster_dark_detections

        dets = [
            _make_detection(1, 60.0, 25.0, datetime(2025, 12, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = dets

        clusters = cluster_dark_detections(db, min_detections=3)
        assert len(clusters) == 0

    def test_distant_detections_not_clustered(self):
        """Detections far apart should not form a cluster."""
        from app.modules.dark_vessel_discovery import cluster_dark_detections

        base_time = datetime(2025, 12, 1, 12, 0)
        dets = [
            _make_detection(1, 60.0, 25.0, base_time),
            _make_detection(2, 30.0, -80.0, base_time),  # Far away
            _make_detection(3, -30.0, 150.0, base_time),  # Very far
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = dets

        clusters = cluster_dark_detections(db, radius_nm=5.0, min_detections=3)
        assert len(clusters) == 0


# --- Orchestrator ---

class TestOrchestrator:
    @patch("app.modules.dark_vessel_discovery.cluster_dark_detections", return_value=[])
    @patch("app.modules.dark_vessel_discovery.auto_hunt_dark_vessels", return_value={})
    def test_orchestrator_hard_fail_on_gap_detection(self, mock_hunt, mock_cluster):
        """If gap detection (HARD) fails, orchestrator aborts."""
        from app.modules.dark_vessel_discovery import discover_dark_vessels

        db = MagicMock()

        with patch("app.modules.gap_detector.run_gap_detection") as mock_gaps:
            mock_gaps.side_effect = RuntimeError("DB locked")

            result = discover_dark_vessels(
                db, start_date="2025-12-01", end_date="2025-12-31",
                skip_fetch=True,
            )

        assert result["run_status"] == "failed"
        assert result["steps"]["gap_detection"]["status"] == "failed"

    @patch("app.modules.dark_vessel_discovery.cluster_dark_detections", return_value=[])
    @patch("app.modules.dark_vessel_discovery.auto_hunt_dark_vessels", return_value={})
    def test_orchestrator_soft_fail_continues(self, mock_hunt, mock_cluster):
        """If GFW import (SOFT) fails, remaining steps still execute."""
        from app.modules.dark_vessel_discovery import discover_dark_vessels

        db = MagicMock()
        # Make top_alerts query work
        db.query.return_value.join.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        with patch("app.modules.gfw_client.import_gfw_gap_events") as mock_gfw, \
             patch("app.modules.gfw_client.sweep_corridors_sar") as mock_sar, \
             patch("app.modules.gap_detector.run_gap_detection") as mock_gaps, \
             patch("app.modules.gap_detector.run_spoofing_detection") as mock_spoof, \
             patch("app.modules.loitering_detector.run_loitering_detection") as mock_loiter, \
             patch("app.modules.sts_detector.detect_sts_events") as mock_sts, \
             patch("app.modules.risk_scoring.rescore_all_alerts") as mock_score, \
             patch("app.modules.identity_resolver.detect_merge_candidates") as mock_merge, \
             patch("app.modules.mmsi_cloning_detector.detect_mmsi_cloning") as mock_clone:

            mock_gfw.side_effect = Exception("GFW down")
            mock_sar.return_value = {}
            mock_gaps.return_value = {"gaps_detected": 5}
            mock_spoof.return_value = {}
            mock_loiter.return_value = {}
            mock_sts.return_value = {}
            mock_score.return_value = {"rescored": 5}
            mock_merge.return_value = {}
            mock_clone.return_value = []

            result = discover_dark_vessels(
                db, start_date="2025-12-01", end_date="2025-12-31",
            )

        assert result["run_status"] == "partial"
        # Gap detection should still have run
        assert result["steps"]["gap_detection"]["status"] == "ok"
        # Scoring should have run
        assert result["steps"]["scoring"]["status"] == "ok"


# --- Hunt threshold fix ---

class TestHuntThresholdFix:
    def test_high_threshold_reachable(self):
        """v1.1 max score=60 should be able to reach HIGH band (>=45)."""
        from app.modules.vessel_hunt import _compute_hunt_score

        det = MagicMock()
        det.length_estimate_m = 190.0  # LOA estimate: 150 + 120000/3000 = 190
        det.vessel_type_inferred = "tanker"
        det.detection_lat = 60.0
        det.detection_lon = 25.0

        mission = MagicMock()
        mission.center_lat = 60.0
        mission.center_lon = 25.0
        mission.max_radius_nm = 100.0

        vessel = MagicMock()
        vessel.deadweight = 120000
        vessel.vessel_type = "Crude Oil Tanker"

        score, breakdown = _compute_hunt_score(det, mission, vessel)
        # heading(15) + drift(15, nearby) + length(20) + class(10) = 60 max
        # This should be >= 45 (HIGH)
        assert score >= 45, f"Score {score} should be HIGH (>=45)"

    def test_score_bands_v1_1(self):
        """find_hunt_candidates should use updated thresholds: HIGH>=45, MEDIUM>=25."""
        # Verify thresholds via config constants (moved from hardcoded to config)
        from app.modules.vessel_hunt import HIGH_SCORE_BAND, MEDIUM_SCORE_BAND
        assert HIGH_SCORE_BAND == 45
        assert MEDIUM_SCORE_BAND == 25
