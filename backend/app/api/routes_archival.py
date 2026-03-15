"""Data retention and archival endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import require_senior_or_admin
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


@router.get("/admin/archives")
def list_archive_batches(
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
):
    """List archive batches with pagination."""
    from app.models.ais_archive_batch import AisArchiveBatch

    q = db.query(AisArchiveBatch)
    if status:
        q = q.filter(AisArchiveBatch.status == status)
    total = q.count()
    batches = q.order_by(AisArchiveBatch.archive_date.desc()).offset(offset).limit(limit).all()
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "batches": [
            {
                "batch_id": b.batch_id,
                "archive_date": b.archive_date.isoformat() if b.archive_date else None,
                "date_range_start": b.date_range_start.isoformat() if b.date_range_start else None,
                "date_range_end": b.date_range_end.isoformat() if b.date_range_end else None,
                "row_count": b.row_count,
                "file_path": b.file_path,
                "file_size_bytes": b.file_size_bytes,
                "compression": b.compression,
                "checksum_sha256": b.checksum_sha256,
                "status": b.status,
                "source_filter": b.source_filter,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in batches
        ],
    }


@router.post("/admin/archives/run")
def run_archive(
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
    cutoff_days: int = Query(90, ge=1),
    source: str | None = Query(None),
):
    """Manually trigger archival of old AIS points."""
    from app.config import settings
    from app.modules.ais_archiver import archive_old_points

    if not getattr(settings, "ARCHIVE_ENABLED", True):
        raise HTTPException(status_code=400, detail="Archival is disabled")

    cutoff_date = datetime.now(UTC) - timedelta(days=cutoff_days)
    batch = archive_old_points(db, cutoff_date, source=source)
    return {
        "batch_id": batch.batch_id,
        "row_count": batch.row_count,
        "file_path": batch.file_path,
        "file_size_bytes": batch.file_size_bytes,
        "status": batch.status,
    }


@router.post("/admin/archives/{batch_id}/restore")
def restore_archive(
    batch_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Restore an archived batch back into the database."""
    from app.modules.ais_archiver import restore_archive_batch

    try:
        count = restore_archive_batch(db, batch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {"batch_id": batch_id, "restored_rows": count, "status": "restored"}


@router.get("/admin/archives/{batch_id}/verify")
def verify_archive(
    batch_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Verify the integrity of an archive file."""
    from app.models.ais_archive_batch import AisArchiveBatch
    from app.modules.ais_archiver import verify_archive_integrity

    batch = db.query(AisArchiveBatch).filter(AisArchiveBatch.batch_id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail=f"Archive batch {batch_id} not found")
    valid = verify_archive_integrity(batch)
    return {"batch_id": batch_id, "valid": valid, "checksum_sha256": batch.checksum_sha256}


@router.delete("/admin/archives/{batch_id}")
def delete_archive(
    batch_id: int,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Delete an archive file and its database record."""
    from pathlib import Path

    from app.models.ais_archive_batch import AisArchiveBatch

    batch = db.query(AisArchiveBatch).filter(AisArchiveBatch.batch_id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail=f"Archive batch {batch_id} not found")

    # Remove file
    if batch.file_path:
        p = Path(batch.file_path)
        if p.exists():
            p.unlink()

    db.delete(batch)
    db.commit()
    return {"batch_id": batch_id, "deleted": True}


@router.get("/admin/retention/stats")
def retention_stats(
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Get data retention and archival statistics."""
    from app.modules.ais_archiver import get_retention_stats

    return get_retention_stats(db)
