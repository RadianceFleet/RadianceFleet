"""Tests for POST /alerts/{alert_id}/verdict endpoint."""

from unittest.mock import MagicMock


def _make_mock_alert(**kwargs):
    alert = MagicMock()
    defaults = {
        "gap_event_id": 1,
        "vessel_id": 1,
        "status": "new",
        "analyst_notes": "",
        "is_false_positive": None,
        "reviewed_by": None,
        "review_date": None,
        "risk_score": 80,
    }
    for k, v in {**defaults, **kwargs}.items():
        setattr(alert, k, v)
    return alert


class TestAlertVerdict:
    """Tests for the /alerts/{id}/verdict endpoint."""

    def test_verdict_confirmed_tp(self, api_client, mock_db):
        mock_alert = _make_mock_alert()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        response = api_client.post(
            "/api/v1/alerts/1/verdict",
            json={"verdict": "confirmed_tp"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["verdict"] == "confirmed_tp"
        assert data["is_false_positive"] is False
        assert mock_alert.is_false_positive is False
        assert mock_alert.status == "confirmed_tp"
        assert mock_alert.review_date is not None

    def test_verdict_confirmed_fp(self, api_client, mock_db):
        mock_alert = _make_mock_alert()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        response = api_client.post(
            "/api/v1/alerts/1/verdict",
            json={"verdict": "confirmed_fp"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["verdict"] == "confirmed_fp"
        assert data["is_false_positive"] is True
        assert mock_alert.is_false_positive is True
        assert mock_alert.status == "confirmed_fp"

    def test_verdict_404_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None

        response = api_client.post(
            "/api/v1/alerts/999/verdict",
            json={"verdict": "confirmed_tp"},
        )
        assert response.status_code == 404

    def test_verdict_invalid_value(self, api_client, mock_db):
        response = api_client.post(
            "/api/v1/alerts/1/verdict",
            json={"verdict": "invalid_value"},
        )
        assert response.status_code == 400

    def test_verdict_with_reason_and_reviewed_by(self, api_client, mock_db):
        mock_alert = _make_mock_alert()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        response = api_client.post(
            "/api/v1/alerts/1/verdict",
            json={
                "verdict": "confirmed_fp",
                "reason": "Vessel was in dry dock",
                "reviewed_by": "analyst_jane",
            },
        )
        assert response.status_code == 200
        assert mock_alert.reviewed_by == "test_admin"  # Auth override sets username
        assert "Vessel was in dry dock" in mock_alert.analyst_notes
        assert "test_admin" in mock_alert.analyst_notes

    def test_verdict_appends_to_existing_notes(self, api_client, mock_db):
        mock_alert = _make_mock_alert(analyst_notes="Previous note here")
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        response = api_client.post(
            "/api/v1/alerts/1/verdict",
            json={"verdict": "confirmed_tp"},
        )
        assert response.status_code == 200
        assert "Previous note here" in mock_alert.analyst_notes
        assert "confirmed_tp" in mock_alert.analyst_notes
