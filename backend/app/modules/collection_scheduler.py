"""Periodic AIS collection scheduler with absolute-time scheduling.

Design decisions:
- No threading.Lock (WAL + busy_timeout=5000 handles concurrency)
- Absolute scheduling (next_run = now + interval before callback, sleep remainder after)
- Non-daemon threads with shutdown_event for clean termination
- Each source gets its own SessionLocal() (thread-safe)
- Digitraffic downsampled to 1 point per 30 min per vessel
- Retention pruning: DELETE FROM ais_points WHERE timestamp_utc < now - 90d
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


class CollectionScheduler:
    """Manages periodic AIS data collection from multiple sources."""

    def __init__(
        self,
        db_factory: Callable[[], Session],
        sources: list[str] | None = None,
    ):
        """Initialize the scheduler.

        Args:
            db_factory: Callable that returns a new SQLAlchemy Session.
            sources: Optional list of source names. If None, auto-discover enabled sources.
        """
        self._db_factory = db_factory
        self._requested_sources = sources
        self._shutdown_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self, duration_seconds: int = 0):
        """Start collection. duration_seconds=0 means run indefinitely."""
        from app.modules.collection_sources import get_available_sources, get_all_sources

        if self._requested_sources:
            all_sources = get_all_sources()
            source_map = {
                name: info for name, info in all_sources.items()
                if name in self._requested_sources
            }
        else:
            source_map = get_available_sources()

        if not source_map:
            logger.warning("No collection sources available/enabled")
            return

        logger.info(
            "Starting collection scheduler with sources: %s (duration=%s)",
            ", ".join(source_map.keys()),
            f"{duration_seconds}s" if duration_seconds else "indefinite",
        )

        # Calculate absolute deadline
        deadline = (
            time.monotonic() + duration_seconds
            if duration_seconds > 0
            else None
        )

        for name, info in source_map.items():
            t = threading.Thread(
                target=self._source_loop,
                args=(name, info.collector, info.interval_seconds, deadline),
                name=f"collector-{name}",
                daemon=False,
            )
            self._threads.append(t)
            t.start()

        # Wait for all threads or deadline
        for t in self._threads:
            if deadline:
                remaining = max(0, deadline - time.monotonic())
                t.join(timeout=remaining + 5)
            else:
                t.join()

    def stop(self):
        """Signal all threads to stop and wait for completion."""
        logger.info("Stopping collection scheduler")
        self._shutdown_event.set()
        for t in self._threads:
            t.join(timeout=30)
        self._threads.clear()

    def _source_loop(
        self,
        source_name: str,
        collector: Callable,
        interval: int,
        deadline: float | None,
    ):
        """Collection loop for a single source with absolute-time scheduling."""
        while not self._shutdown_event.is_set():
            if deadline and time.monotonic() >= deadline:
                break

            next_run = time.monotonic() + interval
            db = self._db_factory()

            try:
                # Record collection run start
                run = self._start_collection_run(db, source_name)

                # Execute collection
                result = collector(db, interval)

                # Record completion
                self._finish_collection_run(db, run, result)

                logger.info(
                    "Collected %s: %s",
                    source_name,
                    {k: v for k, v in result.items() if k != "skipped"},
                )
            except Exception as e:
                logger.error("Collection error for %s: %s", source_name, e)
                try:
                    if run:
                        self._fail_collection_run(db, run, str(e))
                except Exception:
                    pass
            finally:
                try:
                    db.close()
                except Exception:
                    pass

            # Retention pruning
            self._prune_old_points(source_name)

            # Absolute-time sleep: wait until next_run
            remaining = next_run - time.monotonic()
            if remaining > 0:
                self._shutdown_event.wait(timeout=remaining)

    def _start_collection_run(self, db: Session, source_name: str):
        """Create a CollectionRun record."""
        try:
            from app.models.collection_run import CollectionRun
            run = CollectionRun(
                source=source_name,
                started_at=datetime.now(timezone.utc),
                status="running",
            )
            db.add(run)
            db.commit()
            return run
        except Exception as e:
            logger.warning("Failed to create CollectionRun for %s: %s", source_name, e)
            return None

    def _finish_collection_run(self, db: Session, run, result: dict):
        """Mark a CollectionRun as completed."""
        if run is None:
            return
        try:
            run.finished_at = datetime.now(timezone.utc)
            run.points_imported = result.get("points_imported", result.get("points_ingested", 0))
            run.vessels_seen = result.get("vessels_seen", 0)
            run.errors = result.get("errors", 0)
            run.status = "completed"
            run.details_json = json.dumps(result)
            db.commit()
        except Exception as e:
            logger.warning("Failed to update CollectionRun: %s", e)

    def _fail_collection_run(self, db: Session, run, error_msg: str):
        """Mark a CollectionRun as failed."""
        if run is None:
            return
        try:
            run.finished_at = datetime.now(timezone.utc)
            run.status = "failed"
            run.details_json = json.dumps({"error": error_msg})
            db.commit()
        except Exception:
            pass

    # Sources that store historical/archive data — never pruned
    ARCHIVE_SOURCES = {"noaa", "dma", "gfw", "barentswatch_historical"}

    def _prune_old_points(self, source_name: str):
        """Delete AIS points older than retention period.

        Archive sources (NOAA, DMA, GFW, BarentsWatch historical) are never pruned.
        Realtime sources use RETENTION_DAYS_REALTIME (default 90 days).
        """
        if source_name in self.ARCHIVE_SOURCES:
            return

        retention_days = getattr(settings, "RETENTION_DAYS_REALTIME", None)
        if retention_days is None or not isinstance(retention_days, (int, float)):
            retention_days = getattr(settings, "COLLECT_RETENTION_DAYS", 90)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        db = self._db_factory()
        try:
            from app.models.ais_point import AISPoint
            deleted = (
                db.query(AISPoint)
                .filter(
                    AISPoint.timestamp_utc < cutoff,
                    ~AISPoint.source.in_(self.ARCHIVE_SOURCES),
                )
                .delete(synchronize_session=False)
            )
            db.commit()
            if deleted:
                logger.info(
                    "Retention pruning (%s): deleted %d realtime points older than %d days",
                    source_name, deleted, retention_days,
                )
        except Exception as e:
            logger.warning("Retention pruning failed: %s", e)
        finally:
            db.close()
