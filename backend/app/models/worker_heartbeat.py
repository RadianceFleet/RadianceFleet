"""Worker heartbeat tracking for Railway multi-service deployments.

Each long-running worker (WebSocket consumer, cron updater) writes periodic
heartbeats so the /health/workers endpoint can verify liveness and data flow.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from app.models.base import Base


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id = Column(String(50), primary_key=True)
    status = Column(String(20), nullable=False, default="starting")
    last_heartbeat_utc = Column(DateTime, nullable=False)
    started_at_utc = Column(DateTime, nullable=True)
    records_processed = Column(Integer, default=0)
    error_message = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)


def upsert_heartbeat(
    db: Session,
    worker_id: str,
    *,
    status: str,
    records: int = 0,
    error: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Atomic upsert of a worker heartbeat row.

    Uses dialect-specific INSERT ... ON CONFLICT DO UPDATE since SQLAlchemy 2.x
    has no dialect-agnostic upsert.  Caller manages commit.
    """
    now = datetime.now(UTC)
    values = {
        "worker_id": worker_id,
        "status": status,
        "last_heartbeat_utc": now,
        "records_processed": records,
        "error_message": error[:2000] if error else None,
        "metadata_json": json.dumps(metadata) if metadata else None,
    }
    # Set started_at on first heartbeat (status == "starting")
    if status == "starting":
        values["started_at_utc"] = now

    update_set = {k: v for k, v in values.items() if k != "worker_id"}
    # Don't overwrite started_at_utc unless we're explicitly starting
    if status != "starting":
        update_set.pop("started_at_utc", None)

    dialect = db.bind.dialect.name if db.bind else "sqlite"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert

    stmt = insert(WorkerHeartbeat).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["worker_id"],
        set_=update_set,
    )
    db.execute(stmt)
