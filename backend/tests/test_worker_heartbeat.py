"""Tests for worker heartbeat model, upsert helper, and /health/workers endpoint."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.models.worker_heartbeat import WorkerHeartbeat, upsert_heartbeat

# ── Unit tests for upsert_heartbeat ─────────────────────────────────────────


class TestUpsertHeartbeat:
    """Test the dialect-aware upsert helper with a real SQLite DB."""

    @pytest.fixture
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.models.base import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
        session.close()

    def test_insert_new_heartbeat(self, db):
        upsert_heartbeat(db, "ws-worker", status="starting")
        db.commit()

        row = db.query(WorkerHeartbeat).filter_by(worker_id="ws-worker").one()
        assert row.status == "starting"
        assert row.started_at_utc is not None
        assert row.records_processed == 0
        assert row.error_message is None

    def test_upsert_updates_existing(self, db):
        upsert_heartbeat(db, "ws-worker", status="starting")
        db.commit()

        upsert_heartbeat(db, "ws-worker", status="running", records=42)
        db.commit()

        row = db.query(WorkerHeartbeat).filter_by(worker_id="ws-worker").one()
        assert row.status == "running"
        assert row.records_processed == 42
        # started_at should be preserved from the first insert
        assert row.started_at_utc is not None

    def test_upsert_with_error(self, db):
        upsert_heartbeat(db, "cron-updater", status="error", error="connection refused")
        db.commit()

        row = db.query(WorkerHeartbeat).filter_by(worker_id="cron-updater").one()
        assert row.status == "error"
        assert row.error_message == "connection refused"

    def test_upsert_with_metadata(self, db):
        import json

        upsert_heartbeat(
            db, "ws-worker",
            status="running",
            metadata={"msg_per_sec": 12.5, "vessels_seen": 300},
        )
        db.commit()

        row = db.query(WorkerHeartbeat).filter_by(worker_id="ws-worker").one()
        meta = json.loads(row.metadata_json)
        assert meta["msg_per_sec"] == 12.5
        assert meta["vessels_seen"] == 300

    def test_error_message_truncated(self, db):
        long_error = "x" * 3000
        upsert_heartbeat(db, "ws-worker", status="error", error=long_error)
        db.commit()

        row = db.query(WorkerHeartbeat).filter_by(worker_id="ws-worker").one()
        assert len(row.error_message) == 2000

    def test_multiple_workers(self, db):
        upsert_heartbeat(db, "ws-worker", status="running")
        upsert_heartbeat(db, "cron-updater", status="idle")
        db.commit()

        rows = db.query(WorkerHeartbeat).all()
        assert len(rows) == 2
        ids = {r.worker_id for r in rows}
        assert ids == {"ws-worker", "cron-updater"}


# ── API endpoint tests for /health/workers ──────────────────────────────────


class TestHealthWorkersEndpoint:
    """GET /api/v1/health/workers — worker heartbeat status."""

    def test_returns_200_empty(self, api_client, mock_db):
        mock_db.query.return_value.all.return_value = []
        mock_db.query.return_value.scalar.return_value = None

        resp = api_client.get("/api/v1/health/workers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_count"] == 0
        assert data["state"] == "unconfigured"
        assert data["all_healthy"] is False

    def test_healthy_ws_worker(self, api_client, mock_db):
        now = datetime.now(UTC)
        worker = MagicMock()
        worker.worker_id = "ws-worker"
        worker.status = "running"
        worker.last_heartbeat_utc = now - timedelta(seconds=10)
        worker.started_at_utc = now - timedelta(hours=1)
        worker.records_processed = 5000
        worker.error_message = None

        mock_db.query.return_value.all.return_value = [worker]
        mock_db.query.return_value.scalar.return_value = now - timedelta(seconds=30)

        resp = api_client.get("/api/v1/health/workers")
        data = resp.json()
        assert data["worker_count"] == 1
        assert data["all_healthy"] is True
        assert data["state"] == "healthy"
        assert data["data_flowing"] is True
        assert data["workers"]["ws-worker"]["stale"] is False

    def test_stale_ws_worker(self, api_client, mock_db):
        now = datetime.now(UTC)
        worker = MagicMock()
        worker.worker_id = "ws-worker"
        worker.status = "running"
        worker.last_heartbeat_utc = now - timedelta(seconds=300)
        worker.started_at_utc = now - timedelta(hours=1)
        worker.records_processed = 100
        worker.error_message = None

        mock_db.query.return_value.all.return_value = [worker]
        mock_db.query.return_value.scalar.return_value = None

        resp = api_client.get("/api/v1/health/workers")
        data = resp.json()
        assert data["all_healthy"] is False
        assert data["workers"]["ws-worker"]["stale"] is True

    def test_error_worker(self, api_client, mock_db):
        now = datetime.now(UTC)
        worker = MagicMock()
        worker.worker_id = "cron-updater"
        worker.status = "error"
        worker.last_heartbeat_utc = now - timedelta(seconds=5)
        worker.started_at_utc = now - timedelta(minutes=2)
        worker.records_processed = 0
        worker.error_message = "connection refused"

        mock_db.query.return_value.all.return_value = [worker]
        mock_db.query.return_value.scalar.return_value = None

        resp = api_client.get("/api/v1/health/workers")
        data = resp.json()
        assert data["all_healthy"] is False
        assert data["workers"]["cron-updater"]["error_message"] == "connection refused"

    def test_data_not_flowing(self, api_client, mock_db):
        now = datetime.now(UTC)
        worker = MagicMock()
        worker.worker_id = "ws-worker"
        worker.status = "running"
        worker.last_heartbeat_utc = now - timedelta(seconds=10)
        worker.started_at_utc = now - timedelta(hours=1)
        worker.records_processed = 100
        worker.error_message = None

        mock_db.query.return_value.all.return_value = [worker]
        # AIS data is 10 minutes old
        mock_db.query.return_value.scalar.return_value = now - timedelta(minutes=10)

        resp = api_client.get("/api/v1/health/workers")
        data = resp.json()
        assert data["data_flowing"] is False
