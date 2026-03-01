"""Vessel type consistency detector -- identifies vessels reporting AIS type
inconsistent with physical characteristics.

Shadow fleet tankers sometimes misreport their AIS vessel type to avoid
detection. For example, a 100,000 DWT vessel reporting as "fishing vessel"
or "pleasure craft" is physically impossible and indicates deliberate
misrepresentation. This detector cross-references vessel DWT against
reported AIS type for consistency.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import SpoofingTypeEnum
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory

logger = logging.getLogger(__name__)

# ── DWT threshold for "large vessel" classification ───────────────────────
_LARGE_VESSEL_DWT = 5000

# ── AIS vessel types that are inconsistent with large (>5000 DWT) vessels ─
# These types physically cannot apply to vessels above 5000 DWT.
_NON_COMMERCIAL_TYPES: frozenset[str] = frozenset({
    "fishing",
    "fishing vessel",
    "trawler",
    "pleasure craft",
    "pleasure",
    "yacht",
    "sailing",
    "sailing vessel",
    "recreational",
    "tug",
    "tugboat",
    "pilot vessel",
    "pilot",
    "search and rescue",
    "sar",
    "dredger",
    "dredging",
    "diving vessel",
    "diving",
    "military",
    "law enforcement",
    "medical transport",
    "reserved",
    "wing in ground",
    "wig",
})


def _is_non_commercial_type(vessel_type: str | None) -> bool:
    """Check if the vessel type is a non-commercial type."""
    if not vessel_type:
        return False
    normalized = vessel_type.strip().lower()
    return normalized in _NON_COMMERCIAL_TYPES


def run_vessel_type_consistency_detection(db: Session) -> dict:
    """Detect vessels with type/DWT inconsistency.

    Flags vessels where:
      - DWT > 5000 AND AIS type is non-commercial: +25
      - vessel_type field changed recently in VesselHistory: +15

    Returns:
        {"status": "ok", "anomalies_created": N, "vessels_checked": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.TYPE_CONSISTENCY_DETECTION_ENABLED:
        return {"status": "disabled"}

    vessels = db.query(Vessel).all()
    anomalies_created = 0
    vessels_checked = 0

    for vessel in vessels:
        vessels_checked += 1

        # Check for existing anomaly
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == vessel.vessel_id,
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.TYPE_DWT_MISMATCH,
        ).first()
        if existing:
            continue

        score = 0
        evidence: dict = {}

        # Check 1: Large vessel with non-commercial type
        dwt = vessel.deadweight
        vtype = vessel.vessel_type

        if dwt is not None and dwt > _LARGE_VESSEL_DWT and _is_non_commercial_type(vtype):
            score = 25
            evidence = {
                "reason": "type_dwt_mismatch",
                "deadweight": dwt,
                "reported_type": vtype,
                "dwt_threshold": _LARGE_VESSEL_DWT,
                "recent_type_change": False,
            }

        # Check 2: Recent vessel_type change in VesselHistory
        now = datetime.utcnow()
        cutoff = now - timedelta(days=90)
        type_changes = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.field_changed == "vessel_type",
                VesselHistory.observed_at >= cutoff,
            )
            .all()
        )

        if type_changes:
            # A recent type change is suspicious on its own
            if score == 0:
                score = 15
                evidence = {
                    "reason": "recent_type_change",
                    "recent_type_change": True,
                    "changes": [
                        {
                            "old_type": c.old_value,
                            "new_type": c.new_value,
                            "date": c.observed_at.isoformat() if c.observed_at else None,
                        }
                        for c in type_changes
                    ],
                }
            else:
                # Both signals present
                evidence["recent_type_change"] = True
                evidence["type_changes"] = [
                    {
                        "old_type": c.old_value,
                        "new_type": c.new_value,
                        "date": c.observed_at.isoformat() if c.observed_at else None,
                    }
                    for c in type_changes
                ]

        if score == 0:
            continue

        now_ts = datetime.utcnow()
        anomaly = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.TYPE_DWT_MISMATCH,
            start_time_utc=type_changes[0].observed_at if type_changes else now_ts,
            end_time_utc=now_ts,
            risk_score_component=score,
            evidence_json=evidence,
        )
        db.add(anomaly)
        anomalies_created += 1

    db.commit()
    logger.info(
        "Vessel type consistency: %d anomalies from %d vessels checked",
        anomalies_created, vessels_checked,
    )
    return {
        "status": "ok",
        "anomalies_created": anomalies_created,
        "vessels_checked": vessels_checked,
    }
