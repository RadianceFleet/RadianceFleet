"""Tests for alert detail enrichment — spoofing, loitering, STS linked anomalies."""
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta


class TestAlertEnrichment:
    def _make_enriched_alert(self, mock_db, spoofing=None, loitering=None, sts=None, prior_count=0):
        """Set up mocks for the enriched get_alert endpoint."""
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

        call_count = [0]
        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.filter.return_value.first.return_value = None
            result.filter.return_value.all.return_value = []
            result.filter.return_value.scalar.return_value = prior_count
            if call_count[0] == 1:
                # AISGapEvent query
                result.filter.return_value.first.return_value = alert
            elif call_count[0] == 2:
                # Vessel query
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] == 3:
                # Corridor - None
                pass
            elif call_count[0] == 4:
                # MovementEnvelope - None
                pass
            elif call_count[0] == 5:
                # SatelliteCheck - None
                pass
            elif call_count[0] == 6:
                # SpoofingAnomaly query
                result.filter.return_value.filter.return_value.filter.return_value.all.return_value = spoofing or []
                result.filter.return_value.all.return_value = spoofing or []
            elif call_count[0] == 7:
                # LoiteringEvent query
                result.filter.return_value.filter.return_value.filter.return_value.all.return_value = loitering or []
                result.filter.return_value.all.return_value = loitering or []
            elif call_count[0] == 8:
                # StsTransferEvent query
                result.filter.return_value.filter.return_value.all.return_value = sts or []
                result.filter.return_value.all.return_value = sts or []
            elif call_count[0] == 9:
                # prior_similar_count
                result.filter.return_value.filter.return_value.filter.return_value.filter.return_value.filter.return_value.scalar.return_value = prior_count
                result.filter.return_value.scalar.return_value = prior_count
            return result
        mock_db.query.side_effect = query_side_effect

        return alert, vessel

    def test_backward_compat_old_fields_present(self, api_client, mock_db):
        self._make_enriched_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        # Old fields still present
        assert "gap_event_id" in data
        assert "vessel_id" in data
        assert "risk_score" in data
        assert "status" in data

    def test_new_fields_present(self, api_client, mock_db):
        self._make_enriched_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        # New enrichment fields
        assert "spoofing_anomalies" in data
        assert "loitering_events" in data
        assert "sts_events" in data
        assert "prior_similar_count" in data
        assert "is_recurring_pattern" in data

    def test_all_new_fields_null_when_no_linked_data(self, api_client, mock_db):
        self._make_enriched_alert(mock_db)
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["spoofing_anomalies"] is None
        assert data["loitering_events"] is None
        assert data["sts_events"] is None

    def test_spoofing_anomalies_populated(self, api_client, mock_db):
        spoof = MagicMock()
        spoof.anomaly_id = 100
        spoof.anomaly_type = MagicMock(value="anchor_spoof")
        spoof.start_time_utc = datetime(2026, 1, 15, 6, 0, tzinfo=timezone.utc)
        spoof.risk_score_component = 15
        spoof.evidence_json = {"note": "test"}

        self._make_enriched_alert(mock_db, spoofing=[spoof])
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["spoofing_anomalies"] is not None
        assert len(data["spoofing_anomalies"]) == 1
        assert data["spoofing_anomalies"][0]["anomaly_id"] == 100

    def test_loitering_events_populated(self, api_client, mock_db):
        loiter = MagicMock()
        loiter.loiter_id = 200
        loiter.start_time_utc = datetime(2026, 1, 14, tzinfo=timezone.utc)
        loiter.duration_hours = 8.5
        loiter.mean_lat = 36.0
        loiter.mean_lon = 22.0
        loiter.median_sog_kn = 0.5

        self._make_enriched_alert(mock_db, loitering=[loiter])
        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["loitering_events"] is not None
        assert len(data["loitering_events"]) == 1
        assert data["loitering_events"][0]["loiter_id"] == 200

    def test_sts_events_populated(self, api_client, mock_db):
        sts = MagicMock()
        sts.sts_id = 300
        sts.vessel_1_id = 10
        sts.vessel_2_id = 20
        sts.detection_type = MagicMock(value="visible_visible")
        sts.start_time_utc = datetime(2026, 1, 16, tzinfo=timezone.utc)

        partner = MagicMock()
        partner.name = "PARTNER VESSEL"
        partner.mmsi = "987654321"

        call_count = [0]
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

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.filter.return_value.first.return_value = None
            result.filter.return_value.all.return_value = []
            result.filter.return_value.scalar.return_value = 0
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = alert
            elif call_count[0] == 2:
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] == 8:
                result.filter.return_value.all.return_value = [sts]
            elif call_count[0] == 9:
                # Partner vessel lookup
                result.filter.return_value.first.return_value = partner
            return result
        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/alerts/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sts_events"] is not None
        assert len(data["sts_events"]) == 1
        assert data["sts_events"][0]["sts_id"] == 300
