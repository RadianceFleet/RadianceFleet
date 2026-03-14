"""Watchlist auto-update scheduler — periodic refresh of sanctions lists.

Sources and default intervals:
  OFAC SDN:       daily   (US Treasury, CSV)
  OpenSanctions:  daily   (FTM JSON, aggregated sanctions)
  KSE Institute:  weekly  (shadow fleet tracker, CSV)

Each source is fetched independently. A failure in one source does not
block the others. Last-update timestamps are tracked per source to
enforce intervals.

Design: single-threaded synchronous (called from CLI or cron).
For background use, wrap in a threading.Thread like HistoryScheduler.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Source definitions: (name, interval, fetch_fn_name, loader_fn_name, file_prefix)
SOURCES = [
    {
        "name": "OFAC_SDN",
        "interval": timedelta(days=1),
        "fetch": "fetch_ofac_sdn",
        "loader": "load_ofac_sdn",
        "file_prefix": "ofac_sdn_",
    },
    {
        "name": "OPENSANCTIONS",
        "interval": timedelta(days=1),
        "fetch": "fetch_opensanctions_vessels",
        "loader": "load_opensanctions",
        "file_prefix": "opensanctions_vessels_",
    },
    {
        "name": "KSE_SHADOW",
        "interval": timedelta(days=7),
        "fetch": None,  # KSE has no auto-fetch; uses existing file
        "loader": "load_kse_list",
        "file_prefix": "kse_",
    },
]

# In-memory tracker for last successful update per source.
# Persisted to DB via WatchlistUpdateLog if available, otherwise memory-only.
_last_update: dict[str, datetime] = {}


class WatchlistUpdateLog:
    """Lightweight log entry for watchlist updates.

    Stored in the watchlist_update_log table if it exists, otherwise
    updates are tracked in-memory only.
    """

    @staticmethod
    def get_last_update(db: Session, source_name: str) -> datetime | None:
        """Get the last successful update time for a source."""
        # Check in-memory cache first
        if source_name in _last_update:
            return _last_update[source_name]

        # Try DB table
        try:
            from sqlalchemy import text

            row = db.execute(
                text(
                    "SELECT updated_at FROM watchlist_update_log "
                    "WHERE source_name = :src ORDER BY updated_at DESC LIMIT 1"
                ),
                {"src": source_name},
            ).fetchone()
            if row:
                ts = row[0]
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                return ts
        except Exception:
            logger.debug("Failed to query last update timestamp", exc_info=True)
        return None

    @staticmethod
    def record_update(
        db: Session,
        source_name: str,
        status: str,
        added: int = 0,
        removed: int = 0,
        unchanged: int = 0,
        error: str | None = None,
    ) -> None:
        """Record a watchlist update result."""
        now = datetime.now(UTC)
        if status == "success":
            _last_update[source_name] = now

        try:
            from sqlalchemy import text

            db.execute(
                text(
                    "INSERT INTO watchlist_update_log "
                    "(source_name, updated_at, status, added, removed, unchanged, error) "
                    "VALUES (:src, :ts, :status, :added, :removed, :unchanged, :error)"
                ),
                {
                    "src": source_name,
                    "ts": now.isoformat(),
                    "status": status,
                    "added": added,
                    "removed": removed,
                    "unchanged": unchanged,
                    "error": error,
                },
            )
            db.commit()
        except Exception:
            logger.debug("Failed to record watchlist update log", exc_info=True)


def _ensure_log_table(db: Session) -> None:
    """Create watchlist_update_log table if it doesn't exist."""
    try:
        from sqlalchemy import text

        db.execute(
            text(
                "CREATE TABLE IF NOT EXISTS watchlist_update_log ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  source_name VARCHAR(100) NOT NULL,"
                "  updated_at TIMESTAMP NOT NULL,"
                "  status VARCHAR(20) NOT NULL,"
                "  added INTEGER DEFAULT 0,"
                "  removed INTEGER DEFAULT 0,"
                "  unchanged INTEGER DEFAULT 0,"
                "  error TEXT"
                ")"
            )
        )
        db.commit()
    except Exception as e:
        logger.debug("Could not create watchlist_update_log table: %s", e)


def _count_active_entries(db: Session, source_name: str) -> int:
    """Count currently active watchlist entries for a source."""
    from app.models.vessel_watchlist import VesselWatchlist

    return (
        db.query(VesselWatchlist)
        .filter(
            VesselWatchlist.watchlist_source == source_name,
            VesselWatchlist.is_active == True,  # noqa: E712
        )
        .count()
    )


def _get_active_vessel_ids(db: Session, source_name: str) -> set[int]:
    """Get set of vessel_ids with active watchlist entries for a source."""
    from app.models.vessel_watchlist import VesselWatchlist

    rows = (
        db.query(VesselWatchlist.vessel_id)
        .filter(
            VesselWatchlist.watchlist_source == source_name,
            VesselWatchlist.is_active == True,  # noqa: E712
        )
        .all()
    )
    return {r[0] for r in rows}


def _should_update(db: Session, source_name: str, interval: timedelta) -> bool:
    """Check if enough time has elapsed since the last successful update."""
    last = WatchlistUpdateLog.get_last_update(db, source_name)
    if last is None:
        return True
    now = datetime.now(UTC)
    return (now - last) >= interval


def update_source(
    db: Session,
    source_cfg: dict,
    force: bool = False,
) -> dict:
    """Update a single watchlist source.

    Returns dict with keys: source, status, added, removed, unchanged, error.
    """
    source_name = source_cfg["name"]
    interval = source_cfg["interval"]

    if not force and not _should_update(db, source_name, interval):
        logger.info("Watchlist %s: skipping, last update within interval", source_name)
        return {
            "source": source_name,
            "status": "skipped",
            "reason": "within_interval",
        }

    # Snapshot current active entries before update
    before_ids = _get_active_vessel_ids(db, source_name)

    # Fetch new data file if auto-fetch is available
    data_dir = Path(settings.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    file_path = None
    if source_cfg["fetch"]:
        try:
            from app.modules import data_fetcher

            fetch_fn = getattr(data_fetcher, source_cfg["fetch"])
            result = fetch_fn(force=force)
            if result.get("error"):
                logger.warning("Watchlist %s fetch error: %s", source_name, result["error"])
                WatchlistUpdateLog.record_update(db, source_name, "error", error=result["error"])
                return {
                    "source": source_name,
                    "status": "error",
                    "error": result["error"],
                }
            file_path = result.get("path")
        except Exception as e:
            logger.error("Watchlist %s fetch failed: %s", source_name, e)
            WatchlistUpdateLog.record_update(db, source_name, "error", error=str(e))
            return {"source": source_name, "status": "error", "error": str(e)}

    # Find file if not returned by fetch
    if file_path is None:
        from app.modules.data_fetcher import _find_latest

        file_path = _find_latest(data_dir, source_cfg["file_prefix"])

    if file_path is None:
        msg = f"No data file found for {source_name}"
        logger.warning("Watchlist %s: %s", source_name, msg)
        WatchlistUpdateLog.record_update(db, source_name, "error", error=msg)
        return {"source": source_name, "status": "error", "error": msg}

    # Run the loader
    try:
        from app.modules import watchlist_loader

        loader_fn = getattr(watchlist_loader, source_cfg["loader"])
        load_result = loader_fn(db, str(file_path))
    except Exception as e:
        logger.error("Watchlist %s load failed: %s", source_name, e)
        WatchlistUpdateLog.record_update(db, source_name, "error", error=str(e))
        return {"source": source_name, "status": "error", "error": str(e)}

    # Diff: compute added/removed
    after_ids = _get_active_vessel_ids(db, source_name)
    added = len(after_ids - before_ids)
    removed = len(before_ids - after_ids)
    unchanged = len(before_ids & after_ids)

    # Fire webhooks for OFAC_SDN when new vessels are added
    if (
        source_name == "OFAC_SDN"
        and added > 0
        and settings.OFAC_SDN_WEBHOOK_ON_NEW
    ):
        try:
            from app.models.vessel_watchlist import VesselWatchlist

            new_vessel_ids = after_ids - before_ids
            new_entries = (
                db.query(VesselWatchlist)
                .filter(
                    VesselWatchlist.vessel_id.in_(new_vessel_ids),
                    VesselWatchlist.watchlist_source == "OFAC_SDN",
                )
                .all()
            )
            vessels_added = []
            for entry in new_entries:
                vessel_name = None
                if entry.vessel:
                    vessel_name = entry.vessel.name
                vessels_added.append({
                    "vessel_id": entry.vessel_id,
                    "name": vessel_name,
                    "reason": entry.reason,
                })

            import asyncio

            from app.modules.webhook_dispatcher import fire_webhooks

            payload = {
                "added": added,
                "removed": removed,
                "vessels_added": vessels_added,
            }
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(fire_webhooks(db, "ofac_sdn_update", payload))
                else:
                    loop.run_until_complete(fire_webhooks(db, "ofac_sdn_update", payload))
            except RuntimeError:
                asyncio.run(fire_webhooks(db, "ofac_sdn_update", payload))
            logger.info("OFAC SDN webhook fired: added=%d removed=%d", added, removed)
        except Exception:
            logger.warning("Failed to fire OFAC SDN webhook", exc_info=True)

    logger.info(
        "Watchlist %s updated: added=%d removed=%d unchanged=%d matched=%d unmatched=%d",
        source_name,
        added,
        removed,
        unchanged,
        load_result.get("matched", 0),
        load_result.get("unmatched", 0),
    )

    WatchlistUpdateLog.record_update(
        db,
        source_name,
        "success",
        added=added,
        removed=removed,
        unchanged=unchanged,
    )

    return {
        "source": source_name,
        "status": "success",
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
        "matched": load_result.get("matched", 0),
        "unmatched": load_result.get("unmatched", 0),
    }


def run_watchlist_update(
    db: Session,
    force: bool = False,
    sources: list[str] | None = None,
) -> list[dict]:
    """Run watchlist updates for all configured sources.

    Args:
        db: Active SQLAlchemy session.
        force: If True, ignore interval checks and update all sources.
        sources: Optional list of source names to update. If None, update all.

    Returns:
        List of result dicts, one per source.
    """
    _ensure_log_table(db)

    results = []
    for source_cfg in SOURCES:
        if sources and source_cfg["name"] not in sources:
            continue

        try:
            result = update_source(db, source_cfg, force=force)
            results.append(result)
        except Exception as e:
            logger.error(
                "Unexpected error updating watchlist %s: %s",
                source_cfg["name"],
                e,
            )
            results.append(
                {
                    "source": source_cfg["name"],
                    "status": "error",
                    "error": str(e),
                }
            )

    return results
