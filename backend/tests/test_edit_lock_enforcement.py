"""Tests for edit lock enforcement on alert mutation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api._helpers import check_edit_lock
from app.config import settings
from app.models.alert_edit_lock import AlertEditLock
from app.models.analyst import Analyst
from app.models.base import Base
from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory SQLite session with required tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _make_analyst(db: Session, analyst_id: int | None = None, username: str = "analyst1") -> Analyst:
    a = Analyst(
        username=username,
        password_hash="fakehash",
        role="analyst",
    )
    db.add(a)
    db.flush()
    return a


def _make_vessel(db: Session) -> Vessel:
    v = Vessel(mmsi="123456789", name="Test Vessel")
    db.add(v)
    db.flush()
    return v


def _make_alert(db: Session, vessel_id: int) -> AISGapEvent:
    now = datetime.now(UTC)
    alert = AISGapEvent(
        vessel_id=vessel_id,
        gap_start_utc=now - timedelta(hours=6),
        gap_end_utc=now - timedelta(hours=4),
        duration_minutes=120,
        risk_score=50,
    )
    db.add(alert)
    db.flush()
    return alert


def _make_lock(
    db: Session,
    alert_id: int,
    analyst_id: int,
    expired: bool = False,
) -> AlertEditLock:
    now = datetime.now(UTC)
    if expired:
        expires = now - timedelta(seconds=60)
    else:
        expires = now + timedelta(seconds=settings.EDIT_LOCK_TTL_SECONDS)
    lock = AlertEditLock(
        alert_id=alert_id,
        analyst_id=analyst_id,
        acquired_at=now - timedelta(seconds=10),
        expires_at=expires,
    )
    db.add(lock)
    db.flush()
    return lock


# ---------------------------------------------------------------------------
# Unit tests for check_edit_lock()
# ---------------------------------------------------------------------------


class TestCheckEditLockUnit:
    def test_check_edit_lock_disabled(self, db):
        """ENFORCE_EDIT_LOCKS=False skips all DB queries."""
        with patch.object(settings, "ENFORCE_EDIT_LOCKS", False):
            # Should not raise, even with bogus IDs
            check_edit_lock(db, alert_id=999, analyst_id=1)

    def test_check_edit_lock_no_lock(self, db):
        """No lock exists — no error raised."""
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        analyst = _make_analyst(db)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            check_edit_lock(db, alert.gap_event_id, analyst.analyst_id)

    def test_check_edit_lock_own_lock(self, db):
        """Analyst's own lock does not raise."""
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        analyst = _make_analyst(db)
        _make_lock(db, alert.gap_event_id, analyst.analyst_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            check_edit_lock(db, alert.gap_event_id, analyst.analyst_id)

    def test_check_edit_lock_other_analyst(self, db):
        """Another analyst's active lock raises 409."""
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        analyst1 = _make_analyst(db, username="analyst1")
        analyst2 = _make_analyst(db, username="analyst2")
        _make_lock(db, alert.gap_event_id, analyst1.analyst_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            with pytest.raises(HTTPException) as exc_info:
                check_edit_lock(db, alert.gap_event_id, analyst2.analyst_id)
            assert exc_info.value.status_code == 409
            detail = exc_info.value.detail
            assert detail["error"] == "resource_locked"
            assert detail["locked_by_analyst_id"] == analyst1.analyst_id

    def test_check_edit_lock_expired_cleanup(self, db):
        """Expired lock gets cleaned up and does not block."""
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        analyst1 = _make_analyst(db, username="analyst1")
        analyst2 = _make_analyst(db, username="analyst2")
        _make_lock(db, alert.gap_event_id, analyst1.analyst_id, expired=True)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            # Should not raise — the lock is expired and will be cleaned
            check_edit_lock(db, alert.gap_event_id, analyst2.analyst_id)

        # Verify the expired lock was deleted
        remaining = db.query(AlertEditLock).filter(
            AlertEditLock.alert_id == alert.gap_event_id
        ).count()
        assert remaining == 0


# ---------------------------------------------------------------------------
# Integration tests via TestClient
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient

from app.auth import require_auth
from app.database import get_db
from app.main import app


@pytest.fixture()
def client(db):
    """TestClient with overridden DB and auth dependencies."""
    analyst = _make_analyst(db, username="testuser")
    db.commit()

    def _override_db():
        yield db

    def _override_auth():
        return {"analyst_id": analyst.analyst_id, "username": "testuser", "role": "admin"}

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[require_auth] = _override_auth

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, analyst

    app.dependency_overrides.clear()


@pytest.fixture()
def second_analyst(db):
    """Create a second analyst for lock conflict tests."""
    a = _make_analyst(db, username="other_analyst")
    db.commit()
    return a


class TestEndpointLockEnforcement:
    def test_update_status_checks_lock(self, db, client, second_analyst):
        """update_alert_status returns 409 when alert is locked by another analyst."""
        c, analyst = client
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        _make_lock(db, alert.gap_event_id, second_analyst.analyst_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            resp = c.post(
                f"/api/v1/alerts/{alert.gap_event_id}/status",
                json={"status": "under_review"},
            )
        assert resp.status_code == 409

    def test_verdict_checks_lock(self, db, client, second_analyst):
        """submit_alert_verdict returns 409 when alert is locked by another analyst."""
        c, analyst = client
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        _make_lock(db, alert.gap_event_id, second_analyst.analyst_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            resp = c.post(
                f"/api/v1/alerts/{alert.gap_event_id}/verdict",
                json={"verdict": "confirmed_tp"},
            )
        assert resp.status_code == 409

    def test_add_note_checks_lock(self, db, client, second_analyst):
        """add_note returns 409 when alert is locked by another analyst."""
        c, analyst = client
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        _make_lock(db, alert.gap_event_id, second_analyst.analyst_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            resp = c.post(
                f"/api/v1/alerts/{alert.gap_event_id}/notes",
                json={"notes": "test note"},
            )
        assert resp.status_code == 409

    def test_update_status_auto_releases_lock(self, db, client):
        """Lock is released after successful status update."""
        c, analyst = client
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        _make_lock(db, alert.gap_event_id, analyst.analyst_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            resp = c.post(
                f"/api/v1/alerts/{alert.gap_event_id}/status",
                json={"status": "under_review"},
            )
        assert resp.status_code == 200

        # Verify lock was released
        remaining = db.query(AlertEditLock).filter(
            AlertEditLock.alert_id == alert.gap_event_id,
            AlertEditLock.analyst_id == analyst.analyst_id,
        ).count()
        assert remaining == 0

    def test_verdict_auto_releases_lock(self, db, client):
        """Lock is released after successful verdict submission."""
        c, analyst = client
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        _make_lock(db, alert.gap_event_id, analyst.analyst_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            resp = c.post(
                f"/api/v1/alerts/{alert.gap_event_id}/verdict",
                json={"verdict": "confirmed_fp"},
            )
        assert resp.status_code == 200

        # Verify lock was released
        remaining = db.query(AlertEditLock).filter(
            AlertEditLock.alert_id == alert.gap_event_id,
            AlertEditLock.analyst_id == analyst.analyst_id,
        ).count()
        assert remaining == 0

    def test_acquire_lock_toctou_atomic(self, db, client, second_analyst):
        """acquire_lock uses atomic insert — conflict returns 409 with structured detail."""
        c, analyst = client
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        # Second analyst already holds the lock
        _make_lock(db, alert.gap_event_id, second_analyst.analyst_id)
        db.commit()

        resp = c.post(f"/api/v1/alerts/{alert.gap_event_id}/lock")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "resource_locked"
        assert detail["locked_by_analyst_id"] == second_analyst.analyst_id

    def test_acquire_lock_extend_own(self, db, client):
        """Re-acquiring own lock extends expiry rather than failing."""
        c, analyst = client
        v = _make_vessel(db)
        alert = _make_alert(db, v.vessel_id)
        old_lock = _make_lock(db, alert.gap_event_id, analyst.analyst_id)
        old_expires = old_lock.expires_at
        db.commit()

        resp = c.post(f"/api/v1/alerts/{alert.gap_event_id}/lock")
        assert resp.status_code == 200
        data = resp.json()
        assert data["analyst_id"] == analyst.analyst_id
        # The new expiry should be >= the old one (extended)
        new_expires = datetime.fromisoformat(data["expires_at"])
        # Normalize to naive for comparison (SQLite stores naive datetimes)
        if new_expires.tzinfo is not None:
            new_expires = new_expires.replace(tzinfo=None)
        old_expires_naive = old_expires.replace(tzinfo=None) if old_expires.tzinfo else old_expires
        assert new_expires >= old_expires_naive

    def test_bulk_status_checks_locks(self, db, client, second_analyst):
        """Bulk update returns 409 with locked alert IDs."""
        c, analyst = client
        v = _make_vessel(db)
        alert1 = _make_alert(db, v.vessel_id)
        alert2 = _make_alert(db, v.vessel_id)
        # Lock alert1 by another analyst
        _make_lock(db, alert1.gap_event_id, second_analyst.analyst_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            resp = c.post(
                "/api/v1/alerts/bulk-status",
                json={
                    "alert_ids": [alert1.gap_event_id, alert2.gap_event_id],
                    "status": "under_review",
                },
            )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "resource_locked"
        assert alert1.gap_event_id in detail["locked_alert_ids"]

    def test_bulk_status_no_lock_proceeds(self, db, client):
        """Bulk update works when no locks are held by others."""
        c, analyst = client
        v = _make_vessel(db)
        alert1 = _make_alert(db, v.vessel_id)
        alert2 = _make_alert(db, v.vessel_id)
        db.commit()

        with patch.object(settings, "ENFORCE_EDIT_LOCKS", True):
            resp = c.post(
                "/api/v1/alerts/bulk-status",
                json={
                    "alert_ids": [alert1.gap_event_id, alert2.gap_event_id],
                    "status": "under_review",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
