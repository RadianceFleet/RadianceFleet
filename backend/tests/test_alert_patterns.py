"""Tests for H3: Alert recurring pattern fields — prior_similar_count and is_recurring_pattern."""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestAlertPatterns:
    def _make_mock_alert(self, mock_db, prior_count=0):
        """Create a mock alert and set up DB queries for the enriched get_alert endpoint."""
        alert = MagicMock()
        alert.gap_event_id = 1
        alert.vessel_id = 10
        alert.corridor_id = 5
        alert.gap_start_utc = datetime(2026, 1, 15, tzinfo=timezone.utc)
        alert.gap_end_utc = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        alert.duration_minutes = 720
        alert.risk_score = 75
        alert.risk_breakdown_json = None
        alert.status = MagicMock(value="new")
        alert.analyst_notes = None
        alert.impossible_speed_flag = False
        alert.velocity_plausibility_ratio = None
        alert.max_plausible_distance_nm = None
        alert.actual_gap_distance_nm = None
        alert.in_dark_zone = False
        alert.start_point_id = None
        alert.end_point_id = None

        vessel = MagicMock()
        vessel.vessel_id = 10
        vessel.name = "TEST TANKER"
        vessel.mmsi = "123456789"
        vessel.flag = "PA"
        vessel.deadweight = 50000.0

        # Default: return alert for first query, vessel for second, None for rest
        query_results = [alert, vessel, None, None, None, None]
        filter_results = iter(query_results)

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.filter.return_value.first.return_value = None
            result.filter.return_value.all.return_value = []
            result.filter.return_value.scalar.return_value = prior_count
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = alert
            elif call_count[0] == 2:
                result.filter.return_value.first.return_value = vessel
            return result
        mock_db.query.side_effect = query_side_effect

        return alert, vessel

    def test_prior_similar_count_returned(self, api_client, mock_db):
        self._make_mock_alert(mock_db, prior_count=2)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "prior_similar_count" in data

    def test_is_recurring_pattern_true_when_gte_3(self, api_client, mock_db):
        self._make_mock_alert(mock_db, prior_count=3)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_recurring_pattern"] is True

    def test_is_recurring_pattern_false_when_lt_3(self, api_client, mock_db):
        self._make_mock_alert(mock_db, prior_count=2)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_recurring_pattern"] is False

    def test_prior_count_zero_for_first_alert(self, api_client, mock_db):
        self._make_mock_alert(mock_db, prior_count=0)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["prior_similar_count"] == 0
        assert data["is_recurring_pattern"] is False
