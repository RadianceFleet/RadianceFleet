"""Bulk export subscription management endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api._helpers import _audit_log, limiter
from app.auth import require_senior_or_admin
from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class ExportSubscriptionCreate(BaseModel):
    name: str
    schedule: str  # daily/weekly/monthly
    schedule_day: int | None = None
    schedule_hour_utc: int = 6
    export_type: str  # alerts/vessels/ais_positions/evidence_cards
    filter_json: dict | None = None
    columns_json: list[str] | None = None
    format: str  # csv/json/parquet
    delivery_method: str  # email/s3/webhook
    delivery_config_json: dict | None = None


class ExportSubscriptionUpdate(BaseModel):
    name: str | None = None
    schedule: str | None = None
    schedule_day: int | None = None
    schedule_hour_utc: int | None = None
    export_type: str | None = None
    filter_json: dict | None = None
    columns_json: list[str] | None = None
    format: str | None = None
    delivery_method: str | None = None
    delivery_config_json: dict | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_VALID_SCHEDULES = {"daily", "weekly", "monthly"}
_VALID_EXPORT_TYPES = {"alerts", "vessels", "ais_positions", "evidence_cards"}
_VALID_FORMATS = {"csv", "json", "parquet"}
_VALID_DELIVERY_METHODS = {"email", "s3", "webhook"}


def _validate_subscription_fields(
    schedule: str | None = None,
    export_type: str | None = None,
    fmt: str | None = None,
    delivery_method: str | None = None,
) -> None:
    if schedule is not None and schedule not in _VALID_SCHEDULES:
        raise HTTPException(400, f"schedule must be one of {_VALID_SCHEDULES}")
    if export_type is not None and export_type not in _VALID_EXPORT_TYPES:
        raise HTTPException(400, f"export_type must be one of {_VALID_EXPORT_TYPES}")
    if fmt is not None and fmt not in _VALID_FORMATS:
        raise HTTPException(400, f"format must be one of {_VALID_FORMATS}")
    if delivery_method is not None and delivery_method not in _VALID_DELIVERY_METHODS:
        raise HTTPException(400, f"delivery_method must be one of {_VALID_DELIVERY_METHODS}")


def _subscription_to_dict(sub) -> dict:
    """Serialize subscription to dict, masking S3 credentials."""
    delivery_config = dict(sub.delivery_config_json) if sub.delivery_config_json else None
    if delivery_config:
        # Mask sensitive fields
        for key in ("aws_access_key_id", "aws_secret_access_key", "secret"):
            if key in delivery_config:
                delivery_config[key] = "***"

    return {
        "subscription_id": sub.subscription_id,
        "name": sub.name,
        "created_by": sub.created_by,
        "schedule": sub.schedule,
        "schedule_day": sub.schedule_day,
        "schedule_hour_utc": sub.schedule_hour_utc,
        "export_type": sub.export_type,
        "filter_json": sub.filter_json,
        "columns_json": sub.columns_json,
        "format": sub.format,
        "delivery_method": sub.delivery_method,
        "delivery_config_json": delivery_config,
        "is_active": sub.is_active,
        "last_run_at": sub.last_run_at.isoformat() if sub.last_run_at else None,
        "last_run_status": sub.last_run_status,
        "last_run_rows": sub.last_run_rows,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


def _run_to_dict(run) -> dict:
    return {
        "run_id": run.run_id,
        "subscription_id": run.subscription_id,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "status": run.status,
        "row_count": run.row_count,
        "file_size_bytes": run.file_size_bytes,
        "delivery_status": run.delivery_status,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/admin/export-subscriptions", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def list_export_subscriptions(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """List all export subscriptions with pagination."""
    from app.models.export_subscription import ExportSubscription

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(503, "Export subscriptions feature is disabled")

    q = db.query(ExportSubscription).order_by(ExportSubscription.subscription_id.desc())
    total = q.count()
    subs = q.offset(skip).limit(limit).all()
    return {
        "total": total,
        "subscriptions": [_subscription_to_dict(s) for s in subs],
    }


@router.post("/admin/export-subscriptions", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_export_subscription(
    request: Request,
    body: ExportSubscriptionCreate,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Create a new export subscription."""
    from app.models.export_subscription import ExportSubscription

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(503, "Export subscriptions feature is disabled")

    _validate_subscription_fields(body.schedule, body.export_type, body.format, body.delivery_method)

    sub = ExportSubscription(
        name=body.name,
        created_by=auth["analyst_id"],
        schedule=body.schedule,
        schedule_day=body.schedule_day,
        schedule_hour_utc=body.schedule_hour_utc,
        export_type=body.export_type,
        filter_json=body.filter_json,
        columns_json=body.columns_json,
        format=body.format,
        delivery_method=body.delivery_method,
        delivery_config_json=body.delivery_config_json,
        is_active=True,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    _audit_log(
        db,
        "export_subscription_create",
        "export_subscription",
        sub.subscription_id,
        {"name": body.name, "export_type": body.export_type},
        request,
        analyst_id=auth["analyst_id"],
    )
    db.commit()

    return _subscription_to_dict(sub)


@router.get("/admin/export-subscriptions/{subscription_id}", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def get_export_subscription(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Get export subscription details."""
    from app.models.export_subscription import ExportSubscription

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(503, "Export subscriptions feature is disabled")

    sub = (
        db.query(ExportSubscription)
        .filter(ExportSubscription.subscription_id == subscription_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "Export subscription not found")
    return _subscription_to_dict(sub)


@router.put("/admin/export-subscriptions/{subscription_id}", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def update_export_subscription(
    subscription_id: int,
    body: ExportSubscriptionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Update an export subscription."""
    from app.models.export_subscription import ExportSubscription

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(503, "Export subscriptions feature is disabled")

    sub = (
        db.query(ExportSubscription)
        .filter(ExportSubscription.subscription_id == subscription_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "Export subscription not found")

    _validate_subscription_fields(body.schedule, body.export_type, body.format, body.delivery_method)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(sub, field, value)

    db.commit()
    db.refresh(sub)

    _audit_log(
        db,
        "export_subscription_update",
        "export_subscription",
        subscription_id,
        {"fields_updated": list(update_data.keys())},
        request,
        analyst_id=auth["analyst_id"],
    )
    db.commit()

    return _subscription_to_dict(sub)


@router.delete("/admin/export-subscriptions/{subscription_id}", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def delete_export_subscription(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Delete an export subscription (soft delete — deactivate)."""
    from app.models.export_subscription import ExportSubscription

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(503, "Export subscriptions feature is disabled")

    sub = (
        db.query(ExportSubscription)
        .filter(ExportSubscription.subscription_id == subscription_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "Export subscription not found")

    sub.is_active = False
    db.commit()

    _audit_log(
        db,
        "export_subscription_delete",
        "export_subscription",
        subscription_id,
        {"name": sub.name},
        request,
        analyst_id=auth["analyst_id"],
    )
    db.commit()

    return {"status": "deleted", "subscription_id": subscription_id}


@router.post("/admin/export-subscriptions/{subscription_id}/run", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def trigger_export_run(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    auth: dict = Depends(require_senior_or_admin),
):
    """Manually trigger an export run for a subscription."""
    from app.models.export_run import ExportRun
    from app.models.export_subscription import ExportSubscription
    from app.modules.export_delivery import deliver
    from app.modules.export_engine import generate_export

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(503, "Export subscriptions feature is disabled")

    sub = (
        db.query(ExportSubscription)
        .filter(ExportSubscription.subscription_id == subscription_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "Export subscription not found")

    now = datetime.now(UTC)
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
        else:
            run.status = "failed"
            run.error_message = delivery_result.get("error")

        run.finished_at = datetime.now(UTC)
        sub.last_run_at = now
        sub.last_run_status = run.status
        sub.last_run_rows = row_count

        db.commit()

        _audit_log(
            db,
            "export_run_manual",
            "export_subscription",
            subscription_id,
            {"run_id": run.run_id, "status": run.status, "row_count": row_count},
            request,
            analyst_id=auth["analyst_id"],
        )
        db.commit()

        return _run_to_dict(run)

    except Exception as e:
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        run.error_message = str(e)
        sub.last_run_at = now
        sub.last_run_status = "failed"
        db.commit()
        raise HTTPException(500, f"Export failed: {str(e)}") from e


@router.get("/admin/export-subscriptions/{subscription_id}/runs", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def list_export_runs(
    subscription_id: int,
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """List run history for an export subscription."""
    from app.models.export_run import ExportRun
    from app.models.export_subscription import ExportSubscription

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(503, "Export subscriptions feature is disabled")

    sub = (
        db.query(ExportSubscription)
        .filter(ExportSubscription.subscription_id == subscription_id)
        .first()
    )
    if not sub:
        raise HTTPException(404, "Export subscription not found")

    q = (
        db.query(ExportRun)
        .filter(ExportRun.subscription_id == subscription_id)
        .order_by(ExportRun.run_id.desc())
    )
    total = q.count()
    runs = q.offset(skip).limit(limit).all()
    return {
        "total": total,
        "runs": [_run_to_dict(r) for r in runs],
    }


@router.get("/admin/export-runs/{run_id}/download", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def download_export_run(
    run_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _auth: dict = Depends(require_senior_or_admin),
):
    """Download the exported file for a specific run."""
    from app.models.export_run import ExportRun

    if not settings.EXPORT_SUBSCRIPTIONS_ENABLED:
        raise HTTPException(503, "Export subscriptions feature is disabled")

    run = db.query(ExportRun).filter(ExportRun.run_id == run_id).first()
    if not run:
        raise HTTPException(404, "Export run not found")

    if not run.file_path:
        raise HTTPException(404, "No file available for this run")

    file_path = Path(run.file_path)
    if not file_path.exists():
        raise HTTPException(404, "Export file has been deleted or expired")

    # Determine media type
    media_type = "application/octet-stream"
    if file_path.suffix == ".csv":
        media_type = "text/csv"
    elif file_path.suffix == ".json":
        media_type = "application/json"
    elif file_path.suffix == ".parquet":
        media_type = "application/vnd.apache.parquet"

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_path.name,
    )
