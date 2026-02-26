"""Seed the Port table with ~50 major global ports.

These ports are critical to prevent false ANCHOR_SPOOF anomaly fires for vessels
legitimately anchored at or near major ports. Without these records, any vessel
with nav_status=1 for >=72h and SOG<0.1 that is NOT near one of these positions
would generate a spurious anchor_spoof anomaly and corrupt all downstream risk scores.

Covers:
  - Russian oil export terminals (most common false-fire source)
  - Major EU/Mediterranean transit ports
  - Turkish Strait anchorages (legitimate waiting areas)
  - Far East bunkering hubs
  - Gulf bunkering hubs (Singapore, Fujairah)

Usage:
    from app.database import SessionLocal
    from scripts.seed_ports import seed_ports
    db = SessionLocal()
    seed_ports(db)
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# (name, country, lat, lon)
# Coordinates are port entrance / anchorage centroid, not precise dock
MAJOR_PORTS: list[tuple[str, str, float, float]] = [
    # ── Russian oil export terminals ──────────────────────────────────────────
    ("Primorsk", "RU", 60.35, 28.97),
    ("Ust-Luga", "RU", 59.69, 28.43),
    ("Novorossiysk", "RU", 44.72, 37.78),
    ("Kavkaz", "RU", 45.35, 36.73),
    ("Nakhodka/Kozmino", "RU", 42.76, 132.87),
    ("Vysotsk", "RU", 60.63, 28.55),
    ("St Petersburg", "RU", 59.95, 30.20),
    ("Tuapse", "RU", 44.10, 39.07),
    ("Murmansk", "RU", 68.97, 33.05),
    ("De-Kastri", "RU", 51.47, 140.78),
    ("Varandey", "RU", 68.82, 58.07),

    # ── India (major refinery destinations) ──────────────────────────────────
    ("Jamnagar (Reliance)", "IN", 21.85, 69.08),      # World's largest refinery
    ("Vadinar Terminal", "IN", 21.58, 69.20),
    ("Sikka (Hindustan Petroleum)", "IN", 21.70, 69.40),
    ("Paradip (IOCL)", "IN", 20.07, 86.68),
    ("Visakhapatnam (HPCL)", "IN", 17.67, 83.30),
    ("Mangalore (MRPL)", "IN", 12.92, 74.86),

    # ── Major EU ports ─────────────────────────────────────────────────────────
    ("Rotterdam", "NL", 51.94, 4.14),
    ("Hamburg", "DE", 53.55, 10.00),
    ("Antwerp", "BE", 51.23, 4.40),
    ("Amsterdam", "NL", 52.37, 4.92),
    ("Marseille", "FR", 43.30, 5.37),
    ("Genoa", "IT", 44.41, 8.93),
    ("Trieste", "IT", 45.65, 13.77),
    ("Venice", "IT", 45.44, 12.32),
    ("Piraeus", "GR", 37.94, 23.63),
    ("Thessaloniki", "GR", 40.63, 22.93),

    # ── Mediterranean / North Africa ──────────────────────────────────────────
    ("Valletta / Malta Freeport", "MT", 35.89, 14.52),
    ("Marsaxlokk", "MT", 35.83, 14.55),
    ("Augusta", "IT", 37.22, 15.22),
    ("Algeciras", "ES", 36.13, -5.46),
    ("Gibraltar Anchorage", "GI", 36.14, -5.35),
    ("Ceuta Anchorage", "ES", 35.89, -5.30),
    ("Tarragona", "ES", 41.11, 1.25),
    ("Barcelona", "ES", 41.34, 2.17),

    # ── Turkish Strait / Black Sea ─────────────────────────────────────────────
    ("Istanbul", "TR", 41.01, 28.97),
    ("Canakkale Anchorage", "TR", 40.15, 26.40),
    ("Ambarlı", "TR", 40.97, 28.68),
    ("Aliaga", "TR", 38.80, 26.97),
    ("Dortyol (BOTAS Ceyhan)", "TR", 36.83, 35.98),
    ("Iskenderun (Tupras)", "TR", 36.59, 36.18),
    ("Batumi", "GE", 41.64, 41.64),
    ("Poti", "GE", 42.15, 41.68),
    ("Odessa", "UA", 46.49, 30.74),
    ("Constanta", "RO", 44.17, 28.63),

    # ── Egypt ────────────────────────────────────────────────────────────────
    ("Ain Sukhna (SUMED)", "EG", 29.96, 32.50),

    # ── Far East ──────────────────────────────────────────────────────────────
    ("Singapore", "SG", 1.27, 103.83),
    ("Port Klang", "MY", 3.00, 101.39),
    ("Busan", "KR", 35.10, 129.04),
    ("Ulsan", "KR", 35.55, 129.42),
    ("Yeosu", "KR", 34.76, 127.75),
    ("Gwangyang (Petrochem)", "KR", 34.92, 127.28),
    ("Incheon", "KR", 37.46, 126.62),
    ("Ningbo-Zhoushan", "CN", 29.87, 121.55),
    ("Shanghai", "CN", 31.25, 121.71),
    ("Qingdao", "CN", 36.08, 120.38),
    ("Dalian", "CN", 38.92, 121.65),
    ("Kaohsiung", "TW", 22.62, 120.27),
    ("Chiba", "JP", 35.56, 140.07),
    ("Yokohama", "JP", 35.43, 139.65),
    ("Vladivostok", "RU", 43.12, 131.89),

    # ── Gulf / Indian Ocean ───────────────────────────────────────────────────
    ("Fujairah Anchorage", "AE", 25.13, 56.34),
    ("Salalah", "OM", 16.94, 54.00),
    ("Jebel Ali", "AE", 24.98, 55.06),
    ("Ras Tanura", "SA", 26.67, 50.16),
    ("Kuwait City", "KW", 29.37, 48.00),
    ("Basra / Khor Al Zubair", "IQ", 30.53, 47.83),

    # ── West Africa ──────────────────────────────────────────────────────────
    ("Lomé (Togo)", "TG", 6.10, 1.23),
    ("Lagos (Nigeria)", "NG", 6.46, 3.39),

    # ── Brazil ───────────────────────────────────────────────────────────────
    ("São Luís (Maranhão)", "BR", -2.90, -44.30),
]


# EU member state ISO-2 codes (for is_eu flag on Port records)
_EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}


# Major Russian crude oil export terminals (for russian_port_call scoring signal)
_RUSSIAN_OIL_TERMINALS: set[str] = {
    "Primorsk",       # Baltic, crude
    "Ust-Luga",       # Baltic, crude + products
    "Novorossiysk",   # Black Sea, crude
    "Tuapse",         # Black Sea, products
    "Nakhodka/Kozmino",  # Pacific, crude (ESPO)
    "Murmansk",       # Arctic, crude
    "De-Kastri",      # Sakhalin, crude
    "Varandey",       # Arctic, crude
}


def seed_ports(db: Session) -> dict:
    """Insert major ports if not already present. Idempotent — skips existing by name.

    Sets is_eu=True for ports in EU member states (needed for legitimacy scoring).
    Sets is_russian_oil_terminal=True for major Russian crude export terminals.
    """
    from app.models.port import Port

    try:
        from geoalchemy2.shape import from_shape
        from shapely.geometry import Point

        def make_point(lat: float, lon: float):
            return from_shape(Point(lon, lat), srid=4326)
    except ImportError:
        # Fallback: WKT string (works with SpatiaLite raw SQL path)
        def make_point(lat: float, lon: float):
            return f"SRID=4326;POINT({lon} {lat})"

    inserted = 0
    skipped = 0
    updated_eu = 0
    updated_terminal = 0
    for name, country, lat, lon in MAJOR_PORTS:
        is_eu = country in _EU_COUNTRIES
        is_oil_terminal = name in _RUSSIAN_OIL_TERMINALS
        existing = db.query(Port).filter(Port.name == name).first()
        if existing:
            # Fix existing ports that should have is_eu=True
            if is_eu and not existing.is_eu:
                existing.is_eu = True
                updated_eu += 1
            # Fix existing ports that should be marked as oil terminals
            if is_oil_terminal and not existing.is_russian_oil_terminal:
                existing.is_russian_oil_terminal = True
                updated_terminal += 1
            skipped += 1
            continue
        port = Port(
            name=name,
            country=country,
            geometry=make_point(lat, lon),
            major_port=True,
            is_eu=is_eu,
            is_russian_oil_terminal=is_oil_terminal,
        )
        db.add(port)
        inserted += 1

    db.commit()
    logger.info(
        "seed_ports: inserted=%d skipped=%d updated_eu=%d updated_terminal=%d",
        inserted, skipped, updated_eu, updated_terminal,
    )
    return {"inserted": inserted, "skipped": skipped, "updated_eu": updated_eu, "updated_terminal": updated_terminal}
