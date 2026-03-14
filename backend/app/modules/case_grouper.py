"""Case grouping — suggests related alerts for investigation cases."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.models.sts_transfer import StsTransferEvent

# Maximum number of alerts in a single case
CASE_SIZE_CAP = 30

# Alerts with risk_score >= this threshold should start new cases
CRITICAL_SCORE_THRESHOLD = 80


def suggest_case_grouping(db: Session, alert_id: int) -> list[dict]:
    """Find related alerts for potential case grouping.

    Criteria:
    - Same vessel within 7 days
    - Same corridor within 48 hours
    - Linked STS transfers (shared vessel)

    Returns list of {alert_id, reason, score} suggestions.
    Case size cap of 30 alerts; critical alerts (score >= 80) start new cases.
    """
    source_alert = (
        db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    )
    if not source_alert:
        return []

    suggestions: dict[int, dict] = {}

    # --- Same vessel within 7 days ---
    if source_alert.vessel_id and source_alert.gap_start_utc:
        window_start = source_alert.gap_start_utc - timedelta(days=7)
        window_end = source_alert.gap_start_utc + timedelta(days=7)
        same_vessel = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.vessel_id == source_alert.vessel_id,
                AISGapEvent.gap_event_id != alert_id,
                AISGapEvent.gap_start_utc >= window_start,
                AISGapEvent.gap_start_utc <= window_end,
            )
            .all()
        )
        for a in same_vessel:
            if a.risk_score is not None and a.risk_score >= CRITICAL_SCORE_THRESHOLD:
                continue  # Critical alerts should start their own cases
            score = 80
            suggestions[a.gap_event_id] = {
                "alert_id": a.gap_event_id,
                "reason": "same_vessel_7d",
                "score": score,
            }

    # --- Same corridor within 48 hours ---
    if source_alert.corridor_id and source_alert.gap_start_utc:
        window_start = source_alert.gap_start_utc - timedelta(hours=48)
        window_end = source_alert.gap_start_utc + timedelta(hours=48)
        same_corridor = (
            db.query(AISGapEvent)
            .filter(
                AISGapEvent.corridor_id == source_alert.corridor_id,
                AISGapEvent.gap_event_id != alert_id,
                AISGapEvent.gap_start_utc >= window_start,
                AISGapEvent.gap_start_utc <= window_end,
            )
            .all()
        )
        for a in same_corridor:
            if a.gap_event_id in suggestions:
                # Boost score if already matched by vessel
                suggestions[a.gap_event_id]["score"] = min(
                    100, suggestions[a.gap_event_id]["score"] + 20
                )
                suggestions[a.gap_event_id]["reason"] += "+same_corridor_48h"
            else:
                if a.risk_score is not None and a.risk_score >= CRITICAL_SCORE_THRESHOLD:
                    continue
                suggestions[a.gap_event_id] = {
                    "alert_id": a.gap_event_id,
                    "reason": "same_corridor_48h",
                    "score": 60,
                }

    # --- Linked STS transfers (shared vessel) ---
    if source_alert.vessel_id:
        sts_events = (
            db.query(StsTransferEvent)
            .filter(
                (StsTransferEvent.vessel_1_id == source_alert.vessel_id)
                | (StsTransferEvent.vessel_2_id == source_alert.vessel_id)
            )
            .all()
        )
        partner_ids = set()
        for sts in sts_events:
            if sts.vessel_1_id == source_alert.vessel_id:
                partner_ids.add(sts.vessel_2_id)
            else:
                partner_ids.add(sts.vessel_1_id)

        if partner_ids:
            partner_alerts = (
                db.query(AISGapEvent)
                .filter(
                    AISGapEvent.vessel_id.in_(partner_ids),
                    AISGapEvent.gap_event_id != alert_id,
                )
                .all()
            )
            for a in partner_alerts:
                if a.gap_event_id not in suggestions:
                    if a.risk_score is not None and a.risk_score >= CRITICAL_SCORE_THRESHOLD:
                        continue
                    suggestions[a.gap_event_id] = {
                        "alert_id": a.gap_event_id,
                        "reason": "sts_partner",
                        "score": 50,
                    }

    # Enforce case size cap
    result = sorted(suggestions.values(), key=lambda x: x["score"], reverse=True)
    return result[:CASE_SIZE_CAP]
