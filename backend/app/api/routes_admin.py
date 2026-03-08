"""Admin, audit, ingestion, tips, subscriptions, data coverage, API key, and webhook endpoints."""
from __future__ import annotations

import csv
import io
import logging
import secrets
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.auth import (
    create_admin_token, create_token, verify_admin_password, verify_password,
    hash_password, require_auth, require_admin as require_admin_role,
)
from app.api._helpers import _audit_log, _validate_date_range, _check_upload_size, limiter

logger = logging.getLogger(__name__)

router = APIRouter()


def _validate_csv_upload(file: UploadFile) -> None:
    """Validate that an uploaded file is a parseable CSV.

    Checks filename extension and reads the first line to verify UTF-8 encoding
    and comma/tab delimiters. Does NOT rely on Content-Type header.
    """
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must have .csv extension")
    # Read first chunk to validate encoding and format
    first_bytes = file.file.read(8192)
    file.file.seek(0)
    if not first_bytes:
        raise HTTPException(status_code=400, detail="File is empty")
    try:
        first_text = first_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text")
    # Check that first line has comma or tab delimiters (i.e., is tabular)
    first_line = first_text.split("\n", 1)[0]
    if "," not in first_line and "\t" not in first_line:
        raise HTTPException(status_code=400, detail="File does not appear to be CSV (no comma or tab delimiters)")
    # Verify CSV is parseable
    try:
        reader = csv.reader(io.StringIO(first_text))
        header = next(reader)
        if len(header) < 2:
            raise HTTPException(status_code=400, detail="CSV header must have at least 2 columns")
    except csv.Error:
        raise HTTPException(status_code=400, detail="File is not valid CSV")


# ---------------------------------------------------------------------------
# AIS Ingestion
# ---------------------------------------------------------------------------

@router.post("/ais/import", tags=["ingestion"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def import_ais(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Ingest AIS records from CSV. Persists ingestion status to database."""
    from app.modules.ingest import ingest_ais_csv
    from app.models.ingestion_status import update_ingestion_status

    _check_upload_size(file)
    _validate_csv_upload(file)

    try:
        result = ingest_ais_csv(file.file, db)
        update_ingestion_status(db, "csv_import", records=result.get("accepted", 0))
        _audit_log(db, "ais_import", "ingestion", details={
            "file_name": file.filename,
            "accepted": result.get("accepted", 0),
            "rejected": result.get("rejected", 0),
        }, request=request)
        db.commit()
        return result
    except Exception as e:
        try:
            update_ingestion_status(db, "csv_import", error=str(e))
            db.commit()
        except Exception:
            logger.exception("Failed to record ingestion error status")
            db.rollback()
        raise


@router.get("/ingestion-status", tags=["ingestion"])
def ingestion_status(db: Session = Depends(get_db)):
    """Return ingestion status for all sources (persisted in database)."""
    from app.models.ingestion_status import IngestionStatus

    try:
        rows = db.query(IngestionStatus).order_by(IngestionStatus.updated_at.desc()).all()
    except Exception as e:
        logger.debug("Ingestion status query failed (table may not exist): %s", e)
        return {"status": "idle", "sources": []}
    if not rows:
        return {"status": "idle", "sources": []}
    return {
        "status": "ok",
        "sources": [
            {
                "source": r.source,
                "status": r.status,
                "last_run_utc": r.last_run_utc.isoformat() if r.last_run_utc else None,
                "last_success_utc": r.last_success_utc.isoformat() if r.last_success_utc else None,
                "records_ingested": r.records_ingested,
                "errors": r.errors,
                "last_error_message": r.last_error_message,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }



@router.post("/gfw/import", tags=["ingestion"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
async def import_gfw_detections(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Import pre-computed GFW vessel detection CSV (FR8)."""
    _check_upload_size(file)
    _validate_csv_upload(file)
    from app.modules.gfw_import import ingest_gfw_csv
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        return ingest_gfw_csv(db, tmp_path)
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------

@router.get("/audit-log", tags=["admin"])
def list_audit_logs(
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """List audit log entries (PRD NFR5)."""
    from app.models.audit_log import AuditLog
    q = db.query(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        q = q.filter(AuditLog.action == action)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    total = q.count()
    logs = q.offset(skip).limit(limit).all()
    return {
        "total": total,
        "logs": [
            {
                "audit_id": l.audit_id,
                "action": l.action,
                "entity_type": l.entity_type,
                "entity_id": l.entity_id,
                "details": l.details,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ],
    }


# ---------------------------------------------------------------------------
# Admin Auth
# ---------------------------------------------------------------------------

@router.post("/admin/login", tags=["admin"])
@limiter.limit("5/hour")
def admin_login(request: Request, body: dict, db: Session = Depends(get_db)):
    """Rate-limited login. Supports analyst DB login (username+password) and legacy admin login."""
    from app.schemas.analyst import AnalystLoginRequest, AnalystLoginResponse, AnalystRead
    from app.models.analyst import Analyst
    from datetime import datetime as _dt, timezone as _tz

    parsed = AnalystLoginRequest(**body)

    if parsed.username:
        # DB-backed analyst login
        analyst = db.query(Analyst).filter(Analyst.username == parsed.username).first()
        if not analyst or not analyst.is_active:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not verify_password(parsed.password, analyst.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        analyst.last_login_at = _dt.now(_tz.utc)
        db.commit()
        role_val = analyst.role.value if hasattr(analyst.role, "value") else str(analyst.role)
        token = create_token(analyst.analyst_id, analyst.username, role_val)
        return AnalystLoginResponse(
            token=token,
            analyst=AnalystRead.model_validate(analyst),
        ).model_dump()
    else:
        # Legacy admin login (no username — uses ADMIN_PASSWORD env var)
        if not verify_admin_password(parsed.password):
            raise HTTPException(status_code=401, detail="Invalid password")
        return {"token": create_admin_token()}


@router.post("/admin/refresh", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def admin_refresh(request: Request, auth: dict = Depends(require_auth)):
    """Refresh JWT token (called silently by frontend)."""
    return {"token": create_token(auth["analyst_id"], auth["username"], auth["role"])}


# ---------------------------------------------------------------------------
# Analyst Management (admin-only)
# ---------------------------------------------------------------------------

@router.post("/admin/analysts", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_analyst(
    request: Request,
    body: dict,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: create a new analyst account."""
    from app.schemas.analyst import AnalystCreate, AnalystRead
    from app.models.analyst import Analyst
    from app.models.base import AnalystRoleEnum

    parsed = AnalystCreate(**body)
    valid_roles = {e.value for e in AnalystRoleEnum}
    if parsed.role not in valid_roles:
        raise HTTPException(status_code=422, detail=f"Invalid role. Must be one of: {sorted(valid_roles)}")

    existing = db.query(Analyst).filter(Analyst.username == parsed.username).first()
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    analyst = Analyst(
        username=parsed.username,
        display_name=parsed.display_name,
        password_hash=hash_password(parsed.password),
        role=parsed.role,
        is_active=True,
    )
    db.add(analyst)
    db.commit()
    db.refresh(analyst)
    return AnalystRead.model_validate(analyst).model_dump()


@router.get("/admin/analysts", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def list_analysts(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: list all analyst accounts."""
    from app.schemas.analyst import AnalystRead
    from app.models.analyst import Analyst

    analysts = db.query(Analyst).order_by(Analyst.analyst_id).all()
    return [AnalystRead.model_validate(a).model_dump() for a in analysts]


@router.patch("/admin/analysts/{analyst_id}", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def update_analyst(
    analyst_id: int,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: update analyst role, display_name, or is_active."""
    from app.models.analyst import Analyst
    from app.models.base import AnalystRoleEnum

    analyst = db.query(Analyst).filter(Analyst.analyst_id == analyst_id).first()
    if not analyst:
        raise HTTPException(status_code=404, detail="Analyst not found")

    if "role" in body:
        valid_roles = {e.value for e in AnalystRoleEnum}
        if body["role"] not in valid_roles:
            raise HTTPException(status_code=422, detail=f"Invalid role. Must be one of: {sorted(valid_roles)}")
        analyst.role = body["role"]
    if "display_name" in body:
        analyst.display_name = body["display_name"]
    if "is_active" in body:
        analyst.is_active = body["is_active"]

    db.commit()
    return {"status": "updated", "analyst_id": analyst_id}


@router.post("/admin/analysts/{analyst_id}/reset-password", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def reset_analyst_password(
    analyst_id: int,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: reset an analyst's password."""
    from app.models.analyst import Analyst

    analyst = db.query(Analyst).filter(Analyst.analyst_id == analyst_id).first()
    if not analyst:
        raise HTTPException(status_code=404, detail="Analyst not found")

    new_password = body.get("password")
    if not new_password:
        raise HTTPException(status_code=422, detail="password is required")

    analyst.password_hash = hash_password(new_password)
    db.commit()
    return {"status": "password_reset", "analyst_id": analyst_id}


# ---------------------------------------------------------------------------
# Validation Harness
# ---------------------------------------------------------------------------

@router.get("/admin/validate", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def admin_validate(
    request: Request,
    threshold_band: str = Query("high"),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Run validation harness against ground truth."""
    from app.modules.validation_harness import run_validation
    return run_validation(db, threshold_band=threshold_band)


@router.get("/admin/validate/signals", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def admin_validate_signals(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Signal effectiveness report — lift ratios for each risk signal."""
    from app.modules.validation_harness import signal_effectiveness_report
    return signal_effectiveness_report(db)


@router.get("/admin/validate/sweep", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def admin_validate_sweep(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Sweep score thresholds — precision/recall/F2 at each threshold."""
    from app.modules.validation_harness import sweep_thresholds
    return sweep_thresholds(db)


@router.get("/admin/validate/analyst-metrics", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def admin_analyst_metrics(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Analyst feedback metrics — FP rates by score band and corridor."""
    from app.modules.validation_harness import analyst_feedback_metrics
    return analyst_feedback_metrics(db)


@router.post("/admin/purge-observations", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def admin_purge_observations(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: purge AIS observations older than the configured retention window."""
    from app.models.ais_observation import AISObservation
    deleted = AISObservation.purge_old(db)
    db.commit()
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Tip Submission
# ---------------------------------------------------------------------------

class TipRequest(BaseModel):
    mmsi: str
    imo: Optional[str] = None
    behavior_type: str
    detail_text: str
    source_url: Optional[str] = None
    submitter_email: Optional[str] = None
    website: Optional[str] = None  # honeypot — reject if non-empty


@router.post("/tips/vessel", tags=["public"])
@limiter.limit("3/hour")
def submit_tip(request: Request, body: TipRequest, db: Session = Depends(get_db)):
    """Public tip submission. Rate-limited 3/hour per IP."""
    from app.models.tip_submission import TipSubmission, validate_source_url
    if body.website:
        raise HTTPException(status_code=422, detail="Invalid submission")
    if len(body.detail_text) < 50:
        raise HTTPException(status_code=422, detail="detail_text must be at least 50 characters")
    if len(body.detail_text) > 500:
        raise HTTPException(status_code=422, detail="detail_text must be 500 characters or less")
    valid_types = {"AIS_MANIPULATION", "DARK_PERIOD", "SUSPICIOUS_STS", "FLAG_CHANGE", "OTHER"}
    if body.behavior_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"behavior_type must be one of {valid_types}")
    try:
        url = validate_source_url(body.source_url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    tip = TipSubmission(
        mmsi=body.mmsi,
        imo=body.imo,
        behavior_type=body.behavior_type,
        detail_text=body.detail_text,
        source_url=url,
        submitter_email=body.submitter_email,
        status="PENDING",
        submitter_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(tip)
    db.commit()
    return {"status": "received", "message": "Thank you. Analysts will review your tip."}


@router.get("/admin/tips", tags=["admin"])
def get_tips(
    request: Request,
    status: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: list tip submissions, paginated."""
    from app.models.tip_submission import TipSubmission
    from sqlalchemy import select
    q = select(TipSubmission)
    if status:
        q = q.where(TipSubmission.status == status.upper())
    q = q.order_by(TipSubmission.created_at.desc()).offset(offset).limit(limit)
    tips = db.execute(q).scalars().all()
    return [
        {
            "id": t.id, "mmsi": t.mmsi, "imo": t.imo, "behavior_type": t.behavior_type,
            "detail_text": t.detail_text, "source_url": t.source_url,
            "submitter_email": t.submitter_email, "status": t.status,
            "submitter_ip": t.submitter_ip, "created_at": str(t.created_at),
            "analyst_note": t.analyst_note,
        }
        for t in tips
    ]


class TipUpdateRequest(BaseModel):
    status: Optional[str] = None
    analyst_note: Optional[str] = None


@router.patch("/admin/tips/{tip_id}", tags=["admin"])
def update_tip(
    tip_id: int,
    body: TipUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: update tip status and/or analyst note."""
    from app.models.tip_submission import TipSubmission
    tip = db.query(TipSubmission).filter(TipSubmission.id == tip_id).first()
    if not tip:
        raise HTTPException(status_code=404, detail="Tip not found")
    if body.status is not None:
        tip.status = body.status.upper()
    if body.analyst_note is not None:
        tip.analyst_note = body.analyst_note
    db.commit()
    return {"status": "updated", "id": tip_id}


# ---------------------------------------------------------------------------
# Email Subscriptions
# ---------------------------------------------------------------------------

class SubscribeRequest(BaseModel):
    email: str
    mmsi: Optional[str] = None
    corridor_id: Optional[int] = None
    alert_type: Optional[str] = None


class ResendRequest(BaseModel):
    email: str


@router.post("/subscribe", tags=["public"])
@limiter.limit("5/hour")
def subscribe(request: Request, body: SubscribeRequest, db: Session = Depends(get_db)):
    """Public: subscribe to vessel/corridor email alerts (double opt-in)."""
    from app.models.alert_subscription import AlertSubscription, generate_subscription_token
    from app.modules.email_notifier import send_confirmation_email
    if not settings.ADMIN_JWT_SECRET:
        raise HTTPException(status_code=503, detail="Email subscriptions not configured")
    from sqlalchemy import select
    existing = db.execute(
        select(AlertSubscription).where(
            AlertSubscription.email == body.email,
            AlertSubscription.mmsi == body.mmsi,
            AlertSubscription.corridor_id == body.corridor_id,
        )
    ).scalar_one_or_none()
    if existing and existing.confirmed:
        return {"status": "already_confirmed"}
    token = generate_subscription_token(body.email, settings.ADMIN_JWT_SECRET)
    if existing:
        existing.token = token
    else:
        sub = AlertSubscription(
            email=body.email,
            mmsi=body.mmsi,
            corridor_id=body.corridor_id,
            alert_type=body.alert_type,
            token=token,
            consent_ip=request.client.host if request.client else None,
            consent_timestamp=datetime.now(timezone.utc),
        )
        db.add(sub)
    db.commit()
    confirm_url = f"{settings.PUBLIC_URL}/api/v1/subscribe/confirm?token={token}&email={body.email}"
    send_confirmation_email(body.email, confirm_url)
    return {"status": "pending_confirmation"}


@router.post("/subscribe/resend", tags=["public"])
@limiter.limit("3/hour")
def resend_confirmation(request: Request, body: ResendRequest, db: Session = Depends(get_db)):
    """Resend confirmation email (rate-limited)."""
    from app.models.alert_subscription import AlertSubscription, generate_subscription_token
    from app.modules.email_notifier import send_confirmation_email
    from sqlalchemy import select
    if not settings.ADMIN_JWT_SECRET:
        raise HTTPException(status_code=503, detail="Email subscriptions not configured")
    sub = db.execute(
        select(AlertSubscription).where(AlertSubscription.email == body.email, AlertSubscription.confirmed == False)
    ).scalar_one_or_none()
    if not sub:
        return {"status": "not_found"}
    token = generate_subscription_token(body.email, settings.ADMIN_JWT_SECRET)
    sub.token = token
    db.commit()
    confirm_url = f"{settings.PUBLIC_URL}/api/v1/subscribe/confirm?token={token}&email={body.email}"
    send_confirmation_email(body.email, confirm_url)
    return {"status": "resent"}


@router.get("/unsubscribe", tags=["public"])
def unsubscribe(token: str = Query(...), email: str = Query(...), db: Session = Depends(get_db)):
    """One-click unsubscribe via token link."""
    from app.models.alert_subscription import AlertSubscription, verify_subscription_token
    from sqlalchemy import select
    if not settings.ADMIN_JWT_SECRET:
        raise HTTPException(status_code=503, detail="Not configured")
    if not verify_subscription_token(token, email, settings.ADMIN_JWT_SECRET):
        raise HTTPException(status_code=400, detail="Invalid or expired unsubscribe token")
    subs = db.execute(select(AlertSubscription).where(AlertSubscription.email == email)).scalars().all()
    for s in subs:
        db.delete(s)
    db.commit()
    return {"status": "unsubscribed", "email": email}


@router.get("/subscribe/confirm", tags=["public"])
def confirm_subscription(token: str = Query(...), email: str = Query(...), db: Session = Depends(get_db)):
    """Confirm email subscription via token link."""
    from app.models.alert_subscription import AlertSubscription, verify_subscription_token
    from sqlalchemy import select
    if not settings.ADMIN_JWT_SECRET:
        raise HTTPException(status_code=503, detail="Not configured")
    if not verify_subscription_token(token, email, settings.ADMIN_JWT_SECRET):
        raise HTTPException(status_code=400, detail="Invalid or expired confirmation token")
    sub = db.execute(
        select(AlertSubscription).where(AlertSubscription.email == email, AlertSubscription.token == token)
    ).scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    sub.confirmed = True
    db.commit()
    return {"status": "confirmed", "email": email}


# ---------------------------------------------------------------------------
# Data Coverage
# ---------------------------------------------------------------------------

@router.get("/data/coverage", tags=["data"])
def get_data_coverage(db: Session = Depends(get_db)):
    """Per-source data coverage summary."""
    try:
        from app.modules.coverage_tracker import coverage_summary
        return coverage_summary(db)
    except ImportError:
        return {"status": "coverage_tracker not available", "sources": []}
    except Exception as e:
        return {"status": f"error: {str(e)}", "sources": []}


@router.get("/data/coverage/gaps", tags=["data"])
def get_coverage_gaps(
    source: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Find uncovered date ranges for a data source."""
    try:
        from app.modules.coverage_tracker import find_coverage_gaps
        return find_coverage_gaps(db, source=source)
    except ImportError:
        return {"status": "coverage_tracker not available", "gaps": []}
    except Exception as e:
        return {"status": f"error: {str(e)}", "gaps": []}


@router.post("/data/backfill", tags=["data"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def trigger_backfill(
    request: Request,
    source: str = Query(..., description="Data source name"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Trigger manual backfill for a data source and date range."""
    _validate_date_range(date_from, date_to)
    try:
        from app.modules.coverage_tracker import trigger_backfill as _do_backfill
        result = _do_backfill(db, source=source, date_from=date_from, date_to=date_to)
        _audit_log(db, "backfill_trigger", "data_source", details={
            "source": source,
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
        }, request=request)
        db.commit()
        return result
    except ImportError:
        return {"status": "coverage_tracker not available"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backfill failed: {str(e)}")


# ---------------------------------------------------------------------------
# API Key Management
# ---------------------------------------------------------------------------

class ApiKeyCreateRequest(BaseModel):
    name: str


@router.post("/admin/api-keys", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_api_key(
    request: Request,
    body: ApiKeyCreateRequest,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: create a new read-only API key. Returns the raw key ONCE."""
    from app.models.api_key import ApiKey

    raw_key = secrets.token_hex(32)
    hashed = hash_password(raw_key)
    api_key = ApiKey(
        key_hash=hashed,
        name=body.name,
        scope="read_only",
        rate_limit="30/minute",
        created_by=_admin["analyst_id"],
        is_active=True,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)
    _audit_log(db, "api_key_create", "api_key", api_key.key_id,
               {"name": body.name}, request, analyst_id=_admin["analyst_id"])
    db.commit()
    return {
        "key_id": api_key.key_id,
        "name": api_key.name,
        "scope": api_key.scope,
        "rate_limit": api_key.rate_limit,
        "created_at": api_key.created_at.isoformat() if api_key.created_at else None,
        "raw_key": raw_key,
    }


@router.get("/admin/api-keys", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def list_api_keys(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: list all API keys (without hashes)."""
    from app.models.api_key import ApiKey

    keys = db.query(ApiKey).order_by(ApiKey.key_id).all()
    return [
        {
            "key_id": k.key_id,
            "name": k.name,
            "scope": k.scope,
            "rate_limit": k.rate_limit,
            "is_active": k.is_active,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "created_by": k.created_by,
        }
        for k in keys
    ]


@router.delete("/admin/api-keys/{key_id}", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def deactivate_api_key(
    key_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: deactivate an API key (soft delete)."""
    from app.models.api_key import ApiKey

    api_key = db.query(ApiKey).filter(ApiKey.key_id == key_id).first()
    if not api_key:
        raise HTTPException(status_code=404, detail="API key not found")
    api_key.is_active = False
    _audit_log(db, "api_key_deactivate", "api_key", key_id,
               {"name": api_key.name}, request, analyst_id=_admin["analyst_id"])
    db.commit()
    return {"status": "deactivated", "key_id": key_id}


# ---------------------------------------------------------------------------
# Webhook Management
# ---------------------------------------------------------------------------

class WebhookCreateRequest(BaseModel):
    url: str
    events: Optional[str] = "critical_alert"
    secret: Optional[str] = None


@router.post("/admin/webhooks", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def create_webhook(
    request: Request,
    body: WebhookCreateRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin_role),
):
    """Admin: register a new webhook endpoint."""
    from app.models.webhook import Webhook

    wh = Webhook(
        url=body.url,
        events=body.events,
        secret=body.secret,
        created_by=admin.get("analyst_id") if isinstance(admin, dict) else None,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return {"webhook_id": wh.webhook_id, "url": wh.url, "events": wh.events, "is_active": wh.is_active}


@router.get("/admin/webhooks", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def list_webhooks(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: list all registered webhooks (secrets excluded)."""
    from app.models.webhook import Webhook

    webhooks = db.query(Webhook).order_by(Webhook.webhook_id).all()
    return [
        {
            "webhook_id": wh.webhook_id,
            "url": wh.url,
            "events": wh.events,
            "is_active": wh.is_active,
            "created_at": wh.created_at.isoformat() if wh.created_at else None,
        }
        for wh in webhooks
    ]


@router.delete("/admin/webhooks/{webhook_id}", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
def deactivate_webhook(
    webhook_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: deactivate a webhook (soft delete)."""
    from app.models.webhook import Webhook

    wh = db.query(Webhook).filter(Webhook.webhook_id == webhook_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")
    wh.is_active = False
    db.commit()
    return {"status": "deactivated", "webhook_id": webhook_id}


@router.post("/admin/webhooks/{webhook_id}/test", tags=["admin"])
@limiter.limit(settings.RATE_LIMIT_ADMIN)
async def test_webhook(
    webhook_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin_role),
):
    """Admin: send a test event to a webhook URL."""
    from app.models.webhook import Webhook
    from app.modules.webhook_dispatcher import dispatch_webhook

    wh = db.query(Webhook).filter(Webhook.webhook_id == webhook_id).first()
    if not wh:
        raise HTTPException(status_code=404, detail="Webhook not found")

    success = await dispatch_webhook(
        wh.url,
        "test",
        {"message": "This is a test webhook from RadianceFleet."},
        wh.secret,
    )
    if success:
        return {"status": "delivered", "webhook_id": webhook_id}
    raise HTTPException(status_code=502, detail="Webhook delivery failed after 3 attempts")
