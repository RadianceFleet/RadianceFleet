"""Coverage tracker — tracks which date ranges have been imported per source.

Pure functions for querying and recording data coverage windows.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.data_coverage_window import DataCoverageWindow

logger = logging.getLogger(__name__)


def get_covered_dates(db: Session, source: str) -> set[date]:
    """Return set of dates with completed coverage windows for a source."""
    windows = (
        db.query(DataCoverageWindow)
        .filter(
            DataCoverageWindow.source == source,
            DataCoverageWindow.status == "completed",
        )
        .all()
    )
    covered: set[date] = set()
    for w in windows:
        d = w.date_from
        while d <= w.date_to:
            covered.add(d)
            d = date.fromordinal(d.toordinal() + 1)
    return covered


def find_coverage_gaps(
    db: Session,
    source: str,
    from_date: date,
    to_date: date,
) -> list[tuple[date, date]]:
    """Find contiguous date ranges NOT covered by completed windows.

    Returns list of (gap_start, gap_end) tuples, merged for adjacent missing dates.
    """
    covered = get_covered_dates(db, source)
    missing: list[date] = []
    d = from_date
    while d <= to_date:
        if d not in covered:
            missing.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    if not missing:
        return []

    # Merge adjacent dates into contiguous ranges
    gaps: list[tuple[date, date]] = []
    gap_start = missing[0]
    prev = missing[0]
    for d in missing[1:]:
        if d.toordinal() - prev.toordinal() == 1:
            prev = d
        else:
            gaps.append((gap_start, prev))
            gap_start = d
            prev = d
    gaps.append((gap_start, prev))
    return gaps


def record_coverage_window(
    db: Session,
    source: str,
    date_from: date,
    date_to: date,
    *,
    status: str,
    points_imported: int = 0,
    vessels_queried: int = 0,
    vessels_total: int | None = None,
    errors: int = 0,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    notes: str | None = None,
) -> DataCoverageWindow:
    """Upsert a coverage window record (idempotent on source+date_from+date_to+status)."""
    if started_at is None:
        started_at = datetime.now(UTC)
    existing = (
        db.query(DataCoverageWindow)
        .filter(
            DataCoverageWindow.source == source,
            DataCoverageWindow.date_from == date_from,
            DataCoverageWindow.date_to == date_to,
            DataCoverageWindow.status == status,
        )
        .first()
    )
    if existing is not None:
        existing.points_imported = points_imported
        existing.vessels_queried = vessels_queried
        if vessels_total is not None:
            existing.vessels_total = vessels_total
        existing.errors = errors
        existing.finished_at = finished_at
        if notes is not None:
            existing.notes = notes
        db.flush()
        return existing
    window = DataCoverageWindow(
        source=source,
        date_from=date_from,
        date_to=date_to,
        status=status,
        points_imported=points_imported,
        vessels_queried=vessels_queried,
        vessels_total=vessels_total,
        errors=errors,
        started_at=started_at,
        finished_at=finished_at,
        notes=notes,
    )
    db.add(window)
    db.flush()
    return window


def coverage_summary(db: Session) -> dict[str, Any]:
    """Return per-source coverage summary.

    For each source: earliest date, latest date, total_points, completed_windows,
    gap_count (gaps between earliest and latest), next_gap (first gap start date).
    """
    from sqlalchemy import func as sa_func

    rows = (
        db.query(
            DataCoverageWindow.source,
            sa_func.min(DataCoverageWindow.date_from).label("earliest"),
            sa_func.max(DataCoverageWindow.date_to).label("latest"),
            sa_func.sum(DataCoverageWindow.points_imported).label("total_points"),
            sa_func.count(DataCoverageWindow.window_id).label("completed_windows"),
        )
        .filter(DataCoverageWindow.status == "completed")
        .group_by(DataCoverageWindow.source)
        .all()
    )

    summary: dict[str, Any] = {}
    for row in rows:
        source = row.source
        earliest = row.earliest
        latest = row.latest
        gaps = find_coverage_gaps(db, source, earliest, latest) if earliest and latest else []
        summary[source] = {
            "earliest": earliest.isoformat() if earliest else None,
            "latest": latest.isoformat() if latest else None,
            "total_points": int(row.total_points or 0),
            "completed_windows": int(row.completed_windows or 0),
            "gap_count": len(gaps),
            "next_gap": gaps[0][0].isoformat() if gaps else None,
        }
    return summary


def is_gfw_coverage_complete(
    db: Session,
    source: str,
    date_from: date,
    date_to: date,
) -> bool:
    """Check if GFW coverage is complete (vessels_queried >= vessels_total) for a date range."""
    windows = (
        db.query(DataCoverageWindow)
        .filter(
            DataCoverageWindow.source == source,
            DataCoverageWindow.status == "completed",
            DataCoverageWindow.date_from >= date_from,
            DataCoverageWindow.date_to <= date_to,
        )
        .all()
    )
    if not windows:
        return False
    for w in windows:
        if w.vessels_total is None:
            return False
        if w.vessels_queried < w.vessels_total:
            return False
    return True
