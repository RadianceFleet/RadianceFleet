"""Tests for the collaboration notification system."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base
from app.models.analyst import Analyst
from app.models.notification_event import NotificationEvent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(_db_engine):
    Session = sessionmaker(bind=_db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def analyst(db):
    a = Analyst(
        analyst_id=1,
        username="analyst1",
        display_name="Analyst One",
        role="analyst",
        password_hash="$2b$12$fakehash",
        is_active=True,
    )
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def analyst2(db):
    a = Analyst(
        analyst_id=2,
        username="analyst2",
        display_name="Analyst Two",
        role="analyst",
        password_hash="$2b$12$fakehash",
        is_active=True,
    )
    db.add(a)
    db.commit()
    return a


def _analyst_token(analyst_id=1, username="analyst1", role="analyst"):
    from app.auth import create_token

    with patch("app.auth.settings") as mock_settings:
        mock_settings.ADMIN_JWT_SECRET = "test-secret"
        return create_token(analyst_id, username, role)


@pytest.fixture
def real_client(_db_engine):
    """TestClient wired to an in-memory SQLite DB with JWT secret set."""
    from app.database import get_db
    from app.main import app

    Session = sessionmaker(bind=_db_engine)

    def override():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override
    with (
        patch("app.database.init_db"),
        patch("app.auth.settings") as mock_settings,
    ):
        mock_settings.ADMIN_JWT_SECRET = "test-secret"
        mock_settings.RADIANCEFLEET_API_KEY = None
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# emit_event
# ---------------------------------------------------------------------------


class TestEmitEvent:
    def test_creates_record(self, db, analyst):
        from app.modules.collaboration_notifier import emit_event

        emit_event(db, analyst.analyst_id, "assignment", {"alert_id": 42})
        db.commit()

        events = db.query(NotificationEvent).all()
        assert len(events) == 1
        assert events[0].event_type == "assignment"
        assert events[0].target_analyst_id == analyst.analyst_id
        payload = json.loads(events[0].payload_json)
        assert payload["alert_id"] == 42

    def test_creates_record_without_payload(self, db, analyst):
        from app.modules.collaboration_notifier import emit_event

        emit_event(db, analyst.analyst_id, "viewer_join")
        db.commit()

        events = db.query(NotificationEvent).all()
        assert len(events) == 1
        assert events[0].payload_json is None


# ---------------------------------------------------------------------------
# get_pending_events
# ---------------------------------------------------------------------------


class TestGetPendingEvents:
    def test_returns_recent(self, db, analyst):
        from app.modules.collaboration_notifier import emit_event, get_pending_events

        emit_event(db, analyst.analyst_id, "assignment", {"alert_id": 1})
        emit_event(db, analyst.analyst_id, "handoff", {"alert_id": 2})
        db.commit()

        events = get_pending_events(db, analyst.analyst_id)
        assert len(events) == 2
        assert events[0]["event_type"] in ("assignment", "handoff")

    def test_respects_analyst_id(self, db, analyst, analyst2):
        from app.modules.collaboration_notifier import emit_event, get_pending_events

        emit_event(db, analyst.analyst_id, "assignment", {"alert_id": 1})
        emit_event(db, analyst2.analyst_id, "handoff", {"alert_id": 2})
        db.commit()

        events = get_pending_events(db, analyst.analyst_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "assignment"

    def test_respects_since(self, db, analyst):
        from app.modules.collaboration_notifier import emit_event, get_pending_events

        emit_event(db, analyst.analyst_id, "assignment", {"alert_id": 1})
        db.commit()

        # Query with a future timestamp — should return empty
        future = datetime.now(UTC) + timedelta(hours=1)
        events = get_pending_events(db, analyst.analyst_id, since=future)
        assert len(events) == 0


# ---------------------------------------------------------------------------
# mark_read / mark_all_read
# ---------------------------------------------------------------------------


class TestMarkRead:
    def test_updates_is_read(self, db, analyst):
        from app.modules.collaboration_notifier import emit_event, mark_read

        emit_event(db, analyst.analyst_id, "assignment")
        db.commit()

        event = db.query(NotificationEvent).first()
        assert event.is_read is False

        result = mark_read(db, event.event_id, analyst.analyst_id)
        db.commit()
        assert result is True
        db.refresh(event)
        assert event.is_read is True

    def test_returns_false_for_wrong_analyst(self, db, analyst, analyst2):
        from app.modules.collaboration_notifier import emit_event, mark_read

        emit_event(db, analyst.analyst_id, "assignment")
        db.commit()

        event = db.query(NotificationEvent).first()
        result = mark_read(db, event.event_id, analyst2.analyst_id)
        assert result is False

    def test_mark_all_read(self, db, analyst):
        from app.modules.collaboration_notifier import emit_event, mark_all_read

        emit_event(db, analyst.analyst_id, "assignment")
        emit_event(db, analyst.analyst_id, "handoff")
        db.commit()

        count = mark_all_read(db, analyst.analyst_id)
        db.commit()
        assert count == 2

        unread = (
            db.query(NotificationEvent)
            .filter(NotificationEvent.is_read.is_(False))
            .count()
        )
        assert unread == 0


# ---------------------------------------------------------------------------
# cleanup_old_events
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_deletes_old_records(self, db, analyst):
        from app.modules.collaboration_notifier import cleanup_old_events, emit_event

        emit_event(db, analyst.analyst_id, "assignment")
        db.commit()

        # Manually backdate the event
        event = db.query(NotificationEvent).first()
        event.created_at = datetime.now(UTC) - timedelta(hours=48)
        db.commit()

        count = cleanup_old_events(db, max_age_hours=24)
        assert count == 1
        assert db.query(NotificationEvent).count() == 0


# ---------------------------------------------------------------------------
# Convenience emitters
# ---------------------------------------------------------------------------


class TestConvenienceEmitters:
    def test_emit_assignment(self, db, analyst):
        from app.modules.collaboration_notifier import emit_assignment

        emit_assignment(db, analyst.analyst_id, alert_id=10, assigned_by="admin")
        db.commit()

        event = db.query(NotificationEvent).first()
        assert event.event_type == "assignment"
        payload = json.loads(event.payload_json)
        assert payload["alert_id"] == 10
        assert payload["assigned_by"] == "admin"

    def test_emit_handoff(self, db, analyst):
        from app.modules.collaboration_notifier import emit_handoff

        emit_handoff(db, analyst.analyst_id, alert_id=5, from_analyst="alice", notes="urgent")
        db.commit()

        event = db.query(NotificationEvent).first()
        assert event.event_type == "handoff"
        payload = json.loads(event.payload_json)
        assert payload["alert_id"] == 5
        assert payload["from_analyst"] == "alice"
        assert payload["notes"] == "urgent"

    def test_emit_case_update(self, db, analyst):
        from app.modules.collaboration_notifier import emit_case_update

        emit_case_update(db, analyst.analyst_id, case_id=7, action="resolved")
        db.commit()

        event = db.query(NotificationEvent).first()
        assert event.event_type == "case_update"
        payload = json.loads(event.payload_json)
        assert payload["case_id"] == 7
        assert payload["action"] == "resolved"


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestNotificationAPI:
    def test_get_notifications(self, _db_engine, real_client):
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=_db_engine)
        db = Session()
        a = Analyst(
            analyst_id=1,
            username="analyst1",
            display_name="Analyst One",
            role="analyst",
            password_hash="$2b$12$fakehash",
            is_active=True,
        )
        db.add(a)
        db.commit()

        ne = NotificationEvent(
            target_analyst_id=1,
            event_type="assignment",
            payload_json='{"alert_id": 1}',
        )
        db.add(ne)
        db.commit()
        db.close()

        token = _analyst_token()
        resp = real_client.get(
            "/api/v1/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "notifications" in data
        assert data["unread_count"] == 1
        assert len(data["notifications"]) == 1

    def test_mark_read_endpoint(self, _db_engine, real_client):
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=_db_engine)
        db = Session()
        a = Analyst(
            analyst_id=1,
            username="analyst1",
            display_name="Analyst One",
            role="analyst",
            password_hash="$2b$12$fakehash",
            is_active=True,
        )
        db.add(a)
        ne = NotificationEvent(
            target_analyst_id=1,
            event_type="handoff",
            payload_json='{"alert_id": 2}',
        )
        db.add(ne)
        db.commit()
        event_id = ne.event_id
        db.close()

        token = _analyst_token()
        resp = real_client.post(
            f"/api/v1/notifications/{event_id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_mark_read_404_wrong_analyst(self, _db_engine, real_client):
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=_db_engine)
        db = Session()
        # Create analyst 1 (the owner) and analyst 2 (the requester)
        for aid, uname in [(1, "analyst1"), (2, "analyst2")]:
            a = Analyst(
                analyst_id=aid,
                username=uname,
                display_name=uname,
                role="analyst",
                password_hash="$2b$12$fakehash",
                is_active=True,
            )
            db.add(a)
        ne = NotificationEvent(
            target_analyst_id=1,
            event_type="assignment",
        )
        db.add(ne)
        db.commit()
        event_id = ne.event_id
        db.close()

        # Request as analyst 2 — should 404
        token = _analyst_token(analyst_id=2, username="analyst2")
        resp = real_client.post(
            f"/api/v1/notifications/{event_id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    def test_mark_all_read_endpoint(self, _db_engine, real_client):
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=_db_engine)
        db = Session()
        a = Analyst(
            analyst_id=1,
            username="analyst1",
            display_name="Analyst One",
            role="analyst",
            password_hash="$2b$12$fakehash",
            is_active=True,
        )
        db.add(a)
        for i in range(3):
            db.add(
                NotificationEvent(
                    target_analyst_id=1,
                    event_type="assignment",
                    payload_json=f'{{"alert_id": {i}}}',
                )
            )
        db.commit()
        db.close()

        token = _analyst_token()
        resp = real_client.post(
            "/api/v1/notifications/read-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["marked_read"] == 3


# ---------------------------------------------------------------------------
# Unified SSE endpoint
# ---------------------------------------------------------------------------


class TestUnifiedSSE:
    def test_sse_endpoint_registered(self, real_client, _db_engine):
        """Verify the unified SSE endpoint exists and requires auth."""
        resp = real_client.get("/api/v1/sse/events?min_score=0")
        # Without auth, should get 401 (not 404)
        assert resp.status_code == 401

    def test_sse_max_connections(self):
        """Verify the connection counter starts at 0."""
        from app.api.routes_sse_unified import _active_unified_connections

        assert _active_unified_connections == 0
