"""Tests for analyst collaboration: presence, handoff, workload, and SSE."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.analyst import Analyst
from app.models.base import Base
from app.models.gap_event import AISGapEvent
from app.models.handoff_note import HandoffNote
from app.modules import analyst_presence
from app.schemas.collaboration import (
    HandoffRequest,
    HandoffResponse,
    PresenceInfo,
    WorkloadSummary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_presence():
    """Reset presence state before each test."""
    analyst_presence.reset()
    yield
    analyst_presence.reset()


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()


@pytest.fixture
def sample_analysts(db_session: Session):
    """Create sample analysts for testing."""
    a1 = Analyst(
        analyst_id=1,
        username="alice",
        display_name="Alice Smith",
        password_hash="hashed",
        role="analyst",
        is_active=True,
    )
    a2 = Analyst(
        analyst_id=2,
        username="bob",
        display_name="Bob Jones",
        password_hash="hashed",
        role="senior_analyst",
        is_active=True,
    )
    a3 = Analyst(
        analyst_id=3,
        username="charlie",
        display_name=None,
        password_hash="hashed",
        role="analyst",
        is_active=False,
    )
    db_session.add_all([a1, a2, a3])
    db_session.commit()
    return [a1, a2, a3]


@pytest.fixture
def sample_alerts(db_session: Session):
    """Create sample alerts for testing."""
    alerts = []
    for i in range(5):
        alert = AISGapEvent(
            gap_event_id=i + 1,
            vessel_id=100 + i,
            gap_start_utc=datetime(2026, 1, 1, tzinfo=UTC),
            gap_end_utc=datetime(2026, 1, 1, 12, tzinfo=UTC),
            duration_minutes=720,
            risk_score=70 + i * 5,
            status="new" if i < 3 else "dismissed",
            assigned_to=1 if i < 2 else (2 if i == 2 else None),
        )
        alerts.append(alert)
    db_session.add_all(alerts)
    db_session.commit()
    return alerts


# ---------------------------------------------------------------------------
# Presence: heartbeat and TTL
# ---------------------------------------------------------------------------


class TestPresenceHeartbeat:
    def test_heartbeat_registers_analyst(self):
        analyst_presence.heartbeat(1, alert_id=42)
        online = analyst_presence.get_online_analysts()
        assert len(online) == 1
        assert online[0]["analyst_id"] == 1
        assert online[0]["current_alert_id"] == 42

    def test_heartbeat_without_alert_id(self):
        analyst_presence.heartbeat(1)
        online = analyst_presence.get_online_analysts()
        assert len(online) == 1
        assert online[0]["current_alert_id"] is None

    def test_heartbeat_updates_existing(self):
        analyst_presence.heartbeat(1, alert_id=10)
        analyst_presence.heartbeat(1, alert_id=20)
        online = analyst_presence.get_online_analysts()
        assert len(online) == 1
        assert online[0]["current_alert_id"] == 20

    def test_ttl_expiry(self):
        analyst_presence.heartbeat(1, alert_id=10)
        # Manually set last_seen to be past TTL
        analyst_presence._presence[1].last_seen = time.time() - 60
        online = analyst_presence.get_online_analysts()
        assert len(online) == 0

    def test_multiple_analysts_online(self):
        analyst_presence.heartbeat(1, alert_id=10)
        analyst_presence.heartbeat(2, alert_id=20)
        analyst_presence.heartbeat(3)
        online = analyst_presence.get_online_analysts()
        assert len(online) == 3


# ---------------------------------------------------------------------------
# Presence: alert viewers
# ---------------------------------------------------------------------------


class TestAlertViewers:
    def test_get_viewers_for_alert(self):
        analyst_presence.heartbeat(1, alert_id=42)
        analyst_presence.heartbeat(2, alert_id=42)
        analyst_presence.heartbeat(3, alert_id=99)
        viewers = analyst_presence.get_alert_viewers(42)
        assert sorted(viewers) == [1, 2]

    def test_no_viewers(self):
        analyst_presence.heartbeat(1, alert_id=42)
        viewers = analyst_presence.get_alert_viewers(99)
        assert viewers == []

    def test_expired_viewer_excluded(self):
        analyst_presence.heartbeat(1, alert_id=42)
        analyst_presence._presence[1].last_seen = time.time() - 60
        viewers = analyst_presence.get_alert_viewers(42)
        assert viewers == []


# ---------------------------------------------------------------------------
# Presence: snapshot
# ---------------------------------------------------------------------------


class TestPresenceSnapshot:
    def test_snapshot_includes_offline_status(self):
        analyst_presence.heartbeat(1, alert_id=10)
        analyst_presence.heartbeat(2, alert_id=20)
        # Make analyst 2 expired
        analyst_presence._presence[2].last_seen = time.time() - 60
        snapshot = analyst_presence.get_presence_snapshot()
        assert len(snapshot) == 2
        by_id = {s["analyst_id"]: s for s in snapshot}
        assert by_id[1]["is_online"] is True
        assert by_id[2]["is_online"] is False


# ---------------------------------------------------------------------------
# Workload suggestion
# ---------------------------------------------------------------------------


class TestWorkloadSuggestion:
    def test_suggest_least_loaded(self, db_session, sample_analysts, sample_alerts):
        # Alice has 2 open alerts (ids 1, 2), Bob has 1 (id 3)
        result = analyst_presence.suggest_assignment(db_session)
        assert result == 2  # Bob has fewer

    def test_suggest_excludes_ids(self, db_session, sample_analysts, sample_alerts):
        result = analyst_presence.suggest_assignment(db_session, exclude_ids=[2])
        assert result == 1  # Alice is only active option

    def test_suggest_no_active_analysts(self, db_session):
        result = analyst_presence.suggest_assignment(db_session)
        assert result is None

    def test_suggest_all_excluded(self, db_session, sample_analysts, sample_alerts):
        result = analyst_presence.suggest_assignment(db_session, exclude_ids=[1, 2])
        assert result is None  # Charlie is inactive

    def test_suggest_skips_inactive(self, db_session, sample_analysts, sample_alerts):
        # Charlie (id=3) is inactive, should never be suggested
        result = analyst_presence.suggest_assignment(db_session)
        assert result != 3


# ---------------------------------------------------------------------------
# Handoff model
# ---------------------------------------------------------------------------


class TestHandoffModel:
    def test_create_handoff(self, db_session, sample_analysts, sample_alerts):
        handoff = HandoffNote(
            alert_id=1,
            from_analyst_id=1,
            to_analyst_id=2,
            notes="Needs satellite verification",
        )
        db_session.add(handoff)
        db_session.commit()

        saved = db_session.query(HandoffNote).first()
        assert saved.handoff_id is not None
        assert saved.alert_id == 1
        assert saved.from_analyst_id == 1
        assert saved.to_analyst_id == 2
        assert saved.notes == "Needs satellite verification"
        assert saved.created_at is not None

    def test_handoff_relationships(self, db_session, sample_analysts, sample_alerts):
        handoff = HandoffNote(
            alert_id=1,
            from_analyst_id=1,
            to_analyst_id=2,
            notes="Handoff test",
        )
        db_session.add(handoff)
        db_session.commit()
        db_session.refresh(handoff)

        assert handoff.from_analyst.username == "alice"
        assert handoff.to_analyst.username == "bob"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_handoff_request_validation(self):
        req = HandoffRequest(to_analyst_id=2, notes="Some notes")
        assert req.to_analyst_id == 2
        assert req.notes == "Some notes"

    def test_handoff_request_default_notes(self):
        req = HandoffRequest(to_analyst_id=2)
        assert req.notes == ""

    def test_handoff_response(self):
        resp = HandoffResponse(
            handoff_id=1,
            from_analyst="Alice",
            to_analyst="Bob",
            notes="test",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert resp.handoff_id == 1

    def test_presence_info(self):
        info = PresenceInfo(
            analyst_id=1,
            analyst_name="Alice",
            is_online=True,
            current_alert_id=42,
            last_seen=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert info.is_online is True

    def test_workload_summary(self):
        summary = WorkloadSummary(
            analyst_id=1,
            analyst_name="Alice",
            open_alerts=5,
            assigned_alerts=10,
            avg_resolution_hours=2.5,
        )
        assert summary.open_alerts == 5


# ---------------------------------------------------------------------------
# Cleanup / garbage collection
# ---------------------------------------------------------------------------


class TestPresenceCleanup:
    def test_stale_entries_garbage_collected(self):
        analyst_presence.heartbeat(1)
        # Set last_seen well beyond 5x TTL
        analyst_presence._presence[1].last_seen = time.time() - 300
        analyst_presence._cleanup_stale()
        assert 1 not in analyst_presence._presence

    def test_recent_entries_preserved(self):
        analyst_presence.heartbeat(1)
        analyst_presence._cleanup_stale()
        assert 1 in analyst_presence._presence


# ---------------------------------------------------------------------------
# Multi-worker warning
# ---------------------------------------------------------------------------


class TestMultiWorkerWarning:
    def test_warns_on_multi_worker(self, caplog):
        analyst_presence._startup_warned = False
        with patch.dict("os.environ", {"WEB_CONCURRENCY": "4"}):
            analyst_presence._warn_if_multiworker()
        assert analyst_presence._startup_warned is True

    def test_no_warn_single_worker(self, caplog):
        analyst_presence._startup_warned = False
        with patch.dict("os.environ", {}, clear=True):
            analyst_presence._warn_if_multiworker()
        # Still marked as warned (only checks once)
        assert analyst_presence._startup_warned is True
