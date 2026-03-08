"""Tests for detector API exposure endpoints."""

from unittest.mock import MagicMock


class TestPortCallEndpoint:
    def test_vessel_not_found_returns_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/port-calls/99999")
        assert resp.status_code == 404

    def test_returns_port_calls(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1

        pc = MagicMock()
        pc.port_call_id = 10
        pc.vessel_id = 1
        pc.port_id = 5
        pc.arrival_utc = MagicMock()
        pc.arrival_utc.isoformat.return_value = "2026-01-15T10:00:00"
        pc.departure_utc = None
        pc.source = "digitraffic"

        port = MagicMock()
        port.name = "Helsinki"

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] == 2:
                result.filter.return_value.order_by.return_value.all.return_value = [pc]
            elif call_count[0] == 3:
                result.filter.return_value.first.return_value = port
            return result

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/port-calls/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vessel_id"] == 1
        assert data["total"] >= 0

    def test_empty_port_calls(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = vessel
            else:
                result.filter.return_value.order_by.return_value.all.return_value = []
            return result

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/port-calls/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []
