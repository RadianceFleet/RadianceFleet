"""Flag hopping detector -- identifies vessels that change flag state
with suspicious frequency or to shadow fleet registries.

Frequent flag changes are a key indicator of sanctions evasion, especially
when the new flag is a shadow fleet convenience registry.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import SpoofingTypeEnum
from app.models.gap_event import AISGapEvent
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory

logger = logging.getLogger(__name__)

# Shadow fleet registries -- flag changes TO these countries get 2x score
HIGH_RISK_REGISTRIES: frozenset[str] = frozenset({
    "Cameroon", "Gabon", "Comoros", "Gambia", "Palau",
    "Sierra Leone", "Tanzania", "Honduras",
    # Also match ISO codes
    "CM", "GA", "KM", "GM", "PW", "SL", "TZ", "HN",
})

# Well-regulated registries -- flag changes TO these get 0.5x score
WHITE_LIST_REGISTRIES: frozenset[str] = frozenset({
    "Norway", "Denmark", "Germany", "Japan", "Netherlands",
    # Also match ISO codes
    "NO", "DK", "DE", "JP", "NL",
})


def run_flag_hopping_detection(db: Session) -> dict:
    """Detect vessels with suspicious flag change patterns.

    Scoring:
      - 2 flag changes in 90 days: +20pts
      - 3+ flag changes in 90 days: +40pts
      - 5+ flag changes in 365 days: +50pts

    Modifiers:
      - Ownership change within +/-7d of flag change: 50% discount
      - New flag is high-risk registry: 2x multiplier
      - New flag is white-list registry: 0.5x multiplier

    Returns:
        {"status": "ok", "anomalies_created": N, "vessels_checked": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.FLAG_HOPPING_DETECTION_ENABLED:
        return {"status": "disabled"}

    # Get all flag change history entries
    flag_changes = (
        db.query(VesselHistory)
        .filter(VesselHistory.field_changed == "flag")
        .order_by(VesselHistory.vessel_id, VesselHistory.observed_at)
        .all()
    )

    if not flag_changes:
        return {"status": "ok", "anomalies_created": 0, "vessels_checked": 0}

    # Group by vessel_id
    by_vessel: dict[int, list[VesselHistory]] = defaultdict(list)
    for change in flag_changes:
        by_vessel[change.vessel_id].append(change)

    # Get all ownership changes for discount check
    owner_changes = (
        db.query(VesselHistory)
        .filter(VesselHistory.field_changed == "owner_name")
        .all()
    )
    owner_change_dates: dict[int, list[datetime]] = defaultdict(list)
    for oc in owner_changes:
        owner_change_dates[oc.vessel_id].append(oc.observed_at)

    now = datetime.utcnow()
    anomalies_created = 0

    for vessel_id, changes in by_vessel.items():
        if len(changes) < 2:
            continue

        # Count changes in time windows
        changes_90d = [
            c for c in changes
            if (now - c.observed_at).days <= 90
        ]
        changes_365d = [
            c for c in changes
            if (now - c.observed_at).days <= 365
        ]

        # Determine base score
        base_score = 0
        if len(changes_90d) >= 3:
            base_score = 40
        elif len(changes_90d) >= 2:
            base_score = 20

        if len(changes_365d) >= 5:
            base_score = max(base_score, 50)

        if base_score == 0:
            continue

        # Apply ownership-change discount: if any flag change has a
        # concurrent owner change within +/-7d, discount by 50%
        has_ownership_discount = False
        for fc in changes:
            for od in owner_change_dates.get(vessel_id, []):
                if abs((fc.observed_at - od).days) <= 7:
                    has_ownership_discount = True
                    break
            if has_ownership_discount:
                break

        if has_ownership_discount:
            base_score = base_score // 2

        # Apply registry modifier based on most recent flag change
        most_recent = changes[-1]
        new_flag = most_recent.new_value or ""
        if new_flag.strip() in HIGH_RISK_REGISTRIES:
            base_score = base_score * 2
        elif new_flag.strip() in WHITE_LIST_REGISTRIES:
            base_score = base_score // 2

        if base_score <= 0:
            continue

        # Check for existing FLAG_HOPPING anomaly for this vessel
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == vessel_id,
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING,
        ).first()
        if existing:
            continue

        # Build evidence
        flag_history = [
            {
                "old_flag": c.old_value,
                "new_flag": c.new_value,
                "date": c.observed_at.isoformat() if c.observed_at else None,
            }
            for c in changes
        ]

        anomaly = SpoofingAnomaly(
            vessel_id=vessel_id,
            anomaly_type=SpoofingTypeEnum.FLAG_HOPPING,
            start_time_utc=changes[0].observed_at,
            end_time_utc=changes[-1].observed_at,
            risk_score_component=base_score,
            evidence_json={
                "flag_changes": flag_history,
                "changes_90d": len(changes_90d),
                "changes_365d": len(changes_365d),
                "ownership_discount": has_ownership_discount,
                "latest_flag": new_flag,
            },
        )
        db.add(anomaly)
        anomalies_created += 1

        # Dark-period flag change sub-type: check if any flag change
        # coincides with an AIS gap (within +/-6 hours). Vessels commonly
        # change flags during dark periods to avoid detection.
        for fc in changes:
            if not fc.observed_at:
                continue
            gap_window_start = fc.observed_at - timedelta(hours=6)
            gap_window_end = fc.observed_at + timedelta(hours=6)
            overlapping_gap = db.query(AISGapEvent).filter(
                AISGapEvent.vessel_id == vessel_id,
                AISGapEvent.gap_start_utc <= gap_window_end,
                AISGapEvent.gap_end_utc >= gap_window_start,
            ).first()
            if overlapping_gap:
                # Create a sub-type anomaly for dark-period flag change
                dark_flag_existing = db.query(SpoofingAnomaly).filter(
                    SpoofingAnomaly.vessel_id == vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING,
                    SpoofingAnomaly.evidence_json["sub_type"].as_string() == "dark_period_flag_change",
                ).first()
                if not dark_flag_existing:
                    dark_anomaly = SpoofingAnomaly(
                        vessel_id=vessel_id,
                        anomaly_type=SpoofingTypeEnum.FLAG_HOPPING,
                        start_time_utc=fc.observed_at,
                        end_time_utc=fc.observed_at,
                        risk_score_component=20,
                        evidence_json={
                            "sub_type": "dark_period_flag_change",
                            "flag_change_date": fc.observed_at.isoformat(),
                            "old_flag": fc.old_value,
                            "new_flag": fc.new_value,
                            "gap_event_id": overlapping_gap.gap_event_id,
                            "gap_start": overlapping_gap.gap_start_utc.isoformat() if overlapping_gap.gap_start_utc else None,
                            "gap_end": overlapping_gap.gap_end_utc.isoformat() if overlapping_gap.gap_end_utc else None,
                        },
                    )
                    db.add(dark_anomaly)
                    anomalies_created += 1
                break  # One dark-period anomaly per vessel is sufficient

    db.commit()
    logger.info(
        "Flag hopping: %d anomalies from %d vessels checked",
        anomalies_created, len(by_vessel),
    )
    return {
        "status": "ok",
        "anomalies_created": anomalies_created,
        "vessels_checked": len(by_vessel),
    }
