"""Movement envelope interpolation methods.

PRD §7.4 specifies three interpolation strategies based on gap duration:
  <2h:  Linear interpolation (2-point track)
  2-6h: Cubic Hermite spline using start/end COG+SOG (10-20 intermediate positions)
  >6h:  Multi-scenario envelopes (min/max speed bounds → scenario paths + convex hull)

All functions return (positions_json, ellipse_wkt) where:
  positions_json: list of {"lat": float, "lon": float, "t_offset_h": float}
  ellipse_wkt:    WKT POLYGON string for the confidence ellipse (or None)
"""
from __future__ import annotations

import math
from typing import Optional


def _bearing_rad(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from (lat1,lon1) to (lat2,lon2) in radians."""
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2_r)
    y = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
    return math.atan2(x, y)


def _destination_point(lat: float, lon: float, bearing_rad: float, distance_nm: float) -> tuple[float, float]:
    """Compute destination point given start, bearing, and distance in nm."""
    R_nm = 3440.065  # Earth radius in nautical miles
    d = distance_nm / R_nm
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)

    lat2 = math.asin(
        math.sin(lat_r) * math.cos(d) + math.cos(lat_r) * math.sin(d) * math.cos(bearing_rad)
    )
    lon2 = lon_r + math.atan2(
        math.sin(bearing_rad) * math.sin(d) * math.cos(lat_r),
        math.cos(d) - math.sin(lat_r) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)


def interpolate_linear(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    duration_h: float,
) -> tuple[list[dict], None]:
    """Linear interpolation for gaps <2h. Returns 2-point track."""
    return [
        {"lat": start_lat, "lon": start_lon, "t_offset_h": 0.0},
        {"lat": end_lat, "lon": end_lon, "t_offset_h": duration_h},
    ], None


def interpolate_hermite(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    start_sog: float, start_cog: float,
    end_sog: float, end_cog: float,
    duration_h: float,
    num_points: int = 15,
) -> tuple[list[dict], Optional[str]]:
    """Cubic Hermite spline for 2-6h gaps.

    Uses start/end position + velocity vectors (SOG×COG) as tangent conditions.
    Returns intermediate positions and confidence ellipse WKT.
    """
    positions = []
    # Convert SOG+COG to velocity components (nm/h in lat/lon approx)
    # 1 degree lat ≈ 60nm, 1 degree lon ≈ 60nm * cos(lat)
    mid_lat = (start_lat + end_lat) / 2
    cos_lat = math.cos(math.radians(mid_lat))
    nm_per_deg_lat = 60.0
    nm_per_deg_lon = 60.0 * cos_lat if cos_lat > 0.01 else 60.0

    # Tangent vectors from SOG+COG (in degrees/h)
    start_cog_r = math.radians(start_cog or 0)
    end_cog_r = math.radians(end_cog or 0)

    # dx = SOG * sin(COG) → lon component; dy = SOG * cos(COG) → lat component
    m0_lat = (start_sog or 0) * math.cos(start_cog_r) / nm_per_deg_lat * duration_h
    m0_lon = (start_sog or 0) * math.sin(start_cog_r) / nm_per_deg_lon * duration_h
    m1_lat = (end_sog or 0) * math.cos(end_cog_r) / nm_per_deg_lat * duration_h
    m1_lon = (end_sog or 0) * math.sin(end_cog_r) / nm_per_deg_lon * duration_h

    for i in range(num_points):
        t = i / (num_points - 1)
        # Hermite basis functions
        h00 = (1 + 2 * t) * (1 - t) ** 2
        h10 = t * (1 - t) ** 2
        h01 = t ** 2 * (3 - 2 * t)
        h11 = t ** 2 * (t - 1)

        lat = h00 * start_lat + h10 * m0_lat + h01 * end_lat + h11 * m1_lat
        lon = h00 * start_lon + h10 * m0_lon + h01 * end_lon + h11 * m1_lon

        positions.append({
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "t_offset_h": round(t * duration_h, 2),
        })

    # Build confidence ellipse around the spline path
    ellipse_wkt = _build_ellipse_wkt(positions, buffer_nm=5.0)
    return positions, ellipse_wkt


def interpolate_scenarios(
    start_lat: float, start_lon: float,
    end_lat: float, end_lon: float,
    start_sog: float, start_cog: float,
    max_speed_kn: float,
    duration_h: float,
    num_scenarios: int = 5,
) -> tuple[list[dict], Optional[str]]:
    """Multi-scenario envelope for gaps >6h.

    Generates scenario paths at different speed fractions (min/max bounds),
    then computes convex hull as the confidence polygon.
    """
    all_positions = []
    scenarios = []

    # Speed fractions for scenarios: 0.3×, 0.5×, 0.7×, 1.0×, and direct path
    speed_fractions = [0.3, 0.5, 0.7, 1.0]
    bearing = _bearing_rad(start_lat, start_lon, end_lat, end_lon)

    # Generate scenario paths with varying speeds and bearing deviations
    for frac in speed_fractions:
        speed = max_speed_kn * frac
        for bearing_offset in [-0.5, 0, 0.5]:  # radians offset
            scenario_path = []
            steps = 10
            for step in range(steps + 1):
                t = step / steps
                dist = speed * duration_h * t
                b = bearing + bearing_offset * (1 - t)  # bearing converges toward endpoint
                lat, lon = _destination_point(start_lat, start_lon, b, dist)
                pt = {"lat": round(lat, 6), "lon": round(lon, 6), "t_offset_h": round(t * duration_h, 2)}
                scenario_path.append(pt)
                all_positions.append(pt)
            scenarios.append(scenario_path)

    # Direct path always included
    direct = interpolate_linear(start_lat, start_lon, end_lat, end_lon, duration_h)[0]
    all_positions.extend(direct)

    # Build convex hull polygon from all scenario points
    hull_wkt = _convex_hull_wkt(all_positions)

    # Return the direct path + scenario metadata as positions
    return direct + [{"_scenario_count": num_scenarios}], hull_wkt


def _build_ellipse_wkt(positions: list[dict], buffer_nm: float = 5.0) -> Optional[str]:
    """Build a WKT POLYGON buffering around interpolated positions."""
    if len(positions) < 2:
        return None

    # Collect all lat/lon, then build a buffered convex hull
    lats = [p["lat"] for p in positions]
    lons = [p["lon"] for p in positions]

    buffer_deg = buffer_nm / 60.0  # approximate
    min_lat = min(lats) - buffer_deg
    max_lat = max(lats) + buffer_deg
    min_lon = min(lons) - buffer_deg
    max_lon = max(lons) + buffer_deg

    # Simple bounding box as polygon (adequate for map rendering)
    return (
        f"POLYGON(({min_lon} {min_lat}, {max_lon} {min_lat}, "
        f"{max_lon} {max_lat}, {min_lon} {max_lat}, {min_lon} {min_lat}))"
    )


def _convex_hull_wkt(positions: list[dict]) -> Optional[str]:
    """Compute convex hull of positions and return as WKT POLYGON."""
    points = [(p["lon"], p["lat"]) for p in positions if "lat" in p and "lon" in p]
    if len(points) < 3:
        return _build_ellipse_wkt(positions)

    # Graham scan for convex hull
    points = list(set(points))
    if len(points) < 3:
        return _build_ellipse_wkt(positions)

    # Find lowest point (min y, then min x)
    start = min(points, key=lambda p: (p[1], p[0]))
    points.remove(start)

    def polar_angle(p):
        return math.atan2(p[1] - start[1], p[0] - start[0])

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    points.sort(key=polar_angle)
    hull = [start]
    for p in points:
        while len(hull) > 1 and cross(hull[-2], hull[-1], p) <= 0:
            hull.pop()
        hull.append(p)

    if len(hull) < 3:
        return _build_ellipse_wkt(positions)

    # Close the ring
    hull.append(hull[0])
    coords = ", ".join(f"{x} {y}" for x, y in hull)
    return f"POLYGON(({coords}))"
