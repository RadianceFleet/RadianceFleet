"""Shared utilities for route sub-modules."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import HTTPException, UploadFile
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from unidecode import unidecode as _unidecode

from app.config import settings

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_DEFAULT])

# ---------------------------------------------------------------------------
# Coverage Quality — load coverage.yaml at module level
# ---------------------------------------------------------------------------

_COVERAGE_DATA: dict = {}
_coverage_path = Path(__file__).resolve().parents[3] / "config" / "coverage.yaml"
if not _coverage_path.exists():
    _coverage_path = Path(__file__).resolve().parents[2] / "config" / "coverage.yaml"
if _coverage_path.exists():
    with open(_coverage_path) as f:
        _COVERAGE_DATA = yaml.safe_load(f) or {}

_REGION_MATCH_ORDER: list[tuple[str, list[str]]] = [
    ("Nakhodka", ["nakhodka"]),
    (
        "Baltic",
        ["baltic", "primorsk", "ust luga", "kaliningrad", "oresund", "great belt", "murmansk"],
    ),
    ("Turkish Straits", ["turkish", "bosphorus", "dardanelles", "ceyhan", "iskenderun"]),
    ("Black Sea", ["black sea", "kavkaz", "novorossiysk", "crimea", "bulgaria"]),
    ("Persian Gulf", ["hormuz", "fujairah", "khor al zubair", "basra", "gulf of oman"]),
    ("Singapore", ["singapore", "tanjung pelepas", "malacca"]),
    (
        "Mediterranean",
        [
            "mediterranean",
            "ceuta",
            "gibraltar",
            "laconian",
            "malta",
            "hurd",
            "ain sukhna",
            "nador",
            "cyprus",
            "syria",
            "ras lanuf",
        ],
    ),
    (
        "Far East",
        [
            "kozmino",
            "east china",
            "de kastri",
            "sakhalin",
            "daesan",
            "onsan",
            "ulsan",
            "yeosu",
            "shandong",
        ],
    ),
]


def _get_coverage_quality(corridor_name: str) -> str:
    """Map corridor name to coverage quality from coverage.yaml."""
    normalized = re.sub(r"[^a-z0-9 ]", " ", _unidecode(corridor_name).casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for region, keywords in _REGION_MATCH_ORDER:
        for kw in keywords:
            if kw in normalized:
                region_data = _COVERAGE_DATA.get(region, {})
                return region_data.get("quality", "UNKNOWN")
    return "UNKNOWN"


def _compute_data_age_hours(vessel, now) -> float | None:
    """Compute data age in hours from vessel.last_ais_received_utc."""
    try:
        last_utc = getattr(vessel, "last_ais_received_utc", None)
        if last_utc is None or not isinstance(last_utc, datetime):
            return None
        tz_last = last_utc.replace(tzinfo=UTC) if last_utc.tzinfo is None else last_utc
        return round((now - tz_last).total_seconds() / 3600, 1)
    except (TypeError, AttributeError):
        return None


def _compute_freshness_warning(vessel, now) -> str | None:
    """Return warning string if vessel AIS data is stale (>48h)."""
    try:
        last_utc = getattr(vessel, "last_ais_received_utc", None)
        if last_utc is None or not isinstance(last_utc, datetime):
            return None
        tz_last = last_utc.replace(tzinfo=UTC) if last_utc.tzinfo is None else last_utc
        if (now - tz_last).total_seconds() > 48 * 3600:
            return "AIS data is more than 48 hours old"
        return None
    except (TypeError, AttributeError):
        return None


def _audit_log(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: int = None,
    details: dict = None,
    request=None,
    analyst_id=None,
) -> None:
    """Record an analyst action for audit trail (PRD NFR5)."""
    from app.models.audit_log import AuditLog

    log = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
        analyst_id=analyst_id,
        user_agent=request.headers.get("user-agent") if request else None,
        ip_address=request.client.host if request and request.client else None,
    )
    db.add(log)


def _query_new_alerts(last_id: int, min_score: int) -> list[dict]:
    """Query alerts newer than last_id with score >= min_score. Runs in thread."""
    from app.database import SessionLocal
    from app.models.gap_event import AISGapEvent

    db = SessionLocal()
    try:
        alerts = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.gap_event_id > last_id,
                AISGapEvent.risk_score >= min_score,
            )
            .order_by(AISGapEvent.gap_event_id.asc())
            .limit(50)
            .all()
        )
        return [
            {
                "gap_event_id": a.gap_event_id,
                "vessel_id": a.vessel_id,
                "risk_score": a.risk_score,
                "gap_start_utc": a.gap_start_utc.isoformat()
                if a.gap_start_utc
                else None,
                "duration_minutes": a.duration_minutes,
                "status": str(a.status.value)
                if hasattr(a.status, "value")
                else str(a.status),
            }
            for a in alerts
        ]
    finally:
        db.close()


def _validate_date_range(date_from, date_to) -> None:
    """Reject if date_from is after date_to."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be <= date_to")


def _check_upload_size(file: UploadFile) -> None:
    """Reject uploads exceeding MAX_UPLOAD_SIZE_MB."""
    file.file.seek(0, 2)
    size_mb = file.file.tell() / (1024 * 1024)
    file.file.seek(0)
    if size_mb > settings.MAX_UPLOAD_SIZE_MB:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size_mb:.1f} MB). Max: {settings.MAX_UPLOAD_SIZE_MB} MB.",
        )
