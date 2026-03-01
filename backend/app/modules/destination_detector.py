"""Stage 3-A: AIS Destination Manipulation Detector.

Detects vessels whose declared AIS destination field contradicts their actual
navigational behaviour. Three anomaly sub-types:

1. **Heading deviation**: Vessel heading toward known STS zone while declaring
   a legitimate (EU) port as destination.  Score: +40.
2. **Blank/generic destination**: Destination field is empty or uses a generic
   placeholder like "FOR ORDERS", "TBA", "AT SEA".  Score: +10.
3. **Frequent destination changes**: Destination text changed >3 times in the
   last 7 days.  Score: +20.

Guards:
- Only applies to vessels with DWT > 5000 (skip small vessels).
- Creates SpoofingAnomaly records with type DESTINATION_DEVIATION.
- Gated by DESTINATION_DETECTION_ENABLED feature flag.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, date, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.base import SpoofingTypeEnum
from app.models.corridor import Corridor
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel

logger = logging.getLogger(__name__)

# Generic / blank destination patterns (case-insensitive match)
_BLANK_DESTINATIONS = {
    "", "FOR ORDERS", "FOR ORDER", "TBA", "TBN", "AT SEA",
    "NOT AVAILABLE", "N/A", "NA", "NONE", "UNKNOWN", ".",
    "---", "XXX", "STS", "ORDERS", "WAITING",
}

# Minimum DWT to analyse (skip small vessels)
_MIN_DWT = 5000

# Bearing deviation threshold (degrees) — if vessel COG diverges from declared
# port bearing by more than this and is heading toward an STS zone, flag it.
_BEARING_DEVIATION_DEG = 45.0

# Hours of recent AIS data to consider for heading analysis
_HEADING_WINDOW_HOURS = 6

# Load scoring config with fallbacks
try:
    from app.modules.risk_scoring import load_scoring_config
    _cfg = load_scoring_config()
    _dest_cfg = _cfg.get("destination", {})
except Exception:
    _dest_cfg = {}

SCORE_HEADING_TO_STS = int(_dest_cfg.get("heading_to_sts_declaring_eu", 40))
SCORE_BLANK_GENERIC = int(_dest_cfg.get("blank_generic_destination", 10))
SCORE_FREQUENT_CHANGES = int(_dest_cfg.get("destination_changes_3_in_7d", 20))


def _initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute initial bearing (degrees, 0-360) from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)
    x = math.sin(d_lambda) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda))
    return math.degrees(math.atan2(x, y)) % 360


def _bearing_diff(b1: float, b2: float) -> float:
    """Absolute angular difference between two bearings (0-180)."""
    diff = abs(b1 - b2) % 360
    return min(diff, 360 - diff)


def _is_blank_or_generic(dest: Optional[str]) -> bool:
    """Check whether a destination string is blank or generic.

    Returns False for None/missing — a missing destination simply means
    no data was broadcast, not that the vessel is being deceptive.  Only
    explicitly-set generic placeholders should trigger.
    """
    if dest is None:
        return False
    normalized = dest.strip().upper()
    return normalized in _BLANK_DESTINATIONS


def _get_corridor_centers(db: Session) -> list[dict]:
    """Load STS zone corridors with their centroid coordinates."""
    from app.utils.geo import load_geometry

    corridors = db.query(Corridor).filter(
        Corridor.corridor_type.in_(["sts_zone", "STS_ZONE"])
    ).all()

    centers = []
    for corr in corridors:
        geom = load_geometry(corr.geometry)
        if geom is None:
            continue
        centroid = geom.centroid
        centers.append({
            "corridor_id": corr.corridor_id,
            "name": corr.name,
            "lat": centroid.y,
            "lon": centroid.x,
        })
    return centers


def detect_destination_anomalies(
    db: Session,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict[str, Any]:
    """Run destination manipulation detection.

    Args:
        db: SQLAlchemy session.
        date_from: Optional start date for AIS window.
        date_to: Optional end date for AIS window.

    Returns:
        Dict with detection statistics.
    """
    if not settings.DESTINATION_DETECTION_ENABLED:
        return {"status": "disabled", "anomalies_created": 0}

    stats: dict[str, Any] = {
        "status": "ok",
        "vessels_analysed": 0,
        "skipped_small_dwt": 0,
        "blank_destination": 0,
        "frequent_changes": 0,
        "heading_deviation": 0,
        "anomalies_created": 0,
    }

    # Determine time window
    if date_to is None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc)

    if date_from is None:
        window_start = now - timedelta(days=7)
    else:
        window_start = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)

    heading_window_start = now - timedelta(hours=_HEADING_WINDOW_HOURS)

    # Load STS zone corridor centers
    sts_centers = _get_corridor_centers(db)

    # Load EU ports for declared-destination resolution
    from app.models.port import Port
    eu_ports = db.query(Port).filter(Port.is_eu == True).all()  # noqa: E712
    eu_port_names = {p.name.strip().upper() for p in eu_ports if p.name}

    # Get vessels with DWT > 5000 that have recent AIS data
    vessels = (
        db.query(Vessel)
        .filter(Vessel.deadweight > _MIN_DWT)
        .filter(Vessel.merged_into_vessel_id.is_(None))
        .all()
    )

    for vessel in vessels:
        # Get recent AIS points for this vessel
        recent_points = (
            db.query(AISPoint)
            .filter(
                AISPoint.vessel_id == vessel.vessel_id,
                AISPoint.timestamp_utc >= window_start,
                AISPoint.timestamp_utc <= now,
            )
            .order_by(AISPoint.timestamp_utc.desc())
            .all()
        )

        if not recent_points:
            continue

        stats["vessels_analysed"] += 1

        # Extract destination from the dedicated AIS destination column
        latest_point = recent_points[0]
        declared_dest = getattr(latest_point, "destination", None)

        # ── Check 1: Blank/generic destination ──────────────────────────────
        if _is_blank_or_generic(declared_dest):
            # Check for existing anomaly (dedup)
            existing = db.query(SpoofingAnomaly).filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.DESTINATION_DEVIATION,
                SpoofingAnomaly.start_time_utc >= window_start,
            ).first()

            if existing is None:
                anomaly = SpoofingAnomaly(
                    vessel_id=vessel.vessel_id,
                    anomaly_type=SpoofingTypeEnum.DESTINATION_DEVIATION,
                    start_time_utc=latest_point.timestamp_utc,
                    risk_score_component=SCORE_BLANK_GENERIC,
                    evidence_json={
                        "subtype": "blank_generic_destination",
                        "declared_destination": declared_dest,
                    },
                )
                db.add(anomaly)
                stats["blank_destination"] += 1
                stats["anomalies_created"] += 1

        # ── Check 2: Frequent destination changes (>3 in 7 days) ────────────
        # Collect unique destinations from the destination column across the window
        dest_values = []
        for pt in recent_points:
            dest = getattr(pt, "destination", None)
            if dest is not None:
                dest_values.append(dest.strip().upper())

        # Count distinct destination values
        if dest_values:
            unique_dests = set(dest_values)
            if len(unique_dests) > 3:
                existing = db.query(SpoofingAnomaly).filter(
                    SpoofingAnomaly.vessel_id == vessel.vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.DESTINATION_DEVIATION,
                    SpoofingAnomaly.start_time_utc >= window_start,
                ).first()

                if existing is None:
                    anomaly = SpoofingAnomaly(
                        vessel_id=vessel.vessel_id,
                        anomaly_type=SpoofingTypeEnum.DESTINATION_DEVIATION,
                        start_time_utc=latest_point.timestamp_utc,
                        risk_score_component=SCORE_FREQUENT_CHANGES,
                        evidence_json={
                            "subtype": "destination_changes_3_in_7d",
                            "change_count": len(unique_dests),
                            "destinations": list(unique_dests)[:10],
                        },
                    )
                    db.add(anomaly)
                    stats["frequent_changes"] += 1
                    stats["anomalies_created"] += 1

        # ── Check 3: Heading toward STS zone while declaring EU port ────────
        if (
            declared_dest
            and not _is_blank_or_generic(declared_dest)
            and sts_centers
        ):
            dest_upper = declared_dest.strip().upper()
            declares_eu = dest_upper in eu_port_names

            if declares_eu:
                # Get last 6h of AIS points for heading analysis
                heading_points = [
                    pt for pt in recent_points
                    if pt.timestamp_utc >= heading_window_start
                    and pt.cog is not None
                    and pt.lat is not None
                    and pt.lon is not None
                ]

                if len(heading_points) >= 2:
                    # Compute median COG over last 6h
                    cogs = sorted(pt.cog for pt in heading_points)
                    median_cog = cogs[len(cogs) // 2]

                    # Latest position
                    last_lat = heading_points[0].lat
                    last_lon = heading_points[0].lon

                    # Check bearing to each STS zone center
                    for sts in sts_centers:
                        bearing_to_sts = _initial_bearing(
                            last_lat, last_lon, sts["lat"], sts["lon"]
                        )
                        deviation = _bearing_diff(median_cog, bearing_to_sts)

                        if deviation < _BEARING_DEVIATION_DEG:
                            # Vessel heading toward STS zone, not toward declared EU port
                            existing = db.query(SpoofingAnomaly).filter(
                                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.DESTINATION_DEVIATION,
                                SpoofingAnomaly.start_time_utc >= window_start,
                            ).first()

                            if existing is None:
                                anomaly = SpoofingAnomaly(
                                    vessel_id=vessel.vessel_id,
                                    anomaly_type=SpoofingTypeEnum.DESTINATION_DEVIATION,
                                    start_time_utc=latest_point.timestamp_utc,
                                    risk_score_component=SCORE_HEADING_TO_STS,
                                    evidence_json={
                                        "subtype": "heading_to_sts_declaring_eu",
                                        "declared_destination": declared_dest,
                                        "median_cog": round(median_cog, 1),
                                        "bearing_to_sts_zone": round(bearing_to_sts, 1),
                                        "sts_zone": sts["name"],
                                        "deviation_deg": round(deviation, 1),
                                    },
                                )
                                db.add(anomaly)
                                stats["heading_deviation"] += 1
                                stats["anomalies_created"] += 1
                            break  # one STS match per vessel is enough

    db.commit()
    logger.info(
        "Destination detection complete: %d vessels, %d anomalies",
        stats["vessels_analysed"],
        stats["anomalies_created"],
    )
    return stats
