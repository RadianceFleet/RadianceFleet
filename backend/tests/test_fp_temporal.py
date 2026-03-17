"""Tests for temporal FP rate analysis (monthly, watch, weekday groupings)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base, CorridorTypeEnum
from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent
from app.models.scoring_region import ScoringRegion
from app.modules.fp_rate_tracker import (
    compute_fp_rates_by_month,
    compute_fp_rates_by_watch,
    compute_fp_rates_by_weekday,
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
) -> AISGapEvent:
    gap_start = gap_start or datetime(2025, 6, 15, 10, 0, 0)
    gap = AISGapEvent(
        vessel_id=vessel_id,
        corridor_id=corridor_id,
        gap_start_utc=gap_start,
        gap_end_utc=gap_start + timedelta(hours=2),
        duration_minutes=120,
        is_false_positive=is_false_positive,
        review_date=gap_start + timedelta(days=1) if is_false_positive is not None else None,
    )
    db.add(gap)
    db.flush()
    return gap


def _make_region(db: Session, name: str, corridor_ids: list[int]) -> ScoringRegion:
    r = ScoringRegion(name=name, corridor_ids_json=json.dumps(corridor_ids))
    db.add(r)
    db.flush()
    return r


# ---------------------------------------------------------------------------
# Monthly FP rates
# ---------------------------------------------------------------------------


def test_monthly_fp_rates_empty_db(db):
    result = compute_fp_rates_by_month(db)
    assert result == []


def test_monthly_fp_rates_basic(db):
    """Correct month grouping with sufficient observations."""
    c = _make_corridor(db, "C1")
    # Create 35 reviewed alerts in June 2025 (20 FP, 15 TP)
    for i in range(20):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 10 + (i % 15), 8, 0))
    for i in range(15):
        _make_gap(db, c.corridor_id, vessel_id=100 + i, is_false_positive=False,
                  gap_start=datetime(2025, 6, 1 + (i % 15), 12, 0))
    db.flush()

    result = compute_fp_rates_by_month(db)
    assert len(result) == 1
    assert result[0].year == 2025
    assert result[0].month == 6
    assert result[0].total == 35
    assert result[0].fp_count == 20
    assert abs(result[0].fp_rate - 20 / 35) < 0.001


def test_monthly_fp_rates_min_observations(db):
    """Cells below 30 observations are excluded."""
    c = _make_corridor(db, "C1")
    # Only 10 alerts in January
    for i in range(10):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 1, 15, 10, 0))
    db.flush()

    result = compute_fp_rates_by_month(db)
    assert result == []


def test_monthly_fp_rates_by_corridor(db):
    """Corridor filter works correctly."""
    c1 = _make_corridor(db, "C1")
    c2 = _make_corridor(db, "C2")
    # 35 alerts in c1
    for i in range(35):
        _make_gap(db, c1.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 3, 10, 8, 0))
    # 35 alerts in c2
    for i in range(35):
        _make_gap(db, c2.corridor_id, vessel_id=200 + i, is_false_positive=False,
                  gap_start=datetime(2025, 3, 10, 8, 0))
    db.flush()

    result = compute_fp_rates_by_month(db, corridor_id=c1.corridor_id)
    assert len(result) == 1
    assert result[0].fp_rate == 1.0  # All FP in c1


def test_monthly_fp_rates_by_region(db):
    """Region aggregation works."""
    c1 = _make_corridor(db, "C1")
    c2 = _make_corridor(db, "C2")
    region = _make_region(db, "Region1", [c1.corridor_id, c2.corridor_id])
    # 20 FP in c1, 15 TP in c2 = 35 total in March
    for i in range(20):
        _make_gap(db, c1.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 3, 10, 8, 0))
    for i in range(15):
        _make_gap(db, c2.corridor_id, vessel_id=100 + i, is_false_positive=False,
                  gap_start=datetime(2025, 3, 10, 12, 0))
    db.flush()

    result = compute_fp_rates_by_month(db, region_id=region.region_id)
    assert len(result) == 1
    assert result[0].total == 35
    assert result[0].fp_count == 20


# ---------------------------------------------------------------------------
# Watch FP rates
# ---------------------------------------------------------------------------


def test_watch_fp_rates_basic(db):
    """Correct 4h bucket grouping."""
    c = _make_corridor(db, "C1")
    # 6 alerts at hour 2 (bucket 0), 6 at hour 14 (bucket 12)
    for i in range(6):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 15, 2, 0))
    for i in range(6):
        _make_gap(db, c.corridor_id, vessel_id=100 + i, is_false_positive=False,
                  gap_start=datetime(2025, 6, 15, 14, 0))
    db.flush()

    result = compute_fp_rates_by_watch(db)
    assert len(result) == 2
    bucket_map = {r.watch_start_hour: r for r in result}
    assert 0 in bucket_map
    assert 12 in bucket_map
    assert bucket_map[0].fp_rate == 1.0
    assert bucket_map[12].fp_rate == 0.0


def test_watch_fp_rates_bucket_mapping(db):
    """Hour 7 maps to bucket 4, hour 13 maps to bucket 12."""
    c = _make_corridor(db, "C1")
    # 5 at hour 7 (bucket 4), 5 at hour 13 (bucket 12)
    for i in range(5):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 15, 7, 0))
    for i in range(5):
        _make_gap(db, c.corridor_id, vessel_id=100 + i, is_false_positive=False,
                  gap_start=datetime(2025, 6, 15, 13, 0))
    db.flush()

    result = compute_fp_rates_by_watch(db)
    bucket_map = {r.watch_start_hour: r for r in result}
    assert 4 in bucket_map
    assert 12 in bucket_map
    assert 0 not in bucket_map  # No alerts in bucket 0
    assert 8 not in bucket_map  # No alerts in bucket 8


def test_watch_fp_rates_min_observations(db):
    """Buckets below 5 observations are excluded."""
    c = _make_corridor(db, "C1")
    # Only 3 alerts at hour 10 (bucket 8)
    for i in range(3):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 15, 10, 0))
    db.flush()

    result = compute_fp_rates_by_watch(db)
    assert result == []


# ---------------------------------------------------------------------------
# Weekday FP rates
# ---------------------------------------------------------------------------


def test_weekday_fp_rates_basic(db):
    """Correct day-of-week grouping."""
    c = _make_corridor(db, "C1")
    # 2025-06-16 is a Monday; create 6 alerts on it
    for i in range(6):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 16, 10, 0))
    db.flush()

    result = compute_fp_rates_by_weekday(db)
    assert len(result) == 1
    assert result[0].weekday == 0  # Monday
    assert result[0].total == 6


def test_weekday_fp_rates_sunday_mapping(db):
    """SQLite %w=0 (Sunday) maps to Python weekday=6."""
    c = _make_corridor(db, "C1")
    # 2025-06-15 is a Sunday
    for i in range(6):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=False,
                  gap_start=datetime(2025, 6, 15, 10, 0))
    db.flush()

    result = compute_fp_rates_by_weekday(db)
    assert len(result) == 1
    assert result[0].weekday == 6  # Sunday = 6 in Python


def test_weekday_by_corridor(db):
    """Corridor filter on weekday analysis."""
    c1 = _make_corridor(db, "C1")
    c2 = _make_corridor(db, "C2")
    # Monday alerts: 6 FP in c1, 6 TP in c2
    for i in range(6):
        _make_gap(db, c1.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 16, 10, 0))
    for i in range(6):
        _make_gap(db, c2.corridor_id, vessel_id=100 + i, is_false_positive=False,
                  gap_start=datetime(2025, 6, 16, 10, 0))
    db.flush()

    result = compute_fp_rates_by_weekday(db, corridor_id=c1.corridor_id)
    assert len(result) == 1
    assert result[0].fp_rate == 1.0


# ---------------------------------------------------------------------------
# Cross-cutting temporal tests
# ---------------------------------------------------------------------------


def test_temporal_all_fp(db):
    """All alerts are FP → rate = 1.0."""
    c = _make_corridor(db, "C1")
    for i in range(6):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 16, 10, 0))
    db.flush()

    result = compute_fp_rates_by_watch(db)
    assert len(result) == 1
    assert result[0].fp_rate == 1.0


def test_temporal_all_tp(db):
    """All alerts are TP → rate = 0.0."""
    c = _make_corridor(db, "C1")
    for i in range(6):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=False,
                  gap_start=datetime(2025, 6, 16, 14, 0))
    db.flush()

    result = compute_fp_rates_by_watch(db)
    assert len(result) == 1
    assert result[0].fp_rate == 0.0


def test_temporal_mixed_corridors(db):
    """Multiple corridors in one query (no corridor filter)."""
    c1 = _make_corridor(db, "C1")
    c2 = _make_corridor(db, "C2")
    # 5 FP in c1 on Monday, 5 TP in c2 on Monday
    for i in range(5):
        _make_gap(db, c1.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 16, 10, 0))
    for i in range(5):
        _make_gap(db, c2.corridor_id, vessel_id=100 + i, is_false_positive=False,
                  gap_start=datetime(2025, 6, 16, 10, 0))
    db.flush()

    result = compute_fp_rates_by_weekday(db)
    assert len(result) == 1
    assert result[0].total == 10
    assert result[0].fp_count == 5
    assert abs(result[0].fp_rate - 0.5) < 0.001


def test_temporal_unreviewed_excluded(db):
    """Alerts without verdicts (is_false_positive=None) are excluded."""
    c = _make_corridor(db, "C1")
    # 6 reviewed + 10 unreviewed
    for i in range(6):
        _make_gap(db, c.corridor_id, vessel_id=i + 1, is_false_positive=True,
                  gap_start=datetime(2025, 6, 16, 10, 0))
    for i in range(10):
        _make_gap(db, c.corridor_id, vessel_id=200 + i, is_false_positive=None,
                  gap_start=datetime(2025, 6, 16, 10, 0))
    db.flush()

    result = compute_fp_rates_by_watch(db)
    assert len(result) == 1
    assert result[0].total == 6  # Only reviewed alerts counted
