"""Shadow scoring — re-score alerts with proposed overrides without saving."""

from __future__ import annotations

import copy
import logging

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.modules.scoring_config import load_scoring_config

logger = logging.getLogger(__name__)


def _get_score_band(score: int) -> str:
    """Map score to band name."""
    if score >= 76:
        return "critical"
    if score >= 51:
        return "high"
    if score >= 26:
        return "medium"
    return "low"


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
    merged_config = copy.deepcopy(base_config)

    # Apply proposed signal overrides
    for key, value in (proposed_overrides.get("signal_overrides") or {}).items():
        if value is None:
            continue
        parts = key.split(".")
        if len(parts) == 2:
            section, subkey = parts
            if (
                section in merged_config
                and isinstance(merged_config[section], dict)
                and isinstance(value, (int, float))
            ):
                merged_config[section][subkey] = value
        elif len(parts) == 3:
            section, mid, subkey = parts
            if (
                section in merged_config
                and isinstance(merged_config[section], dict)
                and mid in merged_config[section]
                and isinstance(merged_config[section][mid], dict)
                and isinstance(value, (int, float))
            ):
                merged_config[section][mid][subkey] = value

    results = []
    total_delta = 0
    band_changes = 0

    for alert in alerts:
        original_score = alert.risk_score
        original_band = _get_score_band(original_score)

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

        proposed_band = _get_score_band(proposed_score)
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
