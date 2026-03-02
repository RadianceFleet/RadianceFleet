"""Historical data backfill scheduler.

Runs on a weekly cadence (HISTORY_BACKFILL_INTERVAL_HOURS, default 168h).
Per-source max days per run: NOAA=30, DMA=14, GFW=90 — bounded to avoid
hammering upstream servers.  Oldest-first gap filling via coverage_tracker.

Thread-based, same pattern as CollectionScheduler.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Per-source limits: max days to fetch in a single backfill run
_SOURCE_MAX_DAYS: dict[str, int] = {
    "noaa": 30,
    "dma": 14,
    "gfw-gaps": 90,
    "gfw-encounters": 90,
    "gfw-port-visits": 90,
}

# Map source name -> config flag
_SOURCE_FLAGS: dict[str, str] = {
    "noaa": "NOAA_BACKFILL_ENABLED",
    "dma": "DMA_BACKFILL_ENABLED",
    "gfw-gaps": "GFW_GAPS_BACKFILL_ENABLED",
    "gfw-encounters": "GFW_ENCOUNTERS_BACKFILL_ENABLED",
    "gfw-port-visits": "GFW_PORT_VISITS_BACKFILL_ENABLED",
}


class HistoryScheduler:
    """Manages periodic historical data backfill from multiple sources."""

    def __init__(self, db_factory: Callable[[], Session]):
        self._db_factory = db_factory
        self._shutdown_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        """Start the scheduler loop in a background thread."""
        if not settings.HISTORY_BACKFILL_ENABLED:
            logger.info("History backfill disabled (HISTORY_BACKFILL_ENABLED=False)")
            return

        self._thread = threading.Thread(
            target=self._loop,
            name="history-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """Signal the thread to stop and wait."""
        self._shutdown_event.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
            self._thread = None

    def run_now(self, db: Session) -> dict:
        """Run one backfill cycle synchronously (for CLI / tests).

        Returns dict of {source: result_or_error}.
        """
        return self._dispatch_backfill(db)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop(self):
        interval_s = settings.HISTORY_BACKFILL_INTERVAL_HOURS * 3600
        while not self._shutdown_event.is_set():
            db = self._db_factory()
            try:
                results = self._dispatch_backfill(db)
                for source, result in results.items():
                    logger.info("History backfill %s: %s", source, result)
            except Exception as e:
                logger.error("History backfill cycle error: %s", e)
            finally:
                try:
                    db.close()
                except Exception:
                    pass

            self._shutdown_event.wait(timeout=interval_s)

    def _dispatch_backfill(self, db: Session) -> dict:
        """Run backfill for each enabled source.  Oldest-first gap filling."""
        results: dict[str, object] = {}
        enabled_sources = self._get_enabled_sources()

        for source in enabled_sources:
            try:
                result = self._backfill_source(db, source)
                results[source] = result
            except Exception as e:
                logger.error("Backfill failed for %s: %s", source, e)
                results[source] = {"error": str(e)}

        return results

    def _get_enabled_sources(self) -> list[str]:
        """Return list of source names whose per-source flag is True."""
        sources = []
        for source, flag_attr in _SOURCE_FLAGS.items():
            if getattr(settings, flag_attr, False):
                sources.append(source)
        return sources

    def _backfill_source(self, db: Session, source: str) -> dict:
        """Backfill a single source, using coverage gaps if available."""
        max_days = _SOURCE_MAX_DAYS.get(source, 30)

        # Try to get gaps from coverage_tracker (oldest-first)
        gaps = self._find_gaps(db, source, max_days)

        if gaps:
            return self._backfill_gaps(db, source, gaps)

        # Fallback: backfill the most recent max_days window
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=max_days - 1)
        return self._call_source(db, source, start_date, end_date)

    def _find_gaps(
        self, db: Session, source: str, max_days: int
    ) -> list[tuple[date, date]]:
        """Query coverage_tracker for uncovered date ranges (oldest first)."""
        try:
            from app.modules.coverage_tracker import find_coverage_gaps

            # Search a wide window: 2 years back to yesterday
            to_date = date.today() - timedelta(days=1)
            from_date = to_date - timedelta(days=730)
            gaps = find_coverage_gaps(db, source, from_date, to_date)
            # Clamp total days to max_days
            clamped: list[tuple[date, date]] = []
            days_remaining = max_days
            for gap_start, gap_end in gaps:
                if days_remaining <= 0:
                    break
                gap_days = (gap_end - gap_start).days + 1
                if gap_days > days_remaining:
                    gap_end = gap_start + timedelta(days=days_remaining - 1)
                clamped.append((gap_start, gap_end))
                days_remaining -= (gap_end - gap_start).days + 1
            return clamped
        except (ImportError, AttributeError):
            logger.debug("coverage_tracker not available; using fallback window")
            return []
        except Exception as e:
            logger.warning("coverage_tracker.find_coverage_gaps error: %s", e)
            return []

    def _backfill_gaps(
        self, db: Session, source: str, gaps: list[tuple[date, date]]
    ) -> dict:
        """Backfill each gap range and aggregate results."""
        combined: dict[str, object] = {"gaps_processed": len(gaps), "errors": []}
        for gap_start, gap_end in gaps:
            try:
                result = self._call_source(db, source, gap_start, gap_end)
                for k, v in result.items():
                    if isinstance(v, (int, float)):
                        combined[k] = combined.get(k, 0) + v  # type: ignore[operator]
            except Exception as e:
                combined["errors"].append(str(e))  # type: ignore[union-attr]
        return combined

    def _call_source(
        self, db: Session, source: str, start_date: date, end_date: date
    ) -> dict:
        """Dispatch to the appropriate client import function and record coverage."""
        result: dict = {}
        if source == "noaa":
            from app.modules.noaa_client import fetch_and_import_noaa

            result = fetch_and_import_noaa(
                db, start_date=start_date, end_date=end_date
            )
        elif source == "dma":
            from app.modules.dma_client import fetch_and_import_dma

            result = fetch_and_import_dma(db, start_date, end_date)
        elif source == "gfw-gaps":
            from app.modules.gfw_client import import_gfw_gap_events

            result = import_gfw_gap_events(
                db,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
        elif source == "gfw-encounters":
            from app.modules.gfw_client import import_gfw_encounters

            result = import_gfw_encounters(
                db,
                date_from=start_date.isoformat(),
                date_to=end_date.isoformat(),
            )
        elif source == "gfw-port-visits":
            from app.modules.gfw_client import import_gfw_port_visits

            result = import_gfw_port_visits(
                db,
                date_from=start_date.isoformat(),
                date_to=end_date.isoformat(),
            )

        # Record coverage window if coverage_tracker is available
        self._record_coverage(db, source, start_date, end_date)

        return result

    def _record_coverage(
        self, db: Session, source: str, start_date: date, end_date: date
    ) -> None:
        """Record a coverage window via coverage_tracker (best-effort)."""
        try:
            from app.modules.coverage_tracker import record_coverage_window

            record_coverage_window(
                db, source, start_date, end_date,
                status="completed",
            )
            db.commit()
        except (ImportError, AttributeError):
            pass
        except Exception as e:
            logger.debug("Failed to record coverage window: %s", e)
