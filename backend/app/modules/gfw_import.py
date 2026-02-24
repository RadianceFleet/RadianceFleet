"""GFW (Global Fishing Watch) pre-computed vessel detection importer.

FR8: Ingest GFW vessel detections, correlate against AIS, create DarkVesselDetection
records for unmatched detections (potential dark ships in monitored corridors).

Download from: https://globalfishingwatch.org/data-download/
Expected CSV columns: detect_id, timestamp, lat, lon, vessel_length_m, vessel_score, vessel_type
"""
from __future__ import annotations

import csv
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

AIS_MATCH_RADIUS_NM = 2.0
AIS_MATCH_WINDOW_H = 3


def parse_gfw_row(row: dict) -> dict:
    """Parse and validate one GFW detection CSV row. Raises ValueError on bad data."""
    try:
        lat = float(row["lat"])
        lon = float(row["lon"])
    except (KeyError, ValueError) as e:
        raise ValueError(f"Invalid lat/lon in row {row.get('detect_id', '?')}: {e}") from e

    if not (-90 <= lat <= 90):
        raise ValueError(f"lat out of range [{lat}] in row {row.get('detect_id', '?')}")
    if not (-180 <= lon <= 180):
        raise ValueError(f"lon out of range [{lon}] in row {row.get('detect_id', '?')}")

    ts_raw = row.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"Invalid timestamp '{ts_raw}': {e}") from e

    return {
        "scene_id": row.get("detect_id", ""),
        "detection_lat": lat,
        "detection_lon": lon,
        "detection_time_utc": ts,
        "length_estimate_m": float(row["vessel_length_m"]) if row.get("vessel_length_m") else None,
        "model_confidence": float(row.get("vessel_score") or 0),
        "vessel_type_inferred": row.get("vessel_type", "unknown"),
    }


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    R = 3440.065
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def ingest_gfw_csv(db: Session, filepath: str) -> dict:
    """Import GFW detections CSV. Returns {"total", "matched", "dark", "rejected"}."""
    from app.models.stubs import DarkVesselDetection
    from app.models.ais_point import AISPoint

    if not Path(filepath).exists():
        raise FileNotFoundError(f"GFW CSV not found: {filepath}")

    stats: dict[str, int] = {"total": 0, "matched": 0, "dark": 0, "rejected": 0}

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            stats["total"] += 1
            try:
                row = parse_gfw_row(raw)
            except ValueError as e:
                logger.warning("Rejected GFW row: %s", e)
                stats["rejected"] += 1
                continue

            ts = row["detection_time_utc"]
            # Make tz-naive for comparison with DB timestamps
            if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)

            window_start = ts - timedelta(hours=AIS_MATCH_WINDOW_H)
            window_end = ts + timedelta(hours=AIS_MATCH_WINDOW_H)

            candidates = (
                db.query(AISPoint)
                .filter(
                    AISPoint.timestamp_utc >= window_start,
                    AISPoint.timestamp_utc <= window_end,
                )
                .all()
            )

            matched_vessel_id = None
            for pt in candidates:
                if _haversine_nm(row["detection_lat"], row["detection_lon"], pt.lat, pt.lon) <= AIS_MATCH_RADIUS_NM:
                    matched_vessel_id = pt.vessel_id
                    break

            db.add(DarkVesselDetection(
                scene_id=row["scene_id"],
                detection_lat=row["detection_lat"],
                detection_lon=row["detection_lon"],
                detection_time_utc=ts,
                length_estimate_m=row["length_estimate_m"],
                vessel_type_inferred=row["vessel_type_inferred"],
                ais_match_attempted=True,
                ais_match_result="matched" if matched_vessel_id else "unmatched",
                matched_vessel_id=matched_vessel_id,
                model_confidence=row["model_confidence"],
            ))
            if matched_vessel_id:
                stats["matched"] += 1
            else:
                stats["dark"] += 1

    db.commit()
    logger.info("GFW import complete: %s", stats)
    return stats
