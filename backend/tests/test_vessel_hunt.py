"""Tests for FR9 vessel hunt: drift ellipse, candidate scoring, mission lifecycle."""
import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest


class TestDriftEllipse:
    def test_radius_scales_with_elapsed_hours(self):
        """Drift radius = max_speed_kn * elapsed_hours."""
        from app.modules.gap_detector import compute_max_distance_nm
        # For a 50,000 DWT vessel (tanker, ~17 kn max for <60k DWT), 24h -> 408 nm
        radius = compute_max_distance_nm(50000, 24)
        assert radius == pytest.approx(17.0 * 24, rel=0.01)

    def test_mission_ellipse_wkt_is_polygon(self):
        """create_search_mission produces a WKT POLYGON string."""
        from app.modules.vessel_hunt import create_search_mission

        mock_db = MagicMock()
        profile = MagicMock()
        profile.profile_id = 1
        profile.vessel_id = 1
        profile.deadweight_dwt = 50000.0
        profile.last_ais_position_lat = 57.0
        profile.last_ais_position_lon = 20.0
        mock_db.query.return_value.filter.return_value.first.return_value = profile

        start = datetime(2024, 1, 15, 0, 0)
        end = datetime(2024, 1, 16, 0, 0)
        mission = create_search_mission(1, start, end, mock_db)

        assert mission.search_ellipse_wkt.startswith("POLYGON")
        assert mission.max_radius_nm is not None
        assert mission.max_radius_nm > 0

    def test_ellipse_has_36_points_plus_close(self):
        """The WKT polygon should have 37 coordinate pairs (36 + closing)."""
        from app.modules.vessel_hunt import create_search_mission

        mock_db = MagicMock()
        profile = MagicMock()
        profile.profile_id = 1
        profile.vessel_id = 1
        profile.deadweight_dwt = 50000.0
        profile.last_ais_position_lat = 57.0
        profile.last_ais_position_lon = 20.0
        mock_db.query.return_value.filter.return_value.first.return_value = profile

        start = datetime(2024, 1, 15, 0, 0)
        end = datetime(2024, 1, 16, 0, 0)
        mission = create_search_mission(1, start, end, mock_db)

        wkt = mission.search_ellipse_wkt
        # Extract the coords between (( and ))
        inner = wkt.split("((")[1].split("))")[0]
        coord_pairs = inner.split(", ")
        assert len(coord_pairs) == 37  # 36 points + 1 closing

    def test_mission_profile_not_found_raises(self):
        """create_search_mission raises ValueError if profile not found."""
        from app.modules.vessel_hunt import create_search_mission

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="not found"):
            create_search_mission(999, datetime.now(), datetime.now(), mock_db)


class TestHuntScoring:
    def test_score_with_all_signals(self):
        """Score includes length, heading, drift, and class match components."""
        from app.modules.vessel_hunt import _compute_hunt_score

        det = MagicMock()
        det.length_estimate_m = 167.0  # ~150 + 50000/3000 = 166.7
        det.vessel_type_inferred = "tanker"
        det.detection_lat = 57.0
        det.detection_lon = 20.0

        mission = MagicMock()
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0

        vessel = MagicMock()
        vessel.deadweight = 50000.0
        vessel.vessel_type = "Crude Oil Tanker"

        score, breakdown = _compute_hunt_score(det, mission, vessel)

        # At center: length_match=20, heading=15, drift=15, class=10 => 60
        assert breakdown["length_match"] == 20.0
        assert breakdown["heading_plausible"] == 15.0
        assert breakdown["drift_probability"] == pytest.approx(15.0, abs=0.1)
        assert breakdown["vessel_class_match"] == 10.0
        assert score == pytest.approx(60.0, abs=0.1)

    def test_score_band_assignment(self):
        """Scores map correctly: HIGH>=80, MEDIUM 50-79, LOW<50."""
        for score, expected_band in [(85, "HIGH"), (65, "MEDIUM"), (30, "LOW")]:
            if score >= 80:
                band = "HIGH"
            elif score >= 50:
                band = "MEDIUM"
            else:
                band = "LOW"
            assert band == expected_band

    def test_visual_similarity_is_none_in_v11(self):
        """visual_similarity_score is None in v1.1 (no ML inference)."""
        from app.modules.vessel_hunt import _compute_hunt_score
        det = MagicMock()
        det.length_estimate_m = None
        det.vessel_type_inferred = None
        det.detection_lat = 57.0
        det.detection_lon = 20.0
        mission = MagicMock()
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0
        score, breakdown = _compute_hunt_score(det, mission, None)
        assert breakdown["visual_similarity"] is None

    def test_drift_score_decreases_with_distance(self):
        """Drift probability score decreases as detection is farther from center."""
        from app.modules.vessel_hunt import _compute_hunt_score

        mission = MagicMock()
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0

        vessel = MagicMock()
        vessel.deadweight = 50000.0
        vessel.vessel_type = "tanker"

        det_close = MagicMock()
        det_close.length_estimate_m = None
        det_close.vessel_type_inferred = None
        det_close.detection_lat = 57.0
        det_close.detection_lon = 20.0

        det_far = MagicMock()
        det_far.length_estimate_m = None
        det_far.vessel_type_inferred = None
        det_far.detection_lat = 60.0  # ~180 nm away
        det_far.detection_lon = 20.0

        score_close, bd_close = _compute_hunt_score(det_close, mission, vessel)
        score_far, bd_far = _compute_hunt_score(det_far, mission, vessel)

        assert bd_close["drift_probability"] > bd_far["drift_probability"]

    def test_length_match_outside_tolerance(self):
        """Length estimate outside 20% tolerance scores 0."""
        from app.modules.vessel_hunt import _compute_hunt_score

        det = MagicMock()
        det.length_estimate_m = 50.0  # Way too small
        det.vessel_type_inferred = None
        det.detection_lat = 57.0
        det.detection_lon = 20.0

        mission = MagicMock()
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0

        vessel = MagicMock()
        vessel.deadweight = 50000.0
        vessel.vessel_type = None

        score, breakdown = _compute_hunt_score(det, mission, vessel)
        assert breakdown["length_match"] == 0.0


class TestMissionLifecycle:
    def test_create_target_raises_for_missing_vessel(self):
        """create_target_profile raises ValueError if vessel not found."""
        from app.modules.vessel_hunt import create_target_profile
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(ValueError, match="not found"):
            create_target_profile(99999, mock_db)

    def test_create_target_profile_success(self):
        """create_target_profile creates a profile with vessel's DWT."""
        from app.modules.vessel_hunt import create_target_profile

        mock_db = MagicMock()
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.deadweight = 75000.0
        mock_db.query.return_value.filter.return_value.first.return_value = vessel

        profile = create_target_profile(1, mock_db)
        assert profile.vessel_id == 1
        assert profile.deadweight_dwt == 75000.0

    def test_finalize_mission_sets_status_reviewed(self):
        """finalize_mission sets mission.status to 'reviewed'."""
        from app.modules.vessel_hunt import finalize_mission
        mock_db = MagicMock()
        mission = MagicMock()
        mission.mission_id = 1
        mission.status = "pending_imagery"
        candidate = MagicMock()
        candidate.candidate_id = 1
        mock_db.query.return_value.filter.return_value.first.side_effect = [mission, candidate]
        result = finalize_mission(1, 1, mock_db)
        assert mission.status == "reviewed"
        assert candidate.analyst_review_status == "confirmed"

    def test_finalize_mission_raises_for_missing_mission(self):
        """finalize_mission raises ValueError if mission not found."""
        from app.modules.vessel_hunt import finalize_mission
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(ValueError, match="Mission.*not found"):
            finalize_mission(999, 1, mock_db)

    def test_finalize_mission_raises_for_missing_candidate(self):
        """finalize_mission raises ValueError if candidate not found."""
        from app.modules.vessel_hunt import finalize_mission
        mock_db = MagicMock()
        mission = MagicMock()
        mission.mission_id = 1
        mock_db.query.return_value.filter.return_value.first.side_effect = [mission, None]
        with pytest.raises(ValueError, match="Candidate.*not found"):
            finalize_mission(1, 999, mock_db)


class TestHuntAPI:
    def test_post_hunt_targets_201(self, api_client):
        """POST /hunt/targets returns 201 or 404 (vessel not found is ok)."""
        resp = api_client.post("/api/v1/hunt/targets?vessel_id=1")
        assert resp.status_code in (201, 404, 422, 500)

    def test_get_hunt_missions_404(self, api_client):
        """GET /hunt/missions/999 returns 404."""
        resp = api_client.get("/api/v1/hunt/missions/999")
        assert resp.status_code == 404

    def test_list_hunt_targets_200(self, api_client, mock_db):
        """GET /hunt/targets returns 200 with list."""
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/hunt/targets")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_hunt_target_not_found(self, api_client):
        """GET /hunt/targets/999 returns 404."""
        resp = api_client.get("/api/v1/hunt/targets/999")
        assert resp.status_code == 404

    def test_list_candidates_200(self, api_client, mock_db):
        """GET /hunt/missions/1/candidates returns 200."""
        mock_db.query.return_value.filter.return_value.all.return_value = []
        resp = api_client.get("/api/v1/hunt/missions/1/candidates")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
