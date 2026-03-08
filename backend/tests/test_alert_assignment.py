"""Tests for alert assignment endpoints."""
from __future__ import annotations

import jwt
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

from app.config import settings


JWT_SECRET = "test-secret-key-1234567890abcdef"


def _make_token(analyst_id: int = 1, username: str = "alice", role: str = "admin"):
    payload = {"analyst_id": analyst_id, "username": username, "role": role, "sub": username}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _make_mock_alert(**kwargs):
    alert = MagicMock()
    defaults = {
        "gap_event_id": 1,
        "vessel_id": 1,
        "status": "new",
        "assigned_to": None,
        "assigned_at": None,
        "version": 1,
        "risk_score": 80,
    }
    for k, v in {**defaults, **kwargs}.items():
        setattr(alert, k, v)
    return alert


def _make_mock_analyst(**kwargs):
    analyst = MagicMock()
    defaults = {"analyst_id": 1, "username": "alice", "role": "admin"}
    for k, v in {**defaults, **kwargs}.items():
        setattr(analyst, k, v)
    return analyst


class TestAssignAlert:
    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_assign_alert(self, api_client, mock_db):
        mock_analyst = _make_mock_analyst()
        mock_alert = _make_mock_alert()

        # First query returns analyst, second returns alert
        def side_effect(model):
            q = MagicMock()
            if model.__name__ == "Analyst":
                q.filter.return_value.first.return_value = mock_analyst
            else:
                q.filter.return_value.first.return_value = mock_alert
            return q
        mock_db.query.side_effect = side_effect

        token = _make_token()
        resp = api_client.post(
            "/api/v1/alerts/1/assign",
            json={"analyst_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["assigned_to"] == 1
        assert mock_alert.assigned_to == 1
        assert mock_alert.assigned_at is not None

    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_unassign_alert(self, api_client, mock_db):
        mock_alert = _make_mock_alert(assigned_to=1)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_alert

        token = _make_token()
        resp = api_client.delete(
            "/api/v1/alerts/1/assign",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert mock_alert.assigned_to is None
        assert mock_alert.assigned_at is None

    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_my_alerts(self, api_client, mock_db):
        mock_alert = _make_mock_alert(assigned_to=1)
        # Set up __table__.columns for dict comprehension
        col1 = MagicMock()
        col1.name = "gap_event_id"
        mock_alert.__table__ = MagicMock()
        mock_alert.__table__.columns = [col1]
        mock_alert.gap_event_id = 1
        mock_alert.vessel = MagicMock()
        mock_alert.vessel.name = "TestVessel"
        mock_alert.vessel.mmsi = "123456789"

        q = mock_db.query.return_value.options.return_value
        q.filter.return_value.count.return_value = 1
        q.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [mock_alert]

        token = _make_token(analyst_id=1)
        resp = api_client.get(
            "/api/v1/alerts/my",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["vessel_name"] == "TestVessel"

    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_assign_nonexistent_analyst(self, api_client, mock_db):
        def side_effect(model):
            q = MagicMock()
            if model.__name__ == "Analyst":
                q.filter.return_value.first.return_value = None
            else:
                q.filter.return_value.first.return_value = _make_mock_alert()
            return q
        mock_db.query.side_effect = side_effect

        token = _make_token()
        resp = api_client.post(
            "/api/v1/alerts/1/assign",
            json={"analyst_id": 999},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert "Analyst not found" in resp.json()["detail"]

    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_assign_nonexistent_alert(self, api_client, mock_db):
        mock_analyst = _make_mock_analyst()

        def side_effect(model):
            q = MagicMock()
            if model.__name__ == "Analyst":
                q.filter.return_value.first.return_value = mock_analyst
            else:
                q.filter.return_value.first.return_value = None
            return q
        mock_db.query.side_effect = side_effect

        token = _make_token()
        resp = api_client.post(
            "/api/v1/alerts/999/assign",
            json={"analyst_id": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
        assert "Alert not found" in resp.json()["detail"]
