"""Tests for alert edit lock endpoints."""
from __future__ import annotations

import jwt
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.config import settings


JWT_SECRET = "test-secret-key-1234567890abcdef"


def _make_token(analyst_id: int = 1, username: str = "alice", role: str = "admin"):
    payload = {"analyst_id": analyst_id, "username": username, "role": role, "sub": username}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _make_mock_alert(**kwargs):
    alert = MagicMock()
    defaults = {"gap_event_id": 1, "vessel_id": 1, "status": "new", "version": 1}
    for k, v in {**defaults, **kwargs}.items():
        setattr(alert, k, v)
    return alert


def _make_mock_lock(**kwargs):
    lock = MagicMock()
    now = datetime.now(timezone.utc)
    defaults = {
        "lock_id": 1,
        "alert_id": 1,
        "analyst_id": 1,
        "acquired_at": now,
        "expires_at": now + timedelta(seconds=300),
    }
    for k, v in {**defaults, **kwargs}.items():
        setattr(lock, k, v)
    return lock


class TestEditLocks:
    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_acquire_lock(self, api_client, mock_db):
        mock_alert = _make_mock_alert()

        call_count = {"n": 0}

        def side_effect(model):
            call_count["n"] += 1
            q = MagicMock()
            if model.__name__ == "AISGapEvent":
                q.filter.return_value.first.return_value = mock_alert
            elif model.__name__ == "AlertEditLock":
                # First call: delete expired (returns count)
                q.filter.return_value.delete.return_value = 0
                # Second call: check existing lock
                q.filter.return_value.first.return_value = None
            return q
        mock_db.query.side_effect = side_effect

        token = _make_token(analyst_id=1)
        resp = api_client.post(
            "/api/v1/alerts/1/lock",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["analyst_id"] == 1
        assert data["alert_id"] == 1
        assert "expires_at" in data

    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_lock_conflict_409(self, api_client, mock_db):
        mock_alert = _make_mock_alert()
        existing_lock = _make_mock_lock(analyst_id=2)  # held by another analyst

        def side_effect(model):
            q = MagicMock()
            if model.__name__ == "AISGapEvent":
                q.filter.return_value.first.return_value = mock_alert
            elif model.__name__ == "AlertEditLock":
                q.filter.return_value.delete.return_value = 0
                q.filter.return_value.first.return_value = existing_lock
            return q
        mock_db.query.side_effect = side_effect

        token = _make_token(analyst_id=1, username="alice")
        resp = api_client.post(
            "/api/v1/alerts/1/lock",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409
        assert "Lock held" in resp.json()["detail"]

    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_expired_lock_cleaned(self, api_client, mock_db):
        """Expired locks are cleaned and new lock can be acquired."""
        mock_alert = _make_mock_alert()

        def side_effect(model):
            q = MagicMock()
            if model.__name__ == "AISGapEvent":
                q.filter.return_value.first.return_value = mock_alert
            elif model.__name__ == "AlertEditLock":
                # Expired lock cleaned
                q.filter.return_value.delete.return_value = 1
                # No existing lock after cleanup
                q.filter.return_value.first.return_value = None
            return q
        mock_db.query.side_effect = side_effect

        token = _make_token(analyst_id=1, username="test_admin")
        resp = api_client.post(
            "/api/v1/alerts/1/lock",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["analyst_id"] == 1

    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_heartbeat_extends_ttl(self, api_client, mock_db):
        existing_lock = _make_mock_lock(analyst_id=1)
        mock_db.query.return_value.filter.return_value.first.return_value = existing_lock

        token = _make_token(analyst_id=1)
        resp = api_client.post(
            "/api/v1/alerts/1/lock/heartbeat",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert "expires_at" in resp.json()

    @patch.object(settings, "ADMIN_JWT_SECRET", JWT_SECRET)
    def test_release_lock(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.delete.return_value = 1

        token = _make_token(analyst_id=1)
        resp = api_client.delete(
            "/api/v1/alerts/1/lock",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["released"] is True
