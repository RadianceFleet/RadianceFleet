"""Tests for IngestionStatus persistence (task 5D)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.ingestion_status import IngestionStatus, update_ingestion_status


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class TestIngestionStatusModel:
    def test_create_on_first_ingestion(self, db: Session):
        """Status row created on first call for a source."""
        row = update_ingestion_status(db, "digitraffic", records=42)
        db.commit()

        assert row.source == "digitraffic"
        assert row.records_ingested == 42
        assert row.status == "completed"
        assert row.last_success_utc is not None
        assert row.last_error_message is None
        assert row.errors == 0

    def test_update_on_subsequent_runs(self, db: Session):
        """Existing row updated on subsequent calls."""
        update_ingestion_status(db, "kystverket", records=10)
        db.commit()

        update_ingestion_status(db, "kystverket", records=25)
        db.commit()

        rows = db.query(IngestionStatus).filter(
            IngestionStatus.source == "kystverket"
        ).all()
        assert len(rows) == 1
        assert rows[0].records_ingested == 25

    def test_error_tracking(self, db: Session):
        """Error message and counter updated on failure."""
        update_ingestion_status(db, "aisstream", records=100)
        db.commit()

        update_ingestion_status(db, "aisstream", error="Connection timeout")
        db.commit()

        row = db.query(IngestionStatus).filter(
            IngestionStatus.source == "aisstream"
        ).one()
        assert row.status == "error"
        assert row.errors == 1
        assert row.last_error_message == "Connection timeout"
        # last_success_utc should still reflect the previous successful run
        assert row.last_success_utc is not None

    def test_error_counter_increments(self, db: Session):
        """Error counter increments across multiple failures."""
        update_ingestion_status(db, "dma", error="fail 1")
        db.commit()
        update_ingestion_status(db, "dma", error="fail 2")
        db.commit()

        row = db.query(IngestionStatus).filter(
            IngestionStatus.source == "dma"
        ).one()
        assert row.errors == 2
        assert row.last_error_message == "fail 2"

    def test_success_clears_error_message(self, db: Session):
        """Successful run clears last_error_message."""
        update_ingestion_status(db, "barentswatch", error="timeout")
        db.commit()

        update_ingestion_status(db, "barentswatch", records=50)
        db.commit()

        row = db.query(IngestionStatus).filter(
            IngestionStatus.source == "barentswatch"
        ).one()
        assert row.status == "completed"
        assert row.last_error_message is None

    def test_multiple_sources_independent(self, db: Session):
        """Different sources tracked independently."""
        update_ingestion_status(db, "source_a", records=10)
        update_ingestion_status(db, "source_b", records=20)
        db.commit()

        rows = db.query(IngestionStatus).order_by(IngestionStatus.source).all()
        assert len(rows) == 2
        assert rows[0].source == "source_a"
        assert rows[0].records_ingested == 10
        assert rows[1].source == "source_b"
        assert rows[1].records_ingested == 20

    def test_explicit_status_override(self, db: Session):
        """Explicit status kwarg overrides derived status."""
        row = update_ingestion_status(db, "test", records=5, status="running")
        db.commit()
        assert row.status == "running"


class TestIngestionStatusAPI:
    """Test the /ingestion-status endpoint."""

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_ingestion_status_empty(self, client):
        """Returns idle status when no ingestion has occurred."""
        resp = client.get("/api/v1/ingestion-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("idle", "ok")

    def test_admin_ingestion_status_endpoint(self, client):
        """Admin alias endpoint works."""
        resp = client.get("/api/v1/admin/ingestion-status")
        assert resp.status_code == 200
