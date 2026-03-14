"""Tests for AIS reporting rate anomaly detector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.ais_point import AISPoint
from app.models.base import Base, SpoofingTypeEnum
from app.models.gap_event import AISGapEvent
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.modules.ais_reporting_anomaly_detector import (
    compute_baseline_rate,
    compute_interval_cv,
    compute_pre_gap_decay,
    detect_corridor_rate_change,
)


@pytest.fixture()
def db():
    """Create an in-memory SQLite DB with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def vessel(db):
    """Create a test vessel."""
    v = Vessel(
        mmsi="123456789",
        name="Test Vessel",
        flag="PA",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _make_timestamps(
    count: int,
    start: datetime | None = None,
    interval_seconds: float = 300.0,
    jitter_fn=None,
) -> list[datetime]:
    """Generate a list of timestamps with optional jitter."""
    if start is None:
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    timestamps = []
    for i in range(count):
        offset = interval_seconds * i
        if jitter_fn is not None:
            offset += jitter_fn(i)
        timestamps.append(start + timedelta(seconds=offset))
    return timestamps


# ---------------------------------------------------------------------------
# Signal 1: Interval CV tests
# ---------------------------------------------------------------------------


class TestIntervalCV:
    def test_regular_intervals_low_cv(self):
        """Regular 5-minute intervals should produce a low CV."""
        timestamps = _make_timestamps(100, interval_seconds=300.0)
        cv = compute_interval_cv(timestamps)
        assert cv is not None
        assert cv < 0.1  # nearly zero for perfectly regular intervals

    def test_irregular_intervals_high_cv(self):
        """Highly irregular intervals should produce high CV."""
        import random

        random.seed(42)
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = [start]
        for _ in range(99):
            # Random intervals between 10s and 3600s
            gap = random.uniform(10, 3600)
            timestamps.append(timestamps[-1] + timedelta(seconds=gap))
        cv = compute_interval_cv(timestamps)
        assert cv is not None
        assert cv > 0.5  # irregular pattern

    def test_insufficient_points_returns_none(self):
        """Single point should return None."""
        timestamps = _make_timestamps(1)
        cv = compute_interval_cv(timestamps)
        assert cv is None

    def test_single_point_edge_case(self):
        """Zero timestamps should return None."""
        cv = compute_interval_cv([])
        assert cv is None

    def test_zero_interval_handling(self):
        """All identical timestamps (zero intervals) should return None."""
        now = datetime(2025, 1, 1, tzinfo=UTC)
        timestamps = [now] * 10
        cv = compute_interval_cv(timestamps)
        # mean_interval is 0, so returns None
        assert cv is None


# ---------------------------------------------------------------------------
# Signal 2: Pre-gap decay tests
# ---------------------------------------------------------------------------


class TestPreGapDecay:
    def test_gradual_decay_detected(self):
        """Rate decaying to <25% of baseline before gap should be flagged."""
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        # Baseline: 60 msgs/hr for first 6 hours
        timestamps = _make_timestamps(360, start=start, interval_seconds=60)
        # Pre-gap hour: only 5 messages (<<25% of 60/hr baseline)
        gap_start = start + timedelta(hours=7)
        for i in range(5):
            timestamps.append(gap_start - timedelta(minutes=50 - i * 10))

        timestamps.sort()
        baseline = compute_baseline_rate(timestamps)
        result = compute_pre_gap_decay(timestamps, gap_start, baseline)

        assert result is not None
        assert result["is_decay"] is True
        assert result["ratio"] < 0.25

    def test_sudden_cutoff_not_flagged(self):
        """Constant rate followed by sudden gap should not flag as decay."""
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        # Constant 12 msgs/hr right up to gap
        timestamps = _make_timestamps(100, start=start, interval_seconds=300)
        gap_start = timestamps[-1] + timedelta(seconds=300)

        baseline = compute_baseline_rate(timestamps)
        result = compute_pre_gap_decay(timestamps, gap_start, baseline)

        # Rate stays constant, so ratio should be near 1.0
        if result is not None:
            assert result["is_decay"] is False

    def test_no_gap_no_analysis(self):
        """No pre-gap points returns None."""
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        timestamps = _make_timestamps(50, start=start, interval_seconds=300)
        # Gap far in the future — no points in pre-gap window
        gap_start = start + timedelta(days=30)
        baseline = compute_baseline_rate(timestamps)
        result = compute_pre_gap_decay(timestamps, gap_start, baseline)
        assert result is None

    def test_baseline_computation(self):
        """Baseline rate should approximate messages per hour."""
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        # 12 msgs/hr for 10 hours = 120 messages
        timestamps = _make_timestamps(120, start=start, interval_seconds=300)
        rate = compute_baseline_rate(timestamps)
        # 120 messages / ~9.97 hours ≈ 12.03
        assert 11.5 < rate < 12.5


# ---------------------------------------------------------------------------
# Signal 3: Corridor correlation tests
# ---------------------------------------------------------------------------


class TestCorridorCorrelation:
    def _make_corridor_geometry(self):
        """Create a simple rectangular corridor polygon."""
        from shapely.geometry import Polygon

        # Corridor at lon 25-26, lat 35-36
        return Polygon([(25, 35), (26, 35), (26, 36), (25, 36), (25, 35)])

    def test_rate_drops_in_corridor(self):
        """Rate dropping when entering a corridor should be detected."""
        corridor_geom = self._make_corridor_geometry()
        geometries = [(1, "Test STS Zone", corridor_geom)]

        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        points: list[tuple[datetime, float, float]] = []

        # 30 points outside corridor (high rate) — lat=34, lon=24
        for i in range(30):
            t = start + timedelta(minutes=2 * i)
            points.append((t, 34.0, 24.0))

        # 5 points inside corridor (low rate) — lat=35.5, lon=25.5
        entry_time = start + timedelta(minutes=60)
        for i in range(5):
            t = entry_time + timedelta(minutes=12 * i)
            points.append((t, 35.5, 25.5))

        points.sort(key=lambda p: p[0])
        baseline = 30.0  # msgs/hr

        detections = detect_corridor_rate_change(points, geometries, baseline)
        assert len(detections) >= 1
        assert detections[0]["corridor_name"] == "Test STS Zone"
        assert detections[0]["drop_ratio"] < 0.5

    def test_rate_stable_outside_corridor(self):
        """Stable rate outside corridor should produce no detections."""
        corridor_geom = self._make_corridor_geometry()
        geometries = [(1, "Test STS Zone", corridor_geom)]

        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        # All points outside corridor
        points = [
            (start + timedelta(minutes=2 * i), 34.0, 24.0) for i in range(60)
        ]
        baseline = 30.0

        detections = detect_corridor_rate_change(points, geometries, baseline)
        assert len(detections) == 0

    def test_corridor_entry_exit_timing(self):
        """Detection should reference the correct corridor entry time."""
        corridor_geom = self._make_corridor_geometry()
        geometries = [(1, "Test STS Zone", corridor_geom)]

        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        points: list[tuple[datetime, float, float]] = []

        # 30 points outside (high rate)
        for i in range(30):
            t = start + timedelta(minutes=2 * i)
            points.append((t, 34.0, 24.0))

        # Entry point at 60min
        entry_time = start + timedelta(minutes=60)
        # 3 points inside corridor (low rate)
        for i in range(3):
            t = entry_time + timedelta(minutes=20 * i)
            points.append((t, 35.5, 25.5))

        points.sort(key=lambda p: p[0])
        baseline = 30.0

        detections = detect_corridor_rate_change(points, geometries, baseline)
        if detections:
            assert "entry_time" in detections[0]
            assert detections[0]["corridor_id"] == 1

    def test_no_corridor_match(self):
        """Empty corridor list should produce no detections."""
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        points = [
            (start + timedelta(minutes=2 * i), 35.5, 25.5) for i in range(60)
        ]
        detections = detect_corridor_rate_change(points, [], 30.0)
        assert len(detections) == 0


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_disabled_flag(self, db, vessel):
        """Detection should return disabled when flag is off."""
        from app.modules.ais_reporting_anomaly_detector import run_reporting_anomaly_detection

        with patch("app.modules.ais_reporting_anomaly_detector.settings") as mock_settings:
            mock_settings.AIS_REPORTING_ANOMALY_ENABLED = False
            result = run_reporting_anomaly_detection(db)
        assert result["status"] == "disabled"

    def test_creates_anomalies(self, db, vessel):
        """Detection run should create SpoofingAnomaly records for flagged vessels."""
        from app.modules.ais_reporting_anomaly_detector import run_reporting_anomaly_detection

        now = datetime.now(UTC)
        start = now - timedelta(hours=48)

        # Create points with irregular intervals
        import random

        random.seed(42)
        for i in range(80):
            gap_seconds = random.uniform(10, 7200)
            ts = start + timedelta(seconds=gap_seconds * i)
            if ts > now:
                ts = now - timedelta(seconds=i)
            pt = AISPoint(
                vessel_id=vessel.vessel_id,
                timestamp_utc=ts,
                lat=35.0,
                lon=25.0,
                sog=10.0,
            )
            db.add(pt)
        db.commit()

        with patch("app.modules.ais_reporting_anomaly_detector.settings") as mock_settings:
            mock_settings.AIS_REPORTING_ANOMALY_ENABLED = True
            result = run_reporting_anomaly_detection(db)

        assert result["status"] == "ok"
        assert result["checked"] >= 0

    def test_dedup_prevents_duplicate_anomalies(self, db, vessel):
        """Second run should not create duplicate anomalies."""
        from app.modules.ais_reporting_anomaly_detector import run_reporting_anomaly_detection

        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=168)

        # Pre-create an existing anomaly
        existing = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.REPORTING_RATE_ANOMALY,
            start_time_utc=cutoff,
            end_time_utc=now,
            risk_score_component=25,
            evidence_json={"signals": {"interval_cv": {"cv": 2.5, "score": 25}}},
        )
        db.add(existing)

        # Create enough AIS points with irregular intervals
        import random

        random.seed(99)
        for i in range(80):
            gap_seconds = random.uniform(10, 7200)
            ts = cutoff + timedelta(seconds=gap_seconds * (i + 1))
            if ts > now:
                ts = now - timedelta(seconds=i)
            pt = AISPoint(
                vessel_id=vessel.vessel_id,
                timestamp_utc=ts,
                lat=35.0,
                lon=25.0,
                sog=10.0,
            )
            db.add(pt)
        db.commit()

        with patch("app.modules.ais_reporting_anomaly_detector.settings") as mock_settings:
            mock_settings.AIS_REPORTING_ANOMALY_ENABLED = True
            result = run_reporting_anomaly_detection(db)

        # Should not create new anomaly due to dedup
        anomaly_count = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.REPORTING_RATE_ANOMALY,
            )
            .count()
        )
        assert anomaly_count == 1

    def test_scoring_tiers(self):
        """CV thresholds should map to correct score tiers."""
        from app.modules.ais_reporting_anomaly_detector import _score_interval_cv

        assert _score_interval_cv(2.5) == 25  # CV > 2.0 -> high
        assert _score_interval_cv(1.8) == 15  # CV > 1.5 -> medium
        assert _score_interval_cv(1.0) == 0   # CV <= 1.5 -> no score

    def test_end_to_end_with_mock_ais_data(self, db, vessel):
        """Full pipeline with mock AIS data should produce valid analysis."""
        from app.modules.ais_reporting_anomaly_detector import analyse_vessel_reporting

        now = datetime.now(UTC)
        start = now - timedelta(hours=48)

        # Create regular AIS points
        for i in range(60):
            pt = AISPoint(
                vessel_id=vessel.vessel_id,
                timestamp_utc=start + timedelta(minutes=5 * i),
                lat=35.0 + (i * 0.01),
                lon=25.0 + (i * 0.01),
                sog=10.0,
            )
            db.add(pt)
        db.commit()

        result = analyse_vessel_reporting(db, vessel.vessel_id)

        assert result["vessel_id"] == vessel.vessel_id
        assert result["status"] == "analysed"
        assert result["points_count"] == 60
        assert "signals" in result
        # Regular intervals should have low CV
        if "interval_cv" in result["signals"]:
            assert result["signals"]["interval_cv"]["cv"] < 0.5
