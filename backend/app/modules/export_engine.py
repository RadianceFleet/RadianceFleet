"""Bulk export engine — generates CSV/JSON/Parquet files from database queries."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def generate_export(db: Session, subscription) -> tuple[bytes, str, int]:
    """Generate export data for a subscription.

    Returns (file_bytes, filename, row_count).
    """
    export_type = subscription.export_type
    fmt = subscription.format
    filter_json = subscription.filter_json or {}
    columns_json = subscription.columns_json

    # Resolve relative date modes
    resolved_filters = _resolve_date_filters(filter_json)

    # Dispatch to type-specific exporter
    exporters = {
        "alerts": _export_alerts,
        "vessels": _export_vessels,
        "ais_positions": _export_ais_positions,
        "evidence_cards": _export_evidence_cards,
    }
    exporter = exporters.get(export_type)
    if not exporter:
        raise ValueError(f"Unknown export type: {export_type}")

    rows = exporter(db, resolved_filters)

    # Enforce max rows limit
    max_rows = settings.EXPORT_MAX_ROWS
    if len(rows) > max_rows:
        rows = rows[:max_rows]

    # Apply column selection
    if columns_json and rows:
        rows = [{k: row.get(k) for k in columns_json if k in row} for row in rows]

    row_count = len(rows)

    # Format output
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    if fmt == "csv":
        file_bytes = _to_csv(rows)
        filename = f"{export_type}_{timestamp}.csv"
    elif fmt == "json":
        file_bytes = _to_json(rows)
        filename = f"{export_type}_{timestamp}.json"
    elif fmt == "parquet":
        file_bytes = _to_parquet(rows)
        filename = f"{export_type}_{timestamp}.parquet"
    else:
        raise ValueError(f"Unknown format: {fmt}")

    return file_bytes, filename, row_count


def _resolve_date_filters(filter_json: dict) -> dict:
    """Resolve relative date modes to absolute dates."""
    resolved = dict(filter_json)
    date_mode = resolved.pop("date_mode", None)
    if not date_mode:
        return resolved

    now = datetime.now(UTC)
    if date_mode == "last_day":
        resolved["date_from"] = (now - timedelta(days=1)).isoformat()
        resolved["date_to"] = now.isoformat()
    elif date_mode == "last_week":
        resolved["date_from"] = (now - timedelta(weeks=1)).isoformat()
        resolved["date_to"] = now.isoformat()
    elif date_mode == "last_month":
        resolved["date_from"] = (now - timedelta(days=30)).isoformat()
        resolved["date_to"] = now.isoformat()

    return resolved


def _export_alerts(db: Session, filters: dict) -> list[dict]:
    """Export alert (AIS gap event) data."""
    from app.models.gap_event import AISGapEvent

    q = db.query(AISGapEvent).order_by(AISGapEvent.gap_event_id.desc())

    if filters.get("date_from"):
        q = q.filter(AISGapEvent.gap_start_utc >= filters["date_from"])
    if filters.get("date_to"):
        q = q.filter(AISGapEvent.gap_start_utc <= filters["date_to"])
    if filters.get("vessel_id"):
        q = q.filter(AISGapEvent.vessel_id == filters["vessel_id"])
    if filters.get("corridor_id"):
        q = q.filter(AISGapEvent.corridor_id == filters["corridor_id"])
    if filters.get("status"):
        q = q.filter(AISGapEvent.status == filters["status"])

    q = q.limit(settings.EXPORT_MAX_ROWS)
    rows = q.all()

    return [
        {
            "gap_event_id": r.gap_event_id,
            "vessel_id": r.vessel_id,
            "gap_start_utc": r.gap_start_utc.isoformat() if r.gap_start_utc else None,
            "gap_end_utc": r.gap_end_utc.isoformat() if r.gap_end_utc else None,
            "duration_minutes": r.duration_minutes,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "risk_score": r.risk_score,
            "corridor_id": r.corridor_id,
        }
        for r in rows
    ]


def _export_vessels(db: Session, filters: dict) -> list[dict]:
    """Export vessel data."""
    from app.models.vessel import Vessel

    q = db.query(Vessel).order_by(Vessel.vessel_id)

    if filters.get("vessel_id"):
        q = q.filter(Vessel.vessel_id == filters["vessel_id"])
    if filters.get("flag"):
        q = q.filter(Vessel.flag == filters["flag"])

    q = q.limit(settings.EXPORT_MAX_ROWS)
    rows = q.all()

    return [
        {
            "vessel_id": r.vessel_id,
            "mmsi": r.mmsi,
            "imo": r.imo if hasattr(r, "imo") else None,
            "name": r.name if hasattr(r, "name") else None,
            "flag": r.flag if hasattr(r, "flag") else None,
            "vessel_type": r.vessel_type if hasattr(r, "vessel_type") else None,
            "deadweight": r.deadweight if hasattr(r, "deadweight") else None,
        }
        for r in rows
    ]


def _export_ais_positions(db: Session, filters: dict) -> list[dict]:
    """Export AIS position data."""
    from app.models.ais_point import AISPoint

    q = db.query(AISPoint).order_by(AISPoint.timestamp_utc.desc())

    if filters.get("vessel_id"):
        q = q.filter(AISPoint.vessel_id == filters["vessel_id"])
    if filters.get("date_from"):
        q = q.filter(AISPoint.timestamp_utc >= filters["date_from"])
    if filters.get("date_to"):
        q = q.filter(AISPoint.timestamp_utc <= filters["date_to"])

    q = q.limit(settings.EXPORT_MAX_ROWS)
    rows = q.all()

    return [
        {
            "ais_point_id": r.ais_point_id,
            "vessel_id": r.vessel_id,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "speed": r.speed if hasattr(r, "speed") else None,
            "course": r.course if hasattr(r, "course") else None,
            "heading": r.heading if hasattr(r, "heading") else None,
            "timestamp_utc": r.timestamp_utc.isoformat() if r.timestamp_utc else None,
            "source": r.source if hasattr(r, "source") else None,
        }
        for r in rows
    ]


def _export_evidence_cards(db: Session, filters: dict) -> list[dict]:
    """Export evidence card data."""
    from app.models.evidence_card import EvidenceCard

    q = db.query(EvidenceCard).order_by(EvidenceCard.evidence_card_id.desc())

    if filters.get("date_from"):
        q = q.filter(EvidenceCard.created_at >= filters["date_from"])
    if filters.get("date_to"):
        q = q.filter(EvidenceCard.created_at <= filters["date_to"])

    q = q.limit(settings.EXPORT_MAX_ROWS)
    rows = q.all()

    return [
        {
            "evidence_card_id": r.evidence_card_id,
            "gap_event_id": r.gap_event_id,
            "version": r.version,
            "export_format": r.export_format,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "approval_status": r.approval_status if hasattr(r, "approval_status") else None,
            "score_snapshot": r.score_snapshot if hasattr(r, "score_snapshot") else None,
        }
        for r in rows
    ]


def _to_csv(rows: list[dict]) -> bytes:
    """Convert rows to CSV bytes."""
    if not rows:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _to_json(rows: list[dict]) -> bytes:
    """Convert rows to JSON bytes."""
    return json.dumps(rows, indent=2, default=str).encode("utf-8")


def _to_parquet(rows: list[dict]) -> bytes:
    """Convert rows to Parquet bytes using Polars."""
    import polars as pl

    if not rows:
        df = pl.DataFrame()
    else:
        df = pl.DataFrame(rows)
    buf = io.BytesIO()
    df.write_parquet(buf, compression="gzip")
    return buf.getvalue()
