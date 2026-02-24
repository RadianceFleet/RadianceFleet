"""Tests for vessel-specific API endpoints."""
from io import BytesIO


def test_vessel_history_404_for_unknown(api_client):
    response = api_client.get("/api/v1/vessels/99999/history")
    assert response.status_code == 404


def test_vessel_watchlist_404_for_unknown(api_client):
    response = api_client.get("/api/v1/vessels/99999/watchlist")
    assert response.status_code == 404


def test_watchlist_import_rejects_unknown_source(api_client):
    response = api_client.post(
        "/api/v1/watchlist/import",
        data={"source": "invalid_source"},
        files={"file": ("test.csv", BytesIO(b"mmsi,name\n123456789,TEST"), "text/csv")},
    )
    assert response.status_code == 422
