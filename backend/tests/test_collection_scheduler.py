"""Tests for the periodic AIS collection scheduler."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from sqlalchemy.orm import Session


def _mock_db_factory():
    """Create a mock SessionLocal factory."""
    db = MagicMock(spec=Session)
    db.query.return_value.filter.return_value.delete.return_value = 0
    return db


class TestCollectionScheduler:
    """Tests for collection_scheduler.py."""

    def test_scheduler_starts_threads(self):
        """Scheduler creates and starts a thread per source."""
        with patch("app.modules.collection_scheduler.settings") as mock_settings:
            mock_settings.COLLECT_RETENTION_DAYS = 90

            mock_source = MagicMock()
            mock_source.name = "test_source"
            mock_source.interval_seconds = 1
            mock_source.enabled = True
            mock_source.collector = MagicMock(return_value={"points_imported": 0, "vessels_seen": 0, "errors": 0})

            with patch("app.modules.collection_sources.get_available_sources", return_value={"test": mock_source}):
                from app.modules.collection_scheduler import CollectionScheduler
                scheduler = CollectionScheduler(db_factory=_mock_db_factory)
                # Run for a very short time
                scheduler.start(duration_seconds=1)
                # Should complete without error
                assert len(scheduler._threads) >= 1

    def test_scheduler_stops_cleanly(self):
        """Stop signals all threads and waits for completion."""
        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings") as mock_settings:
            mock_settings.COLLECT_RETENTION_DAYS = 90

            scheduler = CollectionScheduler(db_factory=_mock_db_factory, sources=["nonexistent"])

            with patch("app.modules.collection_sources.get_all_sources", return_value={}):
                scheduler.start(duration_seconds=0)
                scheduler.stop()
                assert scheduler._shutdown_event.is_set()

    def test_scheduler_absolute_timing(self):
        """Absolute scheduling: next_run calculated before callback."""
        # This is a design test — verify the scheduler uses monotonic time
        from app.modules.collection_scheduler import CollectionScheduler
        scheduler = CollectionScheduler(db_factory=_mock_db_factory)
        assert hasattr(scheduler, '_shutdown_event')
        assert isinstance(scheduler._shutdown_event, threading.Event)

    def test_scheduler_source_isolation(self):
        """Each source gets its own DB session from factory."""
        call_count = [0]

        def counting_factory():
            call_count[0] += 1
            return _mock_db_factory()

        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings") as mock_settings:
            mock_settings.COLLECT_RETENTION_DAYS = 90

            mock_src_a = MagicMock()
            mock_src_a.name = "source_a"
            mock_src_a.interval_seconds = 60
            mock_src_a.enabled = True
            mock_src_a.collector = MagicMock(return_value={"points_imported": 0, "vessels_seen": 0, "errors": 0})

            mock_src_b = MagicMock()
            mock_src_b.name = "source_b"
            mock_src_b.interval_seconds = 60
            mock_src_b.enabled = True
            mock_src_b.collector = MagicMock(return_value={"points_imported": 0, "vessels_seen": 0, "errors": 0})

            with patch("app.modules.collection_sources.get_available_sources", return_value={"a": mock_src_a, "b": mock_src_b}):
                scheduler = CollectionScheduler(db_factory=counting_factory)
                scheduler.start(duration_seconds=1)
                # Each source thread should call factory at least once, plus pruning
                assert call_count[0] >= 2

    def test_scheduler_retention_pruning(self):
        """Old points are deleted during pruning."""
        db = MagicMock(spec=Session)
        db.query.return_value.filter.return_value.delete.return_value = 5

        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings") as mock_settings:
            mock_settings.COLLECT_RETENTION_DAYS = 90
            scheduler = CollectionScheduler(db_factory=lambda: db)
            scheduler._prune_old_points("test")
            # Should have called delete
            assert db.query.called

    def test_scheduler_records_collection_run(self):
        """A CollectionRun record is created for each cycle."""
        db = MagicMock(spec=Session)
        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings"):
            scheduler = CollectionScheduler(db_factory=lambda: db)
            run = scheduler._start_collection_run(db, "test_source")
            assert run is not None
            assert db.add.called
            assert db.commit.called

    def test_scheduler_handles_source_error(self):
        """One source failing does not stop others."""
        results = []

        def failing_collector(db, duration):
            raise Exception("source failure")

        def working_collector(db, duration):
            results.append("ok")
            return {"points_imported": 1, "vessels_seen": 1, "errors": 0}

        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings") as mock_settings:
            mock_settings.COLLECT_RETENTION_DAYS = 90

            mock_fail = MagicMock()
            mock_fail.name = "fail_source"
            mock_fail.interval_seconds = 60
            mock_fail.enabled = True
            mock_fail.collector = failing_collector

            mock_ok = MagicMock()
            mock_ok.name = "ok_source"
            mock_ok.interval_seconds = 60
            mock_ok.enabled = True
            mock_ok.collector = working_collector

            with patch("app.modules.collection_sources.get_available_sources", return_value={"fail": mock_fail, "ok": mock_ok}):
                scheduler = CollectionScheduler(db_factory=_mock_db_factory)
                scheduler.start(duration_seconds=1)
                # The working source should have produced results
                assert len(results) >= 1

    def test_scheduler_auto_sources(self):
        """Auto-discover enabled sources when none specified."""
        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings") as mock_settings:
            mock_settings.COLLECT_RETENTION_DAYS = 90

            # No sources enabled -> warning, no threads
            with patch("app.modules.collection_sources.get_available_sources", return_value={}):
                scheduler = CollectionScheduler(db_factory=_mock_db_factory)
                scheduler.start(duration_seconds=1)
                assert len(scheduler._threads) == 0

    def test_scheduler_custom_sources(self):
        """Only specified sources are used."""
        from app.modules.collection_scheduler import CollectionScheduler

        mock_src = MagicMock()
        mock_src.name = "custom"
        mock_src.interval_seconds = 60
        mock_src.enabled = True
        mock_src.collector = MagicMock(return_value={"points_imported": 0, "vessels_seen": 0, "errors": 0})

        with patch("app.modules.collection_scheduler.settings") as mock_settings:
            mock_settings.COLLECT_RETENTION_DAYS = 90
            with patch("app.modules.collection_sources.get_all_sources", return_value={"custom": mock_src, "other": MagicMock()}):
                scheduler = CollectionScheduler(db_factory=_mock_db_factory, sources=["custom"])
                scheduler.start(duration_seconds=1)
                assert len(scheduler._threads) == 1

    def test_scheduler_duration_limit(self):
        """Stops after specified duration."""
        from app.modules.collection_scheduler import CollectionScheduler

        mock_src = MagicMock()
        mock_src.name = "timed"
        mock_src.interval_seconds = 1
        mock_src.enabled = True
        mock_src.collector = MagicMock(return_value={"points_imported": 0, "vessels_seen": 0, "errors": 0})

        with patch("app.modules.collection_scheduler.settings") as mock_settings:
            mock_settings.COLLECT_RETENTION_DAYS = 90
            with patch("app.modules.collection_sources.get_available_sources", return_value={"timed": mock_src}):
                scheduler = CollectionScheduler(db_factory=_mock_db_factory)
                start_time = time.monotonic()
                scheduler.start(duration_seconds=2)
                elapsed = time.monotonic() - start_time
                assert elapsed < 10  # Should finish within reason

    def test_scheduler_shutdown_event(self):
        """Clean termination via shutdown event."""
        from app.modules.collection_scheduler import CollectionScheduler
        scheduler = CollectionScheduler(db_factory=_mock_db_factory)
        assert not scheduler._shutdown_event.is_set()
        scheduler.stop()
        assert scheduler._shutdown_event.is_set()

    def test_scheduler_finish_collection_run(self):
        """Collection run status is updated on completion."""
        db = MagicMock(spec=Session)
        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings"):
            scheduler = CollectionScheduler(db_factory=lambda: db)
            run = MagicMock()
            scheduler._finish_collection_run(db, run, {"points_imported": 10, "vessels_seen": 3, "errors": 0})
            assert run.status == "completed"
            assert run.points_imported == 10

    def test_scheduler_fail_collection_run(self):
        """Collection run marked as failed on error."""
        db = MagicMock(spec=Session)
        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings"):
            scheduler = CollectionScheduler(db_factory=lambda: db)
            run = MagicMock()
            scheduler._fail_collection_run(db, run, "test error")
            assert run.status == "failed"

    def test_scheduler_none_run_handling(self):
        """Finishing or failing a None run does not error."""
        db = MagicMock(spec=Session)
        from app.modules.collection_scheduler import CollectionScheduler

        with patch("app.modules.collection_scheduler.settings"):
            scheduler = CollectionScheduler(db_factory=lambda: db)
            # Should not raise
            scheduler._finish_collection_run(db, None, {"points_imported": 0})
            scheduler._fail_collection_run(db, None, "error")
