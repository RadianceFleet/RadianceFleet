"""Insurance Gap Timeline Detector — reconstruct P&I club membership timeline
from VesselHistory, detect coverage gaps, and score by duration with bonuses
for coinciding flag/ownership changes.

Gated by INSURANCE_GAP_DETECTION_ENABLED (disabled by default).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.insurance_gap_event import InsuranceGapEvent
from app.models.vessel_history import VesselHistory
from app.models.vessel_owner import VesselOwner
from app.modules.pi_verification import _is_ig_club

logger = logging.getLogger(__name__)


def detect_insurance_gaps(db: Session, vessel_id: int) -> list[InsuranceGapEvent]:
    """Detect P&I club coverage gaps for a vessel.

    1. Query VesselHistory pi_club_name changes for the vessel
    2. Build chronological club membership timeline
    3. Find coverage gaps exceeding min_gap_days
    4. Check for coinciding flag/ownership changes
    5. Score and persist InsuranceGapEvent records

    Returns list of InsuranceGapEvent objects created or already existing.
    """
    if not settings.INSURANCE_GAP_DETECTION_ENABLED:
        return []

    min_gap_days = settings.INSURANCE_GAP_MIN_DAYS

    # Get P&I club change history for this vessel
    pi_changes = (
        db.query(VesselHistory)
        .filter(
            VesselHistory.vessel_id == vessel_id,
            VesselHistory.field_changed == "pi_club_name",
        )
        .order_by(VesselHistory.observed_at)
        .all()
    )

    # Get baseline from VesselOwner if available
    owner_pi_club = None
    owner = (
        db.query(VesselOwner)
        .filter(VesselOwner.vessel_id == vessel_id)
        .order_by(VesselOwner.owner_id.desc())
        .first()
    )
    if owner and owner.pi_club_name:
        owner_pi_club = owner.pi_club_name

    timeline = _build_pi_timeline(pi_changes, owner_pi_club)
    gaps = _find_coverage_gaps(timeline, min_gap_days)

    events: list[InsuranceGapEvent] = []
    for gap in gaps:
        coinciding = _check_coinciding_events(
            db, vessel_id, gap["gap_start"], gap.get("gap_end")
        )

        prev_ig = _is_ig_club(gap["previous_club"]) if gap["previous_club"] else False
        next_ig = _is_ig_club(gap["next_club"]) if gap["next_club"] else False

        score = _score_gap(
            gap["gap_days"],
            coinciding["flag_change"],
            coinciding["ownership_change"],
            prev_ig,
            next_ig,
        )

        evidence = {
            "previous_club": gap["previous_club"],
            "next_club": gap["next_club"],
            "gap_days": gap["gap_days"],
            "previous_club_is_ig": prev_ig,
            "next_club_is_ig": next_ig,
            "coincides_flag_change": coinciding["flag_change"],
            "coincides_ownership_change": coinciding["ownership_change"],
            "ongoing": gap["gap_end"] is None,
        }

        event = InsuranceGapEvent(
            vessel_id=vessel_id,
            gap_start_utc=gap["gap_start"],
            gap_end_utc=gap["gap_end"],
            gap_days=gap["gap_days"],
            previous_club=gap["previous_club"],
            next_club=gap["next_club"],
            previous_club_is_ig=prev_ig,
            next_club_is_ig=next_ig,
            coincides_with_flag_change=coinciding["flag_change"],
            coincides_with_ownership_change=coinciding["ownership_change"],
            risk_score_component=score,
            evidence_json=evidence,
        )

        # Dedup via unique constraint — use savepoint so rollback doesn't
        # discard previously flushed events in the same transaction
        try:
            with db.begin_nested():
                db.add(event)
                db.flush()
            events.append(event)
        except IntegrityError:
            # Savepoint rolled back, outer transaction intact
            existing = (
                db.query(InsuranceGapEvent)
                .filter(
                    InsuranceGapEvent.vessel_id == vessel_id,
                    InsuranceGapEvent.gap_start_utc == gap["gap_start"],
                )
                .first()
            )
            if existing:
                events.append(existing)

    db.commit()
    logger.info(
        "Insurance gap detection for vessel %d: %d gaps found",
        vessel_id,
        len(events),
    )
    return events


def _build_pi_timeline(
    history_records: list[VesselHistory],
    owner_pi_club: str | None = None,
) -> list[dict]:
    """Build chronological timeline of club memberships from VesselHistory records.

    Each entry: {club_name, start_date, end_date}.
    """
    if not history_records and not owner_pi_club:
        return []

    timeline: list[dict] = []

    if not history_records and owner_pi_club:
        # Only baseline from VesselOwner — current state, no history
        timeline.append({
            "club_name": owner_pi_club,
            "start_date": None,
            "end_date": None,
        })
        return timeline

    # Process history records chronologically
    for i, record in enumerate(history_records):
        # If first record and has old_value, that was the previous club
        if i == 0 and record.old_value:
            timeline.append({
                "club_name": record.old_value,
                "start_date": None,
                "end_date": record.observed_at,
            })
        elif i == 0 and not record.old_value and owner_pi_club:
            # No old_value but owner has a baseline — use it
            timeline.append({
                "club_name": owner_pi_club,
                "start_date": None,
                "end_date": record.observed_at,
            })

        # The new_value from this record starts a new period
        if record.new_value:
            # End date is the next record's observed_at, or None if last
            end_date = None
            if i + 1 < len(history_records):
                end_date = history_records[i + 1].observed_at
            timeline.append({
                "club_name": record.new_value,
                "start_date": record.observed_at,
                "end_date": end_date,
            })
        # NULL new_value means club was removed — gap starts here

    return timeline


def _find_coverage_gaps(timeline: list[dict], min_gap_days: int) -> list[dict]:
    """Find periods between club departures and arrivals exceeding min_gap_days."""
    if len(timeline) < 1:
        return []

    gaps: list[dict] = []
    now = datetime.now(UTC).replace(tzinfo=None)

    for i in range(len(timeline)):
        entry = timeline[i]

        # Check for gap after this entry ends and before next entry starts
        if entry["end_date"] is not None:
            # Look for the next entry that starts after this one ends
            next_start = None
            next_club = None
            if i + 1 < len(timeline):
                next_entry = timeline[i + 1]
                next_start = next_entry["start_date"]
                next_club = next_entry["club_name"]

            if next_start is not None and entry["end_date"] is not None:
                gap_delta = next_start - entry["end_date"]
                gap_days = gap_delta.days
                if gap_days >= min_gap_days:
                    gaps.append({
                        "gap_start": entry["end_date"],
                        "gap_end": next_start,
                        "gap_days": gap_days,
                        "previous_club": entry["club_name"],
                        "next_club": next_club,
                    })

    # Check for ongoing gap: vessel currently without insurance
    if timeline:
        last = timeline[-1]
        if last["end_date"] is not None and last["club_name"] is not None:
            # Club ended but nothing replaced it — ongoing gap
            gap_days = (now - last["end_date"]).days
            if gap_days >= min_gap_days:
                gaps.append({
                    "gap_start": last["end_date"],
                    "gap_end": None,  # ongoing
                    "gap_days": gap_days,
                    "previous_club": last["club_name"],
                    "next_club": None,
                })
        elif last["club_name"] is None and last.get("end_date") is None:
            # Club was explicitly removed — ongoing gap from start_date
            if last.get("start_date") is not None:
                gap_days = (now - last["start_date"]).days
                if gap_days >= min_gap_days:
                    gaps.append({
                        "gap_start": last["start_date"],
                        "gap_end": None,  # ongoing
                        "gap_days": gap_days,
                        "previous_club": None,
                        "next_club": None,
                    })

    # Also detect gaps from NULL new_value transitions (club removed)
    # These create gaps from the removal date until the next club appears
    # Already handled by the timeline structure above

    return gaps


def _check_coinciding_events(
    db: Session,
    vessel_id: int,
    gap_start: datetime,
    gap_end: datetime | None,
) -> dict:
    """Check for flag or ownership changes within the gap period +/- 30 days."""
    buffer = timedelta(days=30)
    search_start = gap_start - buffer
    search_end = (gap_end or datetime.now(UTC).replace(tzinfo=None)) + buffer

    changes = (
        db.query(VesselHistory)
        .filter(
            VesselHistory.vessel_id == vessel_id,
            VesselHistory.field_changed.in_(["flag", "owner_name"]),
            VesselHistory.observed_at >= search_start,
            VesselHistory.observed_at <= search_end,
        )
        .all()
    )

    flag_change = any(c.field_changed == "flag" for c in changes)
    ownership_change = any(c.field_changed == "owner_name" for c in changes)

    return {"flag_change": flag_change, "ownership_change": ownership_change}


def _score_gap(
    gap_days: int,
    coincides_flag: bool,
    coincides_ownership: bool,
    prev_ig: bool,
    next_ig: bool,
) -> float:
    """Tiered scoring for insurance coverage gaps.

    Base score by duration:
    - gap >= 90 days: 35
    - gap >= 60 days: 25
    - gap >= 30 days: 15

    Bonuses:
    - Non-IG transition (prev was IG, next is not): +5
    - Coincides with flag change: +10
    - Coincides with ownership change: +10
    """
    # Base score by duration tier
    if gap_days >= 90:
        score = 35.0
    elif gap_days >= 60:
        score = 25.0
    elif gap_days >= 30:
        score = 15.0
    else:
        score = 0.0

    # Non-IG transition bonus (prev was IG, next is not)
    if prev_ig and not next_ig:
        score += 5.0

    # Coinciding event bonuses
    if coincides_flag:
        score += 10.0
    if coincides_ownership:
        score += 10.0

    return score


def get_vessel_insurance_gaps(db: Session, vessel_id: int) -> list[InsuranceGapEvent]:
    """Retrieve existing InsuranceGapEvent records for a vessel."""
    return (
        db.query(InsuranceGapEvent)
        .filter(InsuranceGapEvent.vessel_id == vessel_id)
        .order_by(InsuranceGapEvent.gap_start_utc)
        .all()
    )
