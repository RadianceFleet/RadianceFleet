"""Tests for GET /loitering (global) endpoint."""

from datetime import UTC, datetime
from unittest.mock import MagicMock


def _make_loitering_event(
    loiter_id=1, vessel_id=1, mean_lat=25.0, mean_lon=55.0, duration_hours=12.0, corridor_id=1
):
    e = MagicMock()
    e.loiter_id = loiter_id
    e.vessel_id = vessel_id
    e.mean_lat = mean_lat
    e.mean_lon = mean_lon
    e.duration_hours = duration_hours
    e.corridor_id = corridor_id
    e.start_time_utc = datetime(2025, 6, 1, tzinfo=UTC)
    e.median_sog_kn = 0.5
    return e


class TestGlobalLoitering:
    def test_happy_path(self, api_client, mock_db):
        events = [
            _make_loitering_event(1, vessel_id=1),
            _make_loitering_event(2, vessel_id=2, mean_lat=30.0),
        ]

        q = mock_db.query.return_value
        q.order_by.return_value.count.return_value = 2
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = events
        q.count.return_value = 2
        q.offset.return_value.limit.return_value.all.return_value = events

        response = api_client.get("/api/v1/loitering")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_empty_result(self, api_client, mock_db):
        q = mock_db.query.return_value
        q.order_by.return_value.count.return_value = 0
        q.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        q.count.return_value = 0
        q.offset.return_value.limit.return_value.all.return_value = []

        response = api_client.get("/api/v1/loitering")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_with_date_filter(self, api_client, mock_db):
        q = mock_db.query.return_value
        fq = q.filter.return_value
        fq.order_by.return_value.count.return_value = 0
        fq.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        fq.count.return_value = 0
        fq.offset.return_value.limit.return_value.all.return_value = []
        fq.filter.return_value = fq  # chained filters

        response = api_client.get("/api/v1/loitering?date_from=2025-01-01&date_to=2025-06-01")
        assert response.status_code == 200
