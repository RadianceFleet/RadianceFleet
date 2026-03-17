"""AIS data archival — compress old AIS points to Parquet before deletion."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_archive_batch import AisArchiveBatch

logger = logging.getLogger(__name__)

# Column list matching AISPoint model for Parquet schema
_AIS_POINT_COLUMNS = [
    "ais_point_id",
    "vessel_id",
    "timestamp_utc",
    "lat",
    "lon",
    "sog",
    "cog",
    "heading",
    "nav_status",
    "ais_class",
    "sog_delta",
    "cog_delta",
    "source",
    "raw_payload_ref",
    "draught",
    "destination",
    "ingested_at",
    "source_timestamp_utc",
]


def _get_protected_point_ids(db: Session) -> set[int]:
    """Return AIS point IDs referenced by gap events (FK-protected)."""
    from app.models.gap_event import AISGapEvent

    referenced = db.execute(
        select(AISGapEvent.start_point_id, AISGapEvent.end_point_id).where(
            or_(
                AISGapEvent.start_point_id.isnot(None),
                AISGapEvent.end_point_id.isnot(None),
            )
        )
    ).fetchall()
    protected: set[int] = set()
    for row in referenced:
        if row[0]:
            protected.add(row[0])
        if row[1]:
            protected.add(row[1])
    return protected


def _compute_sha256(file_path: str | Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _points_to_dataframe(points: list) -> pl.DataFrame:
    """Convert list of AISPoint ORM objects to a Polars DataFrame."""
    rows = []
    for p in points:
        row = {}
        for col in _AIS_POINT_COLUMNS:
            val = getattr(p, col, None)
            # Convert enums to string
            if hasattr(val, "value"):
                val = val.value
            row[col] = val
        rows.append(row)
    return pl.DataFrame(rows)


def archive_old_points(
    db: Session,
    cutoff_date: datetime,
    source: str | None = None,
) -> AisArchiveBatch:
    """Archive AIS points older than cutoff_date to compressed Parquet.

    Args:
        db: Database session.
        cutoff_date: Points with timestamp_utc < cutoff_date are archived.
        source: Optional source filter (only archive points from this source).

    Returns:
        AisArchiveBatch record describing the archive.
    """
    from app.models.ais_archive_batch import AisArchiveBatch
    from app.models.ais_point import AISPoint

    protected_ids = _get_protected_point_ids(db)
    batch_size = getattr(settings, "ARCHIVE_BATCH_SIZE", 50000)

    # Build base query
    q = db.query(AISPoint).filter(AISPoint.timestamp_utc < cutoff_date)
    if source:
        q = q.filter(AISPoint.source == source)
    if protected_ids:
        q = q.filter(AISPoint.ais_point_id.notin_(protected_ids))

    # Order for deterministic batching
    q = q.order_by(AISPoint.timestamp_utc)

    all_points = []
    offset = 0
    while True:
        batch = q.limit(batch_size).offset(offset).all()
        if not batch:
            break
        all_points.extend(batch)
        offset += len(batch)

    if not all_points:
        # Create a record indicating empty archive
        now = datetime.now(UTC)
        batch_record = AisArchiveBatch(
            archive_date=now,
            date_range_start=cutoff_date,
            date_range_end=cutoff_date,
            row_count=0,
            file_path="",
            file_size_bytes=0,
            compression=getattr(settings, "ARCHIVE_COMPRESSION", "gzip"),
            checksum_sha256="",
            status="completed",
            source_filter=source,
            created_at=now,
        )
        db.add(batch_record)
        db.commit()
        return batch_record

    # Convert to DataFrame
    df = _points_to_dataframe(all_points)

    # Determine date range
    date_range_start = min(p.timestamp_utc for p in all_points)
    date_range_end = max(p.timestamp_utc for p in all_points)

    # Create batch record first to get batch_id
    now = datetime.now(UTC)
    compression = getattr(settings, "ARCHIVE_COMPRESSION", "gzip")
    batch_record = AisArchiveBatch(
        archive_date=now,
        date_range_start=date_range_start,
        date_range_end=date_range_end,
        row_count=len(all_points),
        file_path="",  # will update after write
        file_size_bytes=0,
        compression=compression,
        checksum_sha256="",
        status="completed",
        source_filter=source,
        created_at=now,
    )
    db.add(batch_record)
    db.flush()  # get batch_id

    # Build file path
    storage_dir = getattr(settings, "ARCHIVE_STORAGE_DIR", "data/archives")
    year = date_range_start.strftime("%Y")
    month = date_range_start.strftime("%m")
    archive_dir = Path(storage_dir) / "ais_points" / year / month
    archive_dir.mkdir(parents=True, exist_ok=True)

    ext = ".parquet.gz" if compression == "gzip" else ".parquet.zst"
    file_name = f"batch_{batch_record.batch_id}{ext}"
    file_path = archive_dir / file_name

    # Write Parquet
    if compression == "gzip":
        df.write_parquet(str(file_path), compression="gzip")
    else:
        df.write_parquet(str(file_path), compression="zstd")

    # Compute checksum and file size
    checksum = _compute_sha256(file_path)
    file_size = os.path.getsize(file_path)

    batch_record.file_path = str(file_path)
    batch_record.file_size_bytes = file_size
    batch_record.checksum_sha256 = checksum

    # Delete archived points from DB
    point_ids = [p.ais_point_id for p in all_points]
    # Delete in batches to avoid huge IN clauses
    for i in range(0, len(point_ids), batch_size):
        chunk = point_ids[i : i + batch_size]
        db.query(AISPoint).filter(AISPoint.ais_point_id.in_(chunk)).delete(
            synchronize_session=False
        )

    db.commit()

    logger.info(
        "Archived %d AIS points to %s (%d bytes, SHA-256=%s)",
        len(all_points),
        file_path,
        file_size,
        checksum[:16] + "...",
    )
    return batch_record


def restore_archive_batch(db: Session, batch_id: int) -> int:
    """Restore an archived batch back into the database.

    Uses dialect-aware upsert: SQLite INSERT OR IGNORE, PostgreSQL ON CONFLICT DO NOTHING.

    Args:
        db: Database session.
        batch_id: ID of the AisArchiveBatch to restore.

    Returns:
        Number of rows restored.
    """
    from app.models.ais_archive_batch import AisArchiveBatch
    from app.models.ais_point import AISPoint

    batch = db.query(AisArchiveBatch).filter(AisArchiveBatch.batch_id == batch_id).first()
    if not batch:
        raise ValueError(f"Archive batch {batch_id} not found")
    if not batch.file_path or not Path(batch.file_path).exists():
        raise FileNotFoundError(f"Archive file not found: {batch.file_path}")

    df = pl.read_parquet(batch.file_path)
    if df.is_empty():
        batch.status = "restored"
        db.commit()
        return 0

    rows = df.to_dicts()

    # Pre-deduplicate: find existing point IDs
    existing_ids_rows = db.execute(
        select(AISPoint.ais_point_id).where(
            AISPoint.ais_point_id.in_([r["ais_point_id"] for r in rows])
        )
    ).fetchall()
    existing_ids = {row[0] for row in existing_ids_rows}

    new_rows = [r for r in rows if r["ais_point_id"] not in existing_ids]

    if not new_rows:
        batch.status = "restored"
        db.commit()
        return 0

    # Detect dialect
    dialect_name = db.bind.dialect.name if db.bind else "sqlite"

    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(AISPoint.__table__).values(new_rows).on_conflict_do_nothing()
        db.execute(stmt)
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        # Insert in chunks
        chunk_size = 500
        for i in range(0, len(new_rows), chunk_size):
            chunk = new_rows[i : i + chunk_size]
            stmt = sqlite_insert(AISPoint.__table__).values(chunk).on_conflict_do_nothing()
            db.execute(stmt)

    batch.status = "restored"
    db.commit()

    restored_count = len(new_rows)
    logger.info("Restored %d rows from archive batch %d", restored_count, batch_id)
    return restored_count


def verify_archive_integrity(batch) -> bool:
    """Verify the SHA-256 checksum of an archive file.

    Args:
        batch: AisArchiveBatch record.

    Returns:
        True if checksum matches, False otherwise.
    """
    if not batch.file_path or not Path(batch.file_path).exists():
        return False
    actual = _compute_sha256(batch.file_path)
    return actual == batch.checksum_sha256


def cleanup_expired_archives(db: Session, max_age_days: int) -> int:
    """Delete archive files and records older than max_age_days.

    Args:
        db: Database session.
        max_age_days: Maximum age in days.

    Returns:
        Number of archives deleted.
    """
    from app.models.ais_archive_batch import AisArchiveBatch

    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    expired = (
        db.query(AisArchiveBatch)
        .filter(AisArchiveBatch.archive_date < cutoff)
        .all()
    )

    deleted = 0
    for batch in expired:
        # Remove file
        if batch.file_path:
            p = Path(batch.file_path)
            if p.exists():
                p.unlink()
        db.delete(batch)
        deleted += 1

    if deleted:
        db.commit()
        logger.info("Cleaned up %d expired archive batches", deleted)
    return deleted


def get_retention_stats(db: Session) -> dict:
    """Return retention and archival statistics.

    Returns:
        Dict with db_size_estimate, ais_point_count, archive_count,
        total_archived_rows, total_archive_size_bytes.
    """
    from app.models.ais_archive_batch import AisArchiveBatch
    from app.models.ais_point import AISPoint

    ais_count = db.query(AISPoint).count()

    archive_batches = db.query(AisArchiveBatch).all()
    archive_count = len(archive_batches)
    total_archived_rows = sum(b.row_count for b in archive_batches)
    total_archive_size = sum(b.file_size_bytes for b in archive_batches)

    # DB size estimate (SQLite specific, fallback for others)
    db_size = None
    try:
        dialect_name = db.bind.dialect.name if db.bind else "sqlite"
        if dialect_name == "sqlite":
            result = db.execute(text("PRAGMA page_count")).scalar()
            page_size = db.execute(text("PRAGMA page_size")).scalar()
            if result and page_size:
                db_size = result * page_size
    except Exception:  # noqa: S110
        pass

    return {
        "db_size_bytes": db_size,
        "ais_point_count": ais_count,
        "archive_count": archive_count,
        "total_archived_rows": total_archived_rows,
        "total_archive_size_bytes": total_archive_size,
    }
