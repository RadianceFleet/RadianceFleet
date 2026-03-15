"""Spire Maritime satellite AIS collector for Persian Gulf.

Fetches satellite AIS positions from Spire's GraphQL API for the Persian Gulf
bounding box and ingests them into the RadianceFleet database. Tracks quota
usage in CollectionRun.details_json.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Persian Gulf bounding box (GeoJSON polygon coordinates)
PERSIAN_GULF_BBOX = [[[47, 23], [57, 23], [57, 30.5], [47, 30.5], [47, 23]]]


def _get_quota_used_this_month(db: Session) -> int:
    """Count Spire API calls used this calendar month from CollectionRun records."""
    from app.models.collection_run import CollectionRun

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    runs = (
        db.query(CollectionRun)
        .filter(
            CollectionRun.source == "spire",
            CollectionRun.started_at >= month_start,
            CollectionRun.status == "completed",
        )
        .all()
    )

    total = 0
    for run in runs:
        if run.details_json:
            try:
                details = json.loads(run.details_json)
                total += details.get("quota_used", 0)
            except (json.JSONDecodeError, TypeError):
                pass
    return total


def collect_spire_gulf_ais(
    db: Session,
    duration_seconds: int = 300,
) -> dict:
    """Collect satellite AIS positions for the Persian Gulf from Spire Maritime.

    Args:
        db: Active SQLAlchemy session.
        duration_seconds: Not used directly (single-fetch API), kept for interface compat.

    Returns:
        Stats dict: {points_imported, vessels_seen, errors, quota_used}
    """
    from app.models.ais_observation import AISObservation
    from app.models.ais_point import AISPoint
    from app.models.collection_run import CollectionRun
    from app.models.vessel import Vessel
    from app.modules.normalize import is_non_vessel_mmsi
    from app.modules.spire_ais_client import SpireAisClient
    from app.utils.vessel_identity import flag_to_risk_category, mmsi_to_flag

    if not settings.SPIRE_AIS_COLLECTION_ENABLED:
        logger.info("Spire AIS collection disabled")
        return {"points_imported": 0, "vessels_seen": 0, "errors": 0, "quota_used": 0}

    if not settings.SPIRE_AIS_API_KEY:
        logger.warning("SPIRE_AIS_API_KEY not configured")
        return {"points_imported": 0, "vessels_seen": 0, "errors": 0, "quota_used": 0}

    # Check monthly quota
    monthly_quota = settings.SPIRE_MONTHLY_QUOTA
    quota_used = _get_quota_used_this_month(db)
    if quota_used >= monthly_quota:
        logger.warning(
            "Spire monthly quota exhausted (%d/%d)", quota_used, monthly_quota
        )
        return {
            "points_imported": 0,
            "vessels_seen": 0,
            "errors": 0,
            "quota_used": quota_used,
            "quota_exhausted": True,
        }

    # Create collection run record
    run = CollectionRun(
        source="spire",
        started_at=datetime.now(UTC),
        status="running",
    )
    db.add(run)
    db.flush()

    points_imported = 0
    vessels_seen: set[str] = set()
    errors = 0
    api_calls = 0

    try:
        client = SpireAisClient()
        lookback_hours = getattr(settings, "SPIRE_LOOKBACK_HOURS", 2)
        since_utc = datetime.utcnow() - timedelta(hours=lookback_hours)

        positions = client.fetch_positions(
            bbox=PERSIAN_GULF_BBOX,
            since_utc=since_utc,
            limit=500,
        )
        api_calls = 1

        for pos in positions:
            try:
                mmsi = pos["mmsi"]
                if is_non_vessel_mmsi(mmsi):
                    continue

                vessels_seen.add(mmsi)

                # Upsert vessel
                vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                if not vessel:
                    derived_flag = mmsi_to_flag(mmsi)
                    vessel = Vessel(
                        mmsi=mmsi,
                        name=pos.get("name"),
                        imo_number=pos.get("imo"),
                        flag=derived_flag,
                        flag_risk_category=flag_to_risk_category(derived_flag),
                        ais_class="A",
                        ais_source="spire",
                        mmsi_first_seen_utc=pos["timestamp_utc"],
                    )
                    try:
                        with db.begin_nested():
                            db.add(vessel)
                            db.flush()
                    except IntegrityError:
                        vessel = db.query(Vessel).filter(Vessel.mmsi == mmsi).first()
                        if not vessel:
                            errors += 1
                            continue

                timestamp = pos["timestamp_utc"]

                # Update data freshness
                current_ais = getattr(vessel, "last_ais_received_utc", None)
                if (
                    current_ais is None
                    or not isinstance(current_ais, datetime)
                    or timestamp > current_ais
                ):
                    vessel.last_ais_received_utc = timestamp

                # Dedup via unique constraint
                existing = (
                    db.query(AISPoint)
                    .filter(
                        AISPoint.vessel_id == vessel.vessel_id,
                        AISPoint.timestamp_utc == timestamp,
                        AISPoint.source == "spire",
                    )
                    .first()
                )
                if existing:
                    continue

                point = AISPoint(
                    vessel_id=vessel.vessel_id,
                    timestamp_utc=timestamp,
                    lat=pos["lat"],
                    lon=pos["lon"],
                    sog=pos.get("sog"),
                    cog=pos.get("cog"),
                    heading=pos.get("heading"),
                    ais_class="A",
                    source="spire",
                )
                db.add(point)
                points_imported += 1

                # Dual-write to AIS observations
                try:
                    obs = AISObservation(
                        mmsi=mmsi,
                        timestamp_utc=timestamp,
                        lat=pos["lat"],
                        lon=pos["lon"],
                        sog=pos.get("sog"),
                        cog=pos.get("cog"),
                        heading=pos.get("heading"),
                        source="spire",
                    )
                    db.add(obs)
                except Exception as exc:
                    logger.debug("AIS observation dual-write failed: %s", exc)

            except Exception:
                errors += 1

        # Update collection run
        run.finished_at = datetime.now(UTC)
        run.points_imported = points_imported
        run.vessels_seen = len(vessels_seen)
        run.errors = errors
        run.status = "completed"
        run.details_json = json.dumps({
            "quota_used": api_calls,
            "quota_remaining": monthly_quota - quota_used - api_calls,
            "bbox": "Persian Gulf",
            "lookback_hours": lookback_hours,
        })

        db.commit()

    except Exception as exc:
        logger.error("Spire AIS collection failed: %s", exc)
        errors += 1
        run.finished_at = datetime.now(UTC)
        run.status = "failed"
        run.errors = errors
        run.details_json = json.dumps({"error": str(exc), "quota_used": api_calls})
        try:
            db.commit()
        except Exception:
            db.rollback()

    logger.info(
        "Spire AIS: %d points, %d vessels, %d errors, %d API calls",
        points_imported,
        len(vessels_seen),
        errors,
        api_calls,
    )

    return {
        "points_imported": points_imported,
        "vessels_seen": len(vessels_seen),
        "errors": errors,
        "quota_used": api_calls,
    }
