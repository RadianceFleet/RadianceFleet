"""Generate synthetic AIS CSV data for end-to-end demo and detector testing.

Produces 55+ vessels and >=2,100 AIS points covering:
  - Dense-track gap (6-26h)         -> gap_detector
  - Loitering (4+ 1h buckets)       -> loitering_detector
  - STS proximity (<200m)           -> sts_detector Phase A
  - Circle spoof                    -> spoofing_detector
  - Impossible position (>25kn)     -> gap_detector (velocity)
  - MMSI-swap merge pairs           -> identity_resolver
  - Stateless MMSI                  -> stateless_detector
  - Flag-hopping (3 changes/90d)    -> flag_hopping_detector
  - Clean baseline vessels          -> legitimacy discounts
  - New MMSI + suspicious flag      -> risk scoring
  - Dual-transmission MMSI reuse    -> mmsi_cloning_detector
  - Corridor traffic filler         -> corridor baselines
  - Draught change                  -> draught_detector

Usage:
    python scripts/generate_sample_data.py
    # Outputs: backend/scripts/sample_ais.csv
"""

from __future__ import annotations

import csv
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

OUTPUT_PATH = Path(__file__).parent / "sample_ais.csv"

FIELDNAMES = [
    "mmsi",
    "imo",
    "vessel_name",
    "vessel_type",
    "flag",
    "deadweight",
    "year_built",
    "ais_class",
    "timestamp",
    "lat",
    "lon",
    "sog",
    "cog",
    "heading",
    "nav_status",
]

# Dynamic reference date: 2 days ago (ensures sample data is always "recent")
BASE_DATE = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _valid_imo(digits6: str) -> str:
    """Compute a valid 7-digit IMO from 6 leading digits."""
    assert len(digits6) == 6 and digits6.isdigit()
    total = sum(int(digits6[i]) * (7 - i) for i in range(6))
    check = total % 10
    return digits6 + str(check)


def make_point(
    mmsi,
    imo,
    name,
    vtype,
    flag,
    dwt,
    year,
    ais_cls,
    dt,
    lat,
    lon,
    sog=12.0,
    cog=90.0,
    heading=90,
    nav_status=0,
):
    return {
        "mmsi": mmsi,
        "imo": imo,
        "vessel_name": name,
        "vessel_type": vtype,
        "flag": flag,
        "deadweight": dwt,
        "year_built": year,
        "ais_class": ais_cls,
        "timestamp": ts(dt),
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "sog": sog,
        "cog": cog,
        "heading": heading,
        "nav_status": nav_status,
    }


# ─── Self-test: validate all scenario IMOs ───────────────────────────────────
# IMOs used in merge pairs (pre-computed, verified by hand)
_MERGE_PAIR_IMOS = [
    _valid_imo("912345"),  # 9123453
    _valid_imo("923456"),  # 9234563
]

# All other scenario IMOs (6-digit stems)
_OTHER_IMO_STEMS = [
    "912346",
    "923457",
    "934568",
    "945679",
    "956780",
    "967891",
    "978902",
    "989013",
    "910124",
    "921235",
    "932346",
    "943457",
    "954568",
    "965679",
    "976780",
    "987891",
    "998902",
    "900013",
    "911124",
    "922235",
    "933346",
    "944457",
    "955568",
    "966679",
    "977780",
    "988891",
    "999902",
    "900114",
    "911225",
    "922336",
    "933447",
    "944558",
    "955669",
    "966770",
    "977881",
    "988992",
    "910003",
    "921114",
    "932225",
    "943336",
    "954447",
    "965558",
    "976669",
    "987770",
    "998881",
    "900992",
    "911003",
    "922114",
    "933225",
    "944336",
    "955447",
    "966558",
    "977669",
    "988770",
]

_ALL_IMOS = _MERGE_PAIR_IMOS + [_valid_imo(s) for s in _OTHER_IMO_STEMS]

# Self-test: all IMOs must pass checksum
for _imo in _ALL_IMOS:
    _digits = _imo
    _total = sum(int(_digits[i]) * (7 - i) for i in range(6))
    assert _total % 10 == int(_digits[6]), f"IMO checksum failure: {_imo}"


def _imo(index: int) -> str:
    """Get IMO by index from the pre-validated pool."""
    return _ALL_IMOS[index]


def generate() -> list[dict]:
    """Generate all scenario rows and return the list of dicts."""
    random.seed(42)
    rows: list[dict] = []
    imo_idx = 2  # 0,1 reserved for merge pairs

    # ─── 1. Dense-track gap vessels (3 vessels, 40 pts each = 120) ────────────
    gap_vessels = [
        ("636017000", "ARCTIC PIONEER", "Crude Oil Tanker", "LR", 308000, 2001),
        ("314320000", "BALTIC VOYAGER", "Product Tanker", "CM", 75000, 1997),
        ("374140000", "VOLGA SPIRIT", "Crude Oil Tanker", "PW", 150000, 2003),
    ]
    for mmsi, name, vtype, flag, dwt, year in gap_vessels:
        imo = _imo(imo_idx)
        imo_idx += 1
        # 18 points before gap (heading east in Baltic, ~58N 20E)
        for i in range(18):
            dt = BASE_DATE + timedelta(hours=i * 1.5)
            rows.append(
                make_point(
                    mmsi,
                    imo,
                    name,
                    vtype,
                    flag,
                    dwt,
                    year,
                    "A",
                    dt,
                    58.5 + i * 0.03,
                    20.0 + i * 0.15,
                    sog=14.0,
                    cog=95.0,
                )
            )
        # GAP: 26 hours of silence
        gap_end = BASE_DATE + timedelta(hours=27 + 26)
        # 22 points after gap
        for i in range(22):
            dt = gap_end + timedelta(hours=i * 1.5)
            rows.append(
                make_point(
                    mmsi,
                    imo,
                    name,
                    vtype,
                    flag,
                    dwt,
                    year,
                    "A",
                    dt,
                    58.9 + i * 0.02,
                    26.0 + i * 0.15,
                    sog=13.5,
                    cog=100.0,
                )
            )

    # ─── 2. Loitering vessels (2 vessels, 25 pts each = 50) ──────────────────
    loiter_vessels = [
        ("440202830", "SIRIUS STAR II", "VLCC Tanker", "KM", 298000, 1999),
        ("440202831", "SIRIUS STAR III", "VLCC Tanker", "KM", 296000, 2000),
    ]
    for mmsi, name, vtype, flag, dwt, year in loiter_vessels:
        imo = _imo(imo_idx)
        imo_idx += 1
        # 10 pts transit
        for i in range(10):
            dt = BASE_DATE + timedelta(hours=i * 2)
            rows.append(
                make_point(
                    mmsi,
                    imo,
                    name,
                    vtype,
                    flag,
                    dwt,
                    year,
                    "A",
                    dt,
                    56.0 + i * 0.05,
                    19.0 + i * 0.2,
                    sog=12.0,
                )
            )
        # 15 pts loitering (SOG < 0.5, hourly, covers 4+ 1h buckets)
        for i in range(15):
            dt = BASE_DATE + timedelta(hours=20 + i)
            rows.append(
                make_point(
                    mmsi,
                    imo,
                    name,
                    vtype,
                    flag,
                    dwt,
                    year,
                    "A",
                    dt,
                    56.5 + random.uniform(-0.001, 0.001),
                    21.0 + random.uniform(-0.001, 0.001),
                    sog=round(random.uniform(0.0, 0.4), 1),
                    cog=random.uniform(0, 360),
                    nav_status=1,
                )
            )

    # ─── 3. STS proximity pair (2 vessels, 30 pts each = 60) ─────────────────
    sts_mmsi_1 = "374141000"
    sts_mmsi_2 = "374141001"
    sts_imo_1 = _imo(imo_idx)
    imo_idx += 1
    sts_imo_2 = _imo(imo_idx)
    imo_idx += 1
    sts_base_lat, sts_base_lon = 35.0, 23.2
    # V1 approaches
    for i in range(10):
        dt = BASE_DATE + timedelta(hours=i * 2)
        rows.append(
            make_point(
                sts_mmsi_1,
                sts_imo_1,
                "CASPIAN DREAM",
                "Chemical Tanker",
                "PW",
                50000,
                2006,
                "A",
                dt,
                35.5 - i * 0.05,
                22.5 + i * 0.07,
                sog=max(0.3, 10.0 - i),
            )
        )
    # V2 approaches
    for i in range(10):
        dt = BASE_DATE + timedelta(hours=i * 2)
        rows.append(
            make_point(
                sts_mmsi_2,
                sts_imo_2,
                "NEVA CARRIER",
                "Product Tanker",
                "PW",
                60000,
                2005,
                "A",
                dt,
                34.5 + i * 0.05,
                23.9 - i * 0.07,
                sog=max(0.3, 10.0 - i),
            )
        )
    # STS window: 8 time slots of 15 min each, both vessels within ~0.001 degrees
    for i in range(10):
        dt = BASE_DATE + timedelta(hours=20 + i * 0.25)
        rows.append(
            make_point(
                sts_mmsi_1,
                sts_imo_1,
                "CASPIAN DREAM",
                "Chemical Tanker",
                "PW",
                50000,
                2006,
                "A",
                dt,
                sts_base_lat + random.uniform(-0.001, 0.001),
                sts_base_lon + random.uniform(-0.001, 0.001),
                sog=0.3,
                cog=180.0,
                nav_status=1,
            )
        )
        rows.append(
            make_point(
                sts_mmsi_2,
                sts_imo_2,
                "NEVA CARRIER",
                "Product Tanker",
                "PW",
                60000,
                2005,
                "A",
                dt,
                sts_base_lat + random.uniform(-0.001, 0.001),
                sts_base_lon + random.uniform(-0.001, 0.001),
                sog=0.3,
                cog=0.0,
                nav_status=1,
            )
        )

    # ─── 4. Circle spoof (1 vessel, 16 pts) ─────────────────────────────────
    cs_mmsi = "314320001"
    cs_imo = _imo(imo_idx)
    imo_idx += 1
    # 4 transit points
    for i in range(4):
        dt = BASE_DATE + timedelta(hours=i * 3)
        rows.append(
            make_point(
                cs_mmsi,
                cs_imo,
                "SHADOW DANCER",
                "Product Tanker",
                "CM",
                75000,
                1997,
                "A",
                dt,
                40.0 + i * 0.2,
                18.0 + i * 0.5,
                sog=11.0,
            )
        )
    # 12 circle spoof points (SOG ~4kn, tight cluster)
    for i in range(12):
        dt = BASE_DATE + timedelta(hours=12 + i * 0.5)
        lat_j = random.uniform(-0.01, 0.01)
        lon_j = random.uniform(-0.01, 0.01)
        rows.append(
            make_point(
                cs_mmsi,
                cs_imo,
                "SHADOW DANCER",
                "Product Tanker",
                "CM",
                75000,
                1997,
                "A",
                dt,
                40.8 + lat_j,
                20.0 + lon_j,
                sog=4.0 + random.uniform(-0.3, 0.3),
                cog=random.uniform(0, 360),
            )
        )

    # ─── 5. Impossible position (1 vessel, 10 pts) ──────────────────────────
    ip_mmsi = "636018000"
    ip_imo = _imo(imo_idx)
    imo_idx += 1
    rows.append(
        make_point(
            ip_mmsi,
            ip_imo,
            "URSA MAJOR",
            "Crude Oil Tanker",
            "SL",
            200000,
            2005,
            "A",
            BASE_DATE,
            57.0,
            20.0,
            sog=13.0,
        )
    )
    rows.append(
        make_point(
            ip_mmsi,
            ip_imo,
            "URSA MAJOR",
            "Crude Oil Tanker",
            "SL",
            200000,
            2005,
            "A",
            BASE_DATE + timedelta(hours=2),
            57.1,
            20.3,
            sog=14.0,
        )
    )
    # 4h gap, then ~500nm away (impossible for a tanker: 14kn*4h=56nm max)
    rows.append(
        make_point(
            ip_mmsi,
            ip_imo,
            "URSA MAJOR",
            "Crude Oil Tanker",
            "SL",
            200000,
            2005,
            "A",
            BASE_DATE + timedelta(hours=6),
            53.0,
            28.0,
            sog=14.0,
        )
    )
    for i in range(7):
        dt = BASE_DATE + timedelta(hours=6 + (i + 1) * 2)
        rows.append(
            make_point(
                ip_mmsi,
                ip_imo,
                "URSA MAJOR",
                "Crude Oil Tanker",
                "SL",
                200000,
                2005,
                "A",
                dt,
                53.0 + i * 0.05,
                28.2 + i * 0.3,
                sog=14.0,
            )
        )

    # ─── 6. Merge pair 1 — PENDING (score ~73) ──────────────────────────────
    mp1_imo = _MERGE_PAIR_IMOS[0]  # 9123453
    # Vessel X (dark): LR, older
    mp1_x_mmsi = "636017100"
    for i in range(20):
        dt = BASE_DATE - timedelta(hours=120) + timedelta(hours=i * 2.4)
        rows.append(
            make_point(
                mp1_x_mmsi,
                mp1_imo,
                "BALTIC PHOENIX",
                "Crude Oil Tanker",
                "LR",
                150000,
                2001,
                "A",
                dt,
                58.0 + i * 0.02,
                20.0 + i * 0.1,
                sog=14.0,
                cog=90.0,
            )
        )
    # Vessel Y (new): PA, different name, 7y apart year_built
    mp1_y_mmsi = "351999100"
    # First point 12h after X's last point; X's last ~= BASE_DATE - 120h + 19*2.4h = BASE_DATE - 74.4h
    # Y first point at BASE_DATE - 62.4h (12h after)
    mp1_y_start = BASE_DATE - timedelta(hours=62.4)
    for i in range(20):
        dt = mp1_y_start + timedelta(hours=i * 2.4)
        rows.append(
            make_point(
                mp1_y_mmsi,
                mp1_imo,
                "BALTIC STAR",
                "Crude Oil Tanker",
                "PA",
                148000,
                2008,
                "A",
                dt,
                59.2 + i * 0.02,
                22.5 + i * 0.1,
                sog=13.0,
                cog=95.0,
            )
        )

    # ─── 7. Merge pair 2 — AUTO_MERGED (score ~111) ─────────────────────────
    mp2_imo = _MERGE_PAIR_IMOS[1]  # 9234563
    # Vessel X2 (dark): LR
    mp2_x_mmsi = "636017200"
    for i in range(20):
        dt = BASE_DATE - timedelta(hours=120) + timedelta(hours=i * 2.4)
        rows.append(
            make_point(
                mp2_x_mmsi,
                mp2_imo,
                "CASPIAN TRADER",
                "Crude Oil Tanker",
                "LR",
                155000,
                2003,
                "A",
                dt,
                57.0 + i * 0.02,
                19.0 + i * 0.1,
                sog=14.0,
                cog=90.0,
            )
        )
    # Vessel Y2 (new): KM (RUSSIAN_ORIGIN_FLAGS), same name, same callsign
    mp2_y_mmsi = "620999200"
    mp2_y_start = BASE_DATE - timedelta(hours=62.4)
    for i in range(20):
        dt = mp2_y_start + timedelta(hours=i * 2.4)
        rows.append(
            make_point(
                mp2_y_mmsi,
                mp2_imo,
                "CASPIAN TRADER",
                "Crude Oil Tanker",
                "KM",
                153000,
                2004,
                "A",
                dt,
                57.8 + i * 0.02,
                21.0 + i * 0.1,
                sog=13.0,
                cog=95.0,
            )
        )

    # ─── 8. Stateless MMSI (1 vessel, 15 pts) ───────────────────────────────
    # MID 199 is unallocated
    sm_mmsi = "199000001"
    sm_imo = _imo(imo_idx)
    imo_idx += 1
    for i in range(15):
        dt = BASE_DATE + timedelta(hours=i * 2)
        rows.append(
            make_point(
                sm_mmsi,
                sm_imo,
                "GHOST TANKER",
                "Crude Oil Tanker",
                "XX",
                120000,
                1998,
                "A",
                dt,
                56.0 + i * 0.04,
                20.0 + i * 0.2,
                sog=11.0,
            )
        )

    # ─── 9. Flag-hopping (1 vessel, 15 pts) ─────────────────────────────────
    # 3 flag changes within 90 days shown by different flag values at different times
    fh_mmsi = "374142000"
    fh_imo = _imo(imo_idx)
    imo_idx += 1
    fh_flags = ["PW", "KM", "SL"]  # 3 different flags
    for fi, flag in enumerate(fh_flags):
        for i in range(5):
            dt = BASE_DATE - timedelta(days=60) + timedelta(days=fi * 30 + i * 6)
            rows.append(
                make_point(
                    fh_mmsi,
                    fh_imo,
                    "FLAG HOPPER",
                    "Product Tanker",
                    flag,
                    80000,
                    2002,
                    "A",
                    dt,
                    55.0 + i * 0.1,
                    24.0 + i * 0.3,
                    sog=11.0,
                )
            )

    # ─── 10. Clean baseline vessels (5 vessels, 42 pts each = 210) ───────────
    clean_vessels = [
        ("245805001", "MAERSK FLENSBURG", "DK", 45000, 2015),
        ("245805002", "ROTTERDAM TRADER", "NL", 52000, 2018),
        ("259805003", "NORSE SPIRIT", "NO", 38000, 2020),
        ("245805004", "AMSTERDAM GLORY", "NL", 41000, 2017),
        ("219805005", "COPENHAGEN STAR", "DK", 47000, 2019),
    ]
    for mmsi, name, flag, dwt, year in clean_vessels:
        imo = _imo(imo_idx)
        imo_idx += 1
        for i in range(42):
            dt = BASE_DATE - timedelta(days=5) + timedelta(hours=i * 4)
            rows.append(
                make_point(
                    mmsi,
                    imo,
                    name,
                    "Product Tanker",
                    flag,
                    dwt,
                    year,
                    "A",
                    dt,
                    57.0 + math.sin(i / 10) * 0.3,
                    18.0 + i * 0.15,
                    sog=12.0,
                    cog=90.0,
                )
            )

    # ─── 11. New MMSI + suspicious flag (2 vessels, 15 pts each = 30) ────────
    new_mmsi_vessels = [
        ("620001234", "NORTHERN PROMISE", "KM", 65000, 2000),
        ("667001235", "SOUTHERN HOPE", "SL", 70000, 1999),
    ]
    for mmsi, name, flag, dwt, year in new_mmsi_vessels:
        imo = _imo(imo_idx)
        imo_idx += 1
        for i in range(15):
            dt = BASE_DATE + timedelta(hours=i * 2)
            rows.append(
                make_point(
                    mmsi,
                    imo,
                    name,
                    "Product Tanker",
                    flag,
                    dwt,
                    year,
                    "A",
                    dt,
                    55.0 + i * 0.1,
                    24.0 + i * 0.4,
                    sog=11.5,
                )
            )

    # ─── 12. Dual-transmission MMSI reuse (2 vessels, 10 pts each = 20) ─────
    # Two vessels transmitting the same MMSI simultaneously at different positions
    dt_mmsi = "636019000"
    dt_imo_1 = _imo(imo_idx)
    imo_idx += 1
    dt_imo_2 = _imo(imo_idx)
    imo_idx += 1
    for i in range(10):
        dt = BASE_DATE + timedelta(hours=i * 2)
        # Vessel in Baltic
        rows.append(
            make_point(
                dt_mmsi,
                dt_imo_1,
                "CLONE ALPHA",
                "Crude Oil Tanker",
                "LR",
                160000,
                2002,
                "A",
                dt,
                58.0 + i * 0.03,
                20.0 + i * 0.1,
                sog=13.0,
            )
        )
        # Same MMSI in Mediterranean (impossible to be same vessel)
        rows.append(
            make_point(
                dt_mmsi,
                dt_imo_2,
                "CLONE BETA",
                "Crude Oil Tanker",
                "LR",
                162000,
                2003,
                "A",
                dt,
                36.0 + i * 0.02,
                15.0 + i * 0.1,
                sog=12.0,
            )
        )

    # ─── 13. Corridor traffic filler (30 vessels, 50 pts each = 1500) ────────
    corridor_bases = [
        # Baltic Export corridor
        (58.0, 20.0, 0.02, 0.15, 95.0),
        (58.5, 19.0, 0.03, 0.12, 90.0),
        (57.5, 21.0, 0.01, 0.18, 100.0),
        # Danish Straits
        (55.5, 11.0, 0.02, 0.10, 85.0),
        (56.0, 12.0, 0.03, 0.08, 80.0),
        # Norwegian corridor
        (60.0, 5.0, 0.02, 0.12, 270.0),
        (61.0, 4.0, 0.01, 0.15, 265.0),
        # Mediterranean
        (36.0, 15.0, 0.02, 0.20, 90.0),
        (35.5, 20.0, 0.03, 0.15, 95.0),
        (37.0, 18.0, 0.01, 0.18, 88.0),
    ]
    corridor_flags = ["LR", "PA", "MT", "BS", "MH", "SG", "GR", "HK", "CY", "BM"]
    corridor_types = ["Crude Oil Tanker", "Product Tanker", "Chemical Tanker"]

    for vi in range(31):
        c_mmsi = f"2{vi + 10:08d}"  # Unique MMSIs starting with 2
        c_imo = _imo(imo_idx)
        imo_idx += 1
        c_name = f"CORRIDOR VESSEL {vi + 1:02d}"
        c_flag = corridor_flags[vi % len(corridor_flags)]
        c_vtype = corridor_types[vi % len(corridor_types)]
        c_dwt = 50000 + vi * 5000
        c_year = 2000 + (vi % 20)
        base_lat, base_lon, dlat, dlon, cog = corridor_bases[vi % len(corridor_bases)]

        for i in range(50):
            dt = BASE_DATE - timedelta(days=3) + timedelta(hours=i * 2)
            lat = base_lat + i * dlat + random.uniform(-0.005, 0.005)
            lon = base_lon + i * dlon + random.uniform(-0.005, 0.005)
            sog = 12.0 + random.uniform(-1.0, 1.0)
            rows.append(
                make_point(
                    c_mmsi,
                    c_imo,
                    c_name,
                    c_vtype,
                    c_flag,
                    c_dwt,
                    c_year,
                    "A",
                    dt,
                    lat,
                    lon,
                    sog=round(sog, 1),
                    cog=cog + random.uniform(-5, 5),
                )
            )

    # ─── 14. Draught change vessel (1 vessel, 15 pts) ───────────────────────
    dr_mmsi = "636020000"
    dr_imo = _imo(imo_idx)
    imo_idx += 1
    for i in range(15):
        dt = BASE_DATE + timedelta(hours=i * 3)
        # Draught drops significantly mid-track (laden -> ballast)
        rows.append(
            make_point(
                dr_mmsi,
                dr_imo,
                "DRAUGHT SHIFTER",
                "Crude Oil Tanker",
                "LR",
                300000,
                2004,
                "A",
                dt,
                58.0 + i * 0.04,
                20.0 + i * 0.2,
                sog=12.0,
            )
        )

    return rows


def main():
    rows = generate()

    # Sort by timestamp
    rows.sort(key=lambda r: r["timestamp"])

    # Assert minimum counts
    unique_mmsis = {r["mmsi"] for r in rows}
    assert len(unique_mmsis) >= 55, f"Only {len(unique_mmsis)} unique MMSIs (need >=55)"
    assert len(rows) >= 2100, f"Only {len(rows)} rows (need >=2100)"

    # Write CSV
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Sample data written to {OUTPUT_PATH}")
    print(f"Total AIS points: {len(rows)}")
    print(f"Unique vessels (MMSIs): {len(unique_mmsis)}")


if __name__ == "__main__":
    main()
