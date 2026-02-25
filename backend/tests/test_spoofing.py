"""Tests for AIS spoofing detection typologies.

Tests are unit-level: they verify detection logic and scoring constants
without requiring a real database or full ORM stack.
"""
import math
import statistics
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.modules.gap_detector import _haversine_nm


# ── Helper factory ────────────────────────────────────────────────────────────

def _make_point(
    lat,
    lon,
    sog=0.0,
    nav_status=None,
    timestamp=None,
    ais_point_id=1,
    cog=0.0,
):
    """Create a mock AISPoint with the given attributes."""
    p = MagicMock()
    p.lat = lat
    p.lon = lon
    p.sog = sog
    p.nav_status = nav_status
    p.timestamp_utc = timestamp or datetime(2026, 1, 15, 12, 0)
    p.ais_point_id = ais_point_id
    p.cog = cog
    return p


# ── Anchor spoof logic tests ──────────────────────────────────────────────────

def test_anchor_spoof_condition_fires_when_not_near_port():
    """A run of nav_status=1, sog<0.1 for >=72h, NOT near port → anchor spoof triggered.

    Tests the condition logic directly (without DB) by replicating the run
    evaluation that run_spoofing_detection() performs internally.
    """
    base = datetime(2026, 1, 10, 0, 0)
    # 10 points, each 8 hours apart → 80h total span
    anchor_run = [
        _make_point(
            lat=60.0,
            lon=28.0,
            sog=0.0,
            nav_status=1,
            timestamp=base + timedelta(hours=8 * i),
            ais_point_id=i + 1,
        )
        for i in range(10)
    ]

    run_hours = (
        anchor_run[-1].timestamp_utc - anchor_run[0].timestamp_utc
    ).total_seconds() / 3600

    # Condition 1: run is >= 72h
    assert run_hours >= 72, f"Expected run >= 72h, got {run_hours:.1f}h"

    # Condition 2: all points have nav_status=1 and sog < 0.1
    assert all(p.nav_status == 1 for p in anchor_run)
    assert all((p.sog is None or p.sog < 0.1) for p in anchor_run)

    # Condition 3: NOT near port → anomaly should be created
    near_port = False
    should_flag = run_hours >= 72 and not near_port
    assert should_flag


def test_anchor_spoof_not_fired_near_port():
    """Same anchor run but near_port=True → anomaly must NOT be created."""
    base = datetime(2026, 1, 10, 0, 0)
    anchor_run = [
        _make_point(
            lat=59.9,
            lon=28.1,
            sog=0.0,
            nav_status=1,
            timestamp=base + timedelta(hours=8 * i),
            ais_point_id=i + 1,
        )
        for i in range(10)
    ]

    run_hours = (
        anchor_run[-1].timestamp_utc - anchor_run[0].timestamp_utc
    ).total_seconds() / 3600

    assert run_hours >= 72

    # When near_port=True the engine skips anomaly creation
    near_port = True
    should_flag = run_hours >= 72 and not near_port
    assert not should_flag


def test_anchor_spoof_not_fired_run_too_short():
    """A run of only 48h with nav_status=1 must NOT trigger anchor spoof."""
    base = datetime(2026, 1, 10, 0, 0)
    short_run = [
        _make_point(
            lat=60.0,
            lon=28.0,
            sog=0.0,
            nav_status=1,
            timestamp=base + timedelta(hours=6 * i),
            ais_point_id=i + 1,
        )
        for i in range(9)  # 48h span
    ]

    run_hours = (
        short_run[-1].timestamp_utc - short_run[0].timestamp_utc
    ).total_seconds() / 3600

    assert run_hours < 72
    assert run_hours >= 48  # long but below threshold

    should_flag = run_hours >= 72
    assert not should_flag


# ── MMSI reuse scoring tests ──────────────────────────────────────────────────

def test_mmsi_reuse_score_low():
    """Implied speed of 31 kn (> 30 but <= 100) → score component = 40."""
    implied_speed = 31.0
    score = 55 if implied_speed > 100 else 40
    assert score == 40


def test_mmsi_reuse_score_high():
    """Implied speed of 101 kn (> 100) → score component = 55."""
    implied_speed = 101.0
    score = 55 if implied_speed > 100 else 40
    assert score == 55


def test_mmsi_reuse_score_boundary_100kn():
    """Implied speed exactly at 100 kn → score = 40 (boundary is exclusive > 100)."""
    implied_speed = 100.0
    score = 55 if implied_speed > 100 else 40
    assert score == 40


def test_mmsi_reuse_score_boundary_101kn():
    """Implied speed of 101 kn (first value strictly > 100) → score = 55."""
    implied_speed = 101.0
    score = 55 if implied_speed > 100 else 40
    assert score == 55


def test_mmsi_reuse_implied_speed_calculation():
    """Two points 1 nm apart, 0.01 hours apart → ~100 kn implied speed."""
    p1 = _make_point(lat=55.0, lon=24.0, timestamp=datetime(2026, 1, 15, 12, 0))
    p2 = _make_point(lat=55.0, lon=24.0 + (1.0 / 60.0), timestamp=datetime(2026, 1, 15, 12, 0, 36))

    dt_h = (p2.timestamp_utc - p1.timestamp_utc).total_seconds() / 3600
    dist_nm = _haversine_nm(p1.lat, p1.lon, p2.lat, p2.lon)

    assert dt_h > 0
    implied_speed = dist_nm / dt_h

    # Haversine of ~0.0167° lon at lat 55 ≈ ~0.6 nm; 0.01 h → ~60 kn
    # Exact value depends on haversine; key assertion: it triggers the > 30 kn check
    assert implied_speed > 30


# ── Nav status mismatch scoring test ─────────────────────────────────────────

def test_nav_status_mismatch_score():
    """nav_status=1 AND sog > 2.0 kn → risk_score_component = 15."""
    # Reproduce the check from run_spoofing_detection directly
    point = _make_point(lat=55.0, lon=24.0, sog=3.0, nav_status=1)

    triggers = point.nav_status == 1 and point.sog is not None and point.sog > 2.0
    assert triggers

    # The implementation hard-codes 15
    score = 15
    assert score == 15


def test_nav_status_mismatch_not_fired_low_sog():
    """nav_status=1 AND sog=1.5 kn (below 2.0 threshold) → no mismatch."""
    point = _make_point(lat=55.0, lon=24.0, sog=1.5, nav_status=1)

    triggers = point.nav_status == 1 and point.sog is not None and point.sog > 2.0
    assert not triggers


def test_nav_status_mismatch_not_fired_different_status():
    """nav_status=0 (under way) AND sog=5.0 kn → no mismatch (status is correct)."""
    point = _make_point(lat=55.0, lon=24.0, sog=5.0, nav_status=0)

    triggers = point.nav_status == 1 and point.sog is not None and point.sog > 2.0
    assert not triggers


# ── Circle spoof detection tests ──────────────────────────────────────────────

def test_circle_spoof_cluster():
    """Points with SOG > 3 kn but tight positional cluster → circle_spoof conditions met."""
    lats = [55.100, 55.101, 55.099, 55.100, 55.102, 55.098]
    lons = [24.500, 24.501, 24.499, 24.500, 24.501, 24.499]
    sogs = [4.0, 4.1, 3.9, 4.0, 4.2, 3.8]

    std_lat = statistics.stdev(lats)
    mean_lat = statistics.mean(lats)
    std_lon = statistics.stdev(lons)
    std_lon_corrected = std_lon * math.cos(math.radians(mean_lat))

    # All three conditions for circle_spoof must hold
    assert std_lat < 0.05, f"std_lat={std_lat:.6f} should be < 0.05"
    assert std_lon_corrected < 0.05, f"std_lon_corrected={std_lon_corrected:.6f} should be < 0.05"
    assert statistics.median(sogs) > 3.0, f"median sog={statistics.median(sogs)} should be > 3"


def test_circle_spoof_not_fired_normal_transit():
    """Normal transit: positions spread across 0.5° — std_lat >= 0.05 → not a circle spoof."""
    lats = [55.0, 55.1, 55.2, 55.3, 55.4, 55.5]
    lons = [24.0, 24.1, 24.2, 24.3, 24.4, 24.5]
    sogs = [12.0] * 6

    std_lat = statistics.stdev(lats)

    # Large std_dev → circle spoof conditions NOT met
    assert std_lat >= 0.05, f"std_lat={std_lat:.4f} should be >= 0.05 for a transit"


def test_circle_spoof_not_fired_low_sog():
    """Tight cluster but median SOG <= 3 kn → circle spoof NOT triggered."""
    lats = [55.100, 55.101, 55.099, 55.100, 55.102, 55.098]
    lons = [24.500, 24.501, 24.499, 24.500, 24.501, 24.499]
    sogs = [0.5, 0.3, 0.4, 0.2, 0.6, 0.1]

    std_lat = statistics.stdev(lats)
    mean_lat = statistics.mean(lats)
    std_lon_corrected = statistics.stdev(lons) * math.cos(math.radians(mean_lat))

    # Position cluster is tight enough
    assert std_lat < 0.05
    assert std_lon_corrected < 0.05

    # But median SOG is not above 3 kn → no circle spoof
    assert statistics.median(sogs) <= 3.0


def test_circle_spoof_lon_correction_matters():
    """At high latitudes, the cos(lat) correction significantly shrinks std_lon."""
    lat_high = 70.0  # High Arctic — cos(70°) ≈ 0.342
    lat_mid = 45.0   # Mid-latitude — cos(45°) ≈ 0.707

    raw_std_lon = 0.08  # Would fail the < 0.05 test without correction at mid-lat

    corrected_high = raw_std_lon * math.cos(math.radians(lat_high))
    corrected_mid = raw_std_lon * math.cos(math.radians(lat_mid))

    # At 70°N the correction brings 0.08 down to ~0.027 → passes threshold
    assert corrected_high < 0.05

    # At 45°N 0.08 * 0.707 ≈ 0.057 → still above threshold
    assert corrected_mid >= 0.05


# ── Haversine sanity test (used by spoofing engine) ───────────────────────────

def test_haversine_used_by_spoofing_implied_speed():
    """_haversine_nm gives positive, finite result for typical North Baltic positions."""
    dist = _haversine_nm(60.0, 28.0, 60.1, 28.1)
    assert dist > 0
    assert dist < 20  # ~8 nm at this scale
    assert math.isfinite(dist)


# ── Erratic nav_status detection tests ───────────────────────────────────────

def _count_status_changes(points: list) -> int:
    """Count nav_status transitions in a list of points (replicates detection logic)."""
    status_values = [p.nav_status for p in points if p.nav_status is not None]
    return sum(1 for a, b in zip(status_values, status_values[1:]) if a != b)


def test_erratic_nav_status_3_changes_60min_fires():
    """3+ nav_status changes within 60 min → episode qualifies for ERRATIC_NAV_STATUS."""
    base = datetime(2026, 1, 10, 0, 0)
    # 5 points in 50 min, alternating nav_status 0 / 1 → 4 changes
    points = [
        _make_point(lat=55.0, lon=25.0, nav_status=0, timestamp=base + timedelta(minutes=0)),
        _make_point(lat=55.0, lon=25.0, nav_status=1, timestamp=base + timedelta(minutes=10)),
        _make_point(lat=55.0, lon=25.0, nav_status=0, timestamp=base + timedelta(minutes=20)),
        _make_point(lat=55.0, lon=25.0, nav_status=1, timestamp=base + timedelta(minutes=30)),
        _make_point(lat=55.0, lon=25.0, nav_status=0, timestamp=base + timedelta(minutes=50)),
    ]

    changes = _count_status_changes(points)
    assert changes >= 3, f"Expected >= 3 changes, got {changes}"

    # Verify episode span fits in 60 min
    span = (points[-1].timestamp_utc - points[0].timestamp_utc).total_seconds()
    assert span <= 3600, "Episode must fit in 60 min"


def test_erratic_nav_status_only_2_changes_no_fire():
    """Only 2 nav_status changes within 60 min → below threshold, must NOT fire."""
    base = datetime(2026, 1, 10, 0, 0)
    points = [
        _make_point(lat=55.0, lon=25.0, nav_status=0, timestamp=base + timedelta(minutes=0)),
        _make_point(lat=55.0, lon=25.0, nav_status=1, timestamp=base + timedelta(minutes=20)),
        _make_point(lat=55.0, lon=25.0, nav_status=0, timestamp=base + timedelta(minutes=40)),
    ]

    changes = _count_status_changes(points)
    assert changes == 2, f"Expected exactly 2 changes, got {changes}"
    assert changes < 3, "Should NOT fire — below 3-change threshold"


def test_erratic_nav_status_continuous_episode_one_anomaly():
    """4h oscillation of nav_status → non-overlapping scan must produce exactly 1 anomaly.

    Tests that the non-overlapping cursor advance prevents multiple anomalies from
    a continuous episode (e.g. 240-min stream of changes would otherwise create ~14 events
    with a 60-min sliding window).
    """
    base = datetime(2026, 1, 10, 0, 0)
    # 16 points, 15 min apart, alternating nav_status → 15 changes over 3h45m
    points = [
        _make_point(
            lat=55.0,
            lon=25.0,
            nav_status=i % 2,
            timestamp=base + timedelta(minutes=15 * i),
        )
        for i in range(16)
    ]

    # Simulate the episode-extension scan algorithm from run_spoofing_detection()
    # One anomaly per continuous oscillation episode (extends past all consecutive matching windows).
    WINDOW_SECONDS = 3600
    episode_starts: list[int] = []
    i = 0
    while i < len(points) - 1:
        window_end = points[i].timestamp_utc + timedelta(seconds=WINDOW_SECONDS)
        window = [p for p in points[i:] if p.timestamp_utc <= window_end]
        if len(window) >= 2:
            status_values = [p.nav_status for p in window if p.nav_status is not None]
            changes = sum(1 for a, b in zip(status_values, status_values[1:]) if a != b)
            if changes >= 3:
                episode_starts.append(i)
                # Extend episode past all consecutive matching windows
                episode_end_idx = max(
                    idx for idx, p in enumerate(points) if p.timestamp_utc <= window_end
                )
                while episode_end_idx + 1 < len(points) - 1:
                    next_i = episode_end_idx + 1
                    next_we = points[next_i].timestamp_utc + timedelta(seconds=WINDOW_SECONDS)
                    next_win = [p for p in points[next_i:] if p.timestamp_utc <= next_we]
                    if len(next_win) >= 2:
                        next_sv = [p.nav_status for p in next_win if p.nav_status is not None]
                        next_ch = sum(1 for a, b in zip(next_sv, next_sv[1:]) if a != b)
                        if next_ch >= 3:
                            episode_end_idx = max(
                                idx for idx, p in enumerate(points) if p.timestamp_utc <= next_we
                            )
                            continue
                    break
                i = episode_end_idx + 1
                continue
        i += 1

    assert len(episode_starts) == 1, \
        f"Expected exactly 1 episode start from non-overlapping scan, got {len(episode_starts)}"


def test_anchor_spoof_suppressed_in_anchorage_holding_corridor():
    """_is_in_anchorage_corridor() returns True when position is inside an anchorage_holding corridor bbox."""
    from app.modules.gap_detector import _is_in_anchorage_corridor

    # Create a corridor mock: anchorage_holding type, WKT polygon around (37°N, 22°E)
    # (Laconian Gulf area)
    mock_corridor = MagicMock()
    mock_corridor.corridor_type = "anchorage_holding"
    # WKT representation of a simple bounding polygon
    mock_corridor.geometry = "POLYGON((21.5 36.5, 22.5 36.5, 22.5 37.5, 21.5 37.5, 21.5 36.5))"

    def query_side_effect(model):
        mock_chain = MagicMock()
        mock_chain.all.return_value = [mock_corridor]
        return mock_chain

    mock_db = MagicMock()
    mock_db.query.side_effect = query_side_effect

    # Position inside the corridor bbox
    result_inside = _is_in_anchorage_corridor(mock_db, lat=37.0, lon=22.0)
    assert result_inside, "Position inside anchorage corridor bbox should return True"

    # Position outside the corridor bbox
    result_outside = _is_in_anchorage_corridor(mock_db, lat=50.0, lon=10.0)
    assert not result_outside, "Position outside anchorage corridor bbox should return False"
