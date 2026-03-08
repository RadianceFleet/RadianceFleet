"""Tests for the sample AIS data generator.

Validates CSV output: minimum counts, scenario correctness, coordinate validity,
merge pair IMO checksums, and deterministic output.
"""

from __future__ import annotations

import math

import pytest

# Import the generator and constants
from scripts.generate_sample_data import (
    _MERGE_PAIR_IMOS,
    FIELDNAMES,
    _valid_imo,
    generate,
)


@pytest.fixture(scope="module")
def rows():
    """Generate sample data once for all tests."""
    return generate()


@pytest.fixture(scope="module")
def rows_by_mmsi(rows):
    """Group rows by MMSI."""
    by_mmsi: dict[str, list[dict]] = {}
    for r in rows:
        by_mmsi.setdefault(r["mmsi"], []).append(r)
    # Sort each vessel's rows by timestamp
    for pts in by_mmsi.values():
        pts.sort(key=lambda r: r["timestamp"])
    return by_mmsi


def test_csv_meets_minimum_counts(rows, rows_by_mmsi):
    """At least 55 unique MMSIs and at least 2100 rows."""
    assert len(rows) >= 2100, f"Only {len(rows)} rows (need >=2100)"
    assert len(rows_by_mmsi) >= 55, f"Only {len(rows_by_mmsi)} unique MMSIs (need >=55)"


def test_merge_pair_has_shared_imo(rows):
    """Two distinct MMSIs share an IMO with >=12h temporal gap."""
    # Build IMO -> set of MMSIs
    imo_to_mmsis: dict[str, set[str]] = {}
    imo_to_times: dict[str, list[str]] = {}
    for r in rows:
        imo = r["imo"]
        imo_to_mmsis.setdefault(imo, set()).add(r["mmsi"])
        imo_to_times.setdefault(imo, []).append(r["timestamp"])

    found_shared = False
    for imo, mmsis in imo_to_mmsis.items():
        if len(mmsis) >= 2:
            # Check temporal gap: sort all timestamps for this IMO
            times = sorted(imo_to_times[imo])
            # Find max gap between any two consecutive timestamps
            from datetime import datetime

            [datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ") for t in times]
            # Group timestamps by MMSI
            mmsi_times: dict[str, list[datetime]] = {}
            for r in rows:
                if r["imo"] == imo:
                    mmsi_times.setdefault(r["mmsi"], []).append(
                        datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
                    )
            # Find gap between end of one MMSI and start of another
            mmsi_list = list(mmsi_times.keys())
            for i in range(len(mmsi_list)):
                for j in range(len(mmsi_list)):
                    if i == j:
                        continue
                    end_i = max(mmsi_times[mmsi_list[i]])
                    start_j = min(mmsi_times[mmsi_list[j]])
                    gap_hours = abs((start_j - end_i).total_seconds()) / 3600
                    if gap_hours >= 12:
                        found_shared = True
                        break
                if found_shared:
                    break
        if found_shared:
            break

    assert found_shared, "No merge pair found with shared IMO and >=12h temporal gap"


def test_gap_scenario_has_long_gap(rows_by_mmsi):
    """At least one vessel has consecutive timestamps >6h apart."""
    from datetime import datetime

    found = False
    for mmsi, pts in rows_by_mmsi.items():
        for i in range(1, len(pts)):
            t1 = datetime.strptime(pts[i - 1]["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            t2 = datetime.strptime(pts[i]["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            gap_hours = (t2 - t1).total_seconds() / 3600
            if gap_hours > 6:
                found = True
                break
        if found:
            break
    assert found, "No vessel has a gap >6h between consecutive timestamps"


def test_loitering_scenario(rows_by_mmsi):
    """At least one vessel has 4+ consecutive points with SOG < 0.5."""
    found = False
    for mmsi, pts in rows_by_mmsi.items():
        consec = 0
        for p in pts:
            if p["sog"] < 0.5:
                consec += 1
                if consec >= 4:
                    found = True
                    break
            else:
                consec = 0
        if found:
            break
    assert found, "No vessel has 4+ consecutive points with SOG < 0.5"


def test_sts_proximity(rows):
    """Two vessels have overlapping timestamps within 0.002 deg lat/lon."""
    from datetime import datetime

    # Group by timestamp (rounded to nearest 15 min)
    by_time: dict[str, list[dict]] = {}
    for r in rows:
        dt = datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        # Round to 15-min bucket
        bucket = dt.replace(minute=(dt.minute // 15) * 15, second=0)
        key = bucket.strftime("%Y-%m-%dT%H:%M")
        by_time.setdefault(key, []).append(r)

    found = False
    for key, pts in by_time.items():
        if len(pts) < 2:
            continue
        # Check all pairs
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                if pts[i]["mmsi"] == pts[j]["mmsi"]:
                    continue
                dlat = abs(pts[i]["lat"] - pts[j]["lat"])
                dlon = abs(pts[i]["lon"] - pts[j]["lon"])
                if dlat < 0.002 and dlon < 0.002:
                    found = True
                    break
            if found:
                break
        if found:
            break
    assert found, "No two vessels found within 0.002 deg at overlapping timestamps"


def test_impossible_position(rows_by_mmsi):
    """One vessel has consecutive points implying >25kn speed."""
    from datetime import datetime

    found = False
    for mmsi, pts in rows_by_mmsi.items():
        for i in range(1, len(pts)):
            t1 = datetime.strptime(pts[i - 1]["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            t2 = datetime.strptime(pts[i]["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            hours = (t2 - t1).total_seconds() / 3600
            if hours <= 0:
                continue
            # Haversine approximation
            lat1, lon1 = math.radians(pts[i - 1]["lat"]), math.radians(pts[i - 1]["lon"])
            lat2, lon2 = math.radians(pts[i]["lat"]), math.radians(pts[i]["lon"])
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            c = 2 * math.asin(min(1.0, math.sqrt(a)))
            dist_nm = c * 3440.065  # Earth radius in nm
            implied_kn = dist_nm / hours
            if implied_kn > 25:
                found = True
                break
        if found:
            break
    assert found, "No vessel has consecutive points implying >25kn speed"


def test_valid_coordinates(rows):
    """All lat in [-90,90], lon in [-180,180]."""
    for r in rows:
        assert -90 <= r["lat"] <= 90, f"Invalid lat {r['lat']} for MMSI {r['mmsi']}"
        assert -180 <= r["lon"] <= 180, f"Invalid lon {r['lon']} for MMSI {r['mmsi']}"


def test_all_required_columns(rows):
    """CSV has all FIELDNAMES columns."""
    for r in rows:
        for col in FIELDNAMES:
            assert col in r, f"Missing column {col} in row"


def test_generator_asserts_minimum(rows):
    """Running the generator does not raise (implicitly tested by rows fixture)."""
    assert len(rows) > 0


def test_all_merge_pair_imos_checksum_valid():
    """All IMOs used in merge-pair scenarios pass validate_imo_checksum()."""
    # Import the real validator
    import sys

    sys.path.insert(0, "/home/dyn/devel/RadianceFleet/backend")
    from app.utils.vessel_identity import validate_imo_checksum

    for imo in _MERGE_PAIR_IMOS:
        assert validate_imo_checksum(imo), f"IMO {imo} fails checksum validation"

    # Also verify our helper matches
    assert _valid_imo("912345") == "9123453"
    assert _valid_imo("923456") == "9234563"


def test_generator_deterministic_with_seed():
    """Running twice produces identical output."""
    rows1 = generate()
    rows2 = generate()
    assert len(rows1) == len(rows2), "Row counts differ between runs"

    # Sort both by timestamp + mmsi for stable comparison
    def key(r):
        return (r["timestamp"], r["mmsi"], str(r["lat"]), str(r["lon"]))

    rows1.sort(key=key)
    rows2.sort(key=key)
    for i, (r1, r2) in enumerate(zip(rows1, rows2)):
        assert r1 == r2, f"Row {i} differs: {r1} vs {r2}"
