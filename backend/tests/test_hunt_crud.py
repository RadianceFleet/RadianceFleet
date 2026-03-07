"""Tests for hunt workflow CRUD endpoints."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from tests.conftest import make_mock_vessel


class TestCreateHuntTarget:
    """POST /api/v1/hunt/targets"""

    def test_create_hunt_target_success(self, api_client, mock_db):
        vessel = make_mock_vessel(vessel_id=1, deadweight=85000.0)
        mock_db.query.return_value.filter.return_value.first.return_value = vessel

        profile = MagicMock()
        profile.profile_id = 10
        profile.vessel_id = 1
        profile.deadweight_dwt = 85000.0
        profile.last_ais_position_lat = 55.0
        profile.last_ais_position_lon = 25.0
        profile.last_ais_timestamp_utc = None
        profile.profile_created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        profile.created_by_analyst_id = None
        profile.reference_images_json = None
        profile.hull_type = None
        profile.loa_meters = None
        profile.beam_meters = None
        profile.typical_draft_meters = None
        profile.funnel_color = None
        profile.hull_color = None

        def on_refresh(obj):
            for attr in vars(profile):
                if not attr.startswith("_"):
                    setattr(obj, attr, getattr(profile, attr))

        mock_db.refresh.side_effect = on_refresh

        resp = api_client.post(
            "/api/v1/hunt/targets",
            json={"vessel_id": 1, "last_lat": 55.0, "last_lon": 25.0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vessel_id"] == 1
        assert data["deadweight_dwt"] == 85000.0
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_hunt_target_vessel_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None

        resp = api_client.post(
            "/api/v1/hunt/targets",
            json={"vessel_id": 999},
        )
        assert resp.status_code == 404
        assert "Vessel not found" in resp.json()["detail"]


class TestCreateHuntMission:
    """POST /api/v1/hunt/missions"""

    def test_create_hunt_mission_success(self, api_client, mock_db):
        profile = MagicMock()
        profile.profile_id = 10
        profile.vessel_id = 1
        profile.last_ais_position_lat = 55.0
        profile.last_ais_position_lon = 25.0
        mock_db.query.return_value.filter.return_value.first.return_value = profile

        mission = MagicMock()
        mission.mission_id = 20
        mission.vessel_id = 1
        mission.profile_id = 10
        mission.search_start_utc = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mission.search_end_utc = datetime(2024, 1, 2, tzinfo=timezone.utc)
        mission.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mission.analyst_id = None
        mission.search_ellipse_wkt = None
        mission.center_lat = 55.0
        mission.center_lon = 25.0
        mission.max_radius_nm = None
        mission.elapsed_hours = None
        mission.confidence = None
        mission.status = "pending_imagery"

        def on_refresh(obj):
            for attr in vars(mission):
                if not attr.startswith("_"):
                    setattr(obj, attr, getattr(mission, attr))

        mock_db.refresh.side_effect = on_refresh

        resp = api_client.post(
            "/api/v1/hunt/missions",
            json={
                "target_profile_id": 10,
                "search_start_utc": "2024-01-01T00:00:00Z",
                "search_end_utc": "2024-01-02T00:00:00Z",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["vessel_id"] == 1
        assert data["profile_id"] == 10
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_hunt_mission_profile_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None

        resp = api_client.post(
            "/api/v1/hunt/missions",
            json={
                "target_profile_id": 999,
                "search_start_utc": "2024-01-01T00:00:00Z",
                "search_end_utc": "2024-01-02T00:00:00Z",
            },
        )
        assert resp.status_code == 404
        assert "Target profile not found" in resp.json()["detail"]


class TestFinalizeMission:
    """PUT /api/v1/hunt/missions/{id}/finalize"""

    def test_finalize_mission_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None

        resp = api_client.put(
            "/api/v1/hunt/missions/999/finalize",
            json={"candidate_id": 1},
        )
        assert resp.status_code == 404
        assert "Mission not found" in resp.json()["detail"]
