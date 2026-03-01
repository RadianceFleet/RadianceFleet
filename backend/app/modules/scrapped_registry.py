"""Scrapped vessel registry + historical track replay detection.

Part 1 — Scrapped IMO reuse:
  Cross-references active vessels against a YAML registry of scrapped (demolished)
  vessel IMOs. A vessel transmitting a scrapped IMO is strong evidence of identity
  fraud, scored at +50 points.

Part 2 — Historical track replay:
  Detects vessels whose recent track (last 7 days) is nearly identical to a prior
  historical track (30-90 days ago), indicating pre-programmed route replay
  spoofing. Scored at +45 points when correlation > 0.9.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import SpoofingTypeEnum
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.models.ais_point import AISPoint

logger = logging.getLogger(__name__)

# ── Lazy-loaded scrapped vessel registry ────────────────────────────────────
_SCRAPPED_REGISTRY: dict[str, dict] | None = None


def _load_scrapped_registry() -> dict[str, dict]:
    """Load and cache scrapped_vessels.yaml.

    Returns dict keyed by IMO string with value containing name/year/notes.
    """
    global _SCRAPPED_REGISTRY
    if _SCRAPPED_REGISTRY is not None:
        return _SCRAPPED_REGISTRY

    config_path = Path("config/scrapped_vessels.yaml")
    if not config_path.exists():
        logger.warning("scrapped_vessels.yaml not found at %s", config_path)
        _SCRAPPED_REGISTRY = {}
        return _SCRAPPED_REGISTRY

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    entries = raw.get("scrapped_imos", [])
    _SCRAPPED_REGISTRY = {}
    for entry in entries:
        imo = str(entry.get("imo", "")).strip()
        if imo:
            _SCRAPPED_REGISTRY[imo] = {
                "name": entry.get("name", ""),
                "scrapped_year": entry.get("scrapped_year"),
                "notes": entry.get("notes", ""),
            }

    logger.info("Loaded %d scrapped vessel IMOs", len(_SCRAPPED_REGISTRY))
    return _SCRAPPED_REGISTRY


def reload_scrapped_registry() -> dict[str, dict]:
    """Force-reload scrapped registry from disk."""
    global _SCRAPPED_REGISTRY
    _SCRAPPED_REGISTRY = None
    return _load_scrapped_registry()


# ── Part 1: Scrapped IMO reuse detection ────────────────────────────────────

def detect_scrapped_imo_reuse(
    db: Session,
    date_from: Any = None,
    date_to: Any = None,
) -> dict:
    """Detect vessels transmitting IMOs of scrapped/demolished vessels.

    Args:
        db: SQLAlchemy session.
        date_from: Optional start date filter (unused, kept for pipeline compat).
        date_to: Optional end date filter (unused, kept for pipeline compat).

    Returns:
        {"status": "ok", "matches": N, "anomalies_created": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.SCRAPPED_REGISTRY_DETECTION_ENABLED:
        return {"status": "disabled"}

    registry = _load_scrapped_registry()
    if not registry:
        return {"status": "ok", "matches": 0, "anomalies_created": 0}

    # Query all vessels with non-null IMO
    vessels = (
        db.query(Vessel)
        .filter(Vessel.imo.isnot(None), Vessel.imo != "")
        .all()
    )

    matches = 0
    anomalies_created = 0

    for vessel in vessels:
        imo = vessel.imo.strip()
        # Strip "IMO" prefix if present
        if imo.upper().startswith("IMO"):
            imo = imo[3:].strip()

        if imo not in registry:
            continue

        matches += 1
        scrapped_info = registry[imo]

        # Check for existing anomaly with subtype scrapped_imo
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == vessel.vessel_id,
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD,
        ).all()

        already_flagged = any(
            (a.evidence_json or {}).get("subtype") == "scrapped_imo"
            for a in existing
        )
        if already_flagged:
            continue

        now = datetime.now(timezone.utc)
        anomaly = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.IMO_FRAUD,
            start_time_utc=now,
            risk_score_component=50,
            evidence_json={
                "subtype": "scrapped_imo",
                "imo": imo,
                "scrapped_name": scrapped_info["name"],
                "scrapped_year": scrapped_info["scrapped_year"],
                "notes": scrapped_info["notes"],
            },
        )
        db.add(anomaly)
        anomalies_created += 1

    if anomalies_created > 0:
        db.commit()

    logger.info(
        "Scrapped IMO detection: %d matches, %d anomalies created",
        matches, anomalies_created,
    )
    return {
        "status": "ok",
        "matches": matches,
        "anomalies_created": anomalies_created,
    }


# ── Part 2: Historical track replay detection ──────────────────────────────

def _compute_track_correlation(
    recent_points: list[tuple[float, float, float]],
    historical_points: list[tuple[float, float, float]],
) -> float:
    """Compute temporal correlation between two tracks.

    Points are (lat, lon, time_of_day_seconds).
    Aligns by time-of-day and computes spatial correlation.

    Returns correlation score between 0.0 and 1.0.
    """
    if not recent_points or not historical_points:
        return 0.0

    # Bin by hour-of-day for alignment
    recent_bins: dict[int, list[tuple[float, float]]] = {}
    for lat, lon, tod in recent_points:
        hour = int(tod // 3600) % 24
        recent_bins.setdefault(hour, []).append((lat, lon))

    historical_bins: dict[int, list[tuple[float, float]]] = {}
    for lat, lon, tod in historical_points:
        hour = int(tod // 3600) % 24
        historical_bins.setdefault(hour, []).append((lat, lon))

    # Find matching hours
    common_hours = set(recent_bins.keys()) & set(historical_bins.keys())
    if len(common_hours) < 6:
        return 0.0

    total_distance = 0.0
    matched_count = 0

    for hour in sorted(common_hours):
        r_avg_lat = sum(p[0] for p in recent_bins[hour]) / len(recent_bins[hour])
        r_avg_lon = sum(p[1] for p in recent_bins[hour]) / len(recent_bins[hour])
        h_avg_lat = sum(p[0] for p in historical_bins[hour]) / len(historical_bins[hour])
        h_avg_lon = sum(p[1] for p in historical_bins[hour]) / len(historical_bins[hour])

        # Euclidean distance in degrees (approximate)
        dist = math.sqrt((r_avg_lat - h_avg_lat) ** 2 + (r_avg_lon - h_avg_lon) ** 2)
        total_distance += dist
        matched_count += 1

    if matched_count == 0:
        return 0.0

    avg_distance = total_distance / matched_count

    # Convert distance to correlation: 0 degrees distance = 1.0, >0.05 degrees (~3nm) = 0.0
    # Using exponential decay
    correlation = math.exp(-avg_distance / 0.01)
    return min(1.0, max(0.0, correlation))


def detect_track_replay(
    db: Session,
    date_from: Any = None,
    date_to: Any = None,
) -> dict:
    """Detect vessels replaying historical track patterns.

    For each vessel with sufficient AIS data (>200 points):
      - Compare recent track (last 7 days) against historical (30-90 days ago)
      - If temporal correlation >90%, flag as TRACK_REPLAY

    Args:
        db: SQLAlchemy session.
        date_from: Optional (unused, kept for pipeline compat).
        date_to: Optional (unused, kept for pipeline compat).

    Returns:
        {"status": "ok", "vessels_checked": N, "anomalies_created": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.TRACK_REPLAY_DETECTION_ENABLED:
        return {"status": "disabled"}

    now = datetime.now(timezone.utc)
    recent_start = now - timedelta(days=7)
    historical_start = now - timedelta(days=90)
    historical_end = now - timedelta(days=30)

    # Get vessels with sufficient recent AIS data
    from sqlalchemy import func

    vessel_ids_with_data = (
        db.query(AISPoint.vessel_id)
        .filter(AISPoint.timestamp_utc >= recent_start)
        .group_by(AISPoint.vessel_id)
        .having(func.count(AISPoint.ais_point_id) >= 200)
        .all()
    )

    vessels_checked = 0
    anomalies_created = 0

    for (vessel_id,) in vessel_ids_with_data:
        # Skip anchored vessels: check if avg SOG < 0.5kn in recent period
        avg_sog = (
            db.query(func.avg(AISPoint.sog))
            .filter(
                AISPoint.vessel_id == vessel_id,
                AISPoint.timestamp_utc >= recent_start,
                AISPoint.sog.isnot(None),
            )
            .scalar()
        )
        if avg_sog is not None and avg_sog < 0.5:
            continue

        # Get recent track points
        recent_points_raw = (
            db.query(AISPoint.lat, AISPoint.lon, AISPoint.timestamp_utc)
            .filter(
                AISPoint.vessel_id == vessel_id,
                AISPoint.timestamp_utc >= recent_start,
            )
            .order_by(AISPoint.timestamp_utc)
            .all()
        )

        if len(recent_points_raw) < 200:
            continue

        # Get historical track points
        historical_points_raw = (
            db.query(AISPoint.lat, AISPoint.lon, AISPoint.timestamp_utc)
            .filter(
                AISPoint.vessel_id == vessel_id,
                AISPoint.timestamp_utc >= historical_start,
                AISPoint.timestamp_utc <= historical_end,
            )
            .order_by(AISPoint.timestamp_utc)
            .all()
        )

        if len(historical_points_raw) < 200:
            continue

        vessels_checked += 1

        # Extract time-of-day for correlation
        recent_points = [
            (lat, lon, (ts.hour * 3600 + ts.minute * 60 + ts.second))
            for lat, lon, ts in recent_points_raw
        ]
        historical_points = [
            (lat, lon, (ts.hour * 3600 + ts.minute * 60 + ts.second))
            for lat, lon, ts in historical_points_raw
        ]

        correlation = _compute_track_correlation(recent_points, historical_points)

        if correlation <= 0.9:
            continue

        # Check for existing anomaly
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == vessel_id,
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.TRACK_REPLAY,
        ).first()
        if existing:
            continue

        anomaly = SpoofingAnomaly(
            vessel_id=vessel_id,
            anomaly_type=SpoofingTypeEnum.TRACK_REPLAY,
            start_time_utc=now,
            risk_score_component=45,
            evidence_json={
                "correlation": round(correlation, 4),
                "recent_point_count": len(recent_points_raw),
                "historical_point_count": len(historical_points_raw),
                "recent_period_start": recent_start.isoformat(),
                "historical_period": f"{historical_start.isoformat()} to {historical_end.isoformat()}",
            },
        )
        db.add(anomaly)
        anomalies_created += 1

    if anomalies_created > 0:
        db.commit()

    logger.info(
        "Track replay detection: %d vessels checked, %d anomalies created",
        vessels_checked, anomalies_created,
    )
    return {
        "status": "ok",
        "vessels_checked": vessels_checked,
        "anomalies_created": anomalies_created,
    }
