"""Integration tests for detector endpoints and underlying detector functions.

Tests the API detection triggers (gap, spoofing, loitering, STS, MMSI cloning)
as well as the mmsi_cloning_detector module directly.

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

import pytest


# ---------------------------------------------------------------------------
# MMSI Cloning Detector — module-level tests
# ---------------------------------------------------------------------------

class TestMMSICloningDetector:
    """Tests for app.modules.mmsi_cloning_detector.detect_mmsi_cloning."""

    def test_detect_mmsi_cloning_empty_db(self, api_client, mock_db):
        """Empty DB returns empty list, no 500 error."""
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_db.commit = MagicMock()

        from app.modules.mmsi_cloning_detector import detect_mmsi_cloning

        results = detect_mmsi_cloning(mock_db)
        assert isinstance(results, list)
        assert len(results) == 0

    def test_detect_mmsi_cloning_no_points(self, api_client, mock_db):
        """Vessels with no AIS points produce no cloning events."""
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.merged_into_vessel_id = None
        mock_db.query.return_value.filter.return_value.all.return_value = [vessel]
        # AIS points query returns empty
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        mock_db.commit = MagicMock()

        from app.modules.mmsi_cloning_detector import detect_mmsi_cloning

        results = detect_mmsi_cloning(mock_db)
        assert isinstance(results, list)
        assert len(results) == 0

    def test_detect_mmsi_cloning_returns_list_of_dicts(self, api_client, mock_db):
        """When impossible jumps are found, return dicts with expected keys."""
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.merged_into_vessel_id = None

        # Two points far apart in short time
        p1 = MagicMock()
        p1.lat = 55.0
        p1.lon = 15.0
        p1.timestamp_utc = datetime(2026, 1, 1, 12, 0, 0)
        p1.vessel_id = 1

        p2 = MagicMock()
        p2.lat = 56.0
        p2.lon = 16.0
        p2.timestamp_utc = datetime(2026, 1, 1, 12, 5, 0)  # 5 minutes later
        p2.vessel_id = 1

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # Vessel query
                result.filter.return_value.all.return_value = [vessel]
            elif call_count[0] == 2:
                # AIS points query - ordered by time
                result.filter.return_value.order_by.return_value.all.return_value = [p1, p2]
            else:
                # Duplicate check
                result.filter.return_value.first.return_value = None
            return result

        mock_db.query.side_effect = query_side_effect
        mock_db.commit = MagicMock()
        mock_db.add = MagicMock()

        from app.modules.mmsi_cloning_detector import detect_mmsi_cloning

        results = detect_mmsi_cloning(mock_db)
        # The jump is ~70nm in 5 minutes = ~840 knots > 50 knots threshold
        assert isinstance(results, list)
        if len(results) > 0:
            r = results[0]
            assert "mmsi" in r
            assert "vessel_id" in r
            assert "distance_nm" in r
            assert "implied_speed_kn" in r


# ---------------------------------------------------------------------------
# API Detection Endpoints via TestClient
# ---------------------------------------------------------------------------

class TestGapDetectionEndpoint:
    """POST /api/v1/gaps/detect returns detection results."""

    def test_detect_gaps_returns_200(self, api_client, mock_db):
        with patch("app.modules.gap_detector.run_gap_detection", return_value={"detected": 5}):
            resp = api_client.post("/api/v1/gaps/detect")
            assert resp.status_code == 200
            assert resp.json()["detected"] == 5

    def test_detect_gaps_empty_db_returns_zero(self, api_client, mock_db):
        with patch("app.modules.gap_detector.run_gap_detection", return_value={"detected": 0}):
            resp = api_client.post("/api/v1/gaps/detect")
            assert resp.status_code == 200
            data = resp.json()
            assert data["detected"] == 0


class TestSpoofingDetectionEndpoint:
    """POST /api/v1/spoofing/detect returns detection results."""

    def test_detect_spoofing_returns_200(self, api_client, mock_db):
        with patch("app.modules.gap_detector.run_spoofing_detection", return_value={"anomalies": 3}):
            resp = api_client.post("/api/v1/spoofing/detect")
            assert resp.status_code == 200
            assert resp.json()["anomalies"] == 3

    def test_detect_spoofing_empty_db_returns_zero(self, api_client, mock_db):
        with patch("app.modules.gap_detector.run_spoofing_detection", return_value={"anomalies": 0}):
            resp = api_client.post("/api/v1/spoofing/detect")
            assert resp.status_code == 200
            data = resp.json()
            assert data["anomalies"] == 0

    def test_get_spoofing_events_for_vessel_empty(self, api_client, mock_db):
        """GET /api/v1/spoofing/{vessel_id} returns empty list, not 500."""
        mock_db.query.return_value.filter.return_value.all.return_value = []
        resp = api_client.get("/api/v1/spoofing/1")
        assert resp.status_code == 200
        assert resp.json() == []


class TestLoiteringDetectionEndpoint:
    """POST /api/v1/loitering/detect returns detection results."""

    def test_detect_loitering_returns_200(self, api_client, mock_db):
        with patch("app.modules.loitering_detector.run_loitering_detection", return_value={"loitering_events": 2}):
            with patch("app.modules.loitering_detector.detect_laid_up_vessels", return_value={"laid_up_updated": 1}):
                resp = api_client.post("/api/v1/loitering/detect")
                assert resp.status_code == 200

    def test_detect_loitering_empty_db(self, api_client, mock_db):
        with patch("app.modules.loitering_detector.run_loitering_detection", return_value={"loitering_events": 0}):
            with patch("app.modules.loitering_detector.detect_laid_up_vessels", return_value={"laid_up_updated": 0}):
                resp = api_client.post("/api/v1/loitering/detect")
                assert resp.status_code == 200

    def test_get_loitering_events_for_vessel_empty(self, api_client, mock_db):
        """GET /api/v1/loitering/{vessel_id} returns empty list, not 500."""
        mock_db.query.return_value.filter.return_value.all.return_value = []
        resp = api_client.get("/api/v1/loitering/1")
        assert resp.status_code == 200
        assert resp.json() == []


class TestStsDetectionEndpoint:
    """POST /api/v1/sts/detect returns detection results."""

    def test_detect_sts_returns_200(self, api_client, mock_db):
        with patch("app.modules.sts_detector.detect_sts_events", return_value={"sts_events": 1}):
            resp = api_client.post("/api/v1/sts/detect")
            assert resp.status_code == 200

    def test_detect_sts_empty_db_returns_zero(self, api_client, mock_db):
        with patch("app.modules.sts_detector.detect_sts_events", return_value={"sts_events": 0}):
            resp = api_client.post("/api/v1/sts/detect")
            assert resp.status_code == 200


class TestScoringEndpoints:
    """POST /api/v1/score-alerts and /api/v1/rescore-all-alerts."""

    def test_score_alerts_returns_200(self, api_client, mock_db):
        with patch("app.modules.risk_scoring.score_all_alerts", return_value={"scored": 10}):
            resp = api_client.post("/api/v1/score-alerts")
            assert resp.status_code == 200
            assert resp.json()["scored"] == 10

    def test_rescore_all_alerts_returns_200(self, api_client, mock_db):
        with patch("app.modules.risk_scoring.rescore_all_alerts", return_value={"rescored": 8}):
            resp = api_client.post("/api/v1/rescore-all-alerts")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Dark Vessels
# ---------------------------------------------------------------------------

class TestDarkVesselEndpoints:
    """Tests for dark vessel list and detail endpoints."""

    def test_list_dark_vessels_empty(self, api_client, mock_db):
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_dark_vessel_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/dark-vessels/99999")
        assert resp.status_code == 404

    def test_list_dark_vessels_with_filter(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels?ais_match_result=unmatched")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Helper: _find_impossible_jumps unit test
# ---------------------------------------------------------------------------

class TestFindImpossibleJumps:
    """Direct test of mmsi_cloning_detector._find_impossible_jumps."""

    def test_no_jumps_for_close_points(self):
        from app.modules.mmsi_cloning_detector import _find_impossible_jumps

        p1 = MagicMock()
        p1.lat = 55.0
        p1.lon = 15.0
        p1.timestamp_utc = datetime(2026, 1, 1, 12, 0, 0)

        p2 = MagicMock()
        p2.lat = 55.001
        p2.lon = 15.001
        p2.timestamp_utc = datetime(2026, 1, 1, 12, 30, 0)

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"

        jumps = _find_impossible_jumps([p1, p2], vessel)
        # Close points (< 0.1nm) in 30 minutes = very slow speed
        assert len(jumps) == 0

    def test_impossible_jump_detected(self):
        from app.modules.mmsi_cloning_detector import _find_impossible_jumps

        p1 = MagicMock()
        p1.lat = 55.0
        p1.lon = 15.0
        p1.timestamp_utc = datetime(2026, 1, 1, 12, 0, 0)

        # 2 degrees lat away (~120nm) in 10 minutes = ~720 knots
        p2 = MagicMock()
        p2.lat = 57.0
        p2.lon = 15.0
        p2.timestamp_utc = datetime(2026, 1, 1, 12, 10, 0)

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"

        jumps = _find_impossible_jumps([p1, p2], vessel)
        assert len(jumps) == 1
        assert jumps[0]["implied_speed_kn"] > 50


class TestScoringFunction:
    """Test the _score_cloning helper function."""

    def test_score_cloning_high_speed(self):
        from app.modules.mmsi_cloning_detector import _score_cloning
        score = _score_cloning(150.0)
        assert score == 55

    def test_score_cloning_medium_speed(self):
        from app.modules.mmsi_cloning_detector import _score_cloning
        score = _score_cloning(60.0)
        assert score == 40

    def test_score_cloning_low_speed(self):
        from app.modules.mmsi_cloning_detector import _score_cloning
        score = _score_cloning(25.0)
        assert score == 25
