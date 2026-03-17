"""Tests for FP rate tracking, calibration suggestions, and scoring overrides."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base, CorridorTypeEnum
from app.models.corridor import Corridor
from app.models.corridor_scoring_override import CorridorScoringOverride
from app.models.gap_event import AISGapEvent
from app.modules.fp_rate_tracker import (
    _compute_trend,
    _get_corridor_multiplier,
    compute_fp_rate,
    compute_fp_rates,
    generate_calibration_suggestions,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory SQLite session with required tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
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
    review_date: datetime | None = None,
    gap_start: datetime | None = None,
) -> AISGapEvent:
    now = datetime.utcnow()
    gap_start = gap_start or now - timedelta(hours=6)
    gap = AISGapEvent(
        vessel_id=vessel_id,
        corridor_id=corridor_id,
        gap_start_utc=gap_start,
        gap_end_utc=gap_start + timedelta(hours=2),
        duration_minutes=120,
        is_false_positive=is_false_positive,
        review_date=review_date,
    )
    db.add(gap)
    db.flush()
    return gap


# ---------------------------------------------------------------------------
# FP rate computation tests
# ---------------------------------------------------------------------------


class TestFPRateComputation:
    def test_all_fp(self, db):
        """100% FP rate when all reviewed events are false positives."""
        c = _make_corridor(db, "All FP Corridor")
        now = datetime.utcnow()
        for _ in range(10):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now)
        db.commit()

        result = compute_fp_rate(db, c.corridor_id)
        assert result is not None
        assert result.fp_rate == 1.0
        assert result.false_positives == 10
        assert result.total_alerts == 10

    def test_no_fp(self, db):
        """0% FP rate when all reviewed events are true positives."""
        c = _make_corridor(db, "No FP Corridor")
        now = datetime.utcnow()
        for _ in range(8):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=now)
        db.commit()

        result = compute_fp_rate(db, c.corridor_id)
        assert result is not None
        assert result.fp_rate == 0.0
        assert result.false_positives == 0
        assert result.total_alerts == 8

    def test_mixed_fp(self, db):
        """Mixed FP rate calculation."""
        c = _make_corridor(db, "Mixed Corridor")
        now = datetime.utcnow()
        for _ in range(3):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now)
        for _ in range(7):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=now)
        db.commit()

        result = compute_fp_rate(db, c.corridor_id)
        assert result is not None
        assert result.fp_rate == 0.3
        assert result.false_positives == 3
        assert result.total_alerts == 10

    def test_no_alerts(self, db):
        """Corridor with no reviewed alerts returns zero rates."""
        c = _make_corridor(db, "Empty Corridor")
        db.commit()

        result = compute_fp_rate(db, c.corridor_id)
        assert result is not None
        assert result.fp_rate == 0.0
        assert result.total_alerts == 0

    def test_unreviewed_not_counted(self, db):
        """Unreviewed gaps (is_false_positive=None) are excluded."""
        c = _make_corridor(db, "Unreviewed Corridor")
        now = datetime.utcnow()
        _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now)
        _make_gap(db, c.corridor_id, is_false_positive=None, review_date=None)
        _make_gap(db, c.corridor_id, is_false_positive=None, review_date=None)
        db.commit()

        result = compute_fp_rate(db, c.corridor_id)
        assert result.total_alerts == 1
        assert result.false_positives == 1
        assert result.fp_rate == 1.0

    def test_nonexistent_corridor(self, db):
        """Nonexistent corridor returns None."""
        result = compute_fp_rate(db, 99999)
        assert result is None


# ---------------------------------------------------------------------------
# Time-windowed FP rate tests
# ---------------------------------------------------------------------------


class TestTimeWindowedRates:
    def test_30d_window(self, db):
        """Events older than 30 days excluded from 30d rate."""
        c = _make_corridor(db, "Window Test")
        now = datetime.utcnow()
        old = now - timedelta(days=45)

        # 2 recent FPs
        for _ in range(2):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now - timedelta(days=5))
        # 1 recent TP
        _make_gap(db, c.corridor_id, is_false_positive=False, review_date=now - timedelta(days=5))
        # 10 old TPs (outside 30d window)
        for _ in range(10):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=old)
        db.commit()

        result = compute_fp_rate(db, c.corridor_id)
        # All-time: 2 FP / 13 total
        assert result.total_alerts == 13
        assert result.false_positives == 2
        # 30d: 2 FP / 3 total
        assert abs(result.fp_rate_30d - (2.0 / 3.0)) < 0.01

    def test_90d_window(self, db):
        """Events older than 90 days excluded from 90d rate."""
        c = _make_corridor(db, "90d Window")
        now = datetime.utcnow()

        # 5 recent FPs within 90d
        for _ in range(5):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now - timedelta(days=60))
        # 5 old TPs outside 90d
        for _ in range(5):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=now - timedelta(days=120))
        db.commit()

        result = compute_fp_rate(db, c.corridor_id)
        assert result.fp_rate_90d == 1.0  # Only the 5 FPs are within 90d


# ---------------------------------------------------------------------------
# Trend detection tests
# ---------------------------------------------------------------------------


class TestTrendDetection:
    def test_increasing_trend(self, db):
        """FP rate increasing when recent window has higher rate."""
        c = _make_corridor(db, "Increasing Trend")
        now = datetime.utcnow()

        # Previous window (30-60d ago): low FP rate (1/10)
        prev_date = now - timedelta(days=45)
        _make_gap(db, c.corridor_id, is_false_positive=True, review_date=prev_date)
        for _ in range(9):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=prev_date)

        # Recent window (last 30d): high FP rate (8/10)
        recent_date = now - timedelta(days=10)
        for _ in range(8):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=recent_date)
        for _ in range(2):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=recent_date)
        db.commit()

        trend = _compute_trend(db, c.corridor_id, now=now)
        assert trend == "increasing"

    def test_decreasing_trend(self, db):
        """FP rate decreasing when recent window has lower rate."""
        c = _make_corridor(db, "Decreasing Trend")
        now = datetime.utcnow()

        # Previous window: high FP rate (8/10)
        prev_date = now - timedelta(days=45)
        for _ in range(8):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=prev_date)
        for _ in range(2):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=prev_date)

        # Recent window: low FP rate (1/10)
        recent_date = now - timedelta(days=10)
        _make_gap(db, c.corridor_id, is_false_positive=True, review_date=recent_date)
        for _ in range(9):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=recent_date)
        db.commit()

        trend = _compute_trend(db, c.corridor_id, now=now)
        assert trend == "decreasing"

    def test_stable_trend(self, db):
        """Stable when rates are similar between windows."""
        c = _make_corridor(db, "Stable Trend")
        now = datetime.utcnow()

        # Both windows: ~30% FP rate
        for offset_days in [45, 10]:
            date = now - timedelta(days=offset_days)
            for _ in range(3):
                _make_gap(db, c.corridor_id, is_false_positive=True, review_date=date)
            for _ in range(7):
                _make_gap(db, c.corridor_id, is_false_positive=False, review_date=date)
        db.commit()

        trend = _compute_trend(db, c.corridor_id, now=now)
        assert trend == "stable"

    def test_stable_when_insufficient_data(self, db):
        """Stable when previous window has too few data points."""
        c = _make_corridor(db, "Insufficient Data")
        now = datetime.utcnow()

        # Only 1 event in previous window
        _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now - timedelta(days=45))
        # 10 events in recent window
        for _ in range(10):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now - timedelta(days=5))
        db.commit()

        trend = _compute_trend(db, c.corridor_id, now=now)
        assert trend == "stable"


# ---------------------------------------------------------------------------
# Calibration suggestion tests
# ---------------------------------------------------------------------------


class TestCalibrationSuggestions:
    def test_high_fp_suggests_reduction(self, db):
        """Corridor with >50% FP rate gets halving suggestion."""
        c = _make_corridor(db, "High FP", corridor_type=CorridorTypeEnum.EXPORT_ROUTE)
        now = datetime.utcnow()
        for _ in range(8):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now)
        for _ in range(2):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=now)
        db.commit()

        suggestions = generate_calibration_suggestions(db)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.corridor_id == c.corridor_id
        assert s.suggested_multiplier < s.current_multiplier
        assert "50%" in s.reason

    def test_moderate_fp_suggests_reduction(self, db):
        """Corridor with 30-50% FP rate gets 25% reduction suggestion."""
        c = _make_corridor(db, "Moderate FP", corridor_type=CorridorTypeEnum.EXPORT_ROUTE)
        now = datetime.utcnow()
        for _ in range(4):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now)
        for _ in range(6):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=now)
        db.commit()

        suggestions = generate_calibration_suggestions(db)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.suggested_multiplier == round(1.5 * 0.75, 2)

    def test_low_fp_suggests_increase(self, db):
        """Corridor with <5% FP rate and enough alerts gets increase suggestion."""
        c = _make_corridor(db, "Low FP", corridor_type=CorridorTypeEnum.EXPORT_ROUTE)
        now = datetime.utcnow()
        _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now)
        for _ in range(24):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=now)
        db.commit()

        suggestions = generate_calibration_suggestions(db)
        assert len(suggestions) >= 1
        s = suggestions[0]
        assert s.suggested_multiplier > s.current_multiplier

    def test_no_suggestion_with_few_alerts(self, db):
        """No suggestion when corridor has fewer than 5 reviewed alerts."""
        c = _make_corridor(db, "Too Few")
        now = datetime.utcnow()
        for _ in range(3):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now)
        db.commit()

        suggestions = generate_calibration_suggestions(db)
        assert len(suggestions) == 0

    def test_no_suggestion_for_normal_fp_rate(self, db):
        """No suggestion when FP rate is in the 5-15% range."""
        c = _make_corridor(db, "Normal FP")
        now = datetime.utcnow()
        for _ in range(1):
            _make_gap(db, c.corridor_id, is_false_positive=True, review_date=now)
        for _ in range(9):
            _make_gap(db, c.corridor_id, is_false_positive=False, review_date=now)
        db.commit()

        suggestions = generate_calibration_suggestions(db)
        # 10% FP rate, only 10 alerts, should not trigger any suggestion
        assert len(suggestions) == 0


# ---------------------------------------------------------------------------
# Corridor multiplier helper tests
# ---------------------------------------------------------------------------


class TestCorridorMultiplier:
    def test_export_route_default(self, db):
        c = _make_corridor(db, "Export", corridor_type=CorridorTypeEnum.EXPORT_ROUTE)
        mult = _get_corridor_multiplier(c)
        assert mult == 1.5

    def test_sts_zone_default(self, db):
        c = _make_corridor(db, "STS", corridor_type=CorridorTypeEnum.STS_ZONE)
        mult = _get_corridor_multiplier(c)
        assert mult == 1.5

    def test_legitimate_trade_default(self, db):
        c = _make_corridor(db, "Legit", corridor_type=CorridorTypeEnum.LEGITIMATE_TRADE_ROUTE)
        mult = _get_corridor_multiplier(c)
        assert mult == 0.7

    def test_custom_config(self, db):
        c = _make_corridor(db, "Custom", corridor_type=CorridorTypeEnum.EXPORT_ROUTE)
        config = {"corridor": {"high_risk_export_corridor": 2.0}}
        mult = _get_corridor_multiplier(c, config)
        assert mult == 2.0


# ---------------------------------------------------------------------------
# Override model tests
# ---------------------------------------------------------------------------


class TestOverrideModel:
    def test_create_override(self, db):
        """Can create and read back a scoring override."""
        c = _make_corridor(db, "Override Test")
        override = CorridorScoringOverride(
            corridor_id=c.corridor_id,
            corridor_multiplier_override=1.2,
            gap_duration_multiplier=0.8,
            description="Reduce due to high FP rate",
        )
        db.add(override)
        db.commit()
        db.refresh(override)

        assert override.override_id is not None
        assert override.corridor_id == c.corridor_id
        assert override.corridor_multiplier_override == 1.2
        assert override.gap_duration_multiplier == 0.8
        assert override.is_active is True

    def test_deactivate_override(self, db):
        """Can deactivate an override."""
        c = _make_corridor(db, "Deactivate Test")
        override = CorridorScoringOverride(
            corridor_id=c.corridor_id,
            corridor_multiplier_override=1.0,
        )
        db.add(override)
        db.commit()

        override.is_active = False
        db.commit()
        db.refresh(override)

        assert override.is_active is False

    def test_unique_corridor(self, db):
        """Cannot create two overrides for the same corridor."""
        c = _make_corridor(db, "Unique Test")
        o1 = CorridorScoringOverride(corridor_id=c.corridor_id, corridor_multiplier_override=1.0)
        db.add(o1)
        db.commit()

        o2 = CorridorScoringOverride(corridor_id=c.corridor_id, corridor_multiplier_override=1.5)
        db.add(o2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------------------------------------------------------------------------
# Compute FP rates (multi-corridor) tests
# ---------------------------------------------------------------------------


class TestComputeFPRates:
    def test_multiple_corridors_sorted(self, db):
        """Returns corridors sorted by FP rate descending."""
        c1 = _make_corridor(db, "High")
        c2 = _make_corridor(db, "Low")
        now = datetime.utcnow()

        # c1: 80% FP
        for _ in range(8):
            _make_gap(db, c1.corridor_id, is_false_positive=True, review_date=now)
        for _ in range(2):
            _make_gap(db, c1.corridor_id, is_false_positive=False, review_date=now)

        # c2: 20% FP
        for _ in range(2):
            _make_gap(db, c2.corridor_id, is_false_positive=True, review_date=now)
        for _ in range(8):
            _make_gap(db, c2.corridor_id, is_false_positive=False, review_date=now)
        db.commit()

        results = compute_fp_rates(db)
        assert len(results) == 2
        assert results[0].corridor_id == c1.corridor_id
        assert results[1].corridor_id == c2.corridor_id

    def test_corridors_without_reviews_excluded(self, db):
        """Corridors with no reviewed alerts are not returned."""
        c1 = _make_corridor(db, "Reviewed")
        c2 = _make_corridor(db, "Unreviewed")
        now = datetime.utcnow()

        _make_gap(db, c1.corridor_id, is_false_positive=False, review_date=now)
        _make_gap(db, c2.corridor_id, is_false_positive=None, review_date=None)
        db.commit()

        results = compute_fp_rates(db)
        assert len(results) == 1
        assert results[0].corridor_id == c1.corridor_id
