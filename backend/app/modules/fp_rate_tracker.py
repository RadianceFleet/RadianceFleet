"""False-positive rate tracking and calibration suggestion engine.

Computes per-corridor FP rates from analyst verdicts on AISGapEvent records,
provides time-windowed trend analysis, and generates calibration suggestions
to reduce alert fatigue in high-FP corridors.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, and_, func
from sqlalchemy.orm import Session

from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CorridorFPRate:
    """FP rate statistics for a single corridor."""

    corridor_id: int
    corridor_name: str
    total_alerts: int = 0
    false_positives: int = 0
    fp_rate: float = 0.0
    fp_rate_30d: float = 0.0
    fp_rate_90d: float = 0.0
    trend: str = "stable"  # "increasing", "decreasing", "stable"


@dataclass
class CalibrationSuggestion:
    """Auto-generated suggestion to tune a corridor's scoring multiplier."""

    corridor_id: int
    corridor_name: str
    current_multiplier: float
    suggested_multiplier: float
    reason: str
    fp_rate: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _reviewed_gaps_query(db: Session, corridor_id: int | None = None):
    """Base query for gap events that have analyst verdicts."""
    q = db.query(AISGapEvent).filter(AISGapEvent.is_false_positive.isnot(None))
    if corridor_id is not None:
        q = q.filter(AISGapEvent.corridor_id == corridor_id)
    return q


def _fp_rate_for_window(
    db: Session, corridor_id: int, since: datetime | None = None
) -> tuple[int, int, float]:
    """Return (total_reviewed, false_positives, fp_rate) for a time window."""
    filters = [
        AISGapEvent.corridor_id == corridor_id,
        AISGapEvent.is_false_positive.isnot(None),
    ]
    if since is not None:
        filters.append(AISGapEvent.review_date >= since)

    rows = (
        db.query(
            func.count(AISGapEvent.gap_event_id).label("total"),
            func.sum(
                func.cast(AISGapEvent.is_false_positive, Integer)
            ).label("fp_count"),
        )
        .filter(and_(*filters))
        .one()
    )
    total = rows.total or 0
    fp_count = rows.fp_count or 0
    rate = fp_count / total if total > 0 else 0.0
    return total, fp_count, rate


def _compute_trend(
    db: Session, corridor_id: int, now: datetime | None = None
) -> str:
    """Compare 30-day FP rate to previous 30-day window to detect trend.

    Returns "increasing", "decreasing", or "stable".
    """
    now = now or datetime.now(UTC)
    boundary_recent = now - timedelta(days=30)
    boundary_prev = now - timedelta(days=60)

    _, _, rate_recent = _fp_rate_for_window(db, corridor_id, since=boundary_recent)

    # Previous window: 60d ago to 30d ago
    filters = [
        AISGapEvent.corridor_id == corridor_id,
        AISGapEvent.is_false_positive.isnot(None),
        AISGapEvent.review_date >= boundary_prev,
        AISGapEvent.review_date < boundary_recent,
    ]
    rows = (
        db.query(
            func.count(AISGapEvent.gap_event_id).label("total"),
            func.sum(
                func.cast(AISGapEvent.is_false_positive, Integer)
            ).label("fp_count"),
        )
        .filter(and_(*filters))
        .one()
    )
    total_prev = rows.total or 0
    fp_prev = rows.fp_count or 0
    rate_prev = fp_prev / total_prev if total_prev > 0 else 0.0

    # Need enough data in both windows to declare a trend
    if total_prev < 3:
        return "stable"

    diff = rate_recent - rate_prev
    if diff > 0.05:
        return "increasing"
    elif diff < -0.05:
        return "decreasing"
    return "stable"


def _get_corridor_multiplier(corridor: Corridor, config: dict | None = None) -> float:
    """Get the current scoring multiplier for a corridor type.

    Mirrors logic from risk_scoring._corridor_multiplier without importing it
    (to avoid circular deps and keep this module testable standalone).
    """
    if config is None:
        config = {}
    corridor_cfg = config.get("corridor", {})

    ct = str(
        corridor.corridor_type.value
        if hasattr(corridor.corridor_type, "value")
        else corridor.corridor_type
    )

    if ct == "sts_zone":
        return float(corridor_cfg.get("known_sts_zone", 1.5))
    elif ct == "export_route":
        return float(corridor_cfg.get("high_risk_export_corridor", 1.5))
    elif ct == "legitimate_trade_route":
        return float(corridor_cfg.get("legitimate_trade_route", 0.7))
    else:
        return float(corridor_cfg.get("standard_corridor", 1.0))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_fp_rate(db: Session, corridor_id: int) -> CorridorFPRate | None:
    """Compute FP rate statistics for a single corridor."""
    corridor = db.query(Corridor).filter(Corridor.corridor_id == corridor_id).first()
    if corridor is None:
        return None

    now = datetime.now(UTC)
    total, fp_count, rate = _fp_rate_for_window(db, corridor_id)
    _, _, rate_30d = _fp_rate_for_window(db, corridor_id, since=now - timedelta(days=30))
    _, _, rate_90d = _fp_rate_for_window(db, corridor_id, since=now - timedelta(days=90))
    trend = _compute_trend(db, corridor_id, now=now)

    return CorridorFPRate(
        corridor_id=corridor_id,
        corridor_name=corridor.name,
        total_alerts=total,
        false_positives=fp_count,
        fp_rate=round(rate, 4),
        fp_rate_30d=round(rate_30d, 4),
        fp_rate_90d=round(rate_90d, 4),
        trend=trend,
    )


def compute_fp_rates(db: Session) -> list[CorridorFPRate]:
    """Compute FP rates for all corridors that have reviewed gap events."""
    # Get corridors that have at least one reviewed gap event
    corridor_ids = (
        db.query(AISGapEvent.corridor_id)
        .filter(
            AISGapEvent.corridor_id.isnot(None),
            AISGapEvent.is_false_positive.isnot(None),
        )
        .distinct()
        .all()
    )

    results: list[CorridorFPRate] = []
    for (cid,) in corridor_ids:
        rate = compute_fp_rate(db, cid)
        if rate is not None:
            results.append(rate)

    # Sort by FP rate descending so worst corridors appear first
    results.sort(key=lambda r: r.fp_rate, reverse=True)
    return results


def generate_calibration_suggestions(
    db: Session, config: dict | None = None
) -> list[CalibrationSuggestion]:
    """Generate auto-calibration suggestions for corridors with extreme FP rates.

    Rules:
    - FP rate > 50%: suggest halving the corridor multiplier
    - FP rate > 30%: suggest reducing by 25%
    - FP rate > 15%: suggest reducing by 10%
    - FP rate < 5% (with >= 20 alerts): suggest increasing by 15%
    - FP rate < 2% (with >= 20 alerts): suggest increasing by 25%
    """
    rates = compute_fp_rates(db)
    suggestions: list[CalibrationSuggestion] = []

    for fp in rates:
        if fp.total_alerts < 5:
            # Not enough data to make a reliable suggestion
            continue

        corridor = db.query(Corridor).filter(Corridor.corridor_id == fp.corridor_id).first()
        if corridor is None:
            continue

        current_mult = _get_corridor_multiplier(corridor, config)
        suggestion = None

        if fp.fp_rate > 0.50:
            suggested = round(current_mult * 0.50, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} is critically high (>50%). "
                f"Recommend halving corridor multiplier from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )
        elif fp.fp_rate > 0.30:
            suggested = round(current_mult * 0.75, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} exceeds 30% threshold. "
                f"Recommend reducing corridor multiplier from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )
        elif fp.fp_rate > 0.15:
            suggested = round(current_mult * 0.90, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} exceeds 15% threshold. "
                f"Recommend modest reduction from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )
        elif fp.fp_rate < 0.02 and fp.total_alerts >= 20:
            suggested = round(current_mult * 1.25, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} is very low (<2%) with {fp.total_alerts} alerts. "
                f"Corridor may be under-weighted. Suggest increasing from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )
        elif fp.fp_rate < 0.05 and fp.total_alerts >= 20:
            suggested = round(current_mult * 1.15, 2)
            reason = (
                f"FP rate {fp.fp_rate:.0%} is low (<5%) with {fp.total_alerts} alerts. "
                f"Suggest modest increase from {current_mult} to {suggested}."
            )
            suggestion = CalibrationSuggestion(
                corridor_id=fp.corridor_id,
                corridor_name=fp.corridor_name,
                current_multiplier=current_mult,
                suggested_multiplier=suggested,
                reason=reason,
                fp_rate=fp.fp_rate,
            )

        if suggestion is not None:
            suggestions.append(suggestion)

    return suggestions


def compute_region_fp_rate(db: Session, region_id: int) -> CorridorFPRate | None:
    """Compute aggregated FP rate across all corridors in a region."""
    import json

    from app.models.scoring_region import ScoringRegion

    region = db.query(ScoringRegion).filter(ScoringRegion.region_id == region_id).first()
    if region is None:
        return None

    corridor_ids: list[int] = []
    if region.corridor_ids_json:
        import contextlib

        with contextlib.suppress(json.JSONDecodeError, TypeError):
            corridor_ids = json.loads(region.corridor_ids_json)

    if not corridor_ids:
        return CorridorFPRate(
            corridor_id=0,
            corridor_name=region.name,
        )

    # Aggregate FP rates across corridors
    total_reviewed = 0
    total_fp = 0
    for cid in corridor_ids:
        rate = compute_fp_rate(db, cid)
        if rate:
            total_reviewed += rate.total_alerts
            total_fp += rate.false_positives

    fp_rate = total_fp / total_reviewed if total_reviewed > 0 else 0.0

    return CorridorFPRate(
        corridor_id=0,
        corridor_name=region.name,
        total_alerts=total_reviewed,
        false_positives=total_fp,
        fp_rate=round(fp_rate, 4),
    )



# ---------------------------------------------------------------------------
# Auto-calibration: per-signal suggestions
# ---------------------------------------------------------------------------

# Signal bounds (section -> (min_multiplier, max_multiplier))
_SIGNAL_BOUNDS: dict[str, tuple[float, float]] = {
    "gap_duration": (0.5, 3.0),
    "gap_frequency": (0.3, 5.0),
    "speed_anomaly": (0.3, 5.0),
    "spoofing": (0.3, 5.0),
    "metadata": (0.3, 5.0),
    "dark_zone": (0.3, 5.0),
    "sts": (0.3, 5.0),
    "corridor": (0.3, 5.0),
}


def generate_per_signal_suggestions(db: Session) -> list[dict]:
    """Generate per-signal calibration suggestions using FP rate and signal effectiveness data.

    For each corridor with FP rate > 15%, analyze which signals contribute most to
    false positives and suggest per-signal adjustments.

    Constraints:
    - Max +/-15% adjustment per signal per cycle (configurable via AUTO_CALIBRATION_MAX_ADJUSTMENT_PCT)
    - Hard floor/ceiling per signal (gap_duration weight: 0.5x-3.0x, others: 0.3x-5.0x)
    - Cooldown: no re-suggestion if last calibration was within AUTO_CALIBRATION_COOLDOWN_DAYS
    """
    from app.config import settings
    from app.models.calibration_event import CalibrationEvent
    from app.modules.scoring_config import load_scoring_config

    max_adj = getattr(settings, "AUTO_CALIBRATION_MAX_ADJUSTMENT_PCT", 15) / 100.0
    cooldown_days = getattr(settings, "AUTO_CALIBRATION_COOLDOWN_DAYS", 7)

    rates = compute_fp_rates(db)
    config = load_scoring_config()
    suggestions = []

    for fp in rates:
        if fp.total_alerts < 10:
            continue
        if fp.fp_rate <= 0.15:
            continue

        # Check cooldown
        recent_cal = (
            db.query(CalibrationEvent)
            .filter(
                CalibrationEvent.corridor_id == fp.corridor_id,
                CalibrationEvent.created_at >= datetime.now(UTC) - timedelta(days=cooldown_days),
            )
            .first()
        )
        if recent_cal:
            continue

        # Generate per-signal suggestions based on FP rate severity
        adjustment = min(fp.fp_rate * 0.3, max_adj)  # Scale adjustment by FP rate, capped

        signal_suggestions: dict[str, dict] = {}
        for section in ["gap_duration", "spoofing", "dark_zone", "sts"]:
            section_config = config.get(section, {})
            if not isinstance(section_config, dict):
                continue
            for key, val in section_config.items():
                if isinstance(val, (int, float)) and val > 0:
                    proposed = round(val * (1 - adjustment), 2)
                    bounds = _SIGNAL_BOUNDS.get(section, (0.3, 5.0))
                    proposed = max(bounds[0], min(bounds[1], proposed))
                    if proposed != val:
                        signal_suggestions[f"{section}.{key}"] = {
                            "current": val,
                            "proposed": proposed,
                            "adjustment_pct": round((proposed - val) / val * 100, 1),
                        }

        if signal_suggestions:
            suggestions.append({
                "corridor_id": fp.corridor_id,
                "corridor_name": fp.corridor_name,
                "fp_rate": fp.fp_rate,
                "total_alerts": fp.total_alerts,
                "signal_suggestions": signal_suggestions,
                "reason": f"FP rate {fp.fp_rate:.0%} exceeds 15% threshold with {fp.total_alerts} reviewed alerts",
            })

    return suggestions


def run_scheduled_calibration(db: Session) -> dict:
    """Generate proposals only (no auto-apply in v1).

    Returns summary of suggested calibrations. All suggestions require
    senior/admin approval via POST /corridors/{id}/apply-suggestion.
    """
    from app.config import settings

    if not getattr(settings, "AUTO_CALIBRATION_ENABLED", False):
        return {"status": "disabled", "suggestions": []}

    suggestions = generate_per_signal_suggestions(db)
    lift_suggestions = generate_lift_based_suggestions(db)
    return {
        "status": "ok",
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
        "lift_suggestions": lift_suggestions,
    }


# ---------------------------------------------------------------------------
# Lift-based weight adjustment suggestions
# ---------------------------------------------------------------------------

# Map signal names (as they appear in risk_breakdown_json) to
# (yaml_section, yaml_key) tuples in risk_scoring.yaml.
_SIGNAL_CONFIG_MAP: dict[str, tuple[str, str]] = {
    # Gap duration
    "gap_duration_2h_4h": ("gap_duration", "2h_to_4h"),
    "gap_duration_4h_8h": ("gap_duration", "4h_to_8h"),
    "gap_duration_8h_12h": ("gap_duration", "8h_to_12h"),
    "gap_duration_12h_24h": ("gap_duration", "12h_to_24h"),
    "gap_duration_24h_plus": ("gap_duration", "24h_plus"),
    "gap_duration_speed_spike_bonus": ("speed_anomaly", "gap_preceded_by_speed_spike_multiplier"),
    # Speed anomaly
    "speed_spike_before_gap": ("speed_anomaly", "speed_spike"),
    "speed_spoof_before_gap": ("speed_anomaly", "speed_spoof"),
    "speed_impossible": ("speed_anomaly", "speed_impossible"),
    # Movement envelope
    "impossible_reappear": ("movement_envelope", "impossible_reappear"),
    "near_impossible_reappear": ("movement_envelope", "near_impossible_reappear"),
    # Spoofing
    "anchor_in_open_ocean": ("spoofing", "anchor_in_open_ocean"),
    "circle_pattern": ("spoofing", "circle_pattern"),
    "circle_pattern_stationary": ("spoofing", "circle_pattern_stationary"),
    "circle_pattern_deliberate": ("spoofing", "circle_pattern_deliberate"),
    "circle_pattern_equipment": ("spoofing", "circle_pattern_equipment"),
    "slow_roll": ("spoofing", "slow_roll"),
    "nav_status_speed_mismatch": ("spoofing", "nav_status_speed_mismatch"),
    "spoofing_erratic_nav_status": ("spoofing", "erratic_nav_status"),
    "dual_transmission_candidate": ("spoofing", "dual_transmission_candidate"),
    # Watchlist
    "watchlist_ofac": ("watchlist", "vessel_on_ofac_sdn_list"),
    "watchlist_eu": ("watchlist", "vessel_on_eu_sanctions_list"),
    "kse_shadow_fleet": ("watchlist", "vessel_on_kse_shadow_fleet_list"),
    "owner_or_manager_on_sanctions_list": ("watchlist", "owner_or_manager_on_sanctions_list"),
    # Flag state
    "flag_high_risk": ("flag_state", "high_risk_registry"),
    "flag_white_list": ("flag_state", "white_list_flag"),
    "flag_less_than_2y_AND_high_risk": ("flag_state", "flag_less_than_2y_old_AND_high_risk"),
    # Metadata / identity changes
    "flag_and_name_change_48h": ("metadata", "flag_AND_name_change_within_48h"),
    "flag_change_7d": ("metadata", "flag_change_in_last_7d"),
    "flag_change_30d": ("metadata", "flag_change_in_last_30d"),
    "flag_change_single_12m": ("metadata", "single_flag_change_last_12m"),
    "flag_changes_3plus_90d": ("metadata", "3_plus_flag_changes_in_90d"),
    "flag_change_high_to_low_12m": ("metadata", "flag_change_from_high_risk_to_low_risk_12m"),
    "name_change_during_voyage": ("metadata", "name_change_during_active_voyage"),
    "mmsi_change": ("metadata", "mmsi_change_mapped_same_position"),
    "callsign_change": ("metadata", "callsign_change"),
    "no_name_at_all": ("metadata", "no_name_at_all"),
    "name_all_caps_numbers": ("metadata", "name_all_caps_numbers"),
    # Vessel age
    "vessel_age_0_10y": ("vessel_age", "age_0_to_10y"),
    "vessel_age_10_15y": ("vessel_age", "age_10_to_15y"),
    "vessel_age_15_20y": ("vessel_age", "age_15_to_20y"),
    "vessel_age_20_25y": ("vessel_age", "age_20_to_25y"),
    "vessel_age_25plus": ("vessel_age", "age_25_plus_y"),
    "vessel_age_25plus_high_risk": ("vessel_age", "age_25_plus_AND_high_risk_flag"),
    # AIS class
    "ais_class_mismatch": ("ais_class", "large_tanker_using_class_b"),
    "class_switching_a_to_b": ("ais_class", "class_switching_a_to_b"),
    "transmission_frequency_mismatch": ("ais_class", "transmission_frequency_mismatch"),
    # Dark zone
    "dark_zone_exit_impossible": ("dark_zone", "vessel_exits_dark_zone_with_impossible_jump"),
    "dark_zone_entry": ("dark_zone", "gap_immediately_before_dark_zone_entry"),
    "dark_zone_deduction": ("dark_zone", "gap_in_known_jamming_zone"),
    "selective_dark_zone_evasion": ("dark_zone", "selective_dark_zone_evasion"),
    # STS
    "gap_in_sts_tagged_corridor": ("sts", "gap_in_sts_tagged_corridor"),
    "one_vessel_dark_during_proximity": ("sts", "one_vessel_dark_during_proximity"),
    # Behavioral
    "new_mmsi_first_30d": ("behavioral", "new_mmsi_first_30d"),
    "new_mmsi_first_60d": ("behavioral", "new_mmsi_first_60d"),
    "new_mmsi_russian_origin_flag": ("behavioral", "new_mmsi_plus_russian_origin_zone"),
    "vessel_laid_up_30d": ("behavioral", "vessel_laid_up_30d_plus"),
    "vessel_laid_up_60d": ("behavioral", "vessel_laid_up_60d_plus"),
    "vessel_laid_up_in_sts_zone": ("behavioral", "vessel_laid_up_in_sts_zone"),
    "suspicious_mid": ("behavioral", "suspicious_mid"),
    "russian_port_recent": ("behavioral", "russian_port_recent"),
    "russian_port_gap_sts": ("behavioral", "russian_port_gap_sts"),
    "sts_with_sanctioned_vessel": ("behavioral", "sts_with_sanctioned_vessel"),
    "sts_with_shadow_fleet_vessel": ("behavioral", "sts_with_shadow_fleet_vessel"),
    # Legitimacy
    "legitimacy_gap_free_90d": ("legitimacy", "gap_free_90d_clean"),
    "legitimacy_ais_class_a_consistent": ("legitimacy", "ais_class_a_consistent"),
    "legitimacy_white_flag_jurisdiction": ("legitimacy", "white_flag_jurisdiction"),
    "legitimacy_eu_port_calls": ("legitimacy", "consistent_eu_port_calls"),
    "legitimacy_psc_clean_record": ("legitimacy", "psc_clean_record"),
    "legitimacy_ig_pi_club_member": ("legitimacy", "ig_pi_club_member"),
    "legitimacy_long_trading_history": ("legitimacy", "long_trading_history"),
    # P&I insurance
    "pi_coverage_lapsed": ("pi_insurance", "pi_coverage_lapsed"),
    # PSC detention
    "psc_detained_last_12m": ("psc_detention", "psc_detained_last_12m"),
    "psc_major_deficiencies_3_plus": ("psc_detention", "psc_major_deficiencies_3_plus"),
    "psc_multiple_detentions_2": ("psc_detention", "multiple_detentions_2"),
    "psc_multiple_detentions_3_plus": ("psc_detention", "multiple_detentions_3_plus"),
    "psc_detention_in_last_30d": ("psc_detention", "detention_in_last_30d"),
    "psc_detention_in_last_90d": ("psc_detention", "detention_in_last_90d"),
    "psc_paris_mou_ban": ("psc_detention", "paris_mou_ban"),
    "psc_deficiency_count_10_plus": ("psc_detention", "deficiency_count_10_plus"),
    # STS patterns
    "repeat_sts_partnership": ("sts_patterns", "repeat_sts_partnership_3plus"),
    "flag_corridor_coupling": ("sts_patterns", "flag_corridor_coupling"),
    "invalid_metadata_generic_name": ("sts_patterns", "invalid_metadata_generic_name"),
    "invalid_metadata_impossible_dwt": ("sts_patterns", "invalid_metadata_impossible_dwt"),
    # Dark vessel
    "unmatched_detection_in_corridor": ("dark_vessel", "unmatched_detection_in_corridor"),
    "unmatched_detection_outside_corridor": ("dark_vessel", "unmatched_detection_outside_corridor"),
    # Track naturalness
    "synthetic_track_high": ("track_naturalness", "synthetic_track_high"),
    "synthetic_track_medium": ("track_naturalness", "synthetic_track_medium"),
    "synthetic_track_low": ("track_naturalness", "synthetic_track_low"),
    # Draught
    "offshore_draught_change_corroboration": ("draught", "offshore_draught_change_corroboration"),
    "draught_swing_extreme": ("draught", "draught_swing_extreme"),
    "draught_delta_across_gap": ("draught", "draught_delta_across_gap"),
    "draught_sts_confirmation": ("draught", "draught_sts_confirmation"),
    # Identity fraud
    "stateless_mmsi_tier1": ("identity_fraud", "stateless_mmsi_tier1"),
    "stateless_mmsi_tier2": ("identity_fraud", "stateless_mmsi_tier2"),
    "stateless_mmsi_tier3": ("identity_fraud", "stateless_mmsi_tier3"),
    "flag_hopping_2_in_90d": ("identity_fraud", "flag_hopping_2_in_90d"),
    "flag_hopping_3_in_90d": ("identity_fraud", "flag_hopping_3_in_90d"),
    "flag_hopping_5_in_365d": ("identity_fraud", "flag_hopping_5_in_365d"),
    "imo_simultaneous_use": ("identity_fraud", "imo_simultaneous_use"),
    # Identity merge
    "identity_merge_detected": ("identity_merge", "identity_merge_detected"),
    "imo_scrapped_vessel": ("identity_merge", "imo_scrapped_vessel"),
    "imo_fabricated": ("identity_merge", "imo_fabricated"),
}

# Minimum total analyst verdicts before generating any suggestions
_MIN_TOTAL_VERDICTS = 20
# Minimum verdicts per individual signal
_MIN_PER_SIGNAL_VERDICTS = 5


def generate_lift_based_suggestions(db: Session) -> list[dict]:
    """Generate per-signal weight adjustment proposals based on analyst verdict lift scores.

    Calls live_signal_effectiveness() and translates lift values into concrete
    weight-change proposals. Returns list of suggestion dicts — proposals only,
    no auto-apply.
    """
    from app.modules.scoring_config import load_scoring_config
    from app.modules.validation_harness import live_signal_effectiveness

    effectiveness = live_signal_effectiveness(db)
    if not effectiveness:
        return []

    # Check minimum total verdicts
    total_verdicts = sum(e["tp_count"] + e["fp_count"] for e in effectiveness)
    if total_verdicts < _MIN_TOTAL_VERDICTS:
        return []

    config = load_scoring_config()

    # Group dynamic per-event keys: strip numeric suffixes
    # e.g. loitering_201 + loitering_202 → loitering
    grouped: dict[str, dict] = {}
    for entry in effectiveness:
        signal_name = entry["signal"]

        # Filter out _-prefixed metadata keys
        if signal_name.startswith("_"):
            continue

        # Strip numeric suffix for grouping
        base_name = re.sub(r"_\d+$", "", signal_name)

        if base_name not in grouped:
            grouped[base_name] = {
                "tp_count": 0,
                "fp_count": 0,
            }
        grouped[base_name]["tp_count"] += entry["tp_count"]
        grouped[base_name]["fp_count"] += entry["fp_count"]

    # Compute lift and generate suggestions
    total_tp = sum(g["tp_count"] for g in grouped.values())
    total_fp = sum(g["fp_count"] for g in grouped.values())

    suggestions: list[dict] = []
    for signal, counts in sorted(grouped.items()):
        tp_count = counts["tp_count"]
        fp_count = counts["fp_count"]
        signal_total = tp_count + fp_count

        # Per-signal minimum
        if signal_total < _MIN_PER_SIGNAL_VERDICTS:
            continue

        # Compute lift
        tp_freq = tp_count / max(1, total_tp)
        fp_freq = fp_count / max(1, total_fp)

        if fp_freq > 0:
            lift = tp_freq / fp_freq
        elif tp_freq > 0:
            lift = float("inf")
        else:
            lift = 0.0

        # Determine if signal is configurable
        mapping = _SIGNAL_CONFIG_MAP.get(signal)
        configurable = mapping is not None
        current_weight = None
        min_weight_floor = None
        config_path = None

        if mapping:
            section_name, key_name = mapping
            config_path = f"{section_name}.{key_name}"
            section_data = config.get(section_name, {})
            if isinstance(section_data, dict):
                raw_weight = section_data.get(key_name)
                if isinstance(raw_weight, (int, float)):
                    current_weight = raw_weight
                    min_weight_floor = round(current_weight * 0.5)

        # Determine direction and adjustment
        direction = None
        suggested_adjustment_pct = None
        reason = None

        if isinstance(lift, float) and lift < 0.8:
            direction = "reduce"
            # Scale reduction: further from 0.8 → larger reduction, capped at 15%
            raw_pct = min(15.0, round((0.8 - lift) / 0.8 * 30, 1))
            suggested_adjustment_pct = -raw_pct

            # Check weight floor
            if current_weight is not None and min_weight_floor is not None:
                proposed = current_weight * (1 + suggested_adjustment_pct / 100.0)
                if proposed < min_weight_floor:
                    # Clamp to floor
                    suggested_adjustment_pct = round(
                        (min_weight_floor - current_weight) / current_weight * 100, 1
                    )
                    if suggested_adjustment_pct == 0.0:
                        continue  # Already at floor

            reason = (
                f"Lift {lift:.2f} < 0.8 — signal fires more on false positives than true positives. "
                f"Suggest reducing weight by {abs(suggested_adjustment_pct):.1f}%."
            )
        elif isinstance(lift, (int, float)) and lift > 2.0:
            direction = "increase"
            # Scale increase: further from 2.0 → larger increase, capped at 15%
            if isinstance(lift, float) and not (lift == float("inf")):
                raw_pct = min(15.0, round((lift - 2.0) / 2.0 * 15, 1))
            else:
                raw_pct = 15.0
            suggested_adjustment_pct = raw_pct

            reason = (
                f"Lift {lift:.2f} > 2.0 — signal is highly predictive of true positives. "
                f"Suggest increasing weight by {suggested_adjustment_pct:.1f}%."
            )
        else:
            # Normal lift range — no suggestion
            continue

        if not configurable:
            # Report non-configurable signals without adjustment
            suggestions.append({
                "signal": signal,
                "config_path": None,
                "lift": round(lift, 2) if isinstance(lift, float) and lift != float("inf") else lift,
                "direction": direction,
                "suggested_adjustment_pct": None,
                "current_weight": None,
                "min_weight_floor": None,
                "configurable": False,
                "reason": f"Signal not mapped to YAML config — manual review needed. Lift={lift:.2f}.",
                "tp_count": tp_count,
                "fp_count": fp_count,
            })
            continue

        suggestions.append({
            "signal": signal,
            "config_path": config_path,
            "lift": round(lift, 2) if isinstance(lift, float) and lift != float("inf") else lift,
            "direction": direction,
            "suggested_adjustment_pct": suggested_adjustment_pct,
            "current_weight": current_weight,
            "min_weight_floor": min_weight_floor,
            "configurable": True,
            "reason": reason,
            "tp_count": tp_count,
            "fp_count": fp_count,
        })

    return suggestions
