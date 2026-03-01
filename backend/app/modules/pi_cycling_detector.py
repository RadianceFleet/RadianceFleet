"""P&I club change velocity detector -- identifies vessels rapidly cycling
through P&I clubs to evade sanctions.

Rapid changes in Protection & Indemnity club coverage are a strong indicator
of sanctions evasion: legitimate vessels maintain long-term relationships
with established IG P&I Group clubs. Shadow fleet vessels frequently switch
to non-IG clubs or change coverage rapidly as clubs delist sanctioned vessels.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import SpoofingTypeEnum
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory

logger = logging.getLogger(__name__)

# ── International Group of P&I Clubs (IG) ─────────────────────────────────
# These 12 clubs cover ~90% of world tonnage. Non-IG coverage is a risk signal.
IG_PI_CLUBS: frozenset[str] = frozenset({
    "american steamship owners mutual protection and indemnity association",
    "american club",
    "assuranceforeningen skuld",
    "skuld",
    "britannia steam ship insurance association",
    "britannia",
    "gard p&i",
    "gard",
    "japan ship owners' mutual protection & indemnity association",
    "japan p&i club",
    "the london steam-ship owners' mutual insurance association",
    "london p&i club",
    "north of england protecting & indemnity association",
    "north p&i",
    "the shipowners' mutual protection and indemnity association",
    "shipowners club",
    "the standard club",
    "standard club",
    "steamship mutual underwriting association",
    "steamship mutual",
    "the swedish club",
    "swedish club",
    "united kingdom mutual steam ship assurance association",
    "uk p&i club",
    "west of england ship owners mutual insurance association",
    "west of england",
})


def _is_ig_club(club_name: str | None) -> bool:
    """Check if a P&I club name matches an IG group member."""
    if not club_name:
        return False
    normalized = club_name.strip().lower()
    return normalized in IG_PI_CLUBS


def run_pi_cycling_detection(db: Session) -> dict:
    """Detect vessels with suspicious P&I club change patterns.

    Scoring:
      - 2+ P&I club changes in 90 days: +20
      - New club not in IG P&I group: +30

    Returns:
        {"status": "ok", "anomalies_created": N, "vessels_checked": N}
        or {"status": "disabled"} if feature flag is off.
    """
    if not settings.PI_CYCLING_DETECTION_ENABLED:
        return {"status": "disabled"}

    # Get all P&I club changes
    pi_changes = (
        db.query(VesselHistory)
        .filter(VesselHistory.field_changed == "pi_club_name")
        .order_by(VesselHistory.vessel_id, VesselHistory.observed_at)
        .all()
    )

    if not pi_changes:
        return {"status": "ok", "anomalies_created": 0, "vessels_checked": 0}

    # Group by vessel_id
    by_vessel: dict[int, list[VesselHistory]] = defaultdict(list)
    for change in pi_changes:
        by_vessel[change.vessel_id].append(change)

    now = datetime.utcnow()
    anomalies_created = 0

    for vessel_id, changes in by_vessel.items():
        if len(changes) < 2:
            continue

        # Count changes in 90-day window
        changes_90d = [
            c for c in changes
            if (now - c.observed_at).days <= 90
        ]

        if len(changes_90d) < 2:
            continue

        # Check if most recent club is non-IG
        most_recent = changes[-1]
        new_club = most_recent.new_value
        non_ig = not _is_ig_club(new_club)

        # Determine score
        if non_ig:
            score = 30
        else:
            score = 20

        # Check for existing anomaly
        existing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == vessel_id,
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.PI_CYCLING,
        ).first()
        if existing:
            continue

        # Build evidence
        change_history = [
            {
                "old_club": c.old_value,
                "new_club": c.new_value,
                "date": c.observed_at.isoformat() if c.observed_at else None,
            }
            for c in changes
        ]

        anomaly = SpoofingAnomaly(
            vessel_id=vessel_id,
            anomaly_type=SpoofingTypeEnum.PI_CYCLING,
            start_time_utc=changes[0].observed_at,
            end_time_utc=changes[-1].observed_at,
            risk_score_component=score,
            evidence_json={
                "changes_90d": len(changes_90d),
                "total_changes": len(changes),
                "non_ig_club": non_ig,
                "latest_club": new_club,
                "change_history": change_history,
            },
        )
        db.add(anomaly)
        anomalies_created += 1

    db.commit()
    logger.info(
        "P&I cycling: %d anomalies from %d vessels checked",
        anomalies_created, len(by_vessel),
    )
    return {
        "status": "ok",
        "anomalies_created": anomalies_created,
        "vessels_checked": len(by_vessel),
    }
