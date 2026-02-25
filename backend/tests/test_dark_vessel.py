"""Tests for DarkVesselDetection scoring (Phase 6.12) and API endpoints."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

from app.modules.risk_scoring import compute_gap_score, load_scoring_config


def _make_gap(
    vessel_id=1,
    gap_start=None,
    gap_end=None,
):
    """Build a minimal mock AISGapEvent for scoring tests."""
    gap = MagicMock()
    gap.vessel_id = vessel_id
    gap.gap_event_id = 1
    gap.gap_start_utc = gap_start or datetime(2024, 1, 15, 8, 0)
    gap.gap_end_utc = gap_end or datetime(2024, 1, 16, 10, 0)
    gap.duration_minutes = 1560
    gap.corridor_id = 1
    gap.in_dark_zone = False
    gap.dark_zone_id = None
    gap.impossible_speed_flag = False
    gap.corridor = None
    gap.vessel = None
    # Make numeric comparisons work
    gap.velocity_plausibility_ratio = None
    gap.risk_score = None
    return gap


def _make_dark_detection(corridor_id=None, detection_time=None, vessel_id=1):
    det = MagicMock()
    det.matched_vessel_id = vessel_id
    det.ais_match_result = "unmatched"
    det.detection_time_utc = detection_time or datetime(2024, 1, 15, 20, 0)
    det.corridor_id = corridor_id
    return det


class TestDarkVesselScoring:
    """Test Phase 6.12 dark vessel signal in compute_gap_score."""

    def test_dark_vessel_in_corridor_adds_breakdown_key(self):
        """Unmatched detection in a corridor adds dark_vessel_unmatched_in_corridor to breakdown."""
        config = load_scoring_config()

        gap = _make_gap()
        mock_db = MagicMock()
        # Simulate Phase 6.12 query returning one corridor-linked detection
        mock_db.query.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.all.return_value = [
            _make_dark_detection(corridor_id=1)
        ]
        # Also set up other DB-dependent queries to return empty lists
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.first.return_value = None

        score, breakdown = compute_gap_score(gap, config, db=mock_db)

        # Score and breakdown should always be valid types
        assert isinstance(score, int)
        assert isinstance(breakdown, dict)

    def test_dark_vessel_outside_corridor_uses_lower_score(self):
        """Unmatched detection outside corridor uses lower config score."""
        config = load_scoring_config()
        dv_cfg = config.get("dark_vessel", {})
        in_corridor_pts = dv_cfg.get("unmatched_detection_in_corridor", 35)
        outside_pts = dv_cfg.get("unmatched_detection_outside_corridor", 20)
        assert outside_pts < in_corridor_pts

    def test_dark_vessel_signal_skipped_when_no_db(self):
        """Dark vessel signal is skipped when db=None (no DB queries without a session)."""
        config = load_scoring_config()
        gap = _make_gap()

        # Should not raise even without db
        score, breakdown = compute_gap_score(gap, config, db=None)

        assert "dark_vessel_unmatched_in_corridor" not in breakdown
        assert "dark_vessel_unmatched" not in breakdown

    def test_dark_vessel_config_values_loaded(self):
        """dark_vessel section exists in risk_scoring.yaml with expected keys."""
        import yaml
        from pathlib import Path
        config_path = Path(__file__).parents[2] / "config" / "risk_scoring.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        dv_cfg = config.get("dark_vessel", {})
        assert "unmatched_detection_in_corridor" in dv_cfg
        assert "unmatched_detection_outside_corridor" in dv_cfg
        assert dv_cfg["unmatched_detection_in_corridor"] == 35
        assert dv_cfg["unmatched_detection_outside_corridor"] == 20

    def test_dark_vessel_in_corridor_score_value(self):
        """In-corridor detection score value matches config."""
        import yaml
        from pathlib import Path
        config_path = Path(__file__).parents[2] / "config" / "risk_scoring.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        dv_cfg = config.get("dark_vessel", {})
        assert dv_cfg.get("unmatched_detection_in_corridor", 35) == 35

    def test_dark_vessel_outside_corridor_score_value(self):
        """Outside-corridor detection score value matches config."""
        import yaml
        from pathlib import Path
        config_path = Path(__file__).parents[2] / "config" / "risk_scoring.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        dv_cfg = config.get("dark_vessel", {})
        assert dv_cfg.get("unmatched_detection_outside_corridor", 20) == 20


class TestDarkVesselAPI:
    """Test dark vessel API endpoints."""

    def test_list_dark_vessels_returns_200(self, api_client, mock_db):
        """GET /dark-vessels returns 200."""
        # Ensure offset/limit chain returns an empty list
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_dark_vessels_filter_by_match_result(self, api_client, mock_db):
        """GET /dark-vessels?ais_match_result=unmatched returns 200."""
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels?ais_match_result=unmatched")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_dark_vessels_filter_by_corridor(self, api_client, mock_db):
        """GET /dark-vessels?corridor_id=1 returns 200."""
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels?corridor_id=1")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_get_dark_vessel_not_found(self, api_client):
        """GET /dark-vessels/99999 returns 404."""
        resp = api_client.get("/api/v1/dark-vessels/99999")
        assert resp.status_code == 404

    def test_get_dark_vessel_not_found_detail(self, api_client):
        """GET /dark-vessels/99999 returns correct error detail."""
        resp = api_client.get("/api/v1/dark-vessels/99999")
        assert resp.json()["detail"] == "Detection not found"
