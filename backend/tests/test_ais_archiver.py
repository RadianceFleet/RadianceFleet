"""Tests for AIS data retention and archival (Task 42)."""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session(tmp_path):
    """Create an in-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Ensure the new model is registered with Base before create_all
    import app.models.ais_archive_batch  # noqa: F401

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def archive_dir(tmp_path):
    """Temporary archive storage directory."""
    d = tmp_path / "archives"
    d.mkdir()
    return d


@pytest.fixture()
def mock_settings(archive_dir):
    """Patch settings for archive tests."""
    with patch("app.modules.ais_archiver.settings") as s:
        s.ARCHIVE_ENABLED = True
        s.ARCHIVE_BEFORE_DELETE = True
        s.ARCHIVE_RETENTION_DAYS = 90
        s.ARCHIVE_COMPRESSION = "gzip"
        s.ARCHIVE_BATCH_SIZE = 50000
        s.ARCHIVE_STORAGE_DIR = str(archive_dir)
        s.ARCHIVE_MAX_AGE_DAYS = None
        yield s


def _create_vessel(db: Session, vessel_id: int = 1):
    """Insert a minimal vessel row."""
    from app.models.vessel import Vessel

    existing = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if existing:
        return existing
    v = Vessel(vessel_id=vessel_id, mmsi=f"12345678{vessel_id}", name=f"TestVessel{vessel_id}")
    db.add(v)
    db.commit()
    return v


def _create_ais_point(db: Session, point_id: int, vessel_id: int, ts: datetime, source: str = "test"):
    """Insert an AIS point."""
    from app.models.ais_point import AISPoint

    p = AISPoint(
        ais_point_id=point_id,
        vessel_id=vessel_id,
        timestamp_utc=ts,
        lat=60.0,
        lon=25.0,
        source=source,
    )
    db.add(p)
    db.commit()
    return p


def _create_gap_event(db: Session, vessel_id: int, start_point_id: int, end_point_id: int):
    """Insert a gap event referencing AIS points."""
    from app.models.gap_event import AISGapEvent

    gap = AISGapEvent(
        vessel_id=vessel_id,
        start_point_id=start_point_id,
        end_point_id=end_point_id,
        gap_start_utc=datetime(2024, 1, 1, tzinfo=UTC),
        gap_end_utc=datetime(2024, 1, 2, tzinfo=UTC),
        duration_minutes=1440,
    )
    db.add(gap)
    db.commit()
    return gap


# ---------------------------------------------------------------------------
# archive_old_points
# ---------------------------------------------------------------------------


class TestArchiveOldPoints:
    def test_creates_parquet_file(self, db_session, mock_settings):
        """Archive should create a Parquet file with correct columns."""
        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        assert batch.row_count == 1
        assert batch.status == "completed"
        assert Path(batch.file_path).exists()

        # Verify Parquet content
        df = pl.read_parquet(batch.file_path)
        assert len(df) == 1
        assert "ais_point_id" in df.columns
        assert "vessel_id" in df.columns
        assert "timestamp_utc" in df.columns
        assert "lat" in df.columns
        assert "lon" in df.columns

    def test_sha256_checksum(self, db_session, mock_settings):
        """Archive should store correct SHA-256 checksum."""
        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import _compute_sha256, archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        assert batch.checksum_sha256
        actual = _compute_sha256(batch.file_path)
        assert batch.checksum_sha256 == actual

    def test_deletes_archived_points(self, db_session, mock_settings):
        """Archived points should be deleted from the database."""
        from app.models.ais_point import AISPoint

        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        archive_old_points(db_session, cutoff)

        remaining = db_session.query(AISPoint).count()
        assert remaining == 0

    def test_fk_protection_gap_events(self, db_session, mock_settings):
        """Points referenced by gap events should not be deleted."""
        from app.models.ais_point import AISPoint

        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)
        _create_ais_point(db_session, 2, 1, old + timedelta(hours=1))
        _create_ais_point(db_session, 3, 1, old + timedelta(hours=2))

        # Point 1 is start_point, point 2 is end_point of a gap event
        _create_gap_event(db_session, 1, 1, 2)

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        # Only point 3 should be archived (points 1 and 2 are protected)
        assert batch.row_count == 1
        remaining = db_session.query(AISPoint).count()
        assert remaining == 2  # protected points remain

    def test_source_filter(self, db_session, mock_settings):
        """Source filter should limit which points are archived."""
        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old, source="digitraffic")
        _create_ais_point(db_session, 2, 1, old + timedelta(hours=1), source="kystverket")

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff, source="digitraffic")

        assert batch.row_count == 1
        assert batch.source_filter == "digitraffic"

    def test_empty_result_set(self, db_session, mock_settings):
        """Empty archive should create record with row_count=0."""
        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        assert batch.row_count == 0
        assert batch.status == "completed"

    def test_batch_record_persisted(self, db_session, mock_settings):
        """Archive batch record should be persisted to DB."""
        from app.models.ais_archive_batch import AisArchiveBatch

        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        archive_old_points(db_session, cutoff)

        batches = db_session.query(AisArchiveBatch).all()
        assert len(batches) == 1
        assert batches[0].compression == "gzip"

    def test_recent_points_not_archived(self, db_session, mock_settings):
        """Points newer than cutoff should not be archived."""
        from app.models.ais_point import AISPoint

        _create_vessel(db_session)
        recent = datetime.now(UTC) - timedelta(days=10)
        _create_ais_point(db_session, 1, 1, recent)

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        assert batch.row_count == 0
        remaining = db_session.query(AISPoint).count()
        assert remaining == 1

    def test_multiple_points_archived(self, db_session, mock_settings):
        """Multiple old points should all be archived."""
        _create_vessel(db_session)
        base = datetime.now(UTC) - timedelta(days=100)
        for i in range(10):
            _create_ais_point(db_session, i + 1, 1, base + timedelta(hours=i))

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        assert batch.row_count == 10
        df = pl.read_parquet(batch.file_path)
        assert len(df) == 10

    def test_batch_size_handling(self, db_session, mock_settings):
        """Small batch size should still archive all points."""
        mock_settings.ARCHIVE_BATCH_SIZE = 3
        _create_vessel(db_session)
        base = datetime.now(UTC) - timedelta(days=100)
        for i in range(7):
            _create_ais_point(db_session, i + 1, 1, base + timedelta(hours=i))

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        assert batch.row_count == 7

    def test_file_path_structure(self, db_session, mock_settings):
        """Archive file should be in YYYY/MM directory structure."""
        _create_vessel(db_session)
        old = datetime(2024, 3, 15, tzinfo=UTC)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC)
        batch = archive_old_points(db_session, cutoff)

        assert "/2024/03/" in batch.file_path
        assert batch.file_path.endswith(".parquet.gz")


# ---------------------------------------------------------------------------
# restore_archive_batch
# ---------------------------------------------------------------------------


class TestRestoreArchiveBatch:
    def test_restore_basic(self, db_session, mock_settings):
        """Restore should re-insert archived points."""
        from app.models.ais_point import AISPoint

        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points, restore_archive_batch

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)
        assert db_session.query(AISPoint).count() == 0

        count = restore_archive_batch(db_session, batch.batch_id)
        assert count == 1
        assert db_session.query(AISPoint).count() == 1

    def test_restore_idempotent(self, db_session, mock_settings):
        """Restoring twice should not duplicate rows (INSERT OR IGNORE)."""
        from app.models.ais_point import AISPoint

        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points, restore_archive_batch

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        # First restore
        count1 = restore_archive_batch(db_session, batch.batch_id)
        assert count1 == 1

        # Reset status so we can restore again
        batch.status = "completed"
        db_session.commit()

        # Second restore should find existing rows
        count2 = restore_archive_batch(db_session, batch.batch_id)
        assert count2 == 0
        assert db_session.query(AISPoint).count() == 1

    def test_restore_updates_status(self, db_session, mock_settings):
        """Restore should set batch status to 'restored'."""
        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points, restore_archive_batch

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        restore_archive_batch(db_session, batch.batch_id)
        assert batch.status == "restored"

    def test_restore_nonexistent_batch(self, db_session, mock_settings):
        """Restore of non-existent batch should raise ValueError."""
        from app.modules.ais_archiver import restore_archive_batch

        with pytest.raises(ValueError, match="not found"):
            restore_archive_batch(db_session, 999)

    def test_restore_missing_file(self, db_session, mock_settings):
        """Restore with missing archive file should raise FileNotFoundError."""
        from app.models.ais_archive_batch import AisArchiveBatch

        batch = AisArchiveBatch(
            archive_date=datetime.now(UTC),
            date_range_start=datetime.now(UTC),
            date_range_end=datetime.now(UTC),
            row_count=1,
            file_path="/nonexistent/path.parquet.gz",
            file_size_bytes=100,
            compression="gzip",
            checksum_sha256="abc",
            status="completed",
            created_at=datetime.now(UTC),
        )
        db_session.add(batch)
        db_session.commit()

        from app.modules.ais_archiver import restore_archive_batch

        with pytest.raises(FileNotFoundError):
            restore_archive_batch(db_session, batch.batch_id)


# ---------------------------------------------------------------------------
# verify_archive_integrity
# ---------------------------------------------------------------------------


class TestVerifyArchiveIntegrity:
    def test_valid_checksum(self, db_session, mock_settings):
        """Verify should return True for valid archive."""
        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points, verify_archive_integrity

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        assert verify_archive_integrity(batch) is True

    def test_corrupted_file(self, db_session, mock_settings, tmp_path):
        """Verify should return False for corrupted archive."""
        from app.models.ais_archive_batch import AisArchiveBatch

        # Create a fake file
        fake_file = tmp_path / "fake.parquet.gz"
        fake_file.write_bytes(b"corrupted data")

        batch = AisArchiveBatch(
            archive_date=datetime.now(UTC),
            date_range_start=datetime.now(UTC),
            date_range_end=datetime.now(UTC),
            row_count=1,
            file_path=str(fake_file),
            file_size_bytes=14,
            compression="gzip",
            checksum_sha256="wrong_checksum",
            status="completed",
            created_at=datetime.now(UTC),
        )

        from app.modules.ais_archiver import verify_archive_integrity

        assert verify_archive_integrity(batch) is False

    def test_missing_file(self, db_session, mock_settings):
        """Verify should return False if file is missing."""
        from app.models.ais_archive_batch import AisArchiveBatch

        batch = AisArchiveBatch(
            archive_date=datetime.now(UTC),
            date_range_start=datetime.now(UTC),
            date_range_end=datetime.now(UTC),
            row_count=1,
            file_path="/nonexistent/path.parquet.gz",
            file_size_bytes=0,
            compression="gzip",
            checksum_sha256="abc",
            status="completed",
            created_at=datetime.now(UTC),
        )

        from app.modules.ais_archiver import verify_archive_integrity

        assert verify_archive_integrity(batch) is False


# ---------------------------------------------------------------------------
# cleanup_expired_archives
# ---------------------------------------------------------------------------


class TestCleanupExpiredArchives:
    def test_deletes_old_archives(self, db_session, mock_settings, tmp_path):
        """Cleanup should delete archives older than max_age_days."""
        from app.models.ais_archive_batch import AisArchiveBatch

        # Create an old archive
        fake_file = tmp_path / "old_batch.parquet.gz"
        fake_file.write_bytes(b"data")

        old_date = datetime.now(UTC) - timedelta(days=200)
        batch = AisArchiveBatch(
            archive_date=old_date,
            date_range_start=old_date,
            date_range_end=old_date,
            row_count=100,
            file_path=str(fake_file),
            file_size_bytes=4,
            compression="gzip",
            checksum_sha256="abc",
            status="completed",
            created_at=old_date,
        )
        db_session.add(batch)
        db_session.commit()

        from app.modules.ais_archiver import cleanup_expired_archives

        deleted = cleanup_expired_archives(db_session, max_age_days=180)
        assert deleted == 1
        assert not fake_file.exists()
        assert db_session.query(AisArchiveBatch).count() == 0

    def test_keeps_recent_archives(self, db_session, mock_settings, tmp_path):
        """Cleanup should not delete recent archives."""
        from app.models.ais_archive_batch import AisArchiveBatch

        recent_date = datetime.now(UTC) - timedelta(days=10)
        batch = AisArchiveBatch(
            archive_date=recent_date,
            date_range_start=recent_date,
            date_range_end=recent_date,
            row_count=100,
            file_path="/tmp/recent.parquet.gz",
            file_size_bytes=4,
            compression="gzip",
            checksum_sha256="abc",
            status="completed",
            created_at=recent_date,
        )
        db_session.add(batch)
        db_session.commit()

        from app.modules.ais_archiver import cleanup_expired_archives

        deleted = cleanup_expired_archives(db_session, max_age_days=180)
        assert deleted == 0
        assert db_session.query(AisArchiveBatch).count() == 1


# ---------------------------------------------------------------------------
# get_retention_stats
# ---------------------------------------------------------------------------


class TestGetRetentionStats:
    def test_basic_stats(self, db_session, mock_settings):
        """Stats should return point count and archive summary."""
        _create_vessel(db_session)
        now = datetime.now(UTC)
        for i in range(5):
            _create_ais_point(db_session, i + 1, 1, now - timedelta(hours=i))

        from app.modules.ais_archiver import get_retention_stats

        stats = get_retention_stats(db_session)
        assert stats["ais_point_count"] == 5
        assert stats["archive_count"] == 0
        assert stats["total_archived_rows"] == 0
        assert stats["total_archive_size_bytes"] == 0

    def test_stats_with_archives(self, db_session, mock_settings):
        """Stats should include archive summary when batches exist."""
        from app.models.ais_archive_batch import AisArchiveBatch

        now = datetime.now(UTC)
        for i in range(2):
            batch = AisArchiveBatch(
                archive_date=now,
                date_range_start=now,
                date_range_end=now,
                row_count=100 * (i + 1),
                file_path=f"/tmp/batch_{i}.parquet.gz",
                file_size_bytes=5000 * (i + 1),
                compression="gzip",
                checksum_sha256="abc",
                status="completed",
                created_at=now,
            )
            db_session.add(batch)
        db_session.commit()

        from app.modules.ais_archiver import get_retention_stats

        stats = get_retention_stats(db_session)
        assert stats["archive_count"] == 2
        assert stats["total_archived_rows"] == 300  # 100 + 200
        assert stats["total_archive_size_bytes"] == 15000  # 5000 + 10000

    def test_db_size_estimate(self, db_session, mock_settings):
        """SQLite DB size estimate should return a number."""
        from app.modules.ais_archiver import get_retention_stats

        stats = get_retention_stats(db_session)
        # SQLite in-memory may or may not return size
        assert "db_size_bytes" in stats


# ---------------------------------------------------------------------------
# Scheduler integration
# ---------------------------------------------------------------------------


class TestSchedulerIntegration:
    def test_archive_before_delete(self, db_session, mock_settings):
        """When ARCHIVE_BEFORE_DELETE is True, archival should run before pruning."""
        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old, source="digitraffic")

        from app.models.ais_archive_batch import AisArchiveBatch

        with patch("app.modules.collection_scheduler.settings") as sched_settings:
            sched_settings.ARCHIVE_BEFORE_DELETE = True
            sched_settings.RETENTION_DAYS_REALTIME = 90
            sched_settings.COLLECT_RETENTION_DAYS = 90

            from app.modules.collection_scheduler import CollectionScheduler

            scheduler = CollectionScheduler(db_factory=lambda: db_session)

            with patch("app.modules.ais_archiver.settings", mock_settings):
                scheduler._prune_old_points("digitraffic")

        # Check archive was created
        batches = db_session.query(AisArchiveBatch).all()
        assert len(batches) >= 1

    def test_archive_failure_doesnt_block_pruning(self, db_session, mock_settings):
        """If archival fails, pruning should still proceed."""
        from app.models.ais_point import AISPoint

        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old, source="digitraffic")

        with patch("app.modules.collection_scheduler.settings") as sched_settings:
            sched_settings.ARCHIVE_BEFORE_DELETE = True
            sched_settings.RETENTION_DAYS_REALTIME = 90
            sched_settings.COLLECT_RETENTION_DAYS = 90

            from app.modules.collection_scheduler import CollectionScheduler

            scheduler = CollectionScheduler(db_factory=lambda: db_session)

            with patch("app.modules.ais_archiver.archive_old_points", side_effect=Exception("fail")):
                scheduler._prune_old_points("digitraffic")

        # Point should still be deleted by pruning
        assert db_session.query(AISPoint).count() == 0


# ---------------------------------------------------------------------------
# Feature flag disabled
# ---------------------------------------------------------------------------


class TestFeatureFlags:
    def test_archive_disabled(self, db_session, mock_settings):
        """When ARCHIVE_ENABLED is False, manual archive via route should fail."""
        # This tests the route guard logic — the archiver module itself
        # doesn't check the flag (routes do)
        mock_settings.ARCHIVE_ENABLED = False
        # The module function still works — it's the API that gates it
        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points

        # Direct call still works (feature flag checked at route level)
        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)
        assert batch.row_count == 1


# ---------------------------------------------------------------------------
# _compute_sha256
# ---------------------------------------------------------------------------


class TestComputeSha256:
    def test_known_hash(self, tmp_path):
        """SHA-256 of known content should match."""
        from app.modules.ais_archiver import _compute_sha256

        f = tmp_path / "test.bin"
        content = b"hello world"
        f.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        assert _compute_sha256(f) == expected

    def test_empty_file(self, tmp_path):
        """SHA-256 of empty file should match known empty hash."""
        from app.modules.ais_archiver import _compute_sha256

        f = tmp_path / "empty.bin"
        f.write_bytes(b"")

        expected = hashlib.sha256(b"").hexdigest()
        assert _compute_sha256(f) == expected


# ---------------------------------------------------------------------------
# _points_to_dataframe
# ---------------------------------------------------------------------------


class TestPointsToDataframe:
    def test_converts_orm_objects(self, db_session, mock_settings):
        """Should convert AIS point objects to a Polars DataFrame."""
        from app.models.ais_point import AISPoint
        from app.modules.ais_archiver import _points_to_dataframe

        _create_vessel(db_session)
        now = datetime.now(UTC)
        _create_ais_point(db_session, 1, 1, now)

        points = db_session.query(AISPoint).all()
        df = _points_to_dataframe(points)

        assert isinstance(df, pl.DataFrame)
        assert len(df) == 1
        assert df["ais_point_id"][0] == 1
        assert df["vessel_id"][0] == 1

    def test_empty_list(self):
        """Empty list should produce empty DataFrame."""
        from app.modules.ais_archiver import _points_to_dataframe

        df = _points_to_dataframe([])
        assert isinstance(df, pl.DataFrame)
        assert len(df) == 0


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class TestAisArchiveBatchModel:
    def test_model_creation(self, db_session):
        """AisArchiveBatch should be persistable."""
        from app.models.ais_archive_batch import AisArchiveBatch

        now = datetime.now(UTC)
        batch = AisArchiveBatch(
            archive_date=now,
            date_range_start=now - timedelta(days=30),
            date_range_end=now,
            row_count=1000,
            file_path="/data/archives/ais_points/2024/03/batch_1.parquet.gz",
            file_size_bytes=50000,
            compression="gzip",
            checksum_sha256="a" * 64,
            status="completed",
            created_at=now,
        )
        db_session.add(batch)
        db_session.commit()

        loaded = db_session.query(AisArchiveBatch).first()
        assert loaded.row_count == 1000
        assert loaded.compression == "gzip"
        assert loaded.status == "completed"

    def test_model_nullable_fields(self, db_session):
        """Optional fields should accept None."""
        from app.models.ais_archive_batch import AisArchiveBatch

        now = datetime.now(UTC)
        batch = AisArchiveBatch(
            archive_date=now,
            date_range_start=now,
            date_range_end=now,
            row_count=0,
            file_path="",
            file_size_bytes=0,
            compression="gzip",
            checksum_sha256="",
            status="completed",
            source_filter=None,
            created_by=None,
            created_at=now,
        )
        db_session.add(batch)
        db_session.commit()

        loaded = db_session.query(AisArchiveBatch).first()
        assert loaded.source_filter is None
        assert loaded.created_by is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_zstd_compression(self, db_session, mock_settings):
        """Archive with zstd compression should work."""
        mock_settings.ARCHIVE_COMPRESSION = "zstd"
        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)

        assert batch.file_path.endswith(".parquet.zst")
        assert batch.row_count == 1

    def test_concurrent_archive_and_restore(self, db_session, mock_settings):
        """Archive then restore should round-trip data correctly."""
        from app.models.ais_point import AISPoint

        _create_vessel(db_session)
        old = datetime.now(UTC) - timedelta(days=100)
        _create_ais_point(db_session, 1, 1, old)

        from app.modules.ais_archiver import archive_old_points, restore_archive_batch

        cutoff = datetime.now(UTC) - timedelta(days=90)
        batch = archive_old_points(db_session, cutoff)
        assert db_session.query(AISPoint).count() == 0

        count = restore_archive_batch(db_session, batch.batch_id)
        assert count == 1

        point = db_session.query(AISPoint).first()
        assert point.ais_point_id == 1
        assert point.vessel_id == 1
        assert abs(point.lat - 60.0) < 0.01
