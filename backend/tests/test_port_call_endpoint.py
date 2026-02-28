"""Tests for port call endpoint."""
from unittest.mock import MagicMock


class TestPortCallEndpoint:
    def test_returns_port_call_history(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1

        pc = MagicMock()
        pc.port_call_id = 10
        pc.vessel_id = 1
        pc.port_id = 5
        pc.arrival_utc = MagicMock()
        pc.arrival_utc.isoformat.return_value = "2026-01-15T10:00:00"
        pc.departure_utc = MagicMock()
        pc.departure_utc.isoformat.return_value = "2026-01-16T08:00:00"
        pc.source = "gfw"

        port = MagicMock()
        port.name = "Rotterdam"

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
        assert len(data["items"]) == 1
        assert data["total"] == 1

    def test_response_includes_port_name(self, api_client, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 1

        pc = MagicMock()
        pc.port_call_id = 10
        pc.vessel_id = 1
        pc.port_id = 5
        pc.arrival_utc = MagicMock()
        pc.arrival_utc.isoformat.return_value = "2026-01-15T10:00:00"
        pc.departure_utc = None
        pc.source = None

        port = MagicMock()
        port.name = "Fujairah"

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
        assert data["items"][0]["port_name"] == "Fujairah"

    def test_vessel_not_found_returns_404(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/port-calls/99999")
        assert resp.status_code == 404
