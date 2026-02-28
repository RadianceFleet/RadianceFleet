"""Tests for the enriched alert detail endpoint contract.

Verifies that GET /api/v1/alerts/{id} returns the full GapEventDetailRead
schema with both legacy and new fields. Tests backward compatibility.

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Alert Detail — Schema Contract
# ---------------------------------------------------------------------------

class TestAlertDetailContract:
    """GET /api/v1/alerts/{id} must return all GapEventDetailRead fields."""

    def _make_mock_alert(self, mock_db):
        """Create a fully-mocked alert with vessel, corridor, and points."""
        alert = MagicMock()
        alert.gap_event_id = 1
        alert.vessel_id = 10
        alert.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        alert.gap_end_utc = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
        alert.duration_minutes = 360
        alert.corridor_id = 5
        alert.risk_score = 72
        alert.risk_breakdown_json = {"gap_duration_6h": 15, "corridor_sts_zone": 20}
        alert.status = MagicMock(value="under_review")
        alert.analyst_notes = "Suspicious pattern"
        alert.impossible_speed_flag = False
        alert.velocity_plausibility_ratio = 0.8
        alert.max_plausible_distance_nm = 132.0
        alert.actual_gap_distance_nm = 105.0
        alert.in_dark_zone = False
        alert.start_point_id = 100
        alert.end_point_id = 101

        vessel = MagicMock()
        vessel.name = "SHADOW TANKER"
        vessel.mmsi = "987654321"
        vessel.flag = "PA"
        vessel.deadweight = 80000.0

        corridor = MagicMock()
        corridor.name = "Laconian Gulf STS"

        # AIS points
        last_pt = MagicMock()
        last_pt.timestamp_utc = datetime(2026, 1, 15, 11, 58, 0, tzinfo=timezone.utc)
        last_pt.lat = 36.5
        last_pt.lon = 22.8
        last_pt.sog = 8.5
        last_pt.cog = 180.0

        first_pt = MagicMock()
        first_pt.timestamp_utc = datetime(2026, 1, 15, 18, 2, 0, tzinfo=timezone.utc)
        first_pt.lat = 36.2
        first_pt.lon = 23.1
        first_pt.sog = 7.2
        first_pt.cog = 270.0

        # Configure mock_db.query to return the right objects
        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # AISGapEvent query
                result.filter.return_value.first.return_value = alert
            elif call_count[0] == 2:
                # Vessel query
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] == 3:
                # Corridor query
                result.filter.return_value.first.return_value = corridor
            elif call_count[0] == 4:
                # MovementEnvelope query
                result.filter.return_value.first.return_value = None
            elif call_count[0] == 5:
                # SatelliteCheck query
                result.filter.return_value.first.return_value = None
            elif call_count[0] == 6:
                # Start AISPoint query
                result.filter.return_value.first.return_value = last_pt
            elif call_count[0] == 7:
                # End AISPoint query
                result.filter.return_value.first.return_value = first_pt
            else:
                result.filter.return_value.first.return_value = None
            return result

        mock_db.query.side_effect = query_side_effect
        return alert

    def test_alert_detail_has_all_required_fields(self, api_client, mock_db):
        """Verify GapEventDetailRead has all required fields from the schema."""
        self._make_mock_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()

        # Core GapEventRead fields (backward compatibility)
        assert "gap_event_id" in data
        assert "vessel_id" in data
        assert "gap_start_utc" in data
        assert "gap_end_utc" in data
        assert "duration_minutes" in data
        assert "risk_score" in data
        assert "status" in data
        assert "impossible_speed_flag" in data
        assert "in_dark_zone" in data

    def test_alert_detail_has_enrichment_fields(self, api_client, mock_db):
        """Verify GapEventDetailRead enrichment fields are present."""
        self._make_mock_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()

        # Enrichment fields from GapEventDetailRead
        assert "vessel_name" in data
        assert "vessel_mmsi" in data
        assert "vessel_flag" in data
        assert "vessel_deadweight" in data
        assert "corridor_name" in data

    def test_alert_detail_has_movement_envelope(self, api_client, mock_db):
        """Movement envelope field should be present (None when no envelope)."""
        self._make_mock_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()

        assert "movement_envelope" in data
        # No envelope seeded, so it should be None
        assert data["movement_envelope"] is None

    def test_alert_detail_has_ais_boundary_points(self, api_client, mock_db):
        """Last and first AIS boundary points should be populated."""
        self._make_mock_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()

        assert "last_point" in data
        assert "first_point_after" in data

        # Last point should have the expected structure
        lp = data["last_point"]
        assert lp is not None
        assert "timestamp_utc" in lp
        assert "lat" in lp
        assert "lon" in lp

    def test_alert_detail_risk_breakdown(self, api_client, mock_db):
        """Risk breakdown JSON should be returned in the response."""
        self._make_mock_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()

        assert "risk_breakdown_json" in data
        assert data["risk_breakdown_json"] is not None

    def test_alert_detail_velocity_fields(self, api_client, mock_db):
        """Velocity plausibility and distance fields should be present."""
        self._make_mock_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()

        assert "velocity_plausibility_ratio" in data
        assert "max_plausible_distance_nm" in data
        assert "actual_gap_distance_nm" in data


class TestAlertDetail404:
    """GET /api/v1/alerts/{id} returns 404 for unknown alerts."""

    def test_alert_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/alerts/99999")
        assert resp.status_code == 404

    def test_alert_not_found_response_body(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/alerts/99999")
        data = resp.json()
        assert "detail" in data


class TestAlertDetailNullFields:
    """When linked data (satellite, envelope) is absent, fields are null/None."""

    def test_no_corridor_returns_null_corridor_name(self, api_client, mock_db):
        """Alert with no corridor has corridor_name: null."""
        alert = MagicMock()
        alert.gap_event_id = 2
        alert.vessel_id = 10
        alert.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        alert.gap_end_utc = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
        alert.duration_minutes = 360
        alert.corridor_id = None  # No corridor
        alert.risk_score = 30
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

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = alert
            elif call_count[0] == 2:
                result.filter.return_value.first.return_value = None  # No vessel
            elif call_count[0] == 3:
                result.filter.return_value.first.return_value = None  # No envelope
            elif call_count[0] == 4:
                result.filter.return_value.first.return_value = None  # No sat check
            else:
                result.filter.return_value.first.return_value = None
            return result

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/alerts/2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["corridor_name"] is None
        assert data["movement_envelope"] is None
        assert data["satellite_check"] is None
        assert data["last_point"] is None
        assert data["first_point_after"] is None


class TestAlertDetailPerformance:
    """Performance guard: enriched alert request completes quickly."""

    def test_alert_detail_completes_quickly(self, api_client, mock_db):
        """Alert detail should complete in < 500ms even with seeded data."""
        import time

        alert = MagicMock()
        alert.gap_event_id = 1
        alert.vessel_id = 10
        alert.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        alert.gap_end_utc = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)
        alert.duration_minutes = 360
        alert.corridor_id = None
        alert.risk_score = 50
        alert.risk_breakdown_json = {"gap_duration": 15}
        alert.status = MagicMock(value="new")
        alert.analyst_notes = None
        alert.impossible_speed_flag = False
        alert.velocity_plausibility_ratio = None
        alert.max_plausible_distance_nm = None
        alert.actual_gap_distance_nm = None
        alert.in_dark_zone = False
        alert.start_point_id = None
        alert.end_point_id = None

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = alert
            else:
                result.filter.return_value.first.return_value = None
            return result

        mock_db.query.side_effect = query_side_effect

        start = time.time()
        resp = api_client.get("/api/v1/alerts/1")
        elapsed = time.time() - start

        assert resp.status_code == 200
        assert elapsed < 0.5, f"Alert detail took {elapsed:.3f}s, exceeding 500ms"
