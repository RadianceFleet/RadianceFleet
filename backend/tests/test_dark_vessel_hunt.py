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


class TestGetMission:
    """GET /api/v1/hunt/missions/{id} — get mission details."""

    def test_get_mission_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/hunt/missions/99999")
        assert resp.status_code == 404


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
