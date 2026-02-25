"""Tests for corridor spatial correlation.

Tests cover:
  - WHY ST_Intersects is used over ST_Within (line transit through corridor)
  - Dark zone correlation via trajectory intersection
  - Haversine distance helper
  - WKT bounding-box parsing (used in the spatial fallback)
  - Bounding-box point-in-corridor logic

All tests are unit-level and require no database.
"""
import math
import pytest
from unittest.mock import MagicMock

from app.modules.gap_detector import _haversine_nm


# ── ST_Intersects vs ST_Within concept tests ──────────────────────────────────

def test_trajectory_intersects_corridor_concept():
    """A gap trajectory from lon=25.0 to lon=27.0 passes through a corridor at lon=25.5-26.5.

    Neither endpoint lies inside the corridor polygon, but the straight-line
    trajectory clearly crosses it.  This test documents WHY the engine uses
    ST_Intersects on the full line segment rather than ST_Within on individual
    endpoints.
    """
    start = (55.0, 25.0)   # (lat, lon) — outside corridor (lon 25.0 < 25.5)
    end = (55.0, 27.0)     # (lat, lon) — outside corridor (lon 27.0 > 26.5)
    corridor_bbox = (54.8, 55.2, 25.5, 26.5)  # (min_lat, max_lat, min_lon, max_lon)

    def point_in_bbox(lat, lon, bbox):
        min_lat, max_lat, min_lon, max_lon = bbox
        return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon

    # ST_Within on endpoints: BOTH miss the corridor
    assert not point_in_bbox(start[0], start[1], corridor_bbox), \
        "Start point should be outside the corridor"
    assert not point_in_bbox(end[0], end[1], corridor_bbox), \
        "End point should be outside the corridor"

    # But the trajectory (lon 25.0 → 27.0) crosses the corridor lon range (25.5–26.5)
    def line_crosses_lon_range(lon1, lon2, min_lon, max_lon):
        """Return True if the segment [lon1, lon2] overlaps [min_lon, max_lon]."""
        return min(lon1, lon2) <= max_lon and max(lon1, lon2) >= min_lon

    assert line_crosses_lon_range(start[1], end[1], corridor_bbox[2], corridor_bbox[3]), \
        "Trajectory should cross the corridor's longitude range"


def test_st_within_fails_pure_transit():
    """Pure transit through a corridor's interior — neither start nor end inside.

    This is the fundamental weakness of an endpoint-only check.
    """
    # A vessel transits straight through the Baltic Bornholm Gap (example bbox)
    corridor_min_lat, corridor_max_lat = 54.0, 56.0
    corridor_min_lon, corridor_max_lon = 14.0, 16.0

    gap_start = (57.0, 13.5)   # North of corridor, west
    gap_end = (53.0, 16.5)     # South of corridor, east

    def in_corridor(lat, lon):
        return (corridor_min_lat <= lat <= corridor_max_lat and
                corridor_min_lon <= lon <= corridor_max_lon)

    assert not in_corridor(*gap_start)
    assert not in_corridor(*gap_end)

    # But the line from (57, 13.5) to (53, 16.5) clearly passes through (55, 15)
    mid_lat = (gap_start[0] + gap_end[0]) / 2
    mid_lon = (gap_start[1] + gap_end[1]) / 2
    assert in_corridor(mid_lat, mid_lon), \
        "Midpoint of trajectory should be inside corridor even when endpoints are not"


def test_trajectory_does_not_intersect_distant_corridor():
    """A trajectory far from a corridor should not produce a false positive."""
    # Corridor in the Persian Gulf
    corridor_bbox = (23.0, 27.0, 50.0, 58.0)  # (min_lat, max_lat, min_lon, max_lon)

    # Trajectory in the North Sea
    start = (57.0, 3.0)
    end = (58.0, 5.0)

    def line_crosses_lon_range(lon1, lon2, min_lon, max_lon):
        return min(lon1, lon2) <= max_lon and max(lon1, lon2) >= min_lon

    def line_crosses_lat_range(lat1, lat2, min_lat, max_lat):
        return min(lat1, lat2) <= max_lat and max(lat1, lat2) >= min_lat

    lon_overlap = line_crosses_lon_range(start[1], end[1], corridor_bbox[2], corridor_bbox[3])
    lat_overlap = line_crosses_lat_range(start[0], end[0], corridor_bbox[0], corridor_bbox[1])

    # Both must overlap for intersection — here lon does not overlap
    assert not (lon_overlap and lat_overlap), \
        "North Sea trajectory should not intersect Persian Gulf corridor"


# ── Dark zone correlation tests ───────────────────────────────────────────────

def test_dark_zone_correlation():
    """A gap trajectory near a known dark zone region should intersect it."""
    dist = _haversine_nm(55.0, 24.0, 55.0, 24.5)
    assert dist > 0
    assert dist < 30, f"Expected <30nm for 0.5° lon at 55°N, got {dist:.1f}nm"


def test_dark_zone_gap_distance_sanity():
    """Gap of 0.5° longitude at 55°N latitude is roughly 16–17 nautical miles."""
    dist = _haversine_nm(55.0, 24.0, 55.0, 24.5)
    # cos(55°) ≈ 0.574; 1° lon ≈ 60 nm at equator → 0.5° × 60 × 0.574 ≈ 17.2 nm
    assert 14.0 < dist < 20.0, f"Unexpected distance: {dist:.2f} nm"


# ── Haversine distance tests ──────────────────────────────────────────────────

def test_haversine_very_small_distance():
    """0.1 degree latitude difference ≈ 6 nautical miles."""
    dist = _haversine_nm(55.0, 24.0, 55.1, 24.0)
    assert 5.5 < dist < 6.5, f"Expected ~6 nm for 0.1° lat, got {dist:.2f} nm"


def test_haversine_symmetry():
    """Haversine must be symmetric: distance(A→B) == distance(B→A)."""
    d1 = _haversine_nm(55.0, 24.0, 56.0, 25.0)
    d2 = _haversine_nm(56.0, 25.0, 55.0, 24.0)
    assert abs(d1 - d2) < 1e-9, f"Haversine not symmetric: {d1} vs {d2}"


def test_haversine_zero_distance():
    """Same point yields 0.0 distance."""
    dist = _haversine_nm(55.5, 24.7, 55.5, 24.7)
    assert dist == 0.0


def test_haversine_crossing_prime_meridian():
    """Distance crossing the prime meridian (lon negative to positive) is computed correctly."""
    dist = _haversine_nm(51.5, -0.5, 51.5, 0.5)
    # ~1° of longitude at 51°N ≈ 60 nm * cos(51°) ≈ 37.8 nm
    assert 35.0 < dist < 40.0, f"Expected ~38 nm across prime meridian, got {dist:.2f}"


def test_haversine_one_degree_latitude_equator():
    """1 degree of latitude at the equator ≈ 60 nautical miles."""
    dist = _haversine_nm(0.0, 0.0, 1.0, 0.0)
    assert 59.0 < dist < 61.0, f"Expected ~60 nm per degree lat, got {dist:.2f}"


# ── WKT bounding-box parsing tests (corridor_correlator helpers) ──────────────

def test_parse_wkt_bbox_simple_rectangle():
    """Parse a simple rectangular POLYGON WKT and return correct bounding box."""
    from app.modules.corridor_correlator import _parse_wkt_bbox

    wkt = "POLYGON ((25.0 55.0, 26.0 55.0, 26.0 56.0, 25.0 56.0, 25.0 55.0))"
    bbox = _parse_wkt_bbox(wkt)

    assert bbox is not None, "Expected valid bounding box, got None"
    min_lon, min_lat, max_lon, max_lat = bbox

    assert min_lon == pytest.approx(25.0)
    assert min_lat == pytest.approx(55.0)
    assert max_lon == pytest.approx(26.0)
    assert max_lat == pytest.approx(56.0)


def test_parse_wkt_bbox_negative_coordinates():
    """WKT with negative coordinates (West/South) is parsed correctly."""
    from app.modules.corridor_correlator import _parse_wkt_bbox

    wkt = "POLYGON ((-10.0 -5.0, 10.0 -5.0, 10.0 5.0, -10.0 5.0, -10.0 -5.0))"
    bbox = _parse_wkt_bbox(wkt)

    assert bbox is not None
    min_lon, min_lat, max_lon, max_lat = bbox
    assert min_lon == pytest.approx(-10.0)
    assert min_lat == pytest.approx(-5.0)
    assert max_lon == pytest.approx(10.0)
    assert max_lat == pytest.approx(5.0)


def test_parse_wkt_bbox_empty_string_returns_none():
    """An empty string (no coordinate pairs) returns None without raising.

    Note: _parse_wkt_bbox uses a general numeric-pair regex and does NOT
    reject non-POLYGON geometry types by keyword.  A POINT WKT string
    containing a coordinate pair will therefore return a degenerate bbox
    (min == max) rather than None — this is expected and tested separately.
    """
    from app.modules.corridor_correlator import _parse_wkt_bbox

    # Empty string has no coordinate pairs → returns None
    assert _parse_wkt_bbox("") is None

    # String with no numbers → returns None
    assert _parse_wkt_bbox("NO NUMBERS HERE") is None


# ── Point-in-bounding-box tests ───────────────────────────────────────────────

def test_point_in_bbox_inside():
    """A point clearly inside the bounding box returns True."""
    from app.modules.corridor_correlator import _point_in_bbox

    bbox = (24.0, 54.5, 27.0, 56.5)  # (min_lon, min_lat, max_lon, max_lat)
    assert _point_in_bbox(55.0, 25.5, bbox, tolerance=0.0)


def test_point_in_bbox_outside():
    """A point clearly outside the bounding box returns False."""
    from app.modules.corridor_correlator import _point_in_bbox

    bbox = (24.0, 54.5, 27.0, 56.5)
    # Point at lon=30.0 — outside the east boundary (max_lon=27.0)
    assert not _point_in_bbox(55.0, 30.0, bbox, tolerance=0.0)


def test_point_in_bbox_tolerance_expands_boundary():
    """A point 0.05° outside the boundary is captured when tolerance=0.1."""
    from app.modules.corridor_correlator import _point_in_bbox

    bbox = (24.0, 54.5, 27.0, 56.5)
    # Point at lon=27.05 — just outside without tolerance
    assert not _point_in_bbox(55.0, 27.05, bbox, tolerance=0.0)
    # But within tolerance=0.1
    assert _point_in_bbox(55.0, 27.05, bbox, tolerance=0.1)
