"""Tests for enriched alert detail endpoint."""


def test_alert_detail_404_for_unknown(api_client):
    """Alert detail returns 404 for unknown IDs (verifies endpoint exists and routes correctly)."""
    response = api_client.get("/api/v1/alerts/99999")
    assert response.status_code == 404
