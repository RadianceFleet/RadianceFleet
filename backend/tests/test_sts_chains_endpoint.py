"""Tests for GET /sts-chains endpoint."""

from datetime import UTC, datetime
from unittest.mock import MagicMock


def _make_fleet_alert(
    alert_id=1, alert_type="sts_relay_chain", chain_vessel_ids=None, risk_score=50
):
    a = MagicMock()
    a.alert_id = alert_id
    a.alert_type = alert_type
    a.risk_score_component = risk_score
    a.created_utc = datetime(2025, 6, 1, tzinfo=UTC)
    a.evidence_json = {
        "chain_vessel_ids": chain_vessel_ids or [1, 2, 3],
        "intermediary_vessel_ids": [2],
        "hops": [{"from": 1, "to": 2}, {"from": 2, "to": 3}],
    }
    return a


def _make_vessel_row(vessel_id, name):
    """Simulates a SQLAlchemy Row(vessel_id, name) tuple."""
    return (vessel_id, name)


class TestStsChains:
    def test_happy_path(self, api_client, mock_db):
        alerts = [_make_fleet_alert(1), _make_fleet_alert(2, chain_vessel_ids=[4, 5])]

        # Setup chain: query(FleetAlert).filter(...).order_by(...) -> count -> offset -> limit -> all
        q = mock_db.query.return_value.filter.return_value
        q.order_by.return_value.count.return_value = 2
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = alerts
        # count() call
        q.count.return_value = 2
        # offset/limit
        q.offset.return_value.limit.return_value.all.return_value = alerts

        # Vessel name lookup
        mock_db.query.return_value.filter.return_value.all.return_value = [
            _make_vessel_row(1, "Tanker A"),
            _make_vessel_row(2, "Tanker B"),
            _make_vessel_row(3, "Tanker C"),
            _make_vessel_row(4, "Tanker D"),
            _make_vessel_row(5, "Tanker E"),
        ]

        response = api_client.get("/api/v1/sts-chains")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_empty_result(self, api_client, mock_db):
        q = mock_db.query.return_value.filter.return_value
        q.order_by.return_value.count.return_value = 0
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        q.count.return_value = 0
        q.offset.return_value.limit.return_value.all.return_value = []

        response = api_client.get("/api/v1/sts-chains")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
