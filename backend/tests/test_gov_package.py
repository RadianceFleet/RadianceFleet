"""Tests for FR10 gov alert package export."""
from datetime import datetime
from unittest.mock import MagicMock, patch
import pytest


class TestExportGovPackage:
    """Unit tests for export_gov_package()."""

    def test_alert_not_found_returns_error(self):
        from app.modules.evidence_export import export_gov_package

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        result = export_gov_package(999, mock_db)
        assert "error" in result
        assert "not found" in result["error"]

    def test_new_status_blocked(self):
        from app.modules.evidence_export import export_gov_package

        mock_db = MagicMock()
        gap = MagicMock()
        gap.status = "new"
        mock_db.query.return_value.filter.return_value.first.return_value = gap
        result = export_gov_package(999, mock_db)
        assert "error" in result
        assert "analyst review" in result["error"]

    def test_successful_export_has_required_keys(self):
        from app.modules.evidence_export import export_gov_package

        mock_db = MagicMock()
        gap = MagicMock()
        gap.status = "confirmed"
        gap.gap_event_id = 1
        gap.vessel_id = 10
        gap.corridor_id = None
        gap.gap_start_utc = datetime(2024, 1, 1)
        gap.gap_end_utc = datetime(2024, 1, 2)
        gap.duration_minutes = 1440
        gap.risk_score = 85
        gap.risk_breakdown_json = {"gap_duration": 30}
        gap.max_plausible_distance_nm = 360.0
        gap.actual_gap_distance_nm = 100.0
        gap.velocity_plausibility_ratio = 1.5
        gap.impossible_speed_flag = False
        gap.analyst_notes = "Test note"

        vessel = MagicMock()
        vessel.vessel_id = 10
        vessel.mmsi = "123456789"
        vessel.imo = "IMO1234567"
        vessel.name = "Test Vessel"
        vessel.flag = "PA"
        vessel.vessel_type = "tanker"

        def query_side_effect(model):
            m = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if "AISGapEvent" in model_name:
                m.filter.return_value.first.return_value = gap
            elif "Vessel" in model_name and "Target" not in model_name and "Watchlist" not in model_name:
                m.filter.return_value.first.return_value = vessel
            elif "VesselTargetProfile" in model_name:
                m.filter.return_value.first.return_value = None
            else:
                m.filter.return_value.first.return_value = None
                m.filter.return_value.order_by.return_value.first.return_value = None
            return m

        mock_db.query.side_effect = query_side_effect

        result = export_gov_package(1, mock_db)
        assert "error" not in result
        assert "evidence_card" in result
        assert "hunt_context" in result
        assert "package_metadata" in result
        assert result["package_metadata"]["alert_id"] == 1
        assert result["package_metadata"]["vessel_mmsi"] == "123456789"
        assert "disclaimer" in result["package_metadata"]

    def test_export_without_hunt_context(self):
        from app.modules.evidence_export import export_gov_package

        mock_db = MagicMock()
        gap = MagicMock()
        gap.status = "confirmed"
        gap.gap_event_id = 2
        gap.vessel_id = 10
        gap.corridor_id = None
        gap.gap_start_utc = datetime(2024, 1, 1)
        gap.gap_end_utc = datetime(2024, 1, 2)
        gap.duration_minutes = 1440
        gap.risk_score = 50
        gap.risk_breakdown_json = {}
        gap.max_plausible_distance_nm = 200.0
        gap.actual_gap_distance_nm = 50.0
        gap.velocity_plausibility_ratio = 1.0
        gap.impossible_speed_flag = False
        gap.analyst_notes = None

        vessel = MagicMock()
        vessel.vessel_id = 10
        vessel.mmsi = "999999999"
        vessel.imo = None
        vessel.name = "No Hunt"
        vessel.flag = "MT"
        vessel.vessel_type = "bulk_carrier"

        def query_side_effect(model):
            m = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if "AISGapEvent" in model_name:
                m.filter.return_value.first.return_value = gap
            elif "Vessel" in model_name and "Target" not in model_name and "Watchlist" not in model_name:
                m.filter.return_value.first.return_value = vessel
            else:
                m.filter.return_value.first.return_value = None
                m.filter.return_value.order_by.return_value.first.return_value = None
                m.filter.return_value.order_by.return_value.all.return_value = []
            return m

        mock_db.query.side_effect = query_side_effect

        result = export_gov_package(2, mock_db, include_hunt_context=False)
        assert "error" not in result
        assert result["hunt_context"] is None


class TestBuildHuntContext:
    """Unit tests for _build_hunt_context()."""

    def test_no_profile_returns_none(self):
        from app.modules.evidence_export import _build_hunt_context

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        result = _build_hunt_context(10, mock_db)
        assert result is None

    def test_profile_with_no_missions(self):
        from app.modules.evidence_export import _build_hunt_context

        mock_db = MagicMock()
        profile = MagicMock()
        profile.profile_id = 1

        call_count = [0]
        def query_side_effect(model):
            m = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if "VesselTargetProfile" in model_name:
                m.filter.return_value.first.return_value = profile
            elif "SearchMission" in model_name:
                m.filter.return_value.order_by.return_value.all.return_value = []
            else:
                m.filter.return_value.all.return_value = []
            return m

        mock_db.query.side_effect = query_side_effect
        result = _build_hunt_context(10, mock_db)
        assert result is not None
        assert result["profile_id"] == 1
        assert result["missions"] == []

    def test_profile_with_missions_and_candidates(self):
        from app.modules.evidence_export import _build_hunt_context

        mock_db = MagicMock()
        profile = MagicMock()
        profile.profile_id = 1

        mission = MagicMock()
        mission.mission_id = 5
        mission.status = "reviewed"
        mission.max_radius_nm = 360.0
        mission.elapsed_hours = 24.0
        mission.center_lat = 57.0
        mission.center_lon = 20.0

        candidate = MagicMock()
        candidate.candidate_id = 10
        candidate.hunt_score = 45.0
        candidate.score_breakdown_json = {"total": 45.0, "band": "MEDIUM"}
        candidate.detection_lat = 57.1
        candidate.detection_lon = 20.1
        candidate.analyst_review_status = "confirmed"

        def query_side_effect(model):
            m = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if "VesselTargetProfile" in model_name:
                m.filter.return_value.first.return_value = profile
            elif "SearchMission" in model_name:
                m.filter.return_value.order_by.return_value.all.return_value = [mission]
            elif "HuntCandidate" in model_name:
                m.filter.return_value.all.return_value = [candidate]
            else:
                m.filter.return_value.all.return_value = []
            return m

        mock_db.query.side_effect = query_side_effect
        result = _build_hunt_context(10, mock_db)
        assert len(result["missions"]) == 1
        assert result["missions"][0]["mission_id"] == 5
        assert len(result["missions"][0]["candidates"]) == 1
        assert result["missions"][0]["candidates"][0]["hunt_score"] == 45.0


class TestGovPackageAPI:
    """API integration tests for the gov-package endpoint."""

    def test_post_gov_package_400_on_new_status(self, api_client, mock_db):
        gap = MagicMock()
        gap.status = "new"
        mock_db.query.return_value.filter.return_value.first.return_value = gap
        resp = api_client.post("/api/v1/alerts/1/export/gov-package")
        assert resp.status_code == 400

    def test_post_gov_package_400_on_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.post("/api/v1/alerts/999/export/gov-package")
        assert resp.status_code == 400
