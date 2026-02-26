"""Generate synthetic AIS CSV data for end-to-end demo.

Vessels and scenarios:
  A (MMSI 636017000): 26h gap in Baltic Export corridor → critical score
  B (MMSI 314320000): circle spoof pattern (SOG=4kn, tight cluster) → high score
  C (MMSI 374140000): STS event area in Mediterranean zone
  D (MMSI 440202830): on KSE shadow fleet watchlist baseline
  E (MMSI 620001234): new MMSI (<30d) + Comoros flag → +40 signal
  F (MMSI 255805880): 90-day gap-free → legitimacy signal offsets
  G (MMSI 636018000): impossible reappear (velocity_ratio > 1.1) → critical

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
    "mmsi", "imo", "vessel_name", "vessel_type", "flag",
    "deadweight", "year_built", "ais_class",
    "timestamp", "lat", "lon", "sog", "cog", "heading", "nav_status",
]

# Dynamic reference date: 2 days ago (ensures sample data is always "recent")
BASE_DATE = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)


def ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_point(
    mmsi, imo, name, vtype, flag, dwt, year, ais_cls,
    dt, lat, lon, sog=12.0, cog=90.0, heading=90, nav_status=0,
):
    return {
        "mmsi": mmsi, "imo": imo, "vessel_name": name,
        "vessel_type": vtype, "flag": flag, "deadweight": dwt,
        "year_built": year, "ais_class": ais_cls,
        "timestamp": ts(dt), "lat": round(lat, 5), "lon": round(lon, 5),
        "sog": sog, "cog": cog, "heading": heading, "nav_status": nav_status,
    }


rows = []


# ─── Vessel A: VLCC with 26h gap in Baltic Export corridor ─────────────────
# Baltic Export corridor: roughly 57-60°N, 19-29°E
A_mmsi, A_imo, A_name = "636017000", "9123456", "ARCTIC PIONEER"
A_base = {"mmsi": A_mmsi, "imo": A_imo, "vessel_name": A_name,
           "vessel_type": "Crude Oil Tanker", "flag": "LR", "deadweight": 308000,
           "year_built": 2001, "ais_class": "A"}

# Points before gap: heading east in Baltic
for i in range(8):
    dt = BASE_DATE + timedelta(hours=i*2)
    rows.append(make_point(A_mmsi, A_imo, A_name, "Crude Oil Tanker", "LR", 308000, 2001, "A",
                            dt, 58.5 + i*0.05, 20.0 + i*0.3, sog=14.0, cog=95.0))

# GAP: 26 hours of silence (no points)
gap_end = BASE_DATE + timedelta(hours=16 + 26)

# Points after gap: reappears far to east — actual gap distance plausible at 14kn over 26h
for i in range(6):
    dt = gap_end + timedelta(hours=i*2)
    rows.append(make_point(A_mmsi, A_imo, A_name, "Crude Oil Tanker", "LR", 308000, 2001, "A",
                            dt, 58.9 + i*0.03, 26.0 + i*0.3, sog=13.5, cog=100.0))


# ─── Vessel B: Circle spoof (SOG=4kn, positions cluster tight) ─────────────
B_mmsi, B_imo, B_name = "314320000", "9234567", "SHADOW DANCER"
# Normal transit before spoof window
for i in range(4):
    dt = BASE_DATE + timedelta(hours=i*3)
    rows.append(make_point(B_mmsi, B_imo, B_name, "Product Tanker", "CM", 75000, 1997, "A",
                            dt, 40.0 + i*0.2, 18.0 + i*0.5, sog=11.0))

# Circle spoof: 6h window, SOG=4kn, positions within 0.03° cluster
for i in range(12):
    dt = BASE_DATE + timedelta(hours=12 + i * 0.5)
    lat_jitter = random.uniform(-0.01, 0.01)
    lon_jitter = random.uniform(-0.01, 0.01)
    rows.append(make_point(B_mmsi, B_imo, B_name, "Product Tanker", "CM", 75000, 1997, "A",
                            dt, 40.8 + lat_jitter, 20.0 + lon_jitter,
                            sog=4.0 + random.uniform(-0.3, 0.3), cog=random.uniform(0, 360)))


# ─── Vessel C: STS approach in Mediterranean ───────────────────────────────
C1_mmsi, C1_imo, C1_name = "374140000", "9345678", "VOLGA SPIRIT"
C2_mmsi, C2_imo, C2_name = "374141000", "9456789", "CASPIAN DREAM"

# C1 approaches STS zone and loiters
for i in range(6):
    dt = BASE_DATE + timedelta(hours=i*4)
    rows.append(make_point(C1_mmsi, C1_imo, C1_name, "Crude Oil Tanker", "PW", 150000, 2003, "A",
                            dt, 35.5 - i*0.1, 22.0 + i*0.2, sog=max(0.3, 10.0 - i*2)))

# C1 stationary (STS position)
for i in range(10):
    dt = BASE_DATE + timedelta(hours=24 + i)
    rows.append(make_point(C1_mmsi, C1_imo, C1_name, "Crude Oil Tanker", "PW", 150000, 2003, "A",
                            dt, 35.0, 23.2, sog=0.3, cog=180.0, nav_status=1))

# C2 approaches from west
for i in range(8):
    dt = BASE_DATE + timedelta(hours=20 + i)
    rows.append(make_point(C2_mmsi, C2_imo, C2_name, "Chemical Tanker", "PW", 50000, 2006, "A",
                            dt, 35.0 + 0.02, 23.2 - 0.5 + i*0.07, sog=max(0.3, 5.0 - i*0.5),
                            cog=90.0))


# ─── Vessel D: KSE shadow fleet watchlist vessel ───────────────────────────
D_mmsi, D_imo, D_name = "440202830", "9567890", "SIRIUS STAR II"
for i in range(12):
    dt = BASE_DATE + timedelta(hours=i*4)
    rows.append(make_point(D_mmsi, D_imo, D_name, "VLCC Tanker", "KM", 298000, 1999, "A",
                            dt, 56.0 + i*0.05, 19.0 + i*0.2, sog=12.0))


# ─── Vessel E: New MMSI (<30d) + Comoros flag ──────────────────────────────
# mmsi_first_seen_utc will be set during ingest; for demo, vessel appeared 2026-01-10
E_mmsi, E_imo, E_name = "620001234", "9678901", "NORTHERN PROMISE"
for i in range(8):
    dt = BASE_DATE + timedelta(hours=i*3)
    rows.append(make_point(E_mmsi, E_imo, E_name, "Product Tanker", "KM", 65000, 2000, "A",
                            dt, 55.0 + i*0.1, 24.0 + i*0.4, sog=11.5))

# 10h gap in export corridor
gap_end_e = BASE_DATE + timedelta(hours=24 + 10)
for i in range(5):
    dt = gap_end_e + timedelta(hours=i*2)
    rows.append(make_point(E_mmsi, E_imo, E_name, "Product Tanker", "KM", 65000, 2000, "A",
                            dt, 55.8 + i*0.05, 27.0 + i*0.3, sog=11.0))


# ─── Vessel F: Gap-free clean vessel (legitimacy signal) ───────────────────
F_mmsi, F_imo, F_name = "255805880", "9789012", "MAERSK FLENSBURG"
# Dense track over 7 days, no gaps
for i in range(42):
    dt = BASE_DATE - timedelta(days=5) + timedelta(hours=i*4)
    rows.append(make_point(F_mmsi, F_imo, F_name, "Product Tanker", "DK", 45000, 2015, "A",
                            dt, 57.0 + math.sin(i/10)*0.3, 18.0 + i*0.15, sog=12.0,
                            cog=90.0))


# ─── Vessel G: Impossible reappear (velocity_ratio > 1.1) ─────────────────
G_mmsi, G_imo, G_name = "636018000", "9890123", "URSA MAJOR"
# Last seen at position A
rows.append(make_point(G_mmsi, G_imo, G_name, "Crude Oil Tanker", "SL", 200000, 2005, "A",
                        BASE_DATE, 57.0, 20.0, sog=13.0))
rows.append(make_point(G_mmsi, G_imo, G_name, "Crude Oil Tanker", "SL", 200000, 2005, "A",
                        BASE_DATE + timedelta(hours=2), 57.1, 20.3, sog=14.0))

# 4h gap, then reappears 500nm away (physically impossible for a tanker)
# At 14kn × 4h = 56nm max plausible; actual distance: ~500nm → ratio > 8x
rows.append(make_point(G_mmsi, G_imo, G_name, "Crude Oil Tanker", "SL", 200000, 2005, "A",
                        BASE_DATE + timedelta(hours=6), 53.0, 28.0, sog=14.0))  # ~500nm away
for i in range(5):
    dt = BASE_DATE + timedelta(hours=6 + i*2)
    rows.append(make_point(G_mmsi, G_imo, G_name, "Crude Oil Tanker", "SL", 200000, 2005, "A",
                            dt, 53.0 + i*0.05, 28.2 + i*0.3, sog=14.0))


# Write CSV
rows.sort(key=lambda r: r["timestamp"])
with open(OUTPUT_PATH, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(rows)

print(f"Sample data written to {OUTPUT_PATH}")
print(f"Total AIS points: {len(rows)}")
print(f"Vessels: A (26h gap), B (circle spoof), C (STS), D (watchlist), E (new MMSI), F (clean), G (impossible reappear)")


# ─── Dark vessel detections (DB insert — run after ingest so vessel IDs exist) ─
# Import and run this block after `radiancefleet ingest` so vessel rows exist.
#
# Example (run in a Python shell or a post-ingest script):
#
#   from app.database import get_engine
#   from app.models.stubs import DarkVesselDetection
#   from sqlalchemy.orm import Session
#
#   with Session(get_engine()) as db:
#       # Vessel A (MMSI 636017000, 26h gap): unmatched detection in Baltic corridor during gap
#       # gap_start ≈ BASE_DATE + 16h = 2026-01-15T16:00Z
#       # gap_end   ≈ BASE_DATE + 42h = 2026-01-16T18:00Z
#       dv1 = DarkVesselDetection(
#           scene_id="S1A_IW_20260116T060000",
#           detection_lat=58.9,
#           detection_lon=23.5,
#           detection_time_utc=datetime(2026, 1, 16, 6, 0),   # mid-gap for vessel A
#           length_estimate_m=330.0,
#           vessel_type_inferred="tanker",
#           ais_match_attempted=True,
#           ais_match_result="unmatched",
#           matched_vessel_id=None,   # set to vessel A vessel_id after ingest
#           corridor_id=1,            # first Baltic export corridor seed
#           model_confidence=0.87,
#       )
#
#       # Vessel C (MMSI 374140000, STS pair): unmatched detection in STS zone
#       # STS window ≈ BASE_DATE + 24h to +34h
#       dv2 = DarkVesselDetection(
#           scene_id="S1A_IW_20260116T100000",
#           detection_lat=35.0,
#           detection_lon=23.2,
#           detection_time_utc=datetime(2026, 1, 16, 10, 0),  # during STS window for vessel C
#           length_estimate_m=250.0,
#           vessel_type_inferred="tanker",
#           ais_match_attempted=True,
#           ais_match_result="unmatched",
#           matched_vessel_id=None,   # set to vessel C vessel_id after ingest
#           corridor_id=2,            # second corridor (STS zone seed)
#           model_confidence=0.91,
#       )
#
#       db.add_all([dv1, dv2])
#       db.commit()
#       print("Dark vessel detections inserted.")
