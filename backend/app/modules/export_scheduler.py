"""Export scheduler — evaluates and runs due bulk export subscriptions."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def run_due_exports(db: Session) -> list[dict]:
    """Evaluate all active subscriptions and run those that are due.

    Returns a list of result dicts with subscription_id, status, row_count, etc.
    """
    from app.models.export_run import ExportRun
    from app.models.export_subscription import ExportSubscription
    from app.modules.export_delivery import deliver
    from app.modules.export_engine import generate_export

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        logger.debug("Export subscriptions disabled")
        return []

    subscriptions = (
        db.query(ExportSubscription)
        .filter(ExportSubscription.is_active == True)  # noqa: E712
        .all()
    )

    results = []
    now = datetime.now(UTC)

    for sub in subscriptions:
        if not _is_due(sub, now):
            continue

        run = ExportRun(
            subscription_id=sub.subscription_id,
            started_at=now,
            status="running",
        )
        db.add(run)
        db.flush()

        try:
            file_bytes, filename, row_count = generate_export(db, sub)

            # Save file to disk
            export_dir = Path(settings.EXPORT_TEMP_DIR)
            export_dir.mkdir(parents=True, exist_ok=True)
            file_path = export_dir / filename
            file_path.write_bytes(file_bytes)

            run.row_count = row_count
            run.file_size_bytes = len(file_bytes)
            run.file_path = str(file_path)

            # Deliver
            delivery_config = sub.delivery_config_json or {}
            delivery_result = deliver(
                file_bytes, filename, sub.delivery_method, delivery_config
            )

            run.delivery_status = delivery_result.get("status", "failed")
            if delivery_result.get("status") == "sent":
                run.status = "completed"
                run.finished_at = datetime.now(UTC)
            else:
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.error_message = delivery_result.get("error")

            # Update subscription
            sub.last_run_at = now
            sub.last_run_status = run.status
            sub.last_run_rows = row_count

            results.append({
                "subscription_id": sub.subscription_id,
                "run_id": run.run_id,
                "status": run.status,
                "row_count": row_count,
                "delivery_status": run.delivery_status,
            })

        except Exception as e:
            logger.exception("Export failed for subscription %d", sub.subscription_id)
            run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.error_message = str(e)
            sub.last_run_at = now
            sub.last_run_status = "failed"
            results.append({
                "subscription_id": sub.subscription_id,
                "run_id": run.run_id,
                "status": "failed",
                "error": str(e),
            })

        db.commit()

    return results


def _is_due(subscription, now: datetime) -> bool:
    """Check if a subscription is due for execution."""
    last_run = subscription.last_run_at
    schedule = subscription.schedule
    hour = subscription.schedule_hour_utc
    day = subscription.schedule_day

    if schedule == "daily":
        if now.hour < hour:
            return False
        if last_run is None:
            return True
        return (now - last_run) >= timedelta(hours=24)

    elif schedule == "weekly":
        if now.hour < hour:
            return False
        target_day = day if day is not None else 0  # default Monday
        if now.weekday() != target_day:
            return False
        if last_run is None:
            return True
        return (now - last_run) >= timedelta(days=6)

    elif schedule == "monthly":
        if now.hour < hour:
            return False
        target_day = day if day is not None else 1
        if now.day != target_day:
            return False
        if last_run is None:
            return True
        return (now - last_run) >= timedelta(days=27)

    return False


def cleanup_expired_files() -> int:
    """Delete export files older than EXPORT_FILE_RETENTION_HOURS."""
    export_dir = Path(settings.EXPORT_TEMP_DIR)
    if not export_dir.exists():
        return 0

    cutoff = datetime.now(UTC) - timedelta(hours=settings.EXPORT_FILE_RETENTION_HOURS)
    deleted = 0
    for f in export_dir.iterdir():
        if f.is_file():
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
            if mtime < cutoff:
                f.unlink()
                deleted += 1
                logger.info("Deleted expired export file: %s", f)

    return deleted
