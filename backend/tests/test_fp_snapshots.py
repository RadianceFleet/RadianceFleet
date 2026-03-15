"""Tests for FP rate snapshots — creation, querying, and trend analysis."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base, CorridorTypeEnum
from app.models.corridor import Corridor
from app.models.fp_rate_snapshot import FPRateSnapshot
from app.models.gap_event import AISGapEvent
from app.models.scoring_region import ScoringRegion
from app.modules.fp_rate_tracker import (
    create_fp_rate_snapshots,
    get_fp_rate_trend,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _make_corridor(db: Session, name: str = "Test Corridor", **kwargs) -> Corridor:
    kwargs.setdefault("corridor_type", CorridorTypeEnum.EXPORT_ROUTE)
    c = Corridor(name=name, **kwargs)
    db.add(c)
    db.flush()
    return c


def _make_gap(
    db: Session,
    corridor_id: int,
    vessel_id: int = 1,
    is_false_positive: bool | None = None,
    gap_start: datetime | None = None,
    review_date: datetime | None = None,
    risk_breakdown_json: str | None = None,
) -> AISGapEvent:
    gap_start = gap_start or datetime(2025, 6, 15, 10, 0, 0)
    gap = AISGapEvent(
        vessel_id=vessel_id,
        corridor_id=corridor_id,
        gap_start_utc=gap_start,
        gap_end_utc=gap_start + timedelta(hours=2),
        duration_minutes=120,
        is_false_positive=is_false_positive,
        review_date=review_date or (datetime.utcnow() if is_false_positive is not None else None),
        risk_breakdown_json=risk_breakdown_json,
    )
    db.add(gap)
    db.flush()
    return gap


def _make_region(db: Session, name: str, corridor_ids: list[int]) -> ScoringRegion:
    r = ScoringRegion(name=name, corridor_ids_json=json.dumps(corridor_ids))
    db.add(r)
    db.flush()
    return r


def _make_snapshot(
    db: Session,
    corridor_id: int | None = None,
    region_id: int | None = None,
    signal_name: str | None = None,
    snapshot_date: datetime | None = None,
    total_reviewed: int = 10,
    false_positives: int = 3,
    fp_rate: float = 0.3,
) -> FPRateSnapshot:
    snap = FPRateSnapshot(
        corridor_id=corridor_id,
        region_id=region_id,
        signal_name=signal_name,
        snapshot_date=snapshot_date or datetime.utcnow(),
        period_days=30,
        total_reviewed=total_reviewed,
        false_positives=false_positives,
        fp_rate=fp_rate,
    )
    db.add(snap)
    db.flush()
    return snap


# ---------------------------------------------------------------------------
# FPRateSnapshot model tests
# ---------------------------------------------------------------------------


def test_fp_rate_snapshot_model(db):
    """Can create and query FPRateSnapshot."""
    snap = _make_snapshot(db, corridor_id=1, fp_rate=0.25)
    queried = db.query(FPRateSnapshot).filter(FPRateSnapshot.snapshot_id == snap.snapshot_id).first()
    assert queried is not None
    assert queried.corridor_id == 1
    assert queried.fp_rate == 0.25


# ---------------------------------------------------------------------------
# create_fp_rate_snapshots tests
# ---------------------------------------------------------------------------


def test_create_snapshots_empty_db(db):
    """Empty DB returns 0 snapshots."""
    count = create_fp_rate_snapshots(db)
    assert count == 0


def test_create_snapshots_basic(db):
    """Creates corridor snapshots for corridors with enough reviewed alerts."""
    c = _make_corridor(db, "C1")
    now = datetime.utcnow()
    for i in range(10):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=(i < 4),
                  review_date=now - timedelta(days=5))
    db.flush()

    count = create_fp_rate_snapshots(db)
    assert count >= 1
    snaps = db.query(FPRateSnapshot).filter(FPRateSnapshot.corridor_id == c.corridor_id).all()
    assert len(snaps) >= 1


def test_create_snapshots_min_verdicts(db):
    """Corridors with too few reviewed alerts are skipped."""
    c = _make_corridor(db, "C1")
    now = datetime.utcnow()
    # Only 2 reviewed alerts (below default min of 5)
    for i in range(2):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  review_date=now - timedelta(days=5))
    db.flush()

    count = create_fp_rate_snapshots(db)
    assert count == 0


def test_create_snapshots_regions(db):
    """Creates region snapshots for active regions with corridors."""
    c1 = _make_corridor(db, "C1")
    c2 = _make_corridor(db, "C2")
    region = _make_region(db, "TestRegion", [c1.corridor_id, c2.corridor_id])
    now = datetime.utcnow()
    for i in range(6):
        _make_gap(db, c1.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  review_date=now - timedelta(days=5))
    for i in range(6):
        _make_gap(db, c2.corridor_id, vessel_id=100 + i, is_false_positive=False,
                  review_date=now - timedelta(days=5))
    db.flush()

    count = create_fp_rate_snapshots(db)
    assert count >= 1
    region_snaps = db.query(FPRateSnapshot).filter(
        FPRateSnapshot.region_id == region.region_id
    ).all()
    assert len(region_snaps) == 1


def test_create_snapshots_returns_count(db):
    """Returned count matches actual snapshots created."""
    c = _make_corridor(db, "C1")
    now = datetime.utcnow()
    for i in range(10):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  review_date=now - timedelta(days=5))
    db.flush()

    count = create_fp_rate_snapshots(db)
    total = db.query(FPRateSnapshot).count()
    assert count == total


# ---------------------------------------------------------------------------
# get_fp_rate_trend tests
# ---------------------------------------------------------------------------


def test_get_trend_empty(db):
    """No snapshots returns empty list."""
    result = get_fp_rate_trend(db)
    assert result == []


def test_get_trend_basic(db):
    """Returns time series of snapshots."""
    now = datetime.utcnow()
    _make_snapshot(db, corridor_id=1, snapshot_date=now - timedelta(days=30), fp_rate=0.2)
    _make_snapshot(db, corridor_id=1, snapshot_date=now - timedelta(days=7), fp_rate=0.15)
    db.flush()

    result = get_fp_rate_trend(db, corridor_id=1)
    assert len(result) == 2
    assert result[0]["fp_rate"] == 0.2
    assert result[1]["fp_rate"] == 0.15


def test_get_trend_corridor_filter(db):
    """Filters by corridor_id."""
    now = datetime.utcnow()
    _make_snapshot(db, corridor_id=1, snapshot_date=now - timedelta(days=7), fp_rate=0.3)
    _make_snapshot(db, corridor_id=2, snapshot_date=now - timedelta(days=7), fp_rate=0.5)
    db.flush()

    result = get_fp_rate_trend(db, corridor_id=1)
    assert len(result) == 1
    assert result[0]["fp_rate"] == 0.3


def test_get_trend_region_filter(db):
    """Filters by region_id."""
    now = datetime.utcnow()
    _make_snapshot(db, region_id=10, snapshot_date=now - timedelta(days=7), fp_rate=0.4)
    _make_snapshot(db, region_id=20, snapshot_date=now - timedelta(days=7), fp_rate=0.6)
    db.flush()

    result = get_fp_rate_trend(db, region_id=10)
    assert len(result) == 1
    assert result[0]["fp_rate"] == 0.4


def test_get_trend_signal_filter(db):
    """Filters by signal_name."""
    now = datetime.utcnow()
    _make_snapshot(db, corridor_id=1, signal_name="gap_duration", snapshot_date=now - timedelta(days=7), fp_rate=0.2)
    _make_snapshot(db, corridor_id=1, signal_name="spoofing", snapshot_date=now - timedelta(days=7), fp_rate=0.8)
    db.flush()

    result = get_fp_rate_trend(db, signal_name="gap_duration")
    assert len(result) == 1
    assert result[0]["fp_rate"] == 0.2


def test_get_trend_lookback_days(db):
    """Respects lookback window — old snapshots excluded."""
    now = datetime.utcnow()
    _make_snapshot(db, corridor_id=1, snapshot_date=now - timedelta(days=200), fp_rate=0.1)
    _make_snapshot(db, corridor_id=1, snapshot_date=now - timedelta(days=30), fp_rate=0.3)
    db.flush()

    result = get_fp_rate_trend(db, corridor_id=1, lookback_days=180)
    assert len(result) == 1
    assert result[0]["fp_rate"] == 0.3


def test_get_trend_sorted_by_date(db):
    """Results are in ascending date order."""
    now = datetime.utcnow()
    # Insert in reverse order
    _make_snapshot(db, corridor_id=1, snapshot_date=now - timedelta(days=7), fp_rate=0.3)
    _make_snapshot(db, corridor_id=1, snapshot_date=now - timedelta(days=60), fp_rate=0.1)
    _make_snapshot(db, corridor_id=1, snapshot_date=now - timedelta(days=30), fp_rate=0.2)
    db.flush()

    result = get_fp_rate_trend(db, corridor_id=1)
    assert len(result) == 3
    assert result[0]["fp_rate"] == 0.1
    assert result[1]["fp_rate"] == 0.2
    assert result[2]["fp_rate"] == 0.3


def test_snapshot_date_stored(db):
    """snapshot_date is persisted correctly."""
    target_date = datetime(2025, 6, 15, 12, 0, 0)
    snap = _make_snapshot(db, corridor_id=1, snapshot_date=target_date)
    queried = db.query(FPRateSnapshot).filter(FPRateSnapshot.snapshot_id == snap.snapshot_id).first()
    assert queried.snapshot_date == target_date


def test_multiple_snapshots_over_time(db):
    """Trend with multiple dates shows progression."""
    now = datetime.utcnow()
    dates_and_rates = [
        (now - timedelta(days=90), 0.5),
        (now - timedelta(days=60), 0.4),
        (now - timedelta(days=30), 0.3),
        (now - timedelta(days=7), 0.2),
    ]
    for date, rate in dates_and_rates:
        _make_snapshot(db, corridor_id=1, snapshot_date=date, fp_rate=rate)
    db.flush()

    result = get_fp_rate_trend(db, corridor_id=1)
    assert len(result) == 4
    rates = [r["fp_rate"] for r in result]
    assert rates == [0.5, 0.4, 0.3, 0.2]  # Decreasing trend
