"""Stateless MMSI detector -- identifies vessels using unallocated,
landlocked, or micro-territory Maritime Identification Digits.

MIDs are the first 3 digits of a 9-digit MMSI for ship stations.
Vessels broadcasting with truly unallocated MIDs are operating outside
any national registry, which is a strong indicator of identity fraud.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import SpoofingTypeEnum
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.utils.itu_mid_table import (
    ITU_MID_ALLOCATION,
    UNALLOCATED_MIDS,
    LANDLOCKED_MIDS,
    MICRO_TERRITORY_MIDS,
)

logger = logging.getLogger(__name__)


def _extract_ship_mid(mmsi: str) -> int | None:
    """Extract MID from a ship-station MMSI, excluding non-ship patterns.

    Ship station MMSIs: MIDXXXXXX (first digit 2-7).
    Excluded patterns:
      - SAR aircraft: 111MIDXXX
      - AtoN (Aids to Navigation): 99MIDXXXX
      - Coastal stations: 00MIDXXXX
    """
    if not mmsi or not mmsi.isdigit() or len(mmsi) != 9:
        return None

    # Exclude SAR aircraft (111MIDXXX)
    if mmsi.startswith("111"):
        return None

    # Exclude AtoN (99MIDXXXX)
    if mmsi.startswith("99"):
        return None

    # Exclude coastal stations (00MIDXXXX)
    if mmsi.startswith("00"):
        return None

    # Ship station: first 3 digits are MID
    mid = int(mmsi[:3])
    return mid


def run_stateless_detection(db: Session) -> dict:
    """Scan all vessels for stateless/suspicious MMSI MIDs.

    Three detection tiers:
      1. Unallocated MID: +35pts, creates SpoofingAnomaly(STATELESS_MMSI)
      2. Landlocked MID on tanker: +20pts
      3. Micro-territory MID: +10pts (corroborating only)

    Returns:
        {"status": "ok", "tier1": N, "tier2": N, "tier3": N, "vessels_checked": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.STATELESS_MMSI_DETECTION_ENABLED:
        return {"status": "disabled"}

    vessels = db.query(Vessel).all()
    now = datetime.now(timezone.utc)

    tier1_count = 0
    tier2_count = 0
    tier3_count = 0
    vessels_checked = 0

    for vessel in vessels:
        mid = _extract_ship_mid(vessel.mmsi)
        if mid is None:
            continue

        vessels_checked += 1
        tier = None
        score = 0
        country = ITU_MID_ALLOCATION.get(mid)

        if mid in UNALLOCATED_MIDS:
            tier = 1
            score = 35
        elif mid in LANDLOCKED_MIDS:
            # Only flag landlocked MIDs on tanker-type vessels
            if vessel.vessel_type and "tanker" in vessel.vessel_type.lower():
                tier = 2
                score = 20
        elif mid in MICRO_TERRITORY_MIDS:
            tier = 3
            score = 10

        if tier is None:
            continue

        # Check for existing anomaly to avoid duplicates
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == vessel.vessel_id,
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STATELESS_MMSI,
        ).first()
        if existing:
            continue

        anomaly = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.STATELESS_MMSI,
            start_time_utc=now,
            risk_score_component=score,
            evidence_json={
                "mid": mid,
                "country": country,
                "tier": tier,
                "mmsi": vessel.mmsi,
            },
        )
        db.add(anomaly)

        if tier == 1:
            tier1_count += 1
        elif tier == 2:
            tier2_count += 1
        elif tier == 3:
            tier3_count += 1

    db.commit()
    total = tier1_count + tier2_count + tier3_count
    logger.info(
        "Stateless MMSI: %d anomalies (T1=%d, T2=%d, T3=%d) from %d vessels",
        total, tier1_count, tier2_count, tier3_count, vessels_checked,
    )
    return {
        "status": "ok",
        "tier1": tier1_count,
        "tier2": tier2_count,
        "tier3": tier3_count,
        "vessels_checked": vessels_checked,
    }
