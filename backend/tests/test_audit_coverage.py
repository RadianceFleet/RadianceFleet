"""Tests for G5: Audit logging expansion — verify _audit_log is called in state-changing routes."""
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone


class TestCorridorAuditLogging:
    def test_create_corridor_has_audit_log(self, api_client, mock_db):
        mock_db.add = MagicMock()
        mock_db.flush = MagicMock()
        mock_db.commit = MagicMock()
        def set_id(obj):
            obj.corridor_id = 42
        mock_db.add.side_effect = set_id

        with patch("app.api.routes._audit_log") as mock_audit:
            resp = api_client.post(
                "/api/v1/corridors",
                json={"name": "Test Corridor", "corridor_type": "export_route", "risk_weight": 1.5},
            )
            assert resp.status_code == 200
            mock_audit.assert_called_once()
            args = mock_audit.call_args
            assert args[0][1] == "create"
            assert args[0][2] == "corridor"

    def test_update_corridor_has_audit_log(self, api_client, mock_db):
        corridor = MagicMock()
        corridor.corridor_id = 1
        corridor.name = "Original"
        corridor.corridor_type = MagicMock(value="export_route")
        corridor.risk_weight = 1.0
        corridor.is_jamming_zone = False
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        with patch("app.api.routes._audit_log") as mock_audit:
            resp = api_client.patch(
                "/api/v1/corridors/1",
                json={"name": "Updated Name"},
            )
            assert resp.status_code == 200
            mock_audit.assert_called_once()
            args = mock_audit.call_args
            assert args[0][1] == "update"
            assert args[0][2] == "corridor"


class TestWatchlistAuditLogging:
    def test_add_to_watchlist_has_audit_log(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        def set_id(obj):
            obj.watchlist_entry_id = 10
        mock_db.add.side_effect = set_id

        with patch("app.api.routes._audit_log") as mock_audit:
            resp = api_client.post(
                "/api/v1/watchlist",
                json={"vessel_id": 1, "reason": "Test"},
            )
            assert resp.status_code == 200
            mock_audit.assert_called_once()
            args = mock_audit.call_args
            assert args[0][1] == "add"
            assert args[0][2] == "watchlist"


class TestAISImportAuditLogging:
    def test_ais_import_has_audit_log(self, api_client, mock_db):
        with patch("app.modules.ingest.ingest_ais_csv", return_value={
            "accepted": 10, "rejected": 2, "duplicates": 0,
            "replaced": 0, "ignored": 0, "errors": [],
            "errors_truncated": False, "total_errors": 0,
        }):
            with patch("app.api.routes._audit_log") as mock_audit:
                from io import BytesIO
                resp = api_client.post(
                    "/api/v1/ais/import",
                    files={"file": ("test.csv", BytesIO(b"mmsi,timestamp,lat,lon\n"), "text/csv")},
                )
                assert resp.status_code == 200
                mock_audit.assert_called_once()
                args = mock_audit.call_args
                assert args[0][1] == "ais_import"
                assert args[0][2] == "ingestion"
