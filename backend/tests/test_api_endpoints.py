"""Comprehensive API endpoint tests — covers detection triggers, scoring,
evidence export, corridor CRUD, watchlist, bulk operations, dark vessels,
audit log, and error scenarios.

Uses the shared conftest fixtures (mock_db, api_client).
"""
from io import BytesIO
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Detection Triggers
# ---------------------------------------------------------------------------

class TestGapDetection:
    def test_detect_gaps_returns_200(self, api_client, mock_db):
        with patch("app.modules.gap_detector.run_gap_detection", return_value={"detected": 5}):
            resp = api_client.post("/api/v1/gaps/detect")
            assert resp.status_code == 200
            assert resp.json()["detected"] == 5

    def test_detect_gaps_with_date_range(self, api_client, mock_db):
        with patch("app.modules.gap_detector.run_gap_detection", return_value={"detected": 2}):
            resp = api_client.post(
                "/api/v1/gaps/detect?date_from=2026-01-01&date_to=2026-01-31"
            )
            assert resp.status_code == 200


class TestSpoofingDetection:
    def test_detect_spoofing_returns_200(self, api_client, mock_db):
        with patch("app.modules.gap_detector.run_spoofing_detection", return_value={"anomalies": 3}):
            resp = api_client.post("/api/v1/spoofing/detect")
            assert resp.status_code == 200
            assert resp.json()["anomalies"] == 3

    def test_get_spoofing_events_empty(self, api_client, mock_db):
        resp = api_client.get("/api/v1/spoofing/1")
        assert resp.status_code == 200
        assert resp.json() == []


class TestLoiteringDetection:
    def test_detect_loitering_returns_200(self, api_client, mock_db):
        with patch("app.modules.loitering_detector.run_loitering_detection", return_value={"loitering_events": 2}):
            with patch("app.modules.loitering_detector.detect_laid_up_vessels", return_value={"laid_up_updated": 1}):
                resp = api_client.post("/api/v1/loitering/detect")
                assert resp.status_code == 200

    def test_get_loitering_events_empty(self, api_client, mock_db):
        resp = api_client.get("/api/v1/loitering/1")
        assert resp.status_code == 200
        assert resp.json() == []


class TestStsDetection:
    def test_detect_sts_returns_200(self, api_client, mock_db):
        with patch("app.modules.sts_detector.detect_sts_events", return_value={"sts_events": 1}):
            resp = api_client.post("/api/v1/sts/detect")
            assert resp.status_code == 200

    def test_get_sts_events_empty(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/sts-events")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class TestScoring:
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
# Evidence Export
# ---------------------------------------------------------------------------

class TestEvidenceExport:
    def test_export_json_returns_200(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_evidence_card", return_value={"evidence": "card"}):
            resp = api_client.post("/api/v1/alerts/1/export?format=json")
            assert resp.status_code == 200

    def test_export_markdown_returns_200(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_evidence_card", return_value={"markdown": "# Card"}):
            resp = api_client.post("/api/v1/alerts/1/export?format=md")
            assert resp.status_code == 200

    def test_export_csv_returns_200(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_evidence_card", return_value={"csv": "col1,col2"}):
            resp = api_client.post("/api/v1/alerts/1/export?format=csv")
            assert resp.status_code == 200

    def test_export_returns_400_on_error(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_evidence_card", return_value={"error": "Status is new"}):
            resp = api_client.post("/api/v1/alerts/1/export?format=json")
            assert resp.status_code == 400
            assert "Status is new" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Alert Status & Notes
# ---------------------------------------------------------------------------

class TestAlertStatusUpdate:
    def _make_mock_alert(self, mock_db):
        alert = MagicMock()
        alert.status = "new"
        alert.analyst_notes = ""
        alert.gap_event_id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = alert
        return alert

    def test_update_status_ok(self, api_client, mock_db):
        self._make_mock_alert(mock_db)
        resp = api_client.post(
            "/api/v1/alerts/1/status",
            json={"status": "under_review"},
        )
        assert resp.status_code == 200
        assert resp.json()["new_status"] == "under_review"

    def test_update_status_with_reason(self, api_client, mock_db):
        alert = self._make_mock_alert(mock_db)
        resp = api_client.post(
            "/api/v1/alerts/1/status",
            json={"status": "documented", "reason": "Confirmed by satellite imagery"},
        )
        assert resp.status_code == 200
        assert "documented" in alert.analyst_notes

    def test_update_status_404_unknown_alert(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.post(
            "/api/v1/alerts/99999/status",
            json={"status": "dismissed"},
        )
        assert resp.status_code == 404


class TestAlertNotes:
    def test_add_note_ok(self, api_client, mock_db):
        alert = MagicMock()
        alert.analyst_notes = ""
        mock_db.query.return_value.filter.return_value.first.return_value = alert
        resp = api_client.post(
            "/api/v1/alerts/1/notes",
            json={"notes": "Suspicious pattern observed"},
        )
        assert resp.status_code == 200
        assert alert.analyst_notes == "Suspicious pattern observed"

    def test_add_note_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.post(
            "/api/v1/alerts/99999/notes",
            json={"notes": "test"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Bulk Status
# ---------------------------------------------------------------------------

class TestBulkStatus:
    def test_bulk_status_update(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.update.return_value = 3
        resp = api_client.post(
            "/api/v1/alerts/bulk-status",
            json={"alert_ids": [1, 2, 3], "status": "under_review"},
        )
        assert resp.status_code == 200
        assert resp.json()["updated"] == 3

    def test_bulk_status_empty_ids_returns_422(self, api_client, mock_db):
        resp = api_client.post(
            "/api/v1/alerts/bulk-status",
            json={"alert_ids": [], "status": "under_review"},
        )
        assert resp.status_code == 422

    def test_bulk_status_invalid_status_returns_422(self, api_client, mock_db):
        resp = api_client.post(
            "/api/v1/alerts/bulk-status",
            json={"alert_ids": [1], "status": "invalid_status"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Corridor CRUD
# ---------------------------------------------------------------------------

class TestCorridorCRUD:
    def test_create_corridor_ok(self, api_client, mock_db):
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        def set_id(obj):
            obj.corridor_id = 42
        mock_db.add.side_effect = set_id
        resp = api_client.post(
            "/api/v1/corridors",
            json={"name": "Test Corridor", "corridor_type": "export_route", "risk_weight": 1.5},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

    def test_create_corridor_missing_name_returns_422(self, api_client, mock_db):
        resp = api_client.post(
            "/api/v1/corridors",
            json={"corridor_type": "export_route"},
        )
        assert resp.status_code == 422

    def test_create_corridor_invalid_type_returns_400(self, api_client, mock_db):
        resp = api_client.post(
            "/api/v1/corridors",
            json={"name": "Test", "corridor_type": "nonexistent_type"},
        )
        assert resp.status_code == 400

    def test_update_corridor_ok(self, api_client, mock_db):
        corridor = MagicMock()
        corridor.corridor_id = 1
        corridor.name = "Original"
        corridor.corridor_type = MagicMock(value="export_route")
        corridor.risk_weight = 1.0
        corridor.is_jamming_zone = False
        mock_db.query.return_value.filter.return_value.first.return_value = corridor
        resp = api_client.patch(
            "/api/v1/corridors/1",
            json={"name": "Updated Name", "risk_weight": 2.0},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        assert corridor.name == "Updated Name"
        assert corridor.risk_weight == 2.0

    def test_update_corridor_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.patch("/api/v1/corridors/99999", json={"name": "Test"})
        assert resp.status_code == 404

    def test_delete_corridor_ok(self, api_client, mock_db):
        corridor = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = corridor
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        resp = api_client.delete("/api/v1/corridors/1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_corridor_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.delete("/api/v1/corridors/99999")
        assert resp.status_code == 404

    def test_delete_corridor_409_with_linked_gaps(self, api_client, mock_db):
        corridor = MagicMock()
        call_count = [0]
        def filter_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.first.return_value = corridor
            else:
                result.count.return_value = 5
            return result
        mock_db.query.return_value.filter.side_effect = filter_side_effect
        resp = api_client.delete("/api/v1/corridors/1")
        assert resp.status_code == 409
        assert "gap event" in resp.json()["detail"].lower()

    def test_get_corridor_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/corridors/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Watchlist Management
# ---------------------------------------------------------------------------

class TestWatchlistManagement:
    def test_add_to_watchlist_ok(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        def set_id(obj):
            obj.watchlist_entry_id = 10
        mock_db.add.side_effect = set_id
        resp = api_client.post(
            "/api/v1/watchlist",
            json={"vessel_id": 1, "reason": "Suspected sanctions evasion"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "added"

    def test_add_to_watchlist_missing_vessel_id(self, api_client, mock_db):
        resp = api_client.post("/api/v1/watchlist", json={})
        assert resp.status_code == 400

    def test_add_to_watchlist_vessel_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.post("/api/v1/watchlist", json={"vessel_id": 99999})
        assert resp.status_code == 404

    def test_remove_from_watchlist_ok(self, api_client, mock_db):
        entry = MagicMock()
        entry.is_active = True
        mock_db.query.return_value.filter.return_value.first.return_value = entry
        resp = api_client.delete("/api/v1/watchlist/1")
        assert resp.status_code == 200
        assert entry.is_active is False

    def test_remove_from_watchlist_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.delete("/api/v1/watchlist/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Watchlist Import
# ---------------------------------------------------------------------------

class TestWatchlistImport:
    def test_import_ofac_returns_200(self, api_client, mock_db):
        with patch("app.modules.watchlist_loader.load_ofac_sdn", return_value=5):
            resp = api_client.post(
                "/api/v1/watchlist/import",
                data={"source": "ofac"},
                files={"file": ("sdn.csv", BytesIO(b"mmsi,name\n123456789,TEST"), "text/csv")},
            )
            assert resp.status_code == 200
            assert resp.json()["imported"] == 5
            assert resp.json()["source"] == "ofac"

    def test_import_kse_returns_200(self, api_client, mock_db):
        with patch("app.modules.watchlist_loader.load_kse_list", return_value=3):
            resp = api_client.post(
                "/api/v1/watchlist/import",
                data={"source": "kse"},
                files={"file": ("kse.csv", BytesIO(b"mmsi,name\n"), "text/csv")},
            )
            assert resp.status_code == 200
            assert resp.json()["source"] == "kse"

    def test_import_opensanctions_returns_200(self, api_client, mock_db):
        with patch("app.modules.watchlist_loader.load_opensanctions", return_value=7):
            resp = api_client.post(
                "/api/v1/watchlist/import",
                data={"source": "opensanctions"},
                files={"file": ("os.csv", BytesIO(b"mmsi,name\n"), "text/csv")},
            )
            assert resp.status_code == 200

    def test_import_invalid_source_returns_422(self, api_client, mock_db):
        resp = api_client.post(
            "/api/v1/watchlist/import",
            data={"source": "fake_source"},
            files={"file": ("test.csv", BytesIO(b"mmsi,name\n"), "text/csv")},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Alert Map & CSV Export
# ---------------------------------------------------------------------------

class TestAlertMap:
    def test_alert_map_returns_200(self, api_client, mock_db):
        mock_db.query.return_value.options.return_value.order_by.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/alerts/map")
        assert resp.status_code == 200
        assert "points" in resp.json()

    def test_alerts_csv_export_returns_streaming(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.all.return_value = []
        resp = api_client.get("/api/v1/alerts/export")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Dark Vessels
# ---------------------------------------------------------------------------

class TestDarkVessels:
    def test_list_dark_vessels_empty(self, api_client, mock_db):
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_dark_vessel_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/dark-vessels/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Satellite Check
# ---------------------------------------------------------------------------

class TestSatelliteCheck:
    def test_prepare_satellite_check_returns_200(self, api_client, mock_db):
        with patch("app.modules.satellite_query.prepare_satellite_check", return_value={"status": "prepared"}):
            resp = api_client.post("/api/v1/alerts/1/satellite-check")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Vessel Detail & Related
# ---------------------------------------------------------------------------

class TestVesselDetail:
    def _mock_vessel(self, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.imo = "IMO1234567"
        vessel.name = "TEST VESSEL"
        vessel.flag = "PA"
        vessel.vessel_type = "Crude Oil Tanker"
        vessel.deadweight = 50000.0
        vessel.year_built = 2005
        vessel.ais_class = MagicMock(value="A")
        vessel.flag_risk_category = MagicMock(value="high")
        vessel.pi_coverage_status = MagicMock(value="unknown")
        vessel.psc_detained_last_12m = False
        vessel.mmsi_first_seen_utc = datetime(2020, 1, 1, tzinfo=timezone.utc)
        vessel.vessel_laid_up_30d = False
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = False
        vessel.merged_into_vessel_id = None
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        mock_db.query.return_value.filter.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.all.return_value = []
        return vessel

    def test_vessel_detail_returns_profile(self, api_client, mock_db):
        self._mock_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mmsi"] == "123456789"
        assert data["name"] == "TEST VESSEL"
        assert "watchlist_entries" in data
        assert "spoofing_anomalies_30d" in data
        assert "total_gaps_7d" in data

    def test_vessel_detail_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/vessels/99999")
        assert resp.status_code == 404

    def test_vessel_alerts_empty(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        resp = api_client.get("/api/v1/vessels/1/alerts")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Ingestion Status
# ---------------------------------------------------------------------------

class TestIngestionStatus:
    def test_ingestion_status_idle(self, api_client, mock_db):
        resp = api_client.get("/api/v1/ingestion-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "idle"


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_audit_log_empty(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/audit-log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["logs"] == []

    def test_audit_log_with_action_filter(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/audit-log?action=status_change")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Gov Package Export
# ---------------------------------------------------------------------------

class TestGovPackage:
    def test_export_gov_package_ok(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_gov_package", return_value={"package": "data"}):
            resp = api_client.post("/api/v1/alerts/1/export/gov-package")
            assert resp.status_code == 200

    def test_export_gov_package_error(self, api_client, mock_db):
        with patch("app.modules.evidence_export.export_gov_package", return_value={"error": "Not reviewed"}):
            resp = api_client.post("/api/v1/alerts/1/export/gov-package")
            assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Hunt Endpoints
# ---------------------------------------------------------------------------

class TestHuntEndpoints:
    def test_create_hunt_target_404_on_missing_vessel(self, api_client, mock_db):
        with patch("app.modules.vessel_hunt.create_target_profile", side_effect=ValueError("Vessel not found")):
            resp = api_client.post("/api/v1/hunt/targets?vessel_id=99999")
            assert resp.status_code == 404

    def test_list_hunt_targets_empty(self, api_client, mock_db):
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/hunt/targets")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_hunt_target_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/hunt/targets/99999")
        assert resp.status_code == 404

    def test_get_hunt_mission_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/hunt/missions/99999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Input Validation & Edge Cases
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_alerts_limit_over_500_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/alerts?limit=501")
        assert resp.status_code == 422

    def test_vessels_negative_skip_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/vessels?skip=-1")
        assert resp.status_code == 422

    def test_corridors_negative_skip_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/corridors?skip=-1")
        assert resp.status_code == 422

    def test_sts_events_limit_over_500_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/sts-events?limit=501")
        assert resp.status_code == 422
