"""Persistent ingestion status per source.

Replaces app.state-based in-memory tracking (lost on restart) with a
database-backed model that persists across server restarts.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import Session

from app.models.base import Base


class IngestionStatus(Base):
    __tablename__ = "ingestion_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), unique=True, nullable=False)
    last_run_utc = Column(DateTime, nullable=True)
    last_success_utc = Column(DateTime, nullable=True)
    records_ingested = Column(Integer, default=0)
    errors = Column(Integer, default=0)
    last_error_message = Column(String, nullable=True)
    status = Column(String(20), default="idle")  # idle, running, error, completed
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


def update_ingestion_status(
    db: Session,
    source: str,
    *,
    records: int = 0,
    error: str | None = None,
    status: str | None = None,
) -> IngestionStatus:
    """Update or create ingestion status for a source.

    Args:
        db: Active SQLAlchemy session.
        source: Source identifier (e.g. "aisstream", "digitraffic").
        records: Number of records ingested in this run.
        error: Error message if the run failed.
        status: Explicit status override. If None, derived from error presence.

    Returns:
        The updated IngestionStatus record.
    """
    row = db.query(IngestionStatus).filter(IngestionStatus.source == source).first()
    now = datetime.now(timezone.utc)

    if row is None:
        row = IngestionStatus(source=source)
        db.add(row)

    row.last_run_utc = now
    row.records_ingested = records
    row.updated_at = now

    if error:
        row.errors = (row.errors or 0) + 1
        row.last_error_message = error[:2000] if error else None
        row.status = status or "error"
    else:
        row.last_success_utc = now
        row.last_error_message = None
        row.status = status or "completed"

    db.flush()
    return row
