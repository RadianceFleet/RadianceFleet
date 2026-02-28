"""Tests for dark vessel hunt endpoints (FR9).

Tests the hunt target/mission/candidate lifecycle:
  - POST /hunt/targets — create target profile
  - GET /hunt/targets — list targets
  - GET /hunt/targets/{id} — get target
  - POST /hunt/missions — create search mission
  - GET /hunt/missions/{id} — get mission
  - POST /hunt/missions/{id}/find-candidates — run candidate finder
  - GET /hunt/missions/{id}/candidates — list candidates
  - POST /hunt/missions/{id}/confirm/{candidate_id} — confirm candidate

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock, patch


class TestCreateHuntTarget:
    """POST /api/v1/hunt/targets — register a vessel for hunting."""

    def test_create_target_404_on_missing_vessel(self, api_client, mock_db):
        with patch(
            "app.modules.vessel_hunt.create_target_profile",
            side_effect=ValueError("Vessel not found"),
        ):
            resp = api_client.post("/api/v1/hunt/targets?vessel_id=99999")
            assert resp.status_code == 404

    def test_create_target_returns_201(self, api_client, mock_db):
        profile = MagicMock()
        profile.profile_id = 1
        profile.vessel_id = 10
        profile.deadweight_dwt = 50000.0

        with patch(
            "app.modules.vessel_hunt.create_target_profile",
            return_value=profile,
        ):
            resp = api_client.post("/api/v1/hunt/targets?vessel_id=10")
            assert resp.status_code == 201
            data = resp.json()
            assert data["profile_id"] == 1
            assert data["vessel_id"] == 10
            assert data["deadweight_dwt"] == 50000.0


class TestListHuntTargets:
    """GET /api/v1/hunt/targets — list all target profiles."""

    def test_list_targets_empty(self, api_client, mock_db):
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/hunt/targets")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetHuntTarget:
    """GET /api/v1/hunt/targets/{id} — get target profile."""

    def test_get_target_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/hunt/targets/99999")
        assert resp.status_code == 404


class TestCreateSearchMission:
    """POST /api/v1/hunt/missions — create search mission with drift ellipse."""

    def test_create_mission_returns_201(self, api_client, mock_db):
        mission = MagicMock()
        mission.mission_id = 1
        mission.vessel_id = 10
        mission.max_radius_nm = 45.0
        mission.elapsed_hours = 12.0
        mission.status = "pending_imagery"
        mission.search_ellipse_wkt = "POLYGON((...))..."

        with patch(
            "app.modules.vessel_hunt.create_search_mission",
            return_value=mission,
        ):
            resp = api_client.post(
                "/api/v1/hunt/missions"
                "?target_profile_id=1"
                "&search_start_utc=2026-01-15T12:00:00"
                "&search_end_utc=2026-01-16T00:00:00"
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["mission_id"] == 1
            assert data["max_radius_nm"] == 45.0
            assert data["status"] == "pending_imagery"

    def test_create_mission_404_on_missing_profile(self, api_client, mock_db):
        with patch(
            "app.modules.vessel_hunt.create_search_mission",
            side_effect=ValueError("Profile not found"),
        ):
            resp = api_client.post(
                "/api/v1/hunt/missions"
                "?target_profile_id=99999"
                "&search_start_utc=2026-01-15T12:00:00"
                "&search_end_utc=2026-01-16T00:00:00"
            )
            assert resp.status_code == 404


class TestGetMission:
    """GET /api/v1/hunt/missions/{id} — get mission details."""

    def test_get_mission_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/hunt/missions/99999")
        assert resp.status_code == 404


class TestFindCandidates:
    """POST /api/v1/hunt/missions/{id}/find-candidates."""

    def test_find_candidates_returns_list(self, api_client, mock_db):
        c1 = MagicMock()
        c1.candidate_id = 1
        c1.hunt_score = 85.0
        c1.score_breakdown_json = {"proximity": 40, "size": 25, "heading": 20}
        c1.detection_lat = 55.5
        c1.detection_lon = 15.2
        c1.analyst_review_status = None

        with patch(
            "app.modules.vessel_hunt.find_hunt_candidates",
            return_value=[c1],
        ):
            resp = api_client.post("/api/v1/hunt/missions/1/find-candidates")
            assert resp.status_code == 201
            data = resp.json()
            assert len(data) == 1
            assert data[0]["candidate_id"] == 1
            assert data[0]["hunt_score"] == 85.0

    def test_find_candidates_404_on_missing_mission(self, api_client, mock_db):
        with patch(
            "app.modules.vessel_hunt.find_hunt_candidates",
            side_effect=ValueError("Mission not found"),
        ):
            resp = api_client.post("/api/v1/hunt/missions/99999/find-candidates")
            assert resp.status_code == 404

    def test_find_candidates_empty_result(self, api_client, mock_db):
        with patch(
            "app.modules.vessel_hunt.find_hunt_candidates",
            return_value=[],
        ):
            resp = api_client.post("/api/v1/hunt/missions/1/find-candidates")
            assert resp.status_code == 201
            assert resp.json() == []


class TestListHuntCandidates:
    """GET /api/v1/hunt/missions/{id}/candidates."""

    def test_list_candidates_empty(self, api_client, mock_db):
        q = mock_db.query.return_value.filter.return_value
        q.count.return_value = 0
        q.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/hunt/missions/1/candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) == 0
        assert data["total"] == 0


class TestConfirmHuntCandidate:
    """POST /api/v1/hunt/missions/{id}/confirm/{candidate_id}."""

    def test_confirm_candidate_success(self, api_client, mock_db):
        mission = MagicMock()
        mission.mission_id = 1
        mission.status = "confirmed"

        with patch(
            "app.modules.vessel_hunt.finalize_mission",
            return_value=mission,
        ):
            resp = api_client.post("/api/v1/hunt/missions/1/confirm/5")
            assert resp.status_code == 200
            data = resp.json()
            assert data["mission_id"] == 1
            assert data["status"] == "confirmed"

    def test_confirm_candidate_404(self, api_client, mock_db):
        with patch(
            "app.modules.vessel_hunt.finalize_mission",
            side_effect=ValueError("Mission not found"),
        ):
            resp = api_client.post("/api/v1/hunt/missions/99999/confirm/1")
            assert resp.status_code == 404


class TestHuntModels:
    """Verify hunt-related model structures."""

    def test_target_profile_has_expected_fields(self):
        from app.models.stubs import VesselTargetProfile

        columns = {c.name for c in VesselTargetProfile.__table__.columns}
        assert "profile_id" in columns
        assert "vessel_id" in columns
        assert "deadweight_dwt" in columns
        assert "last_ais_position_lat" in columns
        assert "last_ais_position_lon" in columns

    def test_search_mission_has_expected_fields(self):
        from app.models.stubs import SearchMission

        columns = {c.name for c in SearchMission.__table__.columns}
        assert "mission_id" in columns
        assert "vessel_id" in columns
        assert "search_ellipse_wkt" in columns
        assert "max_radius_nm" in columns
        assert "status" in columns

    def test_hunt_candidate_has_expected_fields(self):
        from app.models.stubs import HuntCandidate

        columns = {c.name for c in HuntCandidate.__table__.columns}
        assert "candidate_id" in columns
        assert "mission_id" in columns
        assert "hunt_score" in columns
        assert "detection_lat" in columns
        assert "detection_lon" in columns
        assert "analyst_review_status" in columns

    def test_dark_vessel_detection_has_expected_fields(self):
        from app.models.stubs import DarkVesselDetection

        columns = {c.name for c in DarkVesselDetection.__table__.columns}
        assert "detection_id" in columns
        assert "scene_id" in columns
        assert "detection_lat" in columns
        assert "detection_lon" in columns
        assert "ais_match_result" in columns
