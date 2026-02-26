#!/usr/bin/env python3
"""Generate sample AIS data for 7 test vessels and insert into the database.

Creates ~129 AIS points across 7 vessels with distinct anomaly profiles:
  A  26h gap vessel         — triggers gap duration 24h+, speed spike
  B  Circle spoof vessel    — SOG >3kn but tight position cluster
  C  STS transfer vessel    — proximity events with vessel D
  D  Watchlist vessel       — on OFAC SDN list
  E  New MMSI vessel        — first seen <30d ago
  F  Clean vessel           — low-risk, consistent Class A
  G  Impossible reappear    — position jump implying >30kn
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import typer

# Ensure the backend package is importable when running from repo root.
_backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from app.database import SessionLocal, init_db
from app.models.vessel import Vessel
from app.models.ais_point import AISPoint
from app.models.base import AISClassEnum, FlagRiskEnum, PIStatusEnum

cli = typer.Typer(help="Generate sample AIS data for RadianceFleet development/testing.")

# ---------------------------------------------------------------------------
# Reference timestamp: all positions are relative to this date.
# ---------------------------------------------------------------------------
BASE_TIME = datetime(2026, 2, 1, 6, 0, 0)


# ---------------------------------------------------------------------------
# Vessel definitions
# ---------------------------------------------------------------------------

VESSELS: list[dict] = [
    {
        "mmsi": "538001001",
        "imo": "9100001",
        "name": "SHADOW PIONEER",
        "flag": "PW",
        "vessel_type": "VLCC",
        "deadweight": 220_000.0,
        "year_built": 1998,
        "ais_class": AISClassEnum.A,
        "flag_risk_category": FlagRiskEnum.HIGH_RISK,
        "pi_coverage_status": PIStatusEnum.LAPSED,
        "callsign": "V7AB1",
        "label": "A",
    },
    {
        "mmsi": "538002002",
        "imo": "9200002",
        "name": "CIRCLE DRIFTER",
        "flag": "CM",
        "vessel_type": "Aframax",
        "deadweight": 90_000.0,
        "year_built": 2005,
        "ais_class": AISClassEnum.A,
        "flag_risk_category": FlagRiskEnum.HIGH_RISK,
        "pi_coverage_status": PIStatusEnum.LAPSED,
        "callsign": "TJBC2",
        "label": "B",
    },
    {
        "mmsi": "538003003",
        "imo": "9300003",
        "name": "AEGEAN TRANSFER",
        "flag": "MT",
        "vessel_type": "Suezmax",
        "deadweight": 140_000.0,
        "year_built": 2002,
        "ais_class": AISClassEnum.A,
        "flag_risk_category": FlagRiskEnum.MEDIUM_RISK,
        "pi_coverage_status": PIStatusEnum.UNKNOWN,
        "callsign": "9HCD3",
        "label": "C",
    },
    {
        "mmsi": "538004004",
        "imo": "9400004",
        "name": "DARK HORIZON",
        "flag": "GA",
        "vessel_type": "Panamax",
        "deadweight": 70_000.0,
        "year_built": 2000,
        "ais_class": AISClassEnum.A,
        "flag_risk_category": FlagRiskEnum.HIGH_RISK,
        "pi_coverage_status": PIStatusEnum.LAPSED,
        "callsign": "TREF4",
        "label": "D",
    },
    {
        "mmsi": "538005005",
        "imo": "9500005",
        "name": "NUEVO APPARITION",
        "flag": "HN",
        "vessel_type": "Aframax",
        "deadweight": 85_000.0,
        "year_built": 2008,
        "ais_class": AISClassEnum.A,
        "flag_risk_category": FlagRiskEnum.MEDIUM_RISK,
        "pi_coverage_status": PIStatusEnum.UNKNOWN,
        "mmsi_first_seen_utc": BASE_TIME - timedelta(days=10),
        "callsign": "HRFG5",
        "label": "E",
    },
    {
        "mmsi": "538006006",
        "imo": "9600006",
        "name": "NORTH SEA CARRIER",
        "flag": "NO",
        "vessel_type": "Aframax",
        "deadweight": 100_000.0,
        "year_built": 2018,
        "ais_class": AISClassEnum.A,
        "flag_risk_category": FlagRiskEnum.LOW_RISK,
        "pi_coverage_status": PIStatusEnum.ACTIVE,
        "callsign": "LAHI6",
        "label": "F",
    },
    {
        "mmsi": "538007007",
        "imo": "9700007",
        "name": "GHOST JUMPER",
        "flag": "SL",
        "vessel_type": "Suezmax",
        "deadweight": 130_000.0,
        "year_built": 2001,
        "ais_class": AISClassEnum.A,
        "flag_risk_category": FlagRiskEnum.HIGH_RISK,
        "pi_coverage_status": PIStatusEnum.LAPSED,
        "callsign": "9LSJ7",
        "label": "G",
    },
]


# ---------------------------------------------------------------------------
# AIS point generators per vessel
# ---------------------------------------------------------------------------

def _points_vessel_a(vessel_id: int) -> list[AISPoint]:
    """26h gap vessel. Points 1-8 normal, 26h gap, then points 9-18 resume.
    Pre-gap SOG = 25 kn (speed spike). Med transit near Crete -> Libya.
    """
    pts: list[AISPoint] = []
    # Phase 1: normal transit south of Crete (8 points, 30min intervals)
    base_lat, base_lon = 34.80, 24.00
    for i in range(8):
        t = BASE_TIME + timedelta(minutes=30 * i)
        sog = 14.0 if i < 7 else 25.0  # speed spike on point 8
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=base_lat - 0.05 * i,
            lon=base_lon + 0.08 * i,
            sog=sog,
            cog=160.0 + i * 2,
            heading=158.0 + i * 2,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    # Phase 2: 26h gap, then resume (10 more points, 30min intervals)
    gap_resume = BASE_TIME + timedelta(hours=26 + 3.5)  # 26h gap after point 8
    post_lat, post_lon = 33.20, 26.50
    for i in range(10):
        t = gap_resume + timedelta(minutes=30 * i)
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=post_lat - 0.04 * i,
            lon=post_lon + 0.06 * i,
            sog=12.5 + (i % 3) * 0.5,
            cog=145.0,
            heading=143.0,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    return pts  # 18 points


def _points_vessel_b(vessel_id: int) -> list[AISPoint]:
    """Circle spoof vessel. SOG 5-8 kn but positions within 0.01 deg cluster.
    Location: Laconian Gulf STS zone (~36.5 N, 22.8 E).
    """
    pts: list[AISPoint] = []
    center_lat, center_lon = 36.50, 22.80
    import math
    for i in range(18):
        t = BASE_TIME + timedelta(minutes=20 * i)
        # Tight circle: radius ~0.005 deg (~550m)
        angle = math.radians(i * 20)
        lat = center_lat + 0.005 * math.sin(angle)
        lon = center_lon + 0.005 * math.cos(angle)
        sog = 5.0 + (i % 4) * 1.0  # 5, 6, 7, 8 repeating
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=lat,
            lon=lon,
            sog=sog,
            cog=(i * 20) % 360,
            heading=((i * 20) + 5) % 360,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    return pts  # 18 points


def _points_vessel_c(vessel_id: int) -> list[AISPoint]:
    """STS transfer vessel. Moves toward Laconian Gulf and spends time near vessel D.
    The proximity portion (last 10 points) should be within 200m of vessel D's
    corresponding points.
    """
    pts: list[AISPoint] = []
    # Phase 1: approach (8 points, heading toward STS zone)
    start_lat, start_lon = 36.60, 22.50
    for i in range(8):
        t = BASE_TIME + timedelta(minutes=30 * i)
        lat = start_lat - 0.008 * i
        lon = start_lon + 0.020 * i
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=lat,
            lon=lon,
            sog=8.0 - i * 0.5,
            cog=120.0,
            heading=118.0,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    # Phase 2: STS proximity with vessel D (10 points, 15min windows)
    # Anchor near 36.52 N, 22.70 E, within ~100m of vessel D's STS points
    sts_lat, sts_lon = 36.520, 22.700
    for i in range(10):
        t = BASE_TIME + timedelta(hours=4, minutes=15 * i)
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=sts_lat + 0.0002 * (i % 3),
            lon=sts_lon + 0.0001 * (i % 2),
            sog=0.5 + 0.1 * i,
            cog=90.0,
            heading=88.0,
            nav_status=1,  # at anchor
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    return pts  # 18 points


def _points_vessel_d(vessel_id: int) -> list[AISPoint]:
    """Watchlist vessel. Also participates in STS with vessel C.
    Approach from east, loiter near 36.52 N, 22.70 E matching vessel C.
    """
    pts: list[AISPoint] = []
    # Phase 1: approach from east (8 points)
    start_lat, start_lon = 36.55, 23.00
    for i in range(8):
        t = BASE_TIME + timedelta(minutes=30 * i)
        lat = start_lat - 0.004 * i
        lon = start_lon - 0.035 * i
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=lat,
            lon=lon,
            sog=7.0 - i * 0.3,
            cog=250.0,
            heading=248.0,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    # Phase 2: STS proximity — within ~100m of vessel C's STS points
    # Vessel C is at ~36.520, 22.700; this vessel sits ~0.001 deg south (~110m)
    sts_lat, sts_lon = 36.519, 22.700
    for i in range(10):
        t = BASE_TIME + timedelta(hours=4, minutes=15 * i)
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=sts_lat + 0.0001 * (i % 3),
            lon=sts_lon + 0.0002 * (i % 2),
            sog=0.3 + 0.1 * i,
            cog=270.0,
            heading=268.0,
            nav_status=1,  # at anchor
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    return pts  # 18 points


def _points_vessel_e(vessel_id: int) -> list[AISPoint]:
    """New MMSI vessel. First seen <30d ago. Transit through Turkish Straits area.
    Relatively normal transit but the vessel itself triggers new-MMSI scoring.
    """
    pts: list[AISPoint] = []
    # Bosporus approach area
    base_lat, base_lon = 41.10, 29.00
    for i in range(18):
        t = BASE_TIME + timedelta(minutes=25 * i)
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=base_lat - 0.02 * i,
            lon=base_lon + 0.01 * i,
            sog=11.0 + (i % 3),
            cog=200.0 + i,
            heading=198.0 + i,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    return pts  # 18 points


def _points_vessel_f(vessel_id: int) -> list[AISPoint]:
    """Clean vessel. Consistent Class A, regular 30min intervals, Rotterdam area.
    SOG 12-14 kn. Should score low risk.
    """
    pts: list[AISPoint] = []
    # Rotterdam port approach: ~51.95 N, 4.05 E heading northwest
    base_lat, base_lon = 51.90, 3.90
    for i in range(20):
        t = BASE_TIME + timedelta(minutes=30 * i)
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=base_lat + 0.008 * i,
            lon=base_lon + 0.012 * i,
            sog=12.0 + (i % 3),  # 12, 13, 14 repeating
            cog=340.0 + (i % 5) * 2,
            heading=338.0 + (i % 5) * 2,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    return pts  # 20 points


def _points_vessel_g(vessel_id: int) -> list[AISPoint]:
    """Impossible reappear vessel. Normal transit, then position jump implying >30kn.
    Black Sea area near Novorossiysk -> sudden appearance near Ceuta.
    """
    pts: list[AISPoint] = []
    # Phase 1: Black Sea transit (10 points near Novorossiysk, 30min intervals)
    base_lat, base_lon = 44.60, 37.80
    for i in range(10):
        t = BASE_TIME + timedelta(minutes=30 * i)
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=base_lat - 0.02 * i,
            lon=base_lon + 0.03 * i,
            sog=13.0 + (i % 2),
            cog=140.0,
            heading=138.0,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    # Phase 2: Impossible jump — only 6 hours later but ~2500 nm away near Ceuta
    # 2500 nm / 6 h = 417 kn (well above 30kn threshold)
    jump_time = BASE_TIME + timedelta(hours=5, minutes=30 * 9 + 360)  # 6h after pt 10
    jump_lat, jump_lon = 35.90, -5.30  # near Ceuta, Strait of Gibraltar
    for i in range(9):
        t = jump_time + timedelta(minutes=30 * i)
        pts.append(AISPoint(
            vessel_id=vessel_id,
            timestamp_utc=t,
            lat=jump_lat + 0.01 * i,
            lon=jump_lon - 0.02 * i,
            sog=12.0,
            cog=290.0,
            heading=288.0,
            nav_status=0,
            ais_class=AISClassEnum.A,
            source="sample_gen",
        ))
    return pts  # 19 points


POINT_GENERATORS = {
    "A": _points_vessel_a,
    "B": _points_vessel_b,
    "C": _points_vessel_c,
    "D": _points_vessel_d,
    "E": _points_vessel_e,
    "F": _points_vessel_f,
    "G": _points_vessel_g,
}


# ---------------------------------------------------------------------------
# Main CLI command
# ---------------------------------------------------------------------------

@cli.command()
def generate(
    purge: bool = typer.Option(
        False, "--purge", help="Delete existing sample vessels before inserting."
    ),
) -> None:
    """Generate sample AIS data and insert into the database."""
    init_db()
    session = SessionLocal()

    sample_mmsis = [v["mmsi"] for v in VESSELS]

    try:
        if purge:
            existing = (
                session.query(Vessel)
                .filter(Vessel.mmsi.in_(sample_mmsis))
                .all()
            )
            if existing:
                for v in existing:
                    session.delete(v)
                session.commit()
                typer.echo(f"Purged {len(existing)} existing sample vessel(s).")

        total_points = 0

        for vdef in VESSELS:
            label = vdef.pop("label")

            # Skip if vessel already exists
            exists = (
                session.query(Vessel)
                .filter(Vessel.mmsi == vdef["mmsi"])
                .first()
            )
            if exists:
                vdef["label"] = label  # restore for next potential run
                typer.echo(
                    f"  Vessel {label} (MMSI {vdef['mmsi']}) already exists — skipping. "
                    f"Use --purge to recreate."
                )
                continue

            vessel = Vessel(**vdef)
            session.add(vessel)
            session.flush()  # get vessel_id

            generator = POINT_GENERATORS[label]
            points = generator(vessel.vessel_id)
            session.add_all(points)
            total_points += len(points)

            typer.echo(
                f"  Vessel {label}: {vessel.name} (MMSI {vessel.mmsi}) — "
                f"{len(points)} AIS points"
            )

            vdef["label"] = label  # restore

        session.commit()
        typer.echo(f"\nInserted {total_points} AIS points across {len(VESSELS)} vessels.")

    except Exception as exc:
        session.rollback()
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    cli()
