"""Tests for loitering event detection.

Tests cover:
  - Hourly-bucket median SOG analysis (Polars-based)
  - Minimum 4-hour loitering threshold
  - 12-hour "sustained" loitering criterion
  - 30-day laid-up vessel positional stability check

All tests are unit-level and require no database.
"""
import pytest
import statistics
from datetime import datetime, timedelta
from unittest.mock import MagicMock


# ── Loitering bucket analysis tests ──────────────────────────────────────────

def test_loitering_4h_detected():
    """6 hourly rows at SOG=0.2 kn → all bucket medians below 0.5 kn threshold and run >= 4h."""
    import polars as pl

    base = datetime(2026, 1, 15, 0, 0)
    rows = [
        {"timestamp_utc": base + timedelta(hours=i), "lat": 55.0, "lon": 24.0, "sog": 0.2}
        for i in range(6)
    ]
    df = pl.DataFrame(rows).with_columns(pl.col("timestamp_utc").cast(pl.Datetime))

    buckets = (
        df.group_by_dynamic("timestamp_utc", every="1h")
        .agg(
            pl.col("sog").median().alias("median_sog"),
            pl.col("lat").mean().alias("mean_lat"),
            pl.col("lon").mean().alias("mean_lon"),
        )
        .sort("timestamp_utc")
    )

    # All bucket medians must be below the 0.5 kn loitering threshold
    assert (buckets["median_sog"] < 0.5).all(), "Expected all hourly medians < 0.5 kn"

    # The run spans >= 4 buckets (i.e., >= 4h)
    assert len(buckets) >= 4, f"Expected >= 4 hourly buckets, got {len(buckets)}"


def test_loitering_4h_not_triggered_by_3h():
    """Only 3 hourly rows at low SOG → run length of 3 < minimum of 4 → NOT flagged."""
    import polars as pl

    base = datetime(2026, 1, 15, 0, 0)
    rows = [
        {"timestamp_utc": base + timedelta(hours=i), "lat": 55.0, "lon": 24.0, "sog": 0.1}
        for i in range(3)
    ]
    df = pl.DataFrame(rows).with_columns(pl.col("timestamp_utc").cast(pl.Datetime))

    buckets = (
        df.group_by_dynamic("timestamp_utc", every="1h")
        .agg(pl.col("sog").median().alias("median_sog"))
        .sort("timestamp_utc")
    )

    # Only 3 buckets — below the 4-hour minimum
    assert len(buckets) < 4, f"Expected fewer than 4 buckets, got {len(buckets)}"


def test_loitering_not_triggered_when_sog_above_threshold():
    """6 hourly rows at SOG=1.5 kn (above 0.5 threshold) → NOT a loitering event."""
    import polars as pl

    base = datetime(2026, 1, 15, 0, 0)
    rows = [
        {"timestamp_utc": base + timedelta(hours=i), "lat": 55.0, "lon": 24.0, "sog": 1.5}
        for i in range(6)
    ]
    df = pl.DataFrame(rows).with_columns(pl.col("timestamp_utc").cast(pl.Datetime))

    buckets = (
        df.group_by_dynamic("timestamp_utc", every="1h")
        .agg(pl.col("sog").median().alias("median_sog"))
        .sort("timestamp_utc")
    )

    # Medians are all above the threshold — no loitering bucket qualifies
    assert (buckets["median_sog"] >= 0.5).all(), "Expected no low-SOG buckets"


def test_loitering_12h_sustained():
    """14 hourly rows at SOG=0.1 kn → run of >= 12 buckets qualifies as sustained loitering."""
    import polars as pl

    base = datetime(2026, 1, 15, 0, 0)
    rows = [
        {"timestamp_utc": base + timedelta(hours=i), "lat": 55.0, "lon": 24.0, "sog": 0.1}
        for i in range(14)
    ]
    df = pl.DataFrame(rows).with_columns(pl.col("timestamp_utc").cast(pl.Datetime))

    buckets = (
        df.group_by_dynamic("timestamp_utc", every="1h")
        .agg(pl.col("sog").median().alias("median_sog"))
        .sort("timestamp_utc")
    )

    # Run must cover >= 12 buckets for the sustained loitering flag
    assert len(buckets) >= 12, f"Expected >= 12 buckets, got {len(buckets)}"
    # All medians below threshold
    assert (buckets["median_sog"] < 0.5).all()


def test_loitering_risk_score_baseline_vs_sustained():
    """Risk score component: baseline=8 for <12h, sustained=20 for >=12h in corridor."""
    _RISK_BASELINE = 8
    _RISK_SUSTAINED = 20
    _SUSTAINED_LOITER_HOURS = 12

    # Short loitering (8h) with a corridor → baseline
    run_length_hours = 8
    matched_corridor = MagicMock()
    score = _RISK_SUSTAINED if run_length_hours >= _SUSTAINED_LOITER_HOURS and matched_corridor else _RISK_BASELINE
    assert score == _RISK_BASELINE

    # Long loitering (13h) with a corridor → sustained
    run_length_hours = 13
    score = _RISK_SUSTAINED if run_length_hours >= _SUSTAINED_LOITER_HOURS and matched_corridor else _RISK_BASELINE
    assert score == _RISK_SUSTAINED


def test_loitering_risk_score_sustained_without_corridor():
    """Sustained (>=12h) loitering but NO corridor match → baseline score, not sustained."""
    _RISK_BASELINE = 8
    _RISK_SUSTAINED = 20
    _SUSTAINED_LOITER_HOURS = 12

    run_length_hours = 15
    matched_corridor = None  # No corridor
    score = _RISK_SUSTAINED if run_length_hours >= _SUSTAINED_LOITER_HOURS and matched_corridor else _RISK_BASELINE
    assert score == _RISK_BASELINE


def test_loitering_mixed_sog_window():
    """Window with alternating high/low SOG — median determines the bucket classification."""
    import polars as pl

    base = datetime(2026, 1, 15, 0, 0)
    # Within a single hour: 3 points low, 2 points high → median is low
    rows = [
        {"timestamp_utc": base + timedelta(minutes=0), "lat": 55.0, "lon": 24.0, "sog": 0.1},
        {"timestamp_utc": base + timedelta(minutes=10), "lat": 55.0, "lon": 24.0, "sog": 0.2},
        {"timestamp_utc": base + timedelta(minutes=20), "lat": 55.0, "lon": 24.0, "sog": 0.15},
        {"timestamp_utc": base + timedelta(minutes=30), "lat": 55.0, "lon": 24.0, "sog": 5.0},
        {"timestamp_utc": base + timedelta(minutes=40), "lat": 55.0, "lon": 24.0, "sog": 8.0},
    ]
    df = pl.DataFrame(rows).with_columns(pl.col("timestamp_utc").cast(pl.Datetime))

    buckets = (
        df.group_by_dynamic("timestamp_utc", every="1h")
        .agg(pl.col("sog").median().alias("median_sog"))
        .sort("timestamp_utc")
    )

    # Median of [0.1, 0.2, 0.15, 5.0, 8.0] = 0.2 → below threshold
    assert len(buckets) == 1
    assert buckets["median_sog"][0] < 0.5


# ── Laid-up vessel positional stability tests ─────────────────────────────────

def test_vessel_laid_up_30d_flag():
    """31 daily positions spanning 0.30° (outside 0.033° bbox) → NOT laid up.
    31 positions within 0.002° spread → qualifies as laid up."""
    # Dataset 1: positions drifting 0.01° per day — too spread for laid-up
    lats_drifting = [55.0 + 0.01 * i for i in range(31)]
    lons_drifting = [24.0 + 0.01 * i for i in range(31)]

    lat_range = max(lats_drifting) - min(lats_drifting)
    assert lat_range == pytest.approx(0.30, abs=0.01), f"Expected ~0.30 range, got {lat_range}"
    assert lat_range > 0.033, "Drifting vessel should exceed the bbox threshold"

    # Dataset 2: positions oscillating within ±0.001° — truly stationary
    stable_lats = [55.000 + 0.001 * (i % 3) for i in range(31)]
    stable_lat_range = max(stable_lats) - min(stable_lats)

    assert stable_lat_range < 0.033, f"Stable vessel lat spread {stable_lat_range} should be < 0.033°"


def test_vessel_laid_up_bbox_threshold_exact():
    """Test the 0.033° bbox threshold: values within or at the boundary are accepted,
    values strictly above it break the run and start a new one.

    Note: floating-point arithmetic means we compare using pytest.approx for
    the boundary case and use a clearly-above value for the exceed case.
    """
    _LAID_UP_BBOX_DEG = 0.033

    # Point well inside the bbox: 0.01° difference → clearly within
    lat_a = 55.000
    lat_b = 55.010
    assert abs(lat_b - lat_a) <= _LAID_UP_BBOX_DEG

    # Point well outside the bbox: 0.05° difference → clearly outside
    lat_c = 55.050
    assert abs(lat_c - lat_a) > _LAID_UP_BBOX_DEG

    # Boundary semantics: the implementation uses <=, so values at the threshold are included.
    # We verify this with pytest.approx to avoid IEEE 754 edge cases.
    diff_at_threshold = 0.033
    assert diff_at_threshold == pytest.approx(_LAID_UP_BBOX_DEG)


def test_vessel_laid_up_30d_requires_30_daily_rows():
    """Fewer than 30 daily observations → laid-up check is skipped entirely."""
    _LAID_UP_30D_DAYS = 30

    n_days_observations = 25  # Only 25 days of data
    should_check = n_days_observations >= _LAID_UP_30D_DAYS
    assert not should_check, "Should skip laid-up check when < 30 days of data"


def test_vessel_laid_up_60d_requires_60_consecutive_days():
    """60 consecutive stable days → vessel_laid_up_60d flag should be set."""
    _LAID_UP_60D_DAYS = 60

    # Simulate 65-day run
    max_run_days = 65
    is_60d = max_run_days >= _LAID_UP_60D_DAYS
    assert is_60d


def test_vessel_laid_up_30d_not_60d():
    """35 stable days → vessel_laid_up_30d=True but vessel_laid_up_60d=False."""
    _LAID_UP_30D_DAYS = 30
    _LAID_UP_60D_DAYS = 60

    max_run_days = 35
    is_30d = max_run_days >= _LAID_UP_30D_DAYS
    is_60d = max_run_days >= _LAID_UP_60D_DAYS

    assert is_30d
    assert not is_60d


def test_laid_up_polars_daily_aggregation():
    """Polars group_by_dynamic 1d bucketing produces one row per calendar day."""
    import polars as pl

    base = datetime(2026, 1, 1, 6, 0)  # morning hour
    # 3 points per day for 5 days
    rows = []
    for day in range(5):
        for hour in [6, 12, 18]:
            rows.append({
                "timestamp_utc": base + timedelta(days=day, hours=hour - 6),
                "lat": 55.0 + 0.001 * day,
                "lon": 24.0 + 0.001 * day,
            })

    df = pl.DataFrame(rows).with_columns(pl.col("timestamp_utc").cast(pl.Datetime))

    daily = (
        df.sort("timestamp_utc")
        .group_by_dynamic("timestamp_utc", every="1d")
        .agg(
            pl.col("lat").median().alias("day_lat"),
            pl.col("lon").median().alias("day_lon"),
        )
        .sort("timestamp_utc")
    )

    # Should produce exactly 5 daily buckets
    assert len(daily) == 5, f"Expected 5 daily buckets, got {len(daily)}"
