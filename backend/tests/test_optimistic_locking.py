"""Tests for optimistic locking on alert write endpoints."""
from __future__ import annotations

import pytest
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
        "version": 1,
    }
    for k, v in {**defaults, **kwargs}.items():
        setattr(alert, k, v)
    return alert


class TestOptimisticLocking:
    def test_version_increments_on_status_change(self, api_client, mock_db):
        mock_alert = _make_mock_alert(version=1)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        resp = api_client.post(
            "/api/v1/alerts/1/status",
            json={"status": "under_review"},
        )
        assert resp.status_code == 200
        assert mock_alert.version == 2

    def test_version_increments_on_verdict(self, api_client, mock_db):
        mock_alert = _make_mock_alert(version=1)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        resp = api_client.post(
            "/api/v1/alerts/1/verdict",
            json={"verdict": "confirmed_tp"},
        )
        assert resp.status_code == 200
        assert mock_alert.version == 2

    def test_version_mismatch_409(self, api_client, mock_db):
        mock_alert = _make_mock_alert(version=2)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        resp = api_client.post(
            "/api/v1/alerts/1/status",
            json={"status": "under_review", "version": 1},
        )
        assert resp.status_code == 409
        assert "Version conflict" in resp.json()["detail"]

    def test_no_version_provided_no_conflict(self, api_client, mock_db):
        """When no version is provided, no conflict check — backward compatible."""
        mock_alert = _make_mock_alert(version=5)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        resp = api_client.post(
            "/api/v1/alerts/1/status",
            json={"status": "under_review"},
        )
        assert resp.status_code == 200
        # Version should still increment
        assert mock_alert.version == 6
