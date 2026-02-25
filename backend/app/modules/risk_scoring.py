"""Risk scoring engine.

Applies configurable rules from risk_scoring.yaml to produce an explainable score
for each AIS gap event. See PRD §7.5 for the full scoring specification.

Scoring uses three-phase composition:
  Phase 1 — Additive signals (flat points each; gap_duration gets ×1.4 if speed spike preceded)
  Phase 2 — Corridor multiplier  (additive_subtotal × corridor_factor)
  Phase 3 — Vessel size multiplier (corridor_adjusted × vessel_size_factor)

final_score = round(additive_subtotal × corridor_factor × vessel_size_factor)
No hard cap; 76+ is "critical" regardless of upper bound.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import settings
from app.models.gap_event import AISGapEvent

logger = logging.getLogger(__name__)

_SCORING_CONFIG: dict[str, Any] | None = None


def load_scoring_config() -> dict[str, Any]:
    global _SCORING_CONFIG
    if _SCORING_CONFIG is None:
        config_path = Path(settings.RISK_SCORING_CONFIG)
        if not config_path.exists():
            logger.warning("risk_scoring.yaml not found at %s — using empty config", config_path)
            _SCORING_CONFIG = {}
        else:
            with open(config_path) as f:
                _SCORING_CONFIG = yaml.safe_load(f) or {}
    return _SCORING_CONFIG


def score_all_alerts(db: Session) -> dict:
    """Score all unscored gap events."""
    config = load_scoring_config()
    alerts = db.query(AISGapEvent).filter(AISGapEvent.risk_score == 0).all()
    scored = 0
    for alert in alerts:
        # Count gap frequency windows
        gaps_7d = db.query(AISGapEvent).filter(
            AISGapEvent.vessel_id == alert.vessel_id,
            AISGapEvent.gap_start_utc >= alert.gap_start_utc - timedelta(days=7),
            AISGapEvent.gap_event_id != alert.gap_event_id,
        ).count()
        gaps_14d = db.query(AISGapEvent).filter(
            AISGapEvent.vessel_id == alert.vessel_id,
            AISGapEvent.gap_start_utc >= alert.gap_start_utc - timedelta(days=14),
            AISGapEvent.gap_event_id != alert.gap_event_id,
        ).count()
        gaps_30d = db.query(AISGapEvent).filter(
            AISGapEvent.vessel_id == alert.vessel_id,
            AISGapEvent.gap_start_utc >= alert.gap_start_utc - timedelta(days=30),
            AISGapEvent.gap_event_id != alert.gap_event_id,
        ).count()
        score, breakdown = compute_gap_score(
            alert, config,
            gaps_in_7d=gaps_7d,
            gaps_in_14d=gaps_14d,
            gaps_in_30d=gaps_30d,
            db=db,
            pre_gap_sog=getattr(alert, "pre_gap_sog", None),
        )
        alert.risk_score = score
        alert.risk_breakdown_json = breakdown
        scored += 1
    db.commit()
    logger.info("Scored %d alerts", scored)
    return {"scored": scored}


def rescore_all_alerts(db: Session) -> dict:
    """Clear and re-compute all risk scores. Use after risk_scoring.yaml changes."""
    config = load_scoring_config()
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8]
    # Reset all scores to 0 first
    alerts = db.query(AISGapEvent).all()
    for a in alerts:
        a.risk_score = 0
        a.risk_breakdown_json = None
    db.commit()
    result = score_all_alerts(db)
    result["config_hash"] = config_hash
    result["rescored"] = result.pop("scored")
    logger.info("Rescored %d alerts (config hash: %s)", result["rescored"], config_hash)
    return result


# ── Helper functions ──────────────────────────────────────────────────────────

def _corridor_multiplier(corridor: Any, config: dict) -> tuple[float, str]:
    """Return (multiplier, corridor_type_label) from config.

    Corridor type → config key mapping:
      sts_zone          → known_sts_zone        (2.0x default)
      export_route      → high_risk_export_corridor (1.5x default)
      everything else   → standard_corridor     (1.0x default)
    """
    if corridor is None:
        return 1.0, "none"

    corridor_cfg = config.get("corridor", {})
    # Handles both SQLAlchemy enum objects (have .value) and plain strings
    ct = str(corridor.corridor_type.value if hasattr(corridor.corridor_type, "value") else corridor.corridor_type)

    if ct == "sts_zone":
        return float(corridor_cfg.get("known_sts_zone", 2.0)), ct
    elif ct == "export_route":
        return float(corridor_cfg.get("high_risk_export_corridor", 1.5)), ct
    elif ct == "legitimate_trade_route":
        return float(corridor_cfg.get("legitimate_trade_route", 0.7)), ct
    else:
        # import_route, anchorage_holding, dark_zone → standard_corridor (1.0×)
        # anchorage_holding must NOT be mapped to 0.7× — STS waiting anchorages
        # (e.g. Laconian Gulf) legitimately carry the 2.0× STS zone risk.
        return float(corridor_cfg.get("standard_corridor", 1.0)), ct


def _vessel_size_multiplier(vessel: Any, config: dict) -> tuple[float, str]:
    """Return (multiplier, size_class_label) from config based on deadweight (DWT).

    DWT ranges:
      ≥ 200 000 → VLCC        (1.5x default)
      ≥ 120 000 → Suezmax     (1.3x default)
      ≥  80 000 → Aframax     (1.0x default)
      ≥  60 000 → Panamax     (0.8x default)
      unknown / smaller       (1.0x default — aframax baseline)
    """
    if vessel is None or vessel.deadweight is None:
        return 1.0, "unknown"

    dw = vessel.deadweight
    vm_cfg = config.get("vessel_size_multiplier", {})

    if dw >= 200_000:
        return float(vm_cfg.get("vlcc_200k_plus_dwt", 1.5)), "vlcc"
    elif dw >= 120_000:
        return float(vm_cfg.get("suezmax_120_200k_dwt", 1.3)), "suezmax"
    elif dw >= 80_000:
        return float(vm_cfg.get("aframax_80_120k_dwt", 1.0)), "aframax"
    elif dw >= 60_000:
        return float(vm_cfg.get("panamax_60_80k_dwt", 0.8)), "panamax"
    return 1.0, "sub_panamax"


def _score_band(score: int) -> str:
    """Return human-readable band label for a final score.

    Bands per PRD §7.5 / risk_scoring.yaml:
      low:      0–20
      medium:  21–50
      high:    51–75
      critical: 76+   (no upper bound)
    """
    if score <= 20:
        return "low"
    elif score <= 50:
        return "medium"
    elif score <= 75:
        return "high"
    return "critical"


# ── Main scoring function ─────────────────────────────────────────────────────

def compute_gap_score(
    gap: AISGapEvent,
    config: dict,
    gaps_in_7d: int = 0,
    gaps_in_14d: int = 0,
    gaps_in_30d: int = 0,
    speed_spike_precedes: bool = False,
    scoring_date: datetime = None,
    db: Session = None,
    pre_gap_sog: float = None,
) -> tuple[int, dict]:
    """Compute risk score for a single gap event using three-phase composition.

    Args:
        gap: The AISGapEvent to score (vessel and corridor relationships loaded lazily).
        config: Loaded risk_scoring.yaml dict.
        gaps_in_7d: Count of gap events for this vessel in the prior 7 days.
        gaps_in_14d: Count of gap events for this vessel in the prior 14 days.
        gaps_in_30d: Count of gap events for this vessel in the prior 30 days.
        speed_spike_precedes: True if the AIS point immediately before the gap
            had a speed above the vessel's class-specific spike threshold.
        scoring_date: Datetime to use as "now" for age calculations.
            Defaults to datetime.utcnow() if not provided (Phase 6.1 reproducibility).
        db: Optional SQLAlchemy session for DB-backed signal integration.
            If None, all DB-dependent phases are skipped gracefully.

    Returns:
        (final_score, breakdown_dict)

    Breakdown format:
        Non-prefixed keys → additive signal point values (summed by UI as base score)
        _-prefixed keys   → metadata (multipliers, subtotals) — not summed
    """
    # Phase 6.1: Reproducible scoring date
    if scoring_date is None:
        scoring_date = datetime.utcnow()
    current_year = scoring_date.year

    breakdown: dict[str, Any] = {}
    duration_h = (gap.duration_minutes or 0) / 60

    # ── Phase 1: Additive signals ─────────────────────────────────────────────

    # Gap duration
    gap_cfg = config.get("gap_duration", {})
    gap_duration_pts = 0
    if 2 <= duration_h < 4:
        gap_duration_pts = gap_cfg.get("2h_to_4h", 5)
        breakdown["gap_duration_2h_4h"] = gap_duration_pts
    elif 4 <= duration_h < 8:
        gap_duration_pts = gap_cfg.get("4h_to_8h", 12)
        breakdown["gap_duration_4h_8h"] = gap_duration_pts
    elif 8 <= duration_h < 12:
        gap_duration_pts = gap_cfg.get("8h_to_12h", 25)
        breakdown["gap_duration_8h_12h"] = gap_duration_pts
    elif 12 <= duration_h < 24:
        gap_duration_pts = gap_cfg.get("12h_to_24h", 40)
        breakdown["gap_duration_12h_24h"] = gap_duration_pts
    elif duration_h >= 24:
        gap_duration_pts = gap_cfg.get("24h_plus", 55)
        breakdown["gap_duration_24h_plus"] = gap_duration_pts

    # Speed anomaly standalone points (PRD §7.5 §2.3) — uses pre_gap_sog if available,
    # falls back to legacy speed_spike_precedes bool for backward compatibility.
    # Subsumption: spoof (+25) supersedes spike (+8); both trigger the 1.4× duration bonus.
    speed_cfg = config.get("speed_anomaly", {})
    _speed_spike_triggered = False  # tracks whether the 1.4× multiplier should apply

    vessel_for_speed = gap.vessel
    _raw_sog = pre_gap_sog if pre_gap_sog is not None else getattr(gap, "pre_gap_sog", None)
    # isinstance guard: MagicMock in tests returns another MagicMock from attribute access
    pre_sog = _raw_sog if isinstance(_raw_sog, (int, float)) else None

    if pre_sog is not None and vessel_for_speed is not None:
        dwt = vessel_for_speed.deadweight if isinstance(vessel_for_speed.deadweight, (int, float)) else None
        if dwt is None:
            dwt = 0
        # Determine class-specific thresholds
        if dwt >= 200_000:
            spike_kn = speed_cfg.get("vlcc_200k_plus_dwt", {}).get("spike_threshold_kn", 18)
            spoof_kn = speed_cfg.get("vlcc_200k_plus_dwt", {}).get("spoof_threshold_kn", 22)
        elif dwt >= 120_000:
            spike_kn = speed_cfg.get("suezmax_120_200k_dwt", {}).get("spike_threshold_kn", 19)
            spoof_kn = speed_cfg.get("suezmax_120_200k_dwt", {}).get("spoof_threshold_kn", 23)
        elif dwt >= 80_000:
            spike_kn = speed_cfg.get("aframax_80_120k_dwt", {}).get("spike_threshold_kn", 20)
            spoof_kn = speed_cfg.get("aframax_80_120k_dwt", {}).get("spoof_threshold_kn", 24)
        elif dwt >= 60_000:
            spike_kn = speed_cfg.get("panamax_60_80k_dwt", {}).get("spike_threshold_kn", 20)
            spoof_kn = speed_cfg.get("panamax_60_80k_dwt", {}).get("spoof_threshold_kn", 24)
        else:
            spike_kn, spoof_kn = 20, 24  # sub-Panamax default

        if pre_sog >= spoof_kn:
            # Spoof supersedes spike (subsumption — only higher score fires)
            breakdown["speed_spoof_before_gap"] = speed_cfg.get("speed_spoof", 25)
            _speed_spike_triggered = True
        elif pre_sog >= spike_kn:
            breakdown["speed_spike_before_gap"] = speed_cfg.get("speed_spike", 8)
            _speed_spike_triggered = True
    elif speed_spike_precedes:
        # Legacy bool path (pre_gap_sog unavailable)
        _speed_spike_triggered = True

    # Speed spike bonus: gap_duration sub-score ×1.4 if preceded by speed spike/spoof
    if _speed_spike_triggered and gap_duration_pts > 0:
        spike_mult = speed_cfg.get("gap_preceded_by_speed_spike_multiplier", 1.4)
        bonus = round(gap_duration_pts * (spike_mult - 1.0))
        if bonus > 0:
            breakdown["gap_duration_speed_spike_bonus"] = bonus

    # Impossible / near-impossible reappear
    env_cfg = config.get("movement_envelope", {})
    ratio = gap.velocity_plausibility_ratio
    if gap.impossible_speed_flag:
        breakdown["impossible_reappear"] = env_cfg.get("impossible_reappear", 40)
    elif ratio is not None and 0.7 <= ratio < 1.0:
        breakdown["near_impossible_reappear"] = env_cfg.get("near_impossible_reappear", 15)

    # Phase 6.3: Dark zone 3-scenario geometry semantics
    dz_cfg = config.get("dark_zone", {})
    if gap.in_dark_zone:
        # Use isinstance to guard against MagicMock in tests; only real int FK IDs qualify
        _has_dz_id = isinstance(gap.dark_zone_id, int)
        if gap.impossible_speed_flag and _has_dz_id:
            # Vessel exits dark zone with impossible position jump (+35)
            breakdown["dark_zone_exit_impossible"] = dz_cfg.get("vessel_exits_dark_zone_with_impossible_jump", 35)
        elif _has_dz_id and not gap.impossible_speed_flag:
            # Gap ends in dark zone (entry scenario) or is entirely inside
            # Score as interior deduction only if duration is short (< 1h), else entry
            if (gap.duration_minutes or 0) < 60:
                breakdown["dark_zone_deduction"] = dz_cfg.get("gap_in_known_jamming_zone", -10)
            else:
                breakdown["dark_zone_entry"] = dz_cfg.get("gap_immediately_before_dark_zone_entry", 20)
        else:
            # in_dark_zone=True but no explicit dark_zone_id — corridor is_jamming_zone=True
            breakdown["dark_zone_deduction"] = dz_cfg.get("gap_in_known_jamming_zone", -10)

    # gap_in_sts_tagged_corridor: flat +30 (PRD §STS — separate from the 2.0× corridor multiplier).
    # This goes BEFORE Phase 2 so the 2.0× multiplier is applied to it as well.
    if gap.corridor is not None:
        _ct_val = str(
            gap.corridor.corridor_type.value
            if hasattr(gap.corridor.corridor_type, "value")
            else gap.corridor.corridor_type
        )
        if _ct_val == "sts_zone":
            sts_cfg = config.get("sts", {})
            breakdown["gap_in_sts_tagged_corridor"] = sts_cfg.get("gap_in_sts_tagged_corridor", 30)

    # Phase 6.2: Gap frequency with subsumption hierarchy
    freq_cfg = config.get("gap_frequency", {})
    if gaps_in_30d >= 5:
        breakdown["gap_frequency_5_in_30d"] = freq_cfg.get("5_gaps_in_30d", 50)
    elif gaps_in_14d >= 3:
        breakdown["gap_frequency_3_in_14d"] = freq_cfg.get("3_gaps_in_14d", 32)
    elif gaps_in_7d >= 2:
        breakdown["gap_frequency_2_in_7d"] = freq_cfg.get("2_gaps_in_7d", 18)

    # Vessel-level signals
    vessel = gap.vessel
    if vessel is not None:
        flag_risk = str(
            vessel.flag_risk_category.value
            if hasattr(vessel.flag_risk_category, "value")
            else vessel.flag_risk_category
        )
        flag_cfg = config.get("flag_state", {})

        # Flag state risk
        if flag_risk == "low_risk":
            pts = flag_cfg.get("white_list_flag", -10)
            if pts != 0:
                breakdown["flag_white_list"] = pts
        elif flag_risk == "high_risk":
            breakdown["flag_high_risk"] = flag_cfg.get("high_risk_registry", 15)

        # Vessel age — age_25_plus_AND_high_risk_flag supersedes plain age_25_plus_y
        vessel_age_cfg = config.get("vessel_age", {})
        if vessel.year_built is not None:
            age = max(0, current_year - vessel.year_built)
            if age <= 10:
                pts = vessel_age_cfg.get("age_0_to_10y", -5)
                if pts != 0:
                    breakdown["vessel_age_0_10y"] = pts
            elif age <= 20:
                pts = vessel_age_cfg.get("age_10_to_20y", 0)
                if pts != 0:
                    breakdown["vessel_age_10_20y"] = pts
            elif age <= 25:
                breakdown["vessel_age_20_25y"] = vessel_age_cfg.get("age_20_to_25y", 10)
            else:
                if flag_risk == "high_risk":
                    breakdown["vessel_age_25plus_high_risk"] = vessel_age_cfg.get(
                        "age_25_plus_AND_high_risk_flag", 30
                    )
                else:
                    breakdown["vessel_age_25plus"] = vessel_age_cfg.get("age_25_plus_y", 20)

        # Phase 6.11: AIS class mismatch: large tanker (DWT > 1 000t) using Class B
        # SOLAS requires Class A transponders for vessels > 300 GT
        ais_cls = str(
            vessel.ais_class.value if hasattr(vessel.ais_class, "value") else vessel.ais_class
        )
        if ais_cls == "B" and vessel.deadweight is not None and vessel.deadweight > 1_000:
            ais_cfg = config.get("ais_class", {})
            breakdown["ais_class_mismatch"] = ais_cfg.get("large_tanker_using_class_b", 50)

    # Phase 6.4: Spoofing signals (only linked to this gap or vessel-level within 2h of gap start)
    if db is not None:
        from app.models.spoofing_anomaly import SpoofingAnomaly
        from sqlalchemy import or_, and_
        vessel_spoofing = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.vessel_id == gap.vessel_id,
            or_(
                SpoofingAnomaly.gap_event_id == gap.gap_event_id,
                and_(
                    SpoofingAnomaly.gap_event_id == None,
                    SpoofingAnomaly.end_time_utc >= gap.gap_start_utc - timedelta(hours=2),
                    SpoofingAnomaly.start_time_utc <= gap.gap_start_utc,
                )
            )
        ).all()
        # Erratic nav_status cap: take the single highest score from all erratic anomalies
        # (prevents multiplication from a continuous oscillation episode creating many records)
        erratic_anomalies = [
            s for s in vessel_spoofing
            if str(s.anomaly_type.value if hasattr(s.anomaly_type, "value") else s.anomaly_type)
            == "erratic_nav_status"
        ]
        non_erratic = [
            s for s in vessel_spoofing
            if str(s.anomaly_type.value if hasattr(s.anomaly_type, "value") else s.anomaly_type)
            != "erratic_nav_status"
        ]
        if erratic_anomalies:
            breakdown["spoofing_erratic_nav_status"] = max(
                s.risk_score_component for s in erratic_anomalies
            )
        for s in non_erratic:
            key = f"spoofing_{s.anomaly_type.value if hasattr(s.anomaly_type, 'value') else s.anomaly_type}"
            # Avoid duplicate keys by appending anomaly_id if key already exists
            if key in breakdown:
                key = f"{key}_{s.anomaly_id}"
            breakdown[key] = s.risk_score_component

    # Phase 6.5: Loitering signal integration
    if db is not None:
        from app.models.loitering_event import LoiteringEvent
        loitering = db.query(LoiteringEvent).filter(
            LoiteringEvent.vessel_id == gap.vessel_id,
            LoiteringEvent.start_time_utc >= gap.gap_start_utc - timedelta(hours=48),
            LoiteringEvent.end_time_utc <= gap.gap_end_utc + timedelta(hours=48),
        ).all()
        for le in loitering:
            loiter_key = f"loitering_{le.loiter_id}"
            if le.duration_hours >= 12 and le.corridor_id:
                breakdown[loiter_key] = 20
            elif le.duration_hours >= 4 and le.corridor_id:
                breakdown[loiter_key] = 8
            if le.preceding_gap_id or le.following_gap_id:
                breakdown[f"loiter_gap_loiter_{le.loiter_id}"] = 15

        # Laid-up vessel scoring
        if vessel is not None:
            if getattr(vessel, 'vessel_laid_up_in_sts_zone', False):
                breakdown["vessel_laid_up_in_sts_zone"] = 30
            elif getattr(vessel, 'vessel_laid_up_60d', False):
                breakdown["vessel_laid_up_60d"] = 25
            elif getattr(vessel, 'vessel_laid_up_30d', False):
                breakdown["vessel_laid_up_30d"] = 15

    # Phase 6.6: STS transfer signal integration
    if db is not None:
        from app.models.sts_transfer import StsTransferEvent
        from sqlalchemy import or_
        sts_events = db.query(StsTransferEvent).filter(
            or_(
                StsTransferEvent.vessel_1_id == gap.vessel_id,
                StsTransferEvent.vessel_2_id == gap.vessel_id,
            ),
            StsTransferEvent.start_time_utc >= gap.gap_start_utc - timedelta(days=7),
            StsTransferEvent.end_time_utc <= gap.gap_end_utc + timedelta(days=7),
        ).all()
        for sts in sts_events:
            breakdown[f"sts_event_{sts.sts_id}"] = sts.risk_score_component

    # Phase 6.7: Watchlist scoring
    if db is not None and vessel is not None:
        from app.models.vessel_watchlist import VesselWatchlist
        WATCHLIST_SCORES = {
            "OFAC_SDN": 50, "EU_COUNCIL": 50, "KSE_SHADOW": 30, "LOCAL_INVESTIGATION": 20
        }
        watchlist = db.query(VesselWatchlist).filter(
            VesselWatchlist.vessel_id == vessel.vessel_id,
            VesselWatchlist.is_active == True,
        ).all()
        for w in watchlist:
            score_val = WATCHLIST_SCORES.get(w.watchlist_source, 20)
            breakdown[f"watchlist_{w.watchlist_source}"] = score_val

    # Phase 6.8: Vessel identity changes scoring
    if db is not None and vessel is not None:
        from app.models.vessel_history import VesselHistory
        identity_changes = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.observed_at >= gap.gap_start_utc - timedelta(days=90),
        ).all()
        meta_cfg = config.get("metadata", {})
        flag_changes = [h for h in identity_changes if h.field_changed == "flag"]
        name_changes = [h for h in identity_changes if h.field_changed == "name"]

        # flag+name change within 48h (highest priority flag signal — checked first)
        for fc in flag_changes:
            for nc in name_changes:
                diff_h = abs((fc.observed_at - nc.observed_at).total_seconds()) / 3600
                if diff_h <= 48:
                    breakdown["flag_and_name_change_48h"] = meta_cfg.get("flag_AND_name_change_within_48h", 30)
                    break

        # Flag change subsumption hierarchy: 7d (+35) supersedes 30d (+25) — only one fires
        recent_7d_flag = [h for h in flag_changes if (gap.gap_start_utc - h.observed_at).days <= 7]
        recent_30d_flag = [h for h in flag_changes if (gap.gap_start_utc - h.observed_at).days <= 30]
        if "flag_and_name_change_48h" not in breakdown:
            if recent_7d_flag:
                breakdown["flag_change_7d"] = meta_cfg.get("flag_change_in_last_7d", 35)
            elif recent_30d_flag:
                breakdown["flag_change_30d"] = meta_cfg.get("flag_change_in_last_30d", 25)

        # 3+ flag changes in 90d (stacks with single-change signals — different severity)
        if len(flag_changes) >= 3:
            breakdown["flag_changes_3plus_90d"] = meta_cfg.get("3_plus_flag_changes_in_90d", 40)

        # name_change_during_active_voyage: only fires if change was within 7d of gap start
        # (prevents dry-dock/sale renaming false positives)
        recent_name_changes = [
            h for h in name_changes
            if (gap.gap_start_utc - h.observed_at).days <= 7
        ]
        if recent_name_changes and "flag_and_name_change_48h" not in breakdown:
            breakdown["name_change_during_voyage"] = meta_cfg.get("name_change_during_active_voyage", 30)

        # mmsi_change_mapped_same_position: +45
        mmsi_changes = [h for h in identity_changes if h.field_changed == "mmsi"]
        if mmsi_changes:
            breakdown["mmsi_change"] = meta_cfg.get("mmsi_change_mapped_same_position", 45)

    # Phase 6.9: Legitimacy signals
    if db is not None and vessel is not None:
        # gap_free_90d_clean: no gaps in last 90 days
        from app.models.gap_event import AISGapEvent as _AISGapEvent
        recent_gaps = db.query(_AISGapEvent).filter(
            _AISGapEvent.vessel_id == vessel.vessel_id,
            _AISGapEvent.gap_start_utc >= gap.gap_start_utc - timedelta(days=90),
            _AISGapEvent.gap_event_id != gap.gap_event_id,
        ).count()
        if recent_gaps == 0:
            legitimacy_cfg = config.get("legitimacy", {})
            breakdown["legitimacy_gap_free_90d"] = legitimacy_cfg.get("gap_free_90d_clean", -15)

        # ais_class_a_consistent: all points are Class A
        from app.models.ais_point import AISPoint
        non_a = db.query(AISPoint).filter(
            AISPoint.vessel_id == vessel.vessel_id,
            AISPoint.ais_class != "A",
        ).first()
        if non_a is None:
            legitimacy_cfg = config.get("legitimacy", {})
            breakdown["legitimacy_ais_class_a_consistent"] = legitimacy_cfg.get("ais_class_a_consistent", -5)

    # TODO(v1.1): flag_less_than_2y_old_AND_high_risk: +20
    # Deferred: no authoritative data source reliably maps each ISO flag code to the year
    # its maritime registry became operationally active for shadow fleet use. Hardcoding
    # incorrect years would generate false signals. Requires external registry dataset
    # (UNCTAD, Paris MOU historical records, or KSE Institute research).
    # See risk_scoring.yaml flag_state.flag_less_than_2y_old_AND_high_risk for the weight.

    # Phase 6.10: New MMSI scoring
    if vessel is not None:
        mmsi_first_seen = getattr(vessel, 'mmsi_first_seen_utc', None)
        if isinstance(mmsi_first_seen, datetime):
            try:
                fs = mmsi_first_seen.replace(tzinfo=None) if mmsi_first_seen.tzinfo else mmsi_first_seen
                mmsi_age_days = (scoring_date - fs).days
            except Exception:
                mmsi_age_days = 9999
            if mmsi_age_days < 30:
                behavioral_cfg = config.get("behavioral", {})
                breakdown["new_mmsi_first_30d"] = behavioral_cfg.get("new_mmsi_first_30d", 15)
                RUSSIAN_ORIGIN_FLAGS = {"PW", "MH", "KM", "SL", "HN", "GA", "CM", "TZ"}
                if vessel.flag and vessel.flag.upper() in RUSSIAN_ORIGIN_FLAGS:
                    breakdown["new_mmsi_russian_origin_flag"] = behavioral_cfg.get("new_mmsi_plus_russian_origin_zone", 25)

    # ── Phase 2: Corridor multiplier ─────────────────────────────────────────
    additive_subtotal = sum(v for v in breakdown.values() if isinstance(v, (int, float)))
    corridor_mult, corridor_type = _corridor_multiplier(gap.corridor, config)
    corridor_adjusted = additive_subtotal * corridor_mult

    # ── Phase 3: Vessel size multiplier ──────────────────────────────────────
    vessel_size_mult, vessel_size_class = _vessel_size_multiplier(gap.vessel, config)
    final_score = max(0, round(corridor_adjusted * vessel_size_mult))

    # Metadata (prefixed with _ so UI does not sum them as signal points)
    breakdown["_corridor_type"] = corridor_type
    breakdown["_corridor_multiplier"] = corridor_mult
    breakdown["_vessel_size_class"] = vessel_size_class
    breakdown["_vessel_size_multiplier"] = vessel_size_mult
    breakdown["_additive_subtotal"] = additive_subtotal
    breakdown["_final_score"] = final_score

    return final_score, breakdown
