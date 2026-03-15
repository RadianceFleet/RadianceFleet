"""Shadow scoring — re-score alerts with proposed overrides without saving."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.modules.risk_scoring import _merge_overrides, _score_band
from app.modules.scoring_config import load_scoring_config

logger = logging.getLogger(__name__)


def shadow_score(
    db: Session, corridor_id: int, proposed_overrides: dict, limit: int = 100
) -> dict:
    """Re-score recent alerts with proposed overrides.

    Returns comparison of original vs proposed scores with band changes.
    Does NOT modify any database records.
    """
    from app.modules.risk_scoring import _count_gaps_in_window, compute_gap_score

    # Get recent scored alerts for this corridor
    alerts = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.corridor_id == corridor_id,
            AISGapEvent.risk_score > 0,
        )
        .order_by(AISGapEvent.gap_event_id.desc())
        .limit(limit)
        .all()
    )

    if not alerts:
        return {
            "corridor_id": corridor_id,
            "alerts_scored": 0,
            "band_changes": 0,
            "avg_score_delta": 0.0,
            "predicted_fp_rate_change": None,
            "results": [],
        }

    # Build merged config with proposed overrides
    base_config = load_scoring_config()
    merged_config = _merge_overrides(base_config, proposed_overrides.get("signal_overrides") or {})

    results = []
    total_delta = 0
    band_changes = 0

    for alert in alerts:
        original_score = alert.risk_score
        original_band = _score_band(original_score)

        gaps_7d = _count_gaps_in_window(db, alert, 7)
        gaps_14d = _count_gaps_in_window(db, alert, 14)
        gaps_30d = _count_gaps_in_window(db, alert, 30)

        proposed_score, _ = compute_gap_score(
            alert,
            merged_config,
            gaps_in_7d=gaps_7d,
            gaps_in_14d=gaps_14d,
            gaps_in_30d=gaps_30d,
            db=db,
            pre_gap_sog=getattr(alert, "pre_gap_sog", None),
        )

        proposed_band = _score_band(proposed_score)
        changed = original_band != proposed_band
        if changed:
            band_changes += 1
        total_delta += proposed_score - original_score

        results.append(
            {
                "alert_id": alert.gap_event_id,
                "original_score": original_score,
                "proposed_score": proposed_score,
                "original_band": original_band,
                "proposed_band": proposed_band,
                "band_changed": changed,
            }
        )

    return {
        "corridor_id": corridor_id,
        "alerts_scored": len(results),
        "band_changes": band_changes,
        "avg_score_delta": round(total_delta / len(results), 2) if results else 0.0,
        "predicted_fp_rate_change": None,
        "results": results,
    }
