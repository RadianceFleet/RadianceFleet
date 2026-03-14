"""Tests for circle spoofing pattern classifier."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.modules.circle_classifier import (
    CLASSIFICATION_SCORES,
    classify_circle_pattern,
    check_multi_vessel_coherence,
    compute_centroid_movement,
    compute_radius_stats,
    compute_sog_stats,
)


def _make_point(lat, lon, sog=5.0, ts=None):
    """Create a SimpleNamespace AIS point."""
    return SimpleNamespace(
        lat=lat,
        lon=lon,
        sog=sog,
        timestamp_utc=ts or datetime(2025, 1, 1),
    )


def _make_dict_point(lat, lon, sog=5.0, ts=None):
    """Create a dict AIS point."""
    return {
        "lat": lat,
        "lon": lon,
        "sog": sog,
        "timestamp_utc": ts or datetime(2025, 1, 1),
    }


# --- Stationary pattern tests ---


def test_stationary_pattern_low_centroid_low_sog():
    """Stationary: low centroid movement + low SOG = GPS jamming."""
    # All points clustered within tiny area, low SOG
    base_lat, base_lon = 60.0, 25.0
    points = [
        _make_point(base_lat + 0.0001 * i, base_lon + 0.0001 * (i % 3), sog=0.5)
        for i in range(10)
    ]
    result = classify_circle_pattern(points)
    assert result == "stationary"


def test_stationary_with_dict_points():
    """Stationary classification works with dict points too."""
    base_lat, base_lon = 55.0, 20.0
    points = [
        _make_dict_point(base_lat + 0.0001 * (i % 2), base_lon + 0.0001 * (i % 2), sog=1.0)
        for i in range(10)
    ]
    result = classify_circle_pattern(points)
    assert result == "stationary"


def test_stationary_zero_sog():
    """Stationary when all SOG values are zero."""
    points = [_make_point(60.0, 25.0, sog=0.0) for _ in range(10)]
    result = classify_circle_pattern(points)
    assert result == "stationary"


# --- Deliberate pattern tests ---


def test_deliberate_pattern_high_centroid_consistent_sog():
    """Deliberate: significant centroid movement + consistent high SOG."""
    base_lat, base_lon = 55.0, 20.0
    points = []
    for i in range(12):
        # Move centroid significantly across segments with small consistent radius
        lat = base_lat + 0.05 * i + 0.002 * math.sin(i * 2)
        lon = base_lon + 0.03 * i + 0.002 * math.cos(i * 2)
        points.append(_make_point(lat, lon, sog=8.0))
    result = classify_circle_pattern(points)
    assert result == "deliberate"


def test_deliberate_consistent_high_sog_moderate_movement():
    """Deliberate when SOG is consistently high with moderate centroid movement."""
    base_lat, base_lon = 45.0, 10.0
    points = []
    for i in range(9):
        # Some movement but consistent speed
        lat = base_lat + 0.02 * i
        lon = base_lon + 0.01 * i
        points.append(_make_point(lat, lon, sog=7.0 + 0.5 * (i % 2)))
    result = classify_circle_pattern(points)
    assert result == "deliberate"


# --- Equipment malfunction tests ---


def test_equipment_pattern_high_radius_variance_spiral():
    """Equipment: high radius CV + decreasing radius trend (spiral drift)."""
    center_lat, center_lon = 55.0, 20.0
    points = []
    for i in range(15):
        # Spiral inward: decreasing radius, stays near center
        radius_deg = 0.03 * (1 - i / 15) * (0.5 + 0.5 * abs(math.sin(i)))
        angle = i * math.pi / 4
        lat = center_lat + radius_deg * math.cos(angle)
        lon = center_lon + radius_deg * math.sin(angle)
        # Erratic SOG indicates equipment malfunction
        points.append(_make_point(lat, lon, sog=1.0 + 3.0 * abs(math.sin(i * 2))))
    result = classify_circle_pattern(points)
    assert result == "equipment"


def test_equipment_very_high_radius_cv():
    """Equipment when radius CV is very high even without clear trend."""
    center_lat, center_lon = 55.0, 20.0
    points = []
    for i in range(12):
        # Wildly varying radii but staying near center
        if i % 2 == 0:
            r = 0.015  # larger radius
        else:
            r = 0.001  # tiny radius
        angle = i * math.pi / 6
        lat = center_lat + r * math.cos(angle)
        lon = center_lon + r * math.sin(angle)
        points.append(_make_point(lat, lon, sog=1.5))
    result = classify_circle_pattern(points)
    assert result == "equipment"


# --- Edge cases ---


def test_minimum_points():
    """With fewer than 3 points, default to deliberate."""
    points = [_make_point(55.0, 20.0), _make_point(55.001, 20.001)]
    result = classify_circle_pattern(points)
    assert result == "deliberate"


def test_single_point():
    """Single point defaults to deliberate."""
    result = classify_circle_pattern([_make_point(55.0, 20.0)])
    assert result == "deliberate"


def test_empty_points():
    """Empty list defaults to deliberate."""
    result = classify_circle_pattern([])
    assert result == "deliberate"


def test_all_same_position():
    """All points at exact same position with zero SOG = stationary."""
    points = [_make_point(55.0, 20.0, sog=0.0) for _ in range(10)]
    result = classify_circle_pattern(points)
    assert result == "stationary"


# --- SOG statistics ---


def test_sog_stats_normal():
    """SOG stats computation with normal values."""
    points = [_make_point(55.0, 20.0, sog=s) for s in [5.0, 6.0, 5.5, 5.2, 6.3]]
    stats = compute_sog_stats(points)
    assert stats["count"] == 5
    assert stats["mean"] == pytest.approx(5.6, abs=0.1)
    assert stats["std"] > 0
    assert stats["cv"] > 0


def test_sog_stats_with_none_values():
    """SOG stats skips None values."""
    points = [
        _make_point(55.0, 20.0, sog=5.0),
        _make_point(55.0, 20.0, sog=None),
        _make_point(55.0, 20.0, sog=7.0),
        _make_point(55.0, 20.0, sog=None),
    ]
    stats = compute_sog_stats(points)
    assert stats["count"] == 2
    assert stats["mean"] == pytest.approx(6.0, abs=0.1)


def test_sog_stats_all_none():
    """SOG stats returns zeros when all SOG values are None."""
    points = [_make_point(55.0, 20.0, sog=None) for _ in range(5)]
    stats = compute_sog_stats(points)
    assert stats["count"] == 0
    assert stats["mean"] == 0.0


# --- Radius statistics ---


def test_radius_stats_uniform_circle():
    """Radius stats for points on a uniform circle."""
    center_lat, center_lon = 55.0, 20.0
    r_deg = 0.01
    points = []
    for i in range(8):
        angle = i * math.pi / 4
        lat = center_lat + r_deg * math.cos(angle)
        lon = center_lon + r_deg * math.sin(angle)
        points.append(_make_point(lat, lon))
    stats = compute_radius_stats(points)
    assert stats["mean"] > 0
    # CV should be relatively low for uniform circle
    assert stats["cv"] < 0.5


def test_radius_trend_decreasing():
    """Radius trend is negative when points spiral inward."""
    center_lat, center_lon = 55.0, 20.0
    points = []
    for i in range(10):
        r = 0.05 - 0.004 * i  # decreasing radius
        angle = i * math.pi / 3
        lat = center_lat + r * math.cos(angle)
        lon = center_lon + r * math.sin(angle)
        points.append(_make_point(lat, lon))
    stats = compute_radius_stats(points)
    assert stats["trend"] < 0


# --- Centroid movement ---


def test_centroid_movement_stationary():
    """Centroid movement is near zero for clustered points."""
    points = [_make_point(55.0 + 0.0001 * (i % 3), 20.0 + 0.0001 * (i % 2)) for i in range(9)]
    movement = compute_centroid_movement(points)
    assert movement < 0.5  # Less than 0.5 nm


def test_centroid_movement_moving():
    """Centroid movement is significant when points drift."""
    points = []
    for i in range(9):
        lat = 55.0 + 0.1 * i  # ~6nm per step
        points.append(_make_point(lat, 20.0))
    movement = compute_centroid_movement(points)
    assert movement > 2.0  # More than 2 nm


# --- Classification scores ---


def test_classification_scores():
    """Verify score mapping is correct."""
    assert CLASSIFICATION_SCORES["stationary"] == 25
    assert CLASSIFICATION_SCORES["deliberate"] == 35
    assert CLASSIFICATION_SCORES["equipment"] == 10


# --- Multi-vessel coherence ---


def test_multi_vessel_coherence_found():
    """Coherence check returns True when other circle anomalies exist nearby."""
    mock_db = MagicMock()
    mock_anomaly = MagicMock()
    mock_anomaly.evidence_json = {"centroid_lat": 55.0, "centroid_lon": 20.0}
    mock_db.query.return_value.filter.return_value.all.return_value = [mock_anomaly]

    result = check_multi_vessel_coherence(
        mock_db, 55.01, 20.01, datetime(2025, 1, 1)
    )
    assert result is True


def test_multi_vessel_coherence_none_nearby():
    """Coherence check returns False when no circle anomalies exist nearby."""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = []

    result = check_multi_vessel_coherence(
        mock_db, 55.0, 20.0, datetime(2025, 1, 1)
    )
    assert result is False


def test_multi_vessel_coherence_too_far():
    """Coherence check returns False when anomalies are beyond radius."""
    mock_db = MagicMock()
    mock_anomaly = MagicMock()
    # Very far away
    mock_anomaly.evidence_json = {"centroid_lat": 70.0, "centroid_lon": 50.0}
    mock_db.query.return_value.filter.return_value.all.return_value = [mock_anomaly]

    result = check_multi_vessel_coherence(
        mock_db, 55.0, 20.0, datetime(2025, 1, 1)
    )
    assert result is False


def test_multi_vessel_coherence_no_coords():
    """Coherence check skips anomalies without coordinates in evidence."""
    mock_db = MagicMock()
    mock_anomaly = MagicMock()
    mock_anomaly.evidence_json = {}  # no lat/lon
    mock_db.query.return_value.filter.return_value.all.return_value = [mock_anomaly]

    result = check_multi_vessel_coherence(
        mock_db, 55.0, 20.0, datetime(2025, 1, 1)
    )
    assert result is False


# --- Real-world-like data ---


def test_real_world_stationary_gps_jamming():
    """Simulate real-world GPS jamming: vessel at anchor, AIS reports circles."""
    base_lat, base_lon = 36.8, 22.5  # Laconian Gulf
    t0 = datetime(2025, 6, 15, 10, 0, 0)
    points = []
    for i in range(20):
        # Tiny movement around anchor point, SOG reported as ~0
        angle = i * math.pi / 5
        r = 0.0002  # ~15 meters
        lat = base_lat + r * math.cos(angle)
        lon = base_lon + r * math.sin(angle)
        points.append(_make_point(lat, lon, sog=0.2, ts=t0 + timedelta(minutes=15 * i)))
    result = classify_circle_pattern(points)
    assert result == "stationary"


def test_real_world_deliberate_transit():
    """Simulate deliberate spoofing: vessel transiting while broadcasting circles."""
    base_lat, base_lon = 36.0, 22.0
    t0 = datetime(2025, 6, 15, 10, 0, 0)
    points = []
    for i in range(20):
        # Significant north-east movement + consistent speed
        lat = base_lat + 0.03 * i
        lon = base_lon + 0.015 * i + 0.005 * math.sin(i)
        points.append(_make_point(lat, lon, sog=10.0, ts=t0 + timedelta(minutes=15 * i)))
    result = classify_circle_pattern(points)
    assert result == "deliberate"
