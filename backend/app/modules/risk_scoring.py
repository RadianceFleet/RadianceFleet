"""Risk scoring engine.

Applies configurable rules from risk_scoring.yaml to produce an explainable score
for each AIS gap event. See PRD §7.5 for the full scoring specification.

Scoring uses three-phase composition:
  Phase 1 — Additive signals (flat points each; gap_duration gets ×1.4 if speed spike preceded)
  Phase 2 — Corridor multiplier  (risk_signals × corridor_factor)
  Phase 3 — Vessel size multiplier (corridor_adjusted × vessel_size_factor)

Multipliers apply ONLY to positive (risk) signals. Legitimacy deductions (negative values)
are added after amplification so they always deduct their face value regardless of zone/size.

final_score = round(risk_signals × corridor_factor × vessel_size_factor + legitimacy_signals)
No hard cap; 76+ is "critical" regardless of upper bound.
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics as _stats
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.modules.scoring_config import *  # noqa: F401,F403
from app.modules.scoring_config import (
    _WATCHLIST_DEFAULTS,
    _WATCHLIST_KEY_MAP,
    _is_whitelisted_operator,
    _load_fraudulent_registries_config,
    _load_pi_clubs_config,
    load_scoring_config,
    reload_scoring_config,
)
from app.modules.scoring_stubs import *  # noqa: F401,F403
from app.modules.scoring_stubs import score_watchlist_stubs

logger = logging.getLogger(__name__)


def _gap_frequency_filter(alert: AISGapEvent):
    """Return the provenance-aware filter for gap frequency counting.

    If original_vessel_id is set (post-Stage-0 gaps or merged gaps with
    provenance), count only gaps from the same original identity.
    If null (legacy data), fall back to vessel_id.
    """
    if getattr(alert, "original_vessel_id", None) is not None:
        return AISGapEvent.original_vessel_id == alert.original_vessel_id
    return AISGapEvent.vessel_id == alert.vessel_id


def _count_gaps_in_window(db: Session, alert: AISGapEvent, days: int) -> int:
    """Count gap events for the same identity within a time window."""
    return (
        db.query(AISGapEvent)
        .filter(
            _gap_frequency_filter(alert),
            AISGapEvent.gap_start_utc >= alert.gap_start_utc - timedelta(days=days),
            AISGapEvent.gap_event_id != alert.gap_event_id,
        )
        .count()
    )


def score_all_alerts(db: Session, scoring_date: datetime = None) -> dict:
    """Score all unscored gap events.

    Args:
        scoring_date: Fixed datetime for reproducible scoring (NFR3).
            Defaults to now if not provided.
    """
    config = load_scoring_config()
    alerts = db.query(AISGapEvent).filter(AISGapEvent.risk_score == 0).all()
    scored = 0
    feed_outage_skipped = 0
    for alert in alerts:
        # Skip gaps caused by feed outages — they are infrastructure noise, not evasion
        if getattr(alert, "is_feed_outage", False):
            feed_outage_skipped += 1
            continue
        # Count gap frequency windows (provenance-aware to prevent inflation)
        gaps_7d = _count_gaps_in_window(db, alert, 7)
        gaps_14d = _count_gaps_in_window(db, alert, 14)
        gaps_30d = _count_gaps_in_window(db, alert, 30)
        score, breakdown = compute_gap_score(
            alert,
            config,
            gaps_in_7d=gaps_7d,
            gaps_in_14d=gaps_14d,
            gaps_in_30d=gaps_30d,
            scoring_date=scoring_date,
            db=db,
            pre_gap_sog=getattr(alert, "pre_gap_sog", None),
        )
        alert.risk_score = score
        alert.risk_breakdown_json = breakdown
        scored += 1
    db.commit()
    if feed_outage_skipped:
        logger.info("Scored %d alerts (skipped %d feed outage gaps)", scored, feed_outage_skipped)
    else:
        logger.info("Scored %d alerts", scored)
    return {"scored": scored, "feed_outage_skipped": feed_outage_skipped}


def rescore_all_alerts(db: Session, clear_detections: bool = False) -> dict:
    """Clear and re-compute all risk scores. Use after risk_scoring.yaml changes.

    Args:
        clear_detections: If True, also delete SpoofingAnomaly/LoiteringEvent/StsTransferEvent
            records before re-scoring. Requires re-running detection pipeline after rescore.
            Default False for backward compatibility.
    """
    config = reload_scoring_config()
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:8]

    if clear_detections:
        from app.models.loitering_event import LoiteringEvent
        from app.models.spoofing_anomaly import SpoofingAnomaly
        from app.models.sts_transfer import StsTransferEvent

        db.query(SpoofingAnomaly).delete()
        db.query(LoiteringEvent).delete()
        db.query(StsTransferEvent).delete()
        db.commit()
        logger.info("Cleared detection signals (clear_detections=True)")

    # Reset all scores to 0 via a committed bulk UPDATE before re-scoring.
    # Using two separate transactions (zero then score) avoids the SQLite WAL
    # auto-checkpoint hazard: when the zeroing autoflush exceeds ~1000 WAL pages,
    # SQLite permanently commits the zeros before score_all_alerts can commit
    # the new scores, leaving the DB in a zeroed state on any scoring error.
    from sqlalchemy import update as sa_update

    db.execute(sa_update(AISGapEvent).values(risk_score=0, risk_breakdown_json=None))
    db.commit()
    result = score_all_alerts(db)
    result["config_hash"] = config_hash
    result["rescored"] = result.pop("scored")
    result["detections_cleared"] = clear_detections
    logger.info("Rescored %d alerts (config hash: %s)", result["rescored"], config_hash)
    stub_result = score_watchlist_stubs(db, config=config)
    result["stub_scored"] = stub_result["scored"]
    result["stub_cleared"] = stub_result["cleared"]
    return result


# ── Helper functions ──────────────────────────────────────────────────────────


def _corridor_multiplier(corridor: Any, config: dict) -> tuple[float, str]:
    """Return (multiplier, corridor_type_label) from config.

    NOTE: The corridor model's ``risk_weight`` field is informational metadata
    only. Actual scoring multipliers come from ``risk_scoring.yaml`` [corridor]
    section. This is intentional — analysts tune scoring in one YAML file.

    Corridor type → config key mapping:
      sts_zone          → known_sts_zone        (1.5x default)
      export_route      → high_risk_export_corridor (1.5x default)
      everything else   → standard_corridor     (1.0x default)
    """
    if corridor is None:
        return 1.0, "none"

    corridor_cfg = config.get("corridor", {})
    # Handles both SQLAlchemy enum objects (have .value) and plain strings
    ct = str(
        corridor.corridor_type.value
        if hasattr(corridor.corridor_type, "value")
        else corridor.corridor_type
    )

    if ct == "sts_zone":
        return float(corridor_cfg.get("known_sts_zone", 1.5)), ct
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
      ≥ 200 000 → VLCC        (1.3x default)
      ≥ 120 000 → Suezmax     (1.2x default)
      ≥  80 000 → Aframax     (1.0x default)
      ≥  60 000 → Panamax     (0.8x default)
      unknown / smaller       (1.0x default — aframax baseline)
    """
    if vessel is None or vessel.deadweight is None:
        return 1.0, "unknown"

    dw = vessel.deadweight
    vm_cfg = config.get("vessel_size_multiplier", {})

    if dw >= 200_000:
        return float(vm_cfg.get("vlcc_200k_plus_dwt", 1.3)), "vlcc"
    elif dw >= 120_000:
        return float(vm_cfg.get("suezmax_120_200k_dwt", 1.2)), "suezmax"
    elif dw >= 80_000:
        return float(vm_cfg.get("aframax_80_120k_dwt", 1.0)), "aframax"
    elif dw >= 60_000:
        return float(vm_cfg.get("panamax_60_80k_dwt", 0.8)), "panamax"
    return 1.0, "sub_panamax"


def _vessel_age_points(vessel: Any, config: dict, current_year: int) -> tuple[str, int] | None:
    """Returns (breakdown_key, points) or None if year_built unknown or score is 0.

    Extracted from compute_gap_score() to allow reuse in score_watchlist_stubs().
    Logic mirrors the vessel_age block in compute_gap_score() exactly.
    """
    if vessel.year_built is None:
        return None
    vessel_age_cfg = config.get("vessel_age", {})
    age = max(0, current_year - vessel.year_built)
    flag_risk = str(getattr(vessel, "flag_risk_category", None) or "").lower()
    # Normalise both enum-value strings and raw strings
    if hasattr(getattr(vessel, "flag_risk_category", None), "value"):
        flag_risk = str(vessel.flag_risk_category.value).lower()
    if age <= 10:
        pts = vessel_age_cfg.get("age_0_to_10y", -5)
        if pts != 0:
            return ("vessel_age_0_10y", pts)
        return None
    elif age <= 15:
        pts = vessel_age_cfg.get("age_10_to_15y", 0)
        if pts != 0:
            return ("vessel_age_10_15y", pts)
        return None
    elif age <= 20:
        pts = vessel_age_cfg.get("age_15_to_20y", 5)
        return ("vessel_age_15_20y", pts) if pts != 0 else None
    elif age <= 25:
        pts = vessel_age_cfg.get("age_20_to_25y", 10)
        return ("vessel_age_20_25y", pts) if pts != 0 else None
    else:
        if flag_risk == "high_risk":
            pts = vessel_age_cfg.get("age_25_plus_AND_high_risk_flag", 30)
            return ("vessel_age_25plus_high_risk", pts) if pts != 0 else None
        else:
            pts = vessel_age_cfg.get("age_25_plus_y", 20)
            return ("vessel_age_25plus", pts) if pts != 0 else None


def _sts_with_watchlisted_vessel(db: Session, vessel) -> tuple[int, str | None]:
    """Check if vessel has done STS with any watchlisted vessel.

    Returns (points, watchlist_source) or (0, None) if no match.
    Sanctioned (OFAC/EU) partners score higher than shadow fleet list (KSE/OpenSanctions).
    """
    from sqlalchemy import or_

    from app.models.sts_transfer import StsTransferEvent
    from app.models.vessel_watchlist import VesselWatchlist

    sts_events = (
        db.query(StsTransferEvent)
        .filter(
            or_(
                StsTransferEvent.vessel_1_id == vessel.vessel_id,
                StsTransferEvent.vessel_2_id == vessel.vessel_id,
            )
        )
        .all()
    )

    if not sts_events:
        return 0, None

    best_score = 0
    best_source = None
    _SANCTIONED_SOURCES = {"OFAC_SDN", "EU_COUNCIL"}

    for sts in sts_events:
        partner_id = sts.vessel_2_id if sts.vessel_1_id == vessel.vessel_id else sts.vessel_1_id
        watchlist_hit = (
            db.query(VesselWatchlist)
            .filter(
                VesselWatchlist.vessel_id == partner_id,
                VesselWatchlist.is_active,
            )
            .all()
        )

        for w in watchlist_hit:
            if w.watchlist_source in _SANCTIONED_SOURCES and best_score < 30:
                best_score = 30
                best_source = w.watchlist_source
            elif w.watchlist_source not in _SANCTIONED_SOURCES and best_score < 20:
                best_score = 20
                best_source = w.watchlist_source

    return best_score, best_source


def _had_russian_port_call(db: Session, vessel, gap_start: datetime, days_before: int = 30) -> bool:
    """Check if vessel was near a Russian oil terminal in the N days before gap_start.

    Uses AIS position history within 5nm of any port with is_russian_oil_terminal=True.
    """
    from app.models.ais_point import AISPoint
    from app.models.port import Port
    from app.utils.geo import haversine_nm

    terminals = db.query(Port).filter(Port.is_russian_oil_terminal).all()
    if not terminals:
        return False

    window_start = gap_start - timedelta(days=days_before)
    points = (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel.vessel_id,
            AISPoint.timestamp_utc >= window_start,
            AISPoint.timestamp_utc <= gap_start,
        )
        .all()
    )

    for pt in points:
        for terminal in terminals:
            # Extract lat/lon from WKT port geometry
            try:
                from app.utils.geo import load_geometry

                port_shape = load_geometry(terminal.geometry)
                if port_shape is None:
                    continue
                port_lat, port_lon = port_shape.y, port_shape.x
            except Exception as e:
                logger.debug("Port geometry load failed for terminal: %s", e)
                continue
            if haversine_nm(pt.lat, pt.lon, port_lat, port_lon) <= 5.0:
                return True
    return False


def _temporal_recency_factor(signal_dt: datetime | None, gap_dt: datetime) -> float:
    """Return a recency multiplier for time-sensitive signals.

    Literature consensus (Windward Behavioral Intelligence, Kpler 2024):
    recent anomalies are 2× more predictive of active evasion than events >90d old.

    Args:
        signal_dt: When the signal event occurred (None → return 1.0, no boost).
        gap_dt: The gap start time used as the reference point.

    Returns:
        2.0 if event ≤7d ago (active evasion window)
        1.5 if event ≤30d ago (recent pattern)
        1.0 if event ≤90d ago (baseline)
        0.8 if event >90d ago (historical decay)
    """
    if signal_dt is None:
        return 1.0
    try:
        sig = signal_dt.replace(tzinfo=None) if signal_dt.tzinfo else signal_dt
        ref = gap_dt.replace(tzinfo=None) if gap_dt.tzinfo else gap_dt
        days_ago = (ref - sig).days
    except Exception as e:
        logger.debug("Temporal recency calculation failed: %s", e)
        return 1.0
    if days_ago <= 7:
        return 2.0
    if days_ago <= 30:
        return 1.5
    if days_ago <= 90:
        return 1.0
    return 0.8


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


# ── Pillar key sets (used by compute_gap_score and _corroboration_bonus) ──────

_VESSEL_PILLAR_KEYS = frozenset(
    {
        # Identity, age, flag, ownership quality
        "vessel_age_15_20y",
        "vessel_age_20_25y",
        "vessel_age_25_plus",
        "vessel_age_25_plus_high_risk",
        "high_risk_flag",
        "flag_changes_3plus_90d",
        "flag_change_30d",
        "flag_change_7d",
        "flag_AND_name_change_48h",
        "flag_change_high_to_low",
        "pi_coverage_lapsed",
        "pi_validation",
        "fraudulent_registry",
        "pi_cycling",
        "ism_continuity",
        "psc_detained_last_12m",
        "psc_major_deficiencies_3_plus",
        "owner_or_manager_on_sanctions_list",
        "callsign_change",
        "identity_merge_detected",
        "imo_fabricated",
        "stateless_mmsi",
        "flag_hopping",
        "rename_velocity_2_365d",
        "rename_velocity_3_365d",
        "invalid_metadata_generic_name",
        "invalid_metadata_impossible_dwt",
        "no_name_at_all",
        "name_all_caps_numbers",
        "kse_shadow_profile_match",
        "kse_shadow_profile_strong",
        "ais_class_large_tanker_class_b",
        "class_switching_a_to_b",
    }
)
_POSITION_PILLAR_KEYS = frozenset(
    {
        # AIS reporting quality, spoofing events, gaps
        "anchor_in_open_ocean",
        "circle_pattern",
        "slow_roll",
        "mmsi_reuse_implied_speed_30kn",
        "mmsi_reuse_implied_speed_100kn",
        "nav_status_speed_mismatch",
        "erratic_nav_status",
        "dual_transmission_candidate",
        "cross_receiver_disagreement",
        "identity_swap",
        "speed_spike",
        "speed_spoof",
        "speed_impossible",
        "track_naturalness_high",
        "track_naturalness_medium",
        "track_naturalness_low",
        "sparse_transmission",
        "stale_ais",
        "suspicious_mid",
        "new_mmsi_first_30d",
        "new_mmsi_first_60d",
        "new_mmsi_russian_origin_flag",
        "dark_vessel_unmatched",
        "dark_vessel_in_corridor",
        "eez_boundary_proximity_5nm",
        "eez_boundary_proximity_20nm",
    }
)
_VOYAGE_PILLAR_KEYS = frozenset(
    {
        # Port patterns, STS, route integrity
        "sanctioned_port_visit_confirmed",
        "sanctioned_port_proximity_10nm",
        "crea_sanctioned_destination",
        "russian_port_recent",
        "russian_port_gap_sts",
        "voyage_cycle_pattern",
        "at_sea_no_port_call_90d",
        "at_sea_no_port_call_180d",
        "at_sea_no_port_call_365d",
        "route_deviation_toward_sts",
        "laden_from_russian_terminal_sts",
        "destination_heading_to_sts_eu",
        "destination_blank_generic",
        "destination_changes",
        "route_laundering",
        "sts_chain_3",
        "sts_chain_4_plus",
        "sts_with_sanctioned_vessel",
        "sts_with_shadow_fleet_vessel",
        "draught_sts_confirmation",
        "draught_delta_across_gap",
    }
)
_WATCHLIST_PILLAR_KEYS = frozenset(
    {
        "watchlist_ofac",
        "watchlist_eu",
        "kse_shadow_fleet",
        "watchlist_stub_score",
    }
)


def _corroboration_bonus(breakdown: dict, config: dict) -> int:
    """Count detector families with >=10 points of signal. Bonus for multi-family corroboration."""
    families = {
        "POSITION": _POSITION_PILLAR_KEYS,
        "IDENTITY": _VESSEL_PILLAR_KEYS,
        "VOYAGE": _VOYAGE_PILLAR_KEYS,
        "WATCHLIST": _WATCHLIST_PILLAR_KEYS,
    }
    active = sum(
        1
        for keys in families.values()
        if sum(breakdown.get(k, 0) for k in keys if breakdown.get(k, 0) > 0) >= 10
    )
    if active >= 4:
        return config.get("families_4_bonus", 30)
    if active >= 3:
        return config.get("families_3_bonus", 20)
    if active >= 2:
        return config.get("families_2_bonus", 10)
    return 0


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
            Defaults to datetime.now(timezone.utc) if not provided (Phase 6.1 reproducibility).
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
        scoring_date = datetime.now(UTC).replace(tzinfo=None)
    current_year = scoring_date.year

    # Early exit: whitelisted operators (known-legitimate vessels) score zero
    _vessel_mmsi = getattr(getattr(gap, "vessel", None), "mmsi", None)
    if _is_whitelisted_operator(_vessel_mmsi):
        return 0, {"_whitelisted_operator": True, "_final_score": 0}

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

    # speed_impossible check first — supersedes both spike and spoof (different signal class)
    # Impossible speed (>30kn) indicates MMSI reuse or position error, not evasive behavior.
    # Does NOT trigger 1.4× duration bonus.
    _speed_is_impossible = pre_sog is not None and pre_sog > 30

    if _speed_is_impossible:
        breakdown["speed_impossible"] = speed_cfg.get("speed_impossible", 40)
    elif pre_sog is not None and vessel_for_speed is not None:
        dwt = (
            vessel_for_speed.deadweight
            if isinstance(vessel_for_speed.deadweight, (int, float))
            else None
        )
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
        _impossible = (
            bool(gap.impossible_speed_flag) if gap.impossible_speed_flag is not None else False
        )
        if _impossible and _has_dz_id:
            # Vessel exits dark zone with impossible position jump (+35)
            breakdown["dark_zone_exit_impossible"] = dz_cfg.get(
                "vessel_exits_dark_zone_with_impossible_jump", 35
            )
        elif _has_dz_id and not _impossible:
            # Check entry speed: high pre-gap SOG into a dark zone is suspicious
            # even if the gap is short. A vessel speeding into a jamming zone and
            # going quiet for <2h is intentional evasion, not ambient noise.
            _pre_sog_dz = pre_sog if pre_sog is not None else 0.0
            # Use vessel-class-specific spike threshold (consistent with speed anomaly logic)
            _dz_dwt = (
                vessel_for_speed.deadweight
                if vessel_for_speed is not None
                and isinstance(getattr(vessel_for_speed, "deadweight", None), (int, float))
                else 0
            )
            if _dz_dwt >= 200_000:
                spike_thresh = speed_cfg.get("vlcc_200k_plus_dwt", {}).get("spike_threshold_kn", 18)
            elif _dz_dwt >= 120_000:
                spike_thresh = speed_cfg.get("suezmax_120_200k_dwt", {}).get(
                    "spike_threshold_kn", 19
                )
            elif _dz_dwt >= 80_000:
                spike_thresh = speed_cfg.get("aframax_80_120k_dwt", {}).get(
                    "spike_threshold_kn", 20
                )
            elif _dz_dwt >= 60_000:
                spike_thresh = speed_cfg.get("panamax_60_80k_dwt", {}).get("spike_threshold_kn", 20)
            else:
                spike_thresh = 20
            if _pre_sog_dz > spike_thresh and (gap.duration_minutes or 0) < 360:
                breakdown["dark_zone_entry"] = dz_cfg.get(
                    "gap_immediately_before_dark_zone_entry", 20
                )
            else:
                # Normal-speed gap in dark zone: check if selective evasion vs ambient jamming
                # Query how many OTHER vessels also have gaps in same dark zone / time window
                _selective_evasion = False
                if db is not None:
                    try:
                        from app.models.gap_event import AISGapEvent as _GapDZ

                        # Count other vessels with gaps overlapping this gap's time window in same dark zone
                        _other_dark_gaps = db.query(_GapDZ).filter(
                            _GapDZ.vessel_id != gap.vessel_id,
                            _GapDZ.in_dark_zone,
                            _GapDZ.gap_start_utc <= gap.gap_end_utc,
                            _GapDZ.gap_end_utc >= gap.gap_start_utc,
                        )
                        if gap.dark_zone_id is not None:
                            _other_dark_gaps = _other_dark_gaps.filter(
                                _GapDZ.dark_zone_id == gap.dark_zone_id,
                            )
                        _other_count = _other_dark_gaps.count()
                        if _other_count <= 2:
                            # Partial outage guard: if the ≤2 other-dark vessels
                            # share the same AIS data source, this may be a partial
                            # feed outage rather than selective evasion.
                            _same_source = False
                            _vessel_source = getattr(gap, "source", None)
                            if _other_count > 0 and _vessel_source:
                                try:
                                    _other_sources = [
                                        r.source
                                        for r in _other_dark_gaps.all()
                                        if getattr(r, "source", None)
                                    ]
                                    if _other_sources and all(
                                        s == _vessel_source for s in _other_sources
                                    ):
                                        _same_source = True
                                except Exception as e:
                                    logger.debug(
                                        "Same-source check failed for dark zone evasion: %s", e
                                    )
                            if not _same_source:
                                _selective_evasion = True
                    except Exception as e:
                        logger.debug(
                            "Selective dark zone evasion query failed for vessel %s: %s",
                            vessel.vessel_id,  # noqa: F405
                            e,
                        )

                if _selective_evasion:
                    # Selective dark: only this vessel went dark, others are transmitting
                    breakdown["selective_dark_zone_evasion"] = dz_cfg.get(
                        "selective_dark_zone_evasion", 20
                    )
                else:
                    # Ambient jamming: everyone dark = expected noise
                    breakdown["dark_zone_deduction"] = dz_cfg.get("gap_in_known_jamming_zone", -10)
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

    # Phase 6.2: Gap frequency — compute all applicable tiers, take max.
    # FIX: Previous elif chain checked 3_in_14d (+32) before 4_in_30d (+40),
    # causing vessels with both to get the lower score. Now we evaluate all
    # tiers and select the highest.
    freq_cfg = config.get("gap_frequency", {})
    _freq_candidates: list[tuple[str, int]] = []
    if gaps_in_30d >= 5:
        _freq_candidates.append(("gap_frequency_5_in_30d", freq_cfg.get("5_gaps_in_30d", 50)))
    if gaps_in_30d >= 4:
        _freq_candidates.append(("gap_frequency_4_in_30d", freq_cfg.get("4_gaps_in_30d", 40)))
    if gaps_in_14d >= 3:
        _freq_candidates.append(("gap_frequency_3_in_14d", freq_cfg.get("3_gaps_in_14d", 32)))
    if gaps_in_30d >= 3:
        _freq_candidates.append(("gap_frequency_3_in_30d", freq_cfg.get("3_gaps_in_30d", 25)))
    if gaps_in_7d >= 2:
        _freq_candidates.append(("gap_frequency_2_in_7d", freq_cfg.get("2_gaps_in_7d", 18)))
    if _freq_candidates:
        best_key, best_val = max(_freq_candidates, key=lambda x: x[1])
        breakdown[best_key] = best_val

    # Vessel-level signals
    vessel = gap.vessel

    # Vessel-type filtering (DWT-based effective type)
    # Large vessels (DWT > 5000) are always treated as commercial regardless of
    # AIS-broadcast type. This prevents type-manipulation evasion (100k DWT vessel
    # broadcasting "fishing" would still be scored fully).
    _NON_COMMERCIAL_TYPES = {
        "fishing",
        "pleasure",
        "sailing",
        "tug",
        "pilot",
        "search_rescue",
        "passenger",
    }
    # AIS numeric type codes: 50=pilot, 51=SAR, 52=tug, 53=port tender,
    # 54=anti-pollution, 55=law enforcement, 58=medical transport, 59=gov ship
    # These are stored as "Type 50", "Type 51", etc. in the DB — the keyword
    # set above does not match them, so we need explicit code mapping.
    _NON_COMMERCIAL_AIS_CODES = {
        "type 50",
        "type 51",
        "type 52",
        "type 53",
        "type 54",
        "type 55",
        "type 58",
        "type 59",
    }
    _is_non_commercial = False
    _is_low_risk_flag = False  # Fix 1: gate for data-absence suppression + corridor cap
    _suppress_data_absence = (
        False  # Fix 2: suppress signals that fire on missing data for EU vessels
    )
    _vessel_type_raw = ""  # initialized here so Fix 4 can reference it after the vessel block
    if vessel is not None:
        _vessel_dwt = (
            vessel.deadweight
            if isinstance(getattr(vessel, "deadweight", None), (int, float))
            else None
        )
        _vessel_type_raw = str(vessel.vessel_type or "").lower().strip()
        if _vessel_dwt is not None and _vessel_dwt > 5000:
            # DWT override: always commercial, even if AIS says "fishing"
            _is_non_commercial = False
        elif (
            _vessel_type_raw in _NON_COMMERCIAL_TYPES
            or any(t in _vessel_type_raw for t in _NON_COMMERCIAL_TYPES)
            or _vessel_type_raw in _NON_COMMERCIAL_AIS_CODES
        ):
            _is_non_commercial = True

    if vessel is not None:
        flag_risk = str(
            vessel.flag_risk_category.value
            if hasattr(vessel.flag_risk_category, "value")
            else vessel.flag_risk_category
        )
        # Fix 1: capture flag status for data-absence suppression (Fix 2) and corridor cap (Fix 3)
        _is_low_risk_flag = flag_risk == "low_risk"
        _fp_cfg_suppress = config.get("false_positive_suppression", {})
        _suppress_data_absence = _is_low_risk_flag and _fp_cfg_suppress.get(
            "suppress_data_absence_for_low_risk_flag", True
        )
        flag_cfg = config.get("flag_state", {})

        # Flag state risk
        if flag_risk == "low_risk":
            pts = flag_cfg.get("white_list_flag", -10)
            if pts != 0:
                breakdown["flag_white_list"] = pts
            # Additional false-positive suppression: EU/NATO flag vessels operate under
            # strict maritime oversight — apply extra discount on top of white_list_flag.
            fp_cfg = config.get("false_positive_suppression", {})
            extra_discount = fp_cfg.get("low_risk_flag_extra_discount", -20)
            if extra_discount != 0:
                breakdown["flag_low_risk_extra"] = extra_discount
        elif flag_risk == "high_risk":
            breakdown["flag_high_risk"] = flag_cfg.get("high_risk_registry", 15)

        # Flag + corridor coupling: high-risk flag vessel in geographically suspicious corridor
        if flag_risk == "high_risk" and gap.corridor is not None:
            from app.utils.vessel_identity import RUSSIAN_ORIGIN_FLAGS as _ROF_coupling

            if vessel.flag and vessel.flag.upper() in _ROF_coupling:
                _ct_coupling = str(
                    gap.corridor.corridor_type.value
                    if hasattr(gap.corridor.corridor_type, "value")
                    else gap.corridor.corridor_type
                )
                _tags = getattr(gap.corridor, "tags", None) or ""
                # Cameroon/Palau/etc flag in Baltic or transit corridor = high signal
                if (
                    _ct_coupling in ("export_route", "sts_zone")
                    or "russian_ports" in str(_tags)
                    or "transit" in str(_tags)
                ):
                    coupling_cfg = config.get("sts_patterns", {})
                    breakdown["flag_corridor_coupling"] = coupling_cfg.get(
                        "flag_corridor_coupling", 20
                    )

        # Vessel age — age_25_plus_AND_high_risk_flag supersedes plain age_25_plus_y
        vessel_age_cfg = config.get("vessel_age", {})
        if vessel.year_built is not None:
            age = max(0, current_year - vessel.year_built)
            if age <= 10:
                pts = vessel_age_cfg.get("age_0_to_10y", -5)
                if pts != 0:
                    breakdown["vessel_age_0_10y"] = pts
            elif age <= 15:
                breakdown["vessel_age_10_15y"] = vessel_age_cfg.get("age_10_to_15y", 0)
            elif age <= 20:
                breakdown["vessel_age_15_20y"] = vessel_age_cfg.get("age_15_to_20y", 12)
            elif age <= 25:
                breakdown["vessel_age_20_25y"] = vessel_age_cfg.get("age_20_to_25y", 10)
            else:
                if flag_risk == "high_risk":
                    breakdown["vessel_age_25plus_high_risk"] = vessel_age_cfg.get(
                        "age_25_plus_AND_high_risk_flag", 30
                    )
                else:
                    breakdown["vessel_age_25plus"] = vessel_age_cfg.get("age_25_plus_y", 20)

        # Phase 6.11: AIS class mismatch: large tanker (DWT > 3 000t) using Class B
        # SOLAS requires Class A transponders for vessels > 300 GT (~500 GT ≈ 3 000 DWT for tankers)
        ais_cls = str(
            vessel.ais_class.value if hasattr(vessel.ais_class, "value") else vessel.ais_class
        )
        if ais_cls == "B" and vessel.deadweight is not None and vessel.deadweight > 3_000:
            ais_cfg = config.get("ais_class", {})
            breakdown["ais_class_mismatch"] = ais_cfg.get("large_tanker_using_class_b", 25)

        # Invalid AIS metadata: generic names or impossible DWT values
        metadata_signals_cfg = config.get("sts_patterns", {})
        metadata_cfg = config.get("metadata", {})
        _vessel_name = str(vessel.name or "").strip().upper()
        _GENERIC_NAMES = {
            "TANKER",
            "VESSEL",
            "UNKNOWN",
            "SHIP",
            "BOAT",
            "TBN",
            "TBA",
            "N/A",
            "TEST",
        }
        if not _vessel_name:
            # Completely unnamed vessel with MMSI — highly suspicious
            breakdown["no_name_at_all"] = metadata_cfg.get("no_name_at_all", 20)
        elif _vessel_name in _GENERIC_NAMES or (len(_vessel_name) == 1 and _vessel_name.isalpha()):
            breakdown["invalid_metadata_generic_name"] = metadata_signals_cfg.get(
                "invalid_metadata_generic_name", 15
            )
        else:
            # Pattern: "TANKER 001", "SHIP 22" — all-caps + digits (C4ADS "Unmasked" pattern)
            import re as _re

            if _re.match(r"^[A-Z]+(?: [A-Z0-9]+)* \d+$", _vessel_name):
                breakdown["name_all_caps_numbers"] = metadata_cfg.get("name_all_caps_numbers", 10)
        if vessel.deadweight is not None and isinstance(vessel.deadweight, (int, float)):
            _vessel_type_str = str(vessel.vessel_type or "").lower()
            if vessel.deadweight > 500_000 or (vessel.deadweight < 100 and "tanker" in _vessel_type_str):
                breakdown["invalid_metadata_impossible_dwt"] = metadata_signals_cfg.get(
                    "invalid_metadata_impossible_dwt", 15
                )

        # P&I insurance coverage scoring (PRD: 82% shadow fleet lacks reputable P&I)
        pi_status = str(
            vessel.pi_coverage_status.value
            if hasattr(vessel.pi_coverage_status, "value")
            else vessel.pi_coverage_status
        )
        pi_cfg = config.get("pi_insurance", {})
        if pi_status == "lapsed":
            breakdown["pi_coverage_lapsed"] = pi_cfg.get("pi_coverage_lapsed", 20)
        # pi_coverage_unknown removed — duplicated by pi_validation.unknown_insurer: 25

        # PSC detention scoring
        psc_cfg = config.get("psc_detention", {})
        if vessel.psc_detained_last_12m:
            breakdown["psc_detained_last_12m"] = psc_cfg.get("psc_detained_last_12m", 15)
        if (
            isinstance(vessel.psc_major_deficiencies_last_12m, int)
            and vessel.psc_major_deficiencies_last_12m >= 3
        ):
            breakdown["psc_major_deficiencies_3_plus"] = psc_cfg.get(
                "psc_major_deficiencies_3_plus", 10
            )

    # Enhanced PSC scoring from detention records
    if db is not None and vessel is not None:
        from app.models.psc_detention import PscDetention as _PscDet

        _now_psc = datetime.now(UTC)
        _psc_weights = config.get("psc_detention", {})

        detentions_24m = (
            db.query(_PscDet)
            .filter(
                _PscDet.vessel_id == vessel.vessel_id,
                _PscDet.detention_date >= (_now_psc - timedelta(days=730)).date(),
            )
            .all()
        )

        if len(detentions_24m) >= 3:
            breakdown["psc_multiple_detentions_3_plus"] = _psc_weights.get(
                "multiple_detentions_3_plus", 20
            )
        elif len(detentions_24m) >= 2:
            breakdown["psc_multiple_detentions_2"] = _psc_weights.get("multiple_detentions_2", 10)

        # Recency signals
        _recent_90d = any(
            d.detention_date >= (_now_psc - timedelta(days=90)).date() for d in detentions_24m
        )
        _recent_30d = any(
            d.detention_date >= (_now_psc - timedelta(days=30)).date() for d in detentions_24m
        )

        if _recent_30d:
            breakdown["psc_detention_in_last_30d"] = _psc_weights.get("detention_in_last_30d", 15)
        elif _recent_90d:
            breakdown["psc_detention_in_last_90d"] = _psc_weights.get("detention_in_last_90d", 10)

        # Paris MOU ban
        if any(d.ban_type for d in detentions_24m):
            breakdown["psc_paris_mou_ban"] = _psc_weights.get("paris_mou_ban", 15)

        # High deficiency count
        _total_deficiencies = sum(d.deficiency_count or 0 for d in detentions_24m)
        if _total_deficiencies >= 10:
            breakdown["psc_deficiency_count_10_plus"] = _psc_weights.get(
                "deficiency_count_10_plus", 8
            )

    # class_switching_a_to_b: query VesselHistory for ais_class changes within 90d
    if db is not None and vessel is not None:
        from app.models.vessel_history import VesselHistory as _VH

        ais_class_changes = (
            db.query(_VH)
            .filter(
                _VH.vessel_id == vessel.vessel_id,
                _VH.field_changed == "ais_class",
                _VH.observed_at >= gap.gap_start_utc - timedelta(days=90),
            )
            .all()
        )
        for ch in ais_class_changes:
            old_cls = (ch.old_value or "").strip().upper()
            new_cls = (ch.new_value or "").strip().upper()
            if old_cls == "A" and new_cls == "B":
                ais_cfg = config.get("ais_class", {})
                breakdown["class_switching_a_to_b"] = ais_cfg.get("class_switching_a_to_b", 25)
                break

    # callsign_change: query VesselHistory for callsign changes within 90d
    if db is not None and vessel is not None:
        from app.models.vessel_history import VesselHistory as _VH2

        callsign_changes = (
            db.query(_VH2)
            .filter(
                _VH2.vessel_id == vessel.vessel_id,
                _VH2.field_changed == "callsign",
                _VH2.observed_at >= gap.gap_start_utc - timedelta(days=90),
            )
            .first()
        )
        if callsign_changes:
            meta_cfg = config.get("metadata", {})
            breakdown["callsign_change"] = meta_cfg.get("callsign_change", 20)

    # Owner sanctions check (v1.1 — VesselOwner model now exists)
    if db is not None and vessel is not None:
        from app.models.vessel_owner import VesselOwner

        sanctioned_owner = (
            db.query(VesselOwner)
            .filter(
                VesselOwner.vessel_id == vessel.vessel_id,
                VesselOwner.is_sanctioned,
            )
            .first()
        )
        if sanctioned_owner:
            watchlist_cfg = config.get("watchlist", {})
            breakdown["owner_or_manager_on_sanctions_list"] = watchlist_cfg.get(
                "owner_or_manager_on_sanctions_list", 35
            )

    # Shadow-mode settings import (used for Phase K/L/M/N scoring gates)
    from app.config import settings as _scoring_settings

    # Phase 6.4: Spoofing signals (only linked to this gap or vessel-level within 2h of gap start)
    if db is not None:
        from sqlalchemy import and_, or_

        from app.models.spoofing_anomaly import SpoofingAnomaly

        vessel_spoofing = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == gap.vessel_id,
                or_(
                    SpoofingAnomaly.gap_event_id == gap.gap_event_id,
                    and_(
                        SpoofingAnomaly.gap_event_id == None,  # noqa: E711
                        SpoofingAnomaly.end_time_utc >= gap.gap_start_utc - timedelta(hours=2),
                        SpoofingAnomaly.start_time_utc <= gap.gap_start_utc,
                    ),
                ),
            )
            .all()
        )
        # Erratic nav_status cap: take the single highest score from all erratic anomalies
        # (prevents multiplication from a continuous oscillation episode creating many records)
        erratic_anomalies = [
            s
            for s in vessel_spoofing
            if str(s.anomaly_type.value if hasattr(s.anomaly_type, "value") else s.anomaly_type)
            == "erratic_nav_status"
        ]
        # Shadow-mode exclusion: new anomaly types excluded from scoring
        # when their *_SCORING_ENABLED flag is False (detection creates records
        # but they don't contribute to risk scores).
        _shadow_excluded_types: set[str] = set()
        if not _scoring_settings.TRACK_NATURALNESS_SCORING_ENABLED:
            _shadow_excluded_types.add("synthetic_track")
        if not _scoring_settings.STATELESS_MMSI_SCORING_ENABLED:
            _shadow_excluded_types.add("stateless_mmsi")
        if not _scoring_settings.FLAG_HOPPING_SCORING_ENABLED:
            _shadow_excluded_types.add("flag_hopping")
        if not _scoring_settings.IMO_FRAUD_SCORING_ENABLED:
            _shadow_excluded_types.add("imo_fraud")
        if not _scoring_settings.STALE_AIS_SCORING_ENABLED:
            _shadow_excluded_types.add("stale_ais_data")
        if not _scoring_settings.DESTINATION_SCORING_ENABLED:
            _shadow_excluded_types.add("destination_deviation")
        if not _scoring_settings.TRACK_REPLAY_SCORING_ENABLED:
            _shadow_excluded_types.add("track_replay")
        if not _scoring_settings.ROUTE_LAUNDERING_SCORING_ENABLED:
            _shadow_excluded_types.add("route_laundering")
        if not _scoring_settings.PI_CYCLING_SCORING_ENABLED:
            _shadow_excluded_types.add("pi_cycling")
        if not _scoring_settings.SPARSE_TRANSMISSION_SCORING_ENABLED:
            _shadow_excluded_types.add("sparse_transmission")
        if not _scoring_settings.TYPE_CONSISTENCY_SCORING_ENABLED:
            _shadow_excluded_types.add("type_dwt_mismatch")

        def _type_val(s):
            return str(s.anomaly_type.value if hasattr(s.anomaly_type, "value") else s.anomaly_type)

        non_erratic = [
            s
            for s in vessel_spoofing
            if _type_val(s) != "erratic_nav_status" and _type_val(s) not in _shadow_excluded_types
        ]
        if erratic_anomalies:
            breakdown["spoofing_erratic_nav_status"] = max(
                s.risk_score_component for s in erratic_anomalies
            )
        # Collapse duplicate anomaly records of the same type to their max score.
        # GFW bulk import can create many SpoofingAnomaly records for the same event
        # (e.g., one MMSI_REUSE entry per AIS-record comparison). Counting each record
        # independently multiplies the signal N× — same treatment as erratic_nav_status.
        _type_max: dict[str, int] = {}
        for s in non_erratic:
            t = _type_val(s)
            _type_max[t] = max(_type_max.get(t, 0), s.risk_score_component)
        for t, score in _type_max.items():
            breakdown[f"spoofing_{t}"] = score

    # Phase 6.5: Loitering signal integration
    if db is not None:
        from app.models.corridor import Corridor
        from app.models.loitering_event import LoiteringEvent

        loitering = (
            db.query(LoiteringEvent)
            .filter(
                LoiteringEvent.vessel_id == gap.vessel_id,
                LoiteringEvent.start_time_utc >= gap.gap_start_utc - timedelta(hours=48),
                LoiteringEvent.end_time_utc <= gap.gap_end_utc + timedelta(hours=48),
            )
            .all()
        )
        sts_cfg = config.get("sts", {})
        for le in loitering:
            loiter_key = f"loitering_{le.loiter_id}"

            # Check loiter-gap-loiter patterns first (subsumes duration signals)
            has_lgp = False
            if le.preceding_gap_id and le.following_gap_id:
                breakdown[f"loiter_gap_loiter_full_{le.loiter_id}"] = sts_cfg.get(
                    "loiter_gap_loiter_full_cycle", 25
                )
                has_lgp = True
            elif le.preceding_gap_id or le.following_gap_id:
                breakdown[f"loiter_gap_pattern_{le.loiter_id}"] = sts_cfg.get(
                    "loiter_gap_loiter_pattern_48h_window", 15
                )
                has_lgp = True

            # Duration-based signal only if no loiter-gap-loiter pattern fired
            if not has_lgp:
                if le.duration_hours >= 12 and le.corridor_id:
                    # Check corridor type: +20 only in STS zones, +8 in other corridors
                    loiter_corridor = db.query(Corridor).get(le.corridor_id)
                    _lc_type = str(
                        loiter_corridor.corridor_type.value
                        if loiter_corridor and hasattr(loiter_corridor.corridor_type, "value")
                        else (loiter_corridor.corridor_type if loiter_corridor else "")
                    )
                    if _lc_type == "sts_zone":
                        breakdown[loiter_key] = sts_cfg.get(
                            "loitering_12h_plus_in_sts_corridor", 20
                        )
                    else:
                        breakdown[loiter_key] = sts_cfg.get("loitering_4h_plus_in_corridor", 8)
                elif le.duration_hours >= 4 and le.corridor_id:
                    breakdown[loiter_key] = sts_cfg.get("loitering_4h_plus_in_corridor", 8)

        # Laid-up vessel scoring
        behavioral_cfg = config.get("behavioral", {})
        if vessel is not None:
            if getattr(vessel, "vessel_laid_up_in_sts_zone", False):
                breakdown["vessel_laid_up_in_sts_zone"] = behavioral_cfg.get(
                    "vessel_laid_up_in_sts_zone", 30
                )
            elif getattr(vessel, "vessel_laid_up_60d", False):
                breakdown["vessel_laid_up_60d"] = behavioral_cfg.get("vessel_laid_up_60d_plus", 25)
            elif getattr(vessel, "vessel_laid_up_30d", False):
                breakdown["vessel_laid_up_30d"] = behavioral_cfg.get("vessel_laid_up_30d_plus", 15)

    # Phase 6.6: STS transfer signal integration
    # Dedup: in a 3+ vessel cluster, pairwise events create redundant records per vessel.
    # Take max(risk_score_component) across all STS events for this gap to prevent 2×-3× inflation.
    if db is not None:
        from sqlalchemy import or_

        from app.models.sts_transfer import StsTransferEvent

        sts_events = (
            db.query(StsTransferEvent)
            .filter(
                or_(
                    StsTransferEvent.vessel_1_id == gap.vessel_id,
                    StsTransferEvent.vessel_2_id == gap.vessel_id,
                ),
                StsTransferEvent.start_time_utc >= gap.gap_start_utc - timedelta(days=7),
                StsTransferEvent.end_time_utc <= gap.gap_end_utc + timedelta(days=7),
            )
            .all()
        )
        # Shadow-mode: exclude dark_dark STS events when scoring is disabled
        if not _scoring_settings.DARK_STS_SCORING_ENABLED:
            sts_events = [
                e
                for e in sts_events
                if str(getattr(e.detection_type, "value", e.detection_type)) != "dark_dark"
            ]
        if sts_events:
            sts_cfg = config.get("sts", {})
            best_sts_score = 0
            best_sts = None
            for sts in sts_events:
                base = sts.risk_score_component
                # Bonus for dark-partner STS (one vessel had no AIS during proximity)
                det_type = getattr(sts, "detection_type", None)
                if det_type is not None:
                    dt_val = det_type.value if hasattr(det_type, "value") else str(det_type)
                    if dt_val in ("visible_dark", "dark_dark"):
                        base += sts_cfg.get("one_vessel_dark_during_proximity", 15)
                if base > best_sts_score:
                    best_sts_score = base
                    best_sts = sts
            if best_sts:
                breakdown[f"sts_event_{best_sts.sts_id}"] = best_sts_score

    # Phase: Repeat STS partnerships — same vessel pair doing STS 3+ times
    if db is not None and vessel is not None:
        from sqlalchemy import or_ as _or_repeat

        from app.models.sts_transfer import StsTransferEvent as _STS_repeat

        # Get all STS events for this vessel
        all_sts = (
            db.query(_STS_repeat)
            .filter(
                _or_repeat(
                    _STS_repeat.vessel_1_id == vessel.vessel_id,
                    _STS_repeat.vessel_2_id == vessel.vessel_id,
                )
            )
            .all()
        )
        # Count events per partner
        partner_counts: dict[int, int] = {}
        for sts in all_sts:
            partner = sts.vessel_2_id if sts.vessel_1_id == vessel.vessel_id else sts.vessel_1_id
            partner_counts[partner] = partner_counts.get(partner, 0) + 1
        # Flag if any partner has 3+ events
        repeat_partners = {pid: cnt for pid, cnt in partner_counts.items() if cnt >= 3}
        if repeat_partners:
            sts_pattern_cfg = config.get("sts_patterns", {})
            breakdown["repeat_sts_partnership"] = sts_pattern_cfg.get(
                "repeat_sts_partnership_3plus", 30
            )

    # Phase 6.7: Watchlist scoring (all weights from YAML)
    # Uses module-level _WATCHLIST_KEY_MAP and _WATCHLIST_DEFAULTS (shared with score_watchlist_stubs)
    if db is not None and vessel is not None:
        from app.models.vessel_watchlist import VesselWatchlist

        watchlist_cfg = config.get("watchlist", {})
        watchlist = (
            db.query(VesselWatchlist)
            .filter(
                VesselWatchlist.vessel_id == vessel.vessel_id,
                VesselWatchlist.is_active,
            )
            .all()
        )
        for w in watchlist:
            yaml_key = _WATCHLIST_KEY_MAP.get(w.watchlist_source)
            if yaml_key:
                score_val = watchlist_cfg.get(
                    yaml_key, _WATCHLIST_DEFAULTS.get(w.watchlist_source, 20)
                )
            else:
                score_val = 20  # fallback for unknown sources
            breakdown[f"watchlist_{w.watchlist_source}"] = score_val

    # Phase 6.8: Vessel identity changes scoring
    if db is not None and vessel is not None:
        from app.models.vessel_history import VesselHistory

        identity_changes = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.observed_at >= gap.gap_start_utc - timedelta(days=90),
            )
            .all()
        )
        meta_cfg = config.get("metadata", {})
        flag_changes = [h for h in identity_changes if h.field_changed == "flag"]
        name_changes = [h for h in identity_changes if h.field_changed == "name"]

        # flag+name change within 48h (highest priority flag signal — checked first)
        for fc in flag_changes:
            for nc in name_changes:
                diff_h = abs((fc.observed_at - nc.observed_at).total_seconds()) / 3600
                if diff_h <= 48:
                    breakdown["flag_and_name_change_48h"] = meta_cfg.get(
                        "flag_AND_name_change_within_48h", 30
                    )
                    break

        # Flag change subsumption hierarchy: 7d (+35) supersedes 30d (+25) — only one fires
        recent_7d_flag = [h for h in flag_changes if (gap.gap_start_utc - h.observed_at).days <= 7]
        recent_30d_flag = [
            h for h in flag_changes if (gap.gap_start_utc - h.observed_at).days <= 30
        ]
        if "flag_and_name_change_48h" not in breakdown:
            if recent_7d_flag:
                breakdown["flag_change_7d"] = meta_cfg.get("flag_change_in_last_7d", 35)
            elif recent_30d_flag:
                breakdown["flag_change_30d"] = meta_cfg.get("flag_change_in_last_30d", 25)

        # Single flag change in 12m (lower tier — only fires when no 7d/30d signal triggered)
        if (
            "flag_and_name_change_48h" not in breakdown
            and "flag_change_7d" not in breakdown
            and "flag_change_30d" not in breakdown
        ):
            all_12m_flags = [
                h for h in flag_changes if (gap.gap_start_utc - h.observed_at).days <= 365
            ]
            if len(all_12m_flags) >= 1:
                pts = meta_cfg.get("single_flag_change_last_12m", 15)
                if pts > 0:
                    breakdown["flag_change_single_12m"] = pts

        # 3+ flag changes in 90d (stacks with single-change signals — different severity)
        if len(flag_changes) >= 3:
            breakdown["flag_changes_3plus_90d"] = meta_cfg.get("3_plus_flag_changes_in_90d", 40)

        # Re-flagging countermeasure: flag changed from HIGH_RISK to LOW/MEDIUM in last 12m
        from app.utils.vessel_identity import RUSSIAN_ORIGIN_FLAGS as _ROF

        recent_12m_flag = [
            h for h in flag_changes if (gap.gap_start_utc - h.observed_at).days <= 365
        ]
        for fc in recent_12m_flag:
            old_flag = (fc.old_value or "").strip().upper()
            new_flag = (fc.new_value or "").strip().upper()
            if old_flag in _ROF and new_flag not in _ROF:
                breakdown["flag_change_high_to_low_12m"] = meta_cfg.get(
                    "flag_change_from_high_risk_to_low_risk_12m", 20
                )
                break

        # name_change_during_active_voyage: check if change occurred during active voyage
        # Use port departure as voyage start if available; else fall back to 30d window
        # (wider than original 7d to capture longer voyages; dry-dock renames are still
        # filtered because vessels in dry-dock won't have AIS gaps triggering scoring)
        _voyage_window_days = 30
        if db is not None:
            try:
                from app.models.port_call import PortCall as _PC_name

                last_departure = (
                    db.query(_PC_name)
                    .filter(
                        _PC_name.vessel_id == vessel.vessel_id,
                        _PC_name.departure_utc is not None,
                        _PC_name.departure_utc <= gap.gap_start_utc,
                    )
                    .order_by(_PC_name.departure_utc.desc())
                    .first()
                )
                if last_departure and isinstance(
                    getattr(last_departure, "departure_utc", None), datetime
                ):
                    _voyage_window_days = max(
                        1, (gap.gap_start_utc - last_departure.departure_utc).days
                    )
            except Exception as e:
                logger.warning("Port call voyage window calculation failed: %s", e)
                _voyage_window_days = 30  # documented default
                breakdown["_voyage_window_fallback"] = "default_30d_used"
        recent_name_changes = [
            h
            for h in name_changes
            if (gap.gap_start_utc - h.observed_at).days <= _voyage_window_days
        ]
        if recent_name_changes and "flag_and_name_change_48h" not in breakdown:
            breakdown["name_change_during_voyage"] = meta_cfg.get(
                "name_change_during_active_voyage", 30
            )

        # mmsi_change_mapped_same_position: +45 (same position) or +20 (different position)
        # PRD: +45 is specifically for MMSI changes where vessel position didn't move,
        # indicating same physical ship changed identity. Position shift → lower score.
        mmsi_changes = [h for h in identity_changes if h.field_changed == "mmsi"]
        if mmsi_changes:
            _mmsi_same_position = False
            try:
                from app.models.ais_point import AISPoint as _AP_mmsi
                from app.utils.geo import haversine_nm as _hav_mmsi

                for mc in mmsi_changes:
                    # Query AIS points within ±6h of the change to check position stability
                    before_pt = (
                        db.query(_AP_mmsi)
                        .filter(
                            _AP_mmsi.vessel_id == vessel.vessel_id,
                            _AP_mmsi.timestamp_utc <= mc.observed_at,
                            _AP_mmsi.timestamp_utc >= mc.observed_at - timedelta(hours=6),
                        )
                        .order_by(_AP_mmsi.timestamp_utc.desc())
                        .first()
                    )
                    after_pt = (
                        db.query(_AP_mmsi)
                        .filter(
                            _AP_mmsi.vessel_id == vessel.vessel_id,
                            _AP_mmsi.timestamp_utc >= mc.observed_at,
                            _AP_mmsi.timestamp_utc <= mc.observed_at + timedelta(hours=6),
                        )
                        .order_by(_AP_mmsi.timestamp_utc.asc())
                        .first()
                    )
                    if (
                        before_pt
                        and after_pt
                        and isinstance(getattr(before_pt, "lat", None), (int, float))
                        and isinstance(getattr(after_pt, "lat", None), (int, float))
                    ):
                        dist_nm = _hav_mmsi(
                            before_pt.lat, before_pt.lon, after_pt.lat, after_pt.lon
                        )
                        if dist_nm <= 5.0:
                            _mmsi_same_position = True
                            break
                    else:
                        # Can't verify position — assume same position (conservative)
                        _mmsi_same_position = True
                        break
            except Exception as e:
                logger.warning(
                    "Dark zone evasion scoring failed for vessel %s: %s", vessel.vessel_id, e
                )
                _mmsi_same_position = False  # Fall back to 0 — don't assign wrong +45 score
            if _mmsi_same_position:
                breakdown["mmsi_change"] = meta_cfg.get("mmsi_change_mapped_same_position", 45)
            else:
                breakdown["mmsi_change_different_position"] = 20

    # Phase 6.9: Legitimacy signals
    if db is not None and vessel is not None:
        # gap_free_90d_clean: no gaps in last 90 days
        # Skip for HIGH_RISK flag vessels — a single 4h gap + 90d clean shouldn't wash away flag risk
        from app.models.gap_event import AISGapEvent as _AISGapEvent

        recent_gaps = (
            db.query(_AISGapEvent)
            .filter(
                _AISGapEvent.vessel_id == vessel.vessel_id,
                _AISGapEvent.gap_start_utc >= gap.gap_start_utc - timedelta(days=90),
                _AISGapEvent.gap_event_id != gap.gap_event_id,
            )
            .count()
        )
        _vessel_flag_risk = (
            str(
                vessel.flag_risk_category.value
                if hasattr(vessel.flag_risk_category, "value")
                else vessel.flag_risk_category
            )
            if vessel.flag_risk_category
            else ""
        )
        if recent_gaps == 0 and _vessel_flag_risk != "high_risk":
            legitimacy_cfg = config.get("legitimacy", {})
            breakdown["legitimacy_gap_free_90d"] = legitimacy_cfg.get("gap_free_90d_clean", -10)

        # ais_class_a_consistent: all points are Class A
        from app.models.ais_point import AISPoint

        non_a = (
            db.query(AISPoint)
            .filter(
                AISPoint.vessel_id == vessel.vessel_id,
                AISPoint.ais_class != "A",
            )
            .first()
        )
        if non_a is None:
            legitimacy_cfg = config.get("legitimacy", {})
            breakdown["legitimacy_ais_class_a_consistent"] = legitimacy_cfg.get(
                "ais_class_a_consistent", -5
            )

        # white_flag_jurisdiction: white-list flag registries (PRD §7.5)
        _WHITE_FLAGS = {"NO", "DK", "DE", "JP", "NL"}
        if vessel.flag and vessel.flag.upper() in _WHITE_FLAGS:
            legitimacy_cfg = config.get("legitimacy", {})
            breakdown["legitimacy_white_flag_jurisdiction"] = legitimacy_cfg.get(
                "white_flag_jurisdiction", -10
            )

    # EU port call legitimacy signal (v1.1 — PortCall model now exists)
    if db is not None and vessel is not None:
        from app.models.port import Port
        from app.models.port_call import PortCall

        eu_calls = (
            db.query(PortCall)
            .join(Port, PortCall.port_id == Port.port_id)
            .filter(
                PortCall.vessel_id == vessel.vessel_id,
                Port.is_eu,
            )
            .count()
        )
        if isinstance(eu_calls, int) and eu_calls > 0:
            legitimacy_cfg = config.get("legitimacy", {})
            per_call = legitimacy_cfg.get("consistent_eu_port_calls", -5)
            breakdown["legitimacy_eu_port_calls"] = per_call * min(eu_calls, 3)  # cap at 3 calls

    # PSC clean record legitimacy: 0 detentions in last 3 years
    if db is not None and vessel is not None:
        legitimacy_cfg = config.get("legitimacy", {})
        if not getattr(vessel, "psc_detained_last_12m", False):
            # Check VesselHistory for any PSC detention in 3 years
            try:
                from app.models.vessel_history import VesselHistory as _VH_psc

                psc_detentions = (
                    db.query(_VH_psc)
                    .filter(
                        _VH_psc.vessel_id == vessel.vessel_id,
                        _VH_psc.field_changed == "psc_detained",
                        _VH_psc.observed_at >= gap.gap_start_utc - timedelta(days=1095),
                    )
                    .count()
                )
                if psc_detentions == 0:
                    breakdown["legitimacy_psc_clean_record"] = legitimacy_cfg.get(
                        "psc_clean_record", -10
                    )
            except Exception as e:
                logger.debug(
                    "PSC clean record scoring failed for vessel %s: %s", vessel.vessel_id, e
                )

    # IG P&I club member legitimacy: insured by International Group club
    if db is not None and vessel is not None:
        legitimacy_cfg = config.get("legitimacy", {})
        try:
            from app.models.vessel_owner import VesselOwner as _VO_pi_legit

            pi_owner = (
                db.query(_VO_pi_legit).filter(_VO_pi_legit.vessel_id == vessel.vessel_id).first()
            )
            pi_club = pi_owner.pi_club_name if pi_owner else None
            if pi_club and isinstance(pi_club, str) and pi_club.strip():
                pi_clubs_data = _load_pi_clubs_config()
                ig_clubs = pi_clubs_data.get("ig_member_clubs", [])
                # Build set of IG club names (full + short forms)
                ig_names: set[str] = set()
                for club in ig_clubs:
                    if isinstance(club, dict):
                        ig_names.add(club.get("name", "").lower())
                        ig_names.add(club.get("short", "").lower())
                    elif isinstance(club, str):
                        ig_names.add(club.lower())
                if pi_club.strip().lower() in ig_names:
                    breakdown["legitimacy_ig_pi_club_member"] = legitimacy_cfg.get(
                        "ig_pi_club_member", -15
                    )
        except Exception as e:
            logger.debug("IG P&I club scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # Long trading history legitimacy: >10 years continuous AIS history
    if db is not None and vessel is not None:
        legitimacy_cfg = config.get("legitimacy", {})
        _created = getattr(vessel, "created_at", None)
        if isinstance(_created, datetime):
            _created_naive = _created.replace(tzinfo=None) if _created.tzinfo else _created
            _history_years = (scoring_date - _created_naive).days / 365.25
            if _history_years > 10:
                breakdown["legitimacy_long_trading_history"] = legitimacy_cfg.get(
                    "long_trading_history", -8
                )

    # flag_less_than_2y_old_AND_high_risk: vessel adopted current flag < 2 years ago
    # AND the flag is high_risk. Query VesselHistory for most recent flag change.
    if db is not None and vessel is not None:
        _flag_risk_str = (
            str(
                vessel.flag_risk_category.value
                if hasattr(vessel.flag_risk_category, "value")
                else vessel.flag_risk_category
            )
            if vessel.flag_risk_category
            else ""
        )
        if _flag_risk_str == "high_risk":
            from app.models.vessel_history import VesselHistory as _VH_flag2y

            latest_flag_change = (
                db.query(_VH_flag2y)
                .filter(
                    _VH_flag2y.vessel_id == vessel.vessel_id,
                    _VH_flag2y.field_changed == "flag",
                )
                .order_by(_VH_flag2y.observed_at.desc())
                .first()
            )
            if latest_flag_change and latest_flag_change.observed_at:
                _flag_change_dt = latest_flag_change.observed_at
                if _flag_change_dt.tzinfo:
                    _flag_change_dt = _flag_change_dt.replace(tzinfo=None)
                _flag_age_days = (scoring_date - _flag_change_dt).days
                if 0 <= _flag_age_days < 730:
                    flag_cfg = config.get("flag_state", {})
                    breakdown["flag_less_than_2y_AND_high_risk"] = flag_cfg.get(
                        "flag_less_than_2y_old_AND_high_risk", 20
                    )

    # Transmission frequency mismatch: Class A vessel transmitting at Class B intervals
    # Class A should transmit every 2-10s; if median interval > 25s, flag it
    if db is not None and vessel is not None:
        ais_cls_str = str(
            vessel.ais_class.value if hasattr(vessel.ais_class, "value") else vessel.ais_class
        )
        if ais_cls_str == "A":
            from app.models.ais_point import AISPoint as _AP2

            recent_points = (
                db.query(_AP2)
                .filter(
                    _AP2.vessel_id == vessel.vessel_id,
                    _AP2.timestamp_utc >= gap.gap_start_utc - timedelta(hours=24),
                    _AP2.timestamp_utc <= gap.gap_start_utc,
                )
                .order_by(_AP2.timestamp_utc.asc())
                .all()
            )
            if len(recent_points) >= 3:
                intervals = [
                    (
                        recent_points[i + 1].timestamp_utc - recent_points[i].timestamp_utc
                    ).total_seconds()
                    for i in range(len(recent_points) - 1)
                ]
                median_interval = _stats.median(intervals)
                if median_interval > 25:
                    ais_cfg = config.get("ais_class", {})
                    breakdown["transmission_frequency_mismatch"] = ais_cfg.get(
                        "transmission_frequency_mismatch", 8
                    )

    # Phase 6.10: New MMSI scoring
    if vessel is not None:
        mmsi_first_seen = getattr(vessel, "mmsi_first_seen_utc", None)
        if isinstance(mmsi_first_seen, datetime):
            try:
                fs = (
                    mmsi_first_seen.replace(tzinfo=None)
                    if mmsi_first_seen.tzinfo
                    else mmsi_first_seen
                )
                mmsi_age_days = (scoring_date - fs).days
            except Exception as e:
                logger.debug("MMSI age calculation failed: %s", e)
                mmsi_age_days = 9999
            behavioral_cfg = config.get("behavioral", {})
            if mmsi_age_days < 30 and not _suppress_data_absence:
                # Fix 2a: GFW bulk import sets mmsi_first_seen_utc ≈ import date for all vessels,
                # making every EU vessel appear "new". Suppress for low-risk flags.
                breakdown["new_mmsi_first_30d"] = behavioral_cfg.get("new_mmsi_first_30d", 15)
                from app.utils.vessel_identity import RUSSIAN_ORIGIN_FLAGS

                if vessel.flag and vessel.flag.upper() in RUSSIAN_ORIGIN_FLAGS:
                    breakdown["new_mmsi_russian_origin_flag"] = behavioral_cfg.get(
                        "new_mmsi_plus_russian_origin_zone", 25
                    )
            elif mmsi_age_days < 60:
                breakdown["new_mmsi_first_60d"] = behavioral_cfg.get("new_mmsi_first_60d", 8)

    # Suspicious MID: unallocated or known-stateless MMSI
    if vessel is not None and vessel.mmsi:
        from app.utils.vessel_identity import is_suspicious_mid

        if is_suspicious_mid(vessel.mmsi):
            behavioral_cfg = config.get("behavioral", {})
            breakdown["suspicious_mid"] = behavioral_cfg.get("suspicious_mid", 25)

    # Russian port call composite signal (highest-value shadow fleet indicator)
    if db is not None and vessel is not None:
        russian_port = _had_russian_port_call(db, vessel, gap.gap_start_utc)
        if russian_port:
            # Check if gap is also in an STS corridor (composite signal)
            _in_sts = False
            if gap.corridor is not None:
                _ct = str(
                    gap.corridor.corridor_type.value
                    if hasattr(gap.corridor.corridor_type, "value")
                    else gap.corridor.corridor_type
                )
                _in_sts = _ct == "sts_zone"
            behavioral_cfg = config.get("behavioral", {})
            if _in_sts:
                # Full composite: Russian port → gap → STS zone
                breakdown["russian_port_gap_sts"] = behavioral_cfg.get("russian_port_gap_sts", 40)
            else:
                breakdown["russian_port_recent"] = behavioral_cfg.get("russian_port_recent", 25)

    # Voyage pattern cycles: Russian port + STS + repeated gaps suggests trade cycle
    if db is not None and vessel is not None:
        _has_russian_port = (
            "russian_port_recent" in breakdown or "russian_port_gap_sts" in breakdown
        )
        _has_sts = any(k.startswith("sts_event_") for k in breakdown)
        _has_freq_gaps = any(k.startswith("gap_frequency_") for k in breakdown)
        if _has_russian_port and _has_sts and _has_freq_gaps:
            voyage_cfg = config.get("sts_patterns", {})
            breakdown["voyage_cycle_pattern"] = voyage_cfg.get("voyage_cycle_pattern", 30)

    # STS network association: guilt-by-association with watchlisted partners
    if db is not None and vessel is not None:
        sts_assoc_pts, sts_assoc_source = _sts_with_watchlisted_vessel(db, vessel)
        if sts_assoc_pts > 0:
            _SANCTIONED_SOURCES = {"OFAC_SDN", "EU_COUNCIL"}
            if sts_assoc_source in _SANCTIONED_SOURCES:
                breakdown["sts_with_sanctioned_vessel"] = sts_assoc_pts
            else:
                breakdown["sts_with_shadow_fleet_vessel"] = sts_assoc_pts

    # Phase 6.12: Dark vessel detection signal
    # FIX: Use spatial+temporal proximity instead of matched_vessel_id (which is NULL
    # for unmatched detections, making the old query always return 0 rows).
    if db is not None and gap.vessel_id is not None:
        from app.models.stubs import DarkVesselDetection
        from app.utils.geo import haversine_nm as _dv_haversine

        # Query unmatched dark detections within the gap's time window (±6h buffer)
        candidate_detections = (
            db.query(DarkVesselDetection)
            .filter(
                DarkVesselDetection.ais_match_result == "unmatched",
                DarkVesselDetection.detection_time_utc.between(
                    gap.gap_start_utc - timedelta(hours=6),
                    gap.gap_end_utc + timedelta(hours=6),
                ),
            )
            .all()
        )

        # Filter by spatial proximity: detection within gap's plausible area
        # Use gap off/on positions, start/end AIS points, or corridor match
        gap_lat = gap_lon = None
        if hasattr(gap, "gap_off_lat") and gap.gap_off_lat is not None:
            gap_lat, gap_lon = gap.gap_off_lat, gap.gap_off_lon
        elif gap.start_point is not None:
            gap_lat, gap_lon = gap.start_point.lat, gap.start_point.lon

        max_radius = gap.max_plausible_distance_nm or 200.0  # fallback

        dark_detections = []
        for det in candidate_detections:
            if det.detection_lat is None or det.detection_lon is None:
                continue
            # Match by corridor if both have one
            if det.corridor_id is not None and gap.corridor_id is not None:  # noqa: SIM102
                if det.corridor_id == gap.corridor_id:
                    dark_detections.append(det)
                    continue
            # Match by spatial proximity
            if gap_lat is not None and gap_lon is not None:
                dist = _dv_haversine(gap_lat, gap_lon, det.detection_lat, det.detection_lon)
                if dist <= max_radius:
                    dark_detections.append(det)

        dv_cfg = config.get("dark_vessel", {})
        has_corridor_det = any(d.corridor_id is not None for d in dark_detections)
        if dark_detections:
            if has_corridor_det:
                breakdown["dark_vessel_unmatched_in_corridor"] = dv_cfg.get(
                    "unmatched_detection_in_corridor", 35
                )
            else:
                breakdown["dark_vessel_unmatched"] = dv_cfg.get(
                    "unmatched_detection_outside_corridor", 20
                )

    # Phase 6.13: Sanctioned port scoring
    # Signal 1: Confirmed PortCall to sanctioned terminal (+50)
    # Signal 2: Gap endpoint within 10nm of sanctioned terminal (+25)
    # Signal 3: CREA voyage arrival/departure matches sanctioned terminal (+35)
    if db is not None and vessel is not None:
        sp_cfg = config.get("sanctioned_port", {})
        try:
            from app.models.port import Port as _SP_Port
            from app.models.port_call import PortCall as _SP_PC
            from app.utils.geo import haversine_nm as _sp_haversine

            sanctioned_terminals = (
                db.query(_SP_Port)
                .filter(
                    _SP_Port.is_sanctioned == True  # noqa: E712
                )
                .all()
            )

            if sanctioned_terminals:
                # Signal 1: PortCall record directly linked to sanctioned terminal
                confirmed_visit = (
                    db.query(_SP_PC)
                    .filter(
                        _SP_PC.vessel_id == vessel.vessel_id,
                        _SP_PC.port_id.in_([p.port_id for p in sanctioned_terminals]),
                    )
                    .first()
                )
                if confirmed_visit:
                    breakdown["sanctioned_port_visit_confirmed"] = sp_cfg.get("visit_confirmed", 50)

                # Signal 2: Gap endpoint within 10nm of sanctioned terminal
                elif not confirmed_visit:
                    _gap_lat = getattr(gap, "gap_off_lat", None) or getattr(gap, "gap_on_lat", None)
                    _gap_lon = getattr(gap, "gap_off_lon", None) or getattr(gap, "gap_on_lon", None)
                    if _gap_lat is not None and _gap_lon is not None:
                        from app.utils.geo import load_geometry as _sp_load_geom

                        for term in sanctioned_terminals:
                            try:
                                _tp = _sp_load_geom(term.geometry)
                                if _tp is None:
                                    continue
                                _dist = _sp_haversine(_gap_lat, _gap_lon, _tp.y, _tp.x)
                                if _dist <= 10.0:
                                    breakdown["sanctioned_port_proximity_10nm"] = sp_cfg.get(
                                        "proximity_10nm", 25
                                    )
                                    break
                            except Exception as e:
                                logger.debug("Sanctioned port geometry load failed: %s", e)
                                continue
        except Exception as e:
            logger.debug("Sanctioned port scoring failed for vessel %s: %s", vessel.vessel_id, e)

        # Signal 3: CREA voyage — arrival/departure matches sanctioned terminal
        try:
            from app.models.crea_voyage import CreaVoyage as _CreaV

            _SANCTIONED_PORT_NAMES = {
                "primorsk",
                "ust-luga",
                "ust luga",
                "novorossiysk",
                "kozmino",
                "de-kastri",
                "varandey",
                "taman",
                "tuapse",
                "vysotsk",
                "kavkaz",
            }
            crea_voyages = db.query(_CreaV).filter(_CreaV.vessel_id == vessel.vessel_id).all()
            for cv in crea_voyages:
                _dp = (cv.departure_port or "").lower()
                _ap = (cv.arrival_port or "").lower()
                if any(s in _dp or s in _ap for s in _SANCTIONED_PORT_NAMES):
                    breakdown["crea_sanctioned_destination"] = sp_cfg.get("crea_confirmed", 35)
                    break
        except Exception as e:
            logger.debug("CREA voyage scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # Phase 6.14: Distance-to-EEZ-boundary gap signal
    # GFW Science Advances (2022): EEZ boundary proximity is the #1 predictor of
    # intentional AIS disabling (peer-reviewed, 50-80M events).
    #
    # FALSE-POSITIVE GUARD: Only fire for high-risk flag vessels with gaps ≥4h.
    # Without this gate, Baltic/North Sea legitimate traffic generates 85-90% FP.
    eez_cfg = config.get("eez_proximity", {})
    if eez_cfg.get("enabled", True) and vessel is not None:
        _min_gap_h = eez_cfg.get("min_gap_duration_hours", 4)
        _req_high_risk = eez_cfg.get("require_high_risk_flag", True)
        _flag_risk_ok = not _req_high_risk or ("high_risk" in flag_risk)
        # Use already-computed duration_h (from duration_minutes) — never access gap.gap_duration_hours
        # directly since existing tests mock gap without setting that attribute.
        if duration_h >= _min_gap_h and _flag_risk_ok:
            _eez_lat = getattr(gap, "gap_off_lat", None) or getattr(gap, "gap_on_lat", None)
            _eez_lon = getattr(gap, "gap_off_lon", None) or getattr(gap, "gap_on_lon", None)
            if _eez_lat is not None and _eez_lon is not None:
                try:
                    from app.utils.eez_boundaries import distance_to_nearest_eez_boundary_nm

                    _dist_nm, _eez_name = distance_to_nearest_eez_boundary_nm(_eez_lat, _eez_lon)
                    if _dist_nm <= eez_cfg.get("within_5nm_threshold", 5.0):
                        breakdown["eez_boundary_proximity_5nm"] = eez_cfg.get("within_5nm", 25)
                        breakdown["_eez_boundary_name"] = _eez_name
                    elif _dist_nm <= eez_cfg.get("within_20nm_threshold", 20.0):
                        breakdown["eez_boundary_proximity_20nm"] = eez_cfg.get("within_20nm", 15)
                        breakdown["_eez_boundary_name"] = _eez_name
                except Exception as e:
                    logger.debug("EEZ boundary proximity scoring failed: %s", e)

    # Phase: Identity merge signals
    if db is not None and vessel is not None:
        merge_cfg = config.get("identity_merge", {})

        # identity_merge_detected: vessel has absorbed identities
        try:
            from app.models.vessel_history import VesselHistory

            absorbed_count = (
                db.query(VesselHistory)
                .filter(
                    VesselHistory.vessel_id == vessel.vessel_id,
                    VesselHistory.field_changed == "mmsi_absorbed",
                )
                .count()
            )
            if isinstance(absorbed_count, int) and absorbed_count > 0:
                breakdown["identity_merge_detected"] = merge_cfg.get("identity_merge_detected", 30)
        except Exception as e:
            logger.debug("Identity merge scoring failed for vessel %s: %s", vessel.vessel_id, e)

        # imo_fabricated: IMO fails checksum
        _imo = vessel.imo if isinstance(vessel.imo, str) else None
        if _imo:
            from app.utils.vessel_identity import validate_imo_checksum

            if not validate_imo_checksum(_imo):
                breakdown["imo_fabricated"] = merge_cfg.get("imo_fabricated", 40)

    # gap_reactivation_in_jamming_zone: re-enables AIS in jamming zone + has other risk
    # FIX: Exclude structural keys (gap_duration, gap_in_known_jamming_zone, gap_reactivation)
    # from the "other_risk" check to prevent self-amplification — gap_duration always
    # fires first, so every dark zone gap was getting +15 that negated the -10
    # dark_zone_deduction, turning legitimate vessels into false positives.
    # NOTE: Moved outside the `if db is not None` block — this check only examines breakdown.
    _STRUCTURAL_PREFIXES = (
        "gap_duration_",
        "gap_in_known_jamming_zone",
        "gap_reactivation",
        "gap_frequency_",
    )
    if gap.in_dark_zone:
        _reactivation_cfg = config.get("identity_merge", {})
        other_risk = any(
            v > 0
            for k, v in breakdown.items()
            if not k.startswith("_")
            and isinstance(v, (int, float))
            and k not in ("gap_reactivation_in_jamming_zone",)
            and not any(k.startswith(prefix) for prefix in _STRUCTURAL_PREFIXES)
        )
        if other_risk:
            breakdown["gap_reactivation_in_jamming_zone"] = _reactivation_cfg.get(
                "gap_reactivation_in_jamming_zone", 15
            )

    # ── Phase K/L/M: New detector scoring (gated by dual flags) ─────────────
    from app.models.base import SpoofingTypeEnum

    # ── Phase K: Track naturalness scoring ──────────────────────────────────
    if (
        _scoring_settings.TRACK_NATURALNESS_SCORING_ENABLED
        and db is not None
        and vessel is not None
    ):
        tn_cfg = config.get("track_naturalness", {})
        tn_anomalies = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.SYNTHETIC_TRACK,
            )
            .all()
        )
        if tn_anomalies:
            best = max(tn_anomalies, key=lambda a: a.risk_score_component)
            ev = best.evidence_json or {}
            tier = ev.get("tier", "low")
            tier_key = f"synthetic_track_{tier}"
            pts = tn_cfg.get(tier_key, best.risk_score_component)
            breakdown[f"track_naturalness_{tier}"] = pts

    # ── Phase L: Draught intelligence scoring (corroborating only) ────────
    # FIX: Cap at single highest draught event (like STS best_sts_score does)
    # to prevent unbounded accumulation — a vessel with 5 historical events
    # was scoring +125 from draught alone.
    if _scoring_settings.DRAUGHT_SCORING_ENABLED and db is not None and vessel is not None:
        draught_cfg = config.get("draught", {})
        try:
            from app.models.draught_event import DraughtChangeEvent

            draught_events = (
                db.query(DraughtChangeEvent)
                .filter(
                    DraughtChangeEvent.vessel_id == vessel.vessel_id,
                )
                .all()
            )
            best_draught_key = None
            best_draught_score = 0
            for de in draught_events:
                score = de.risk_score_component
                if de.linked_sts_id is not None:
                    key = "draught_sts_confirmation"
                    pts = draught_cfg.get("draught_sts_confirmation", score)
                elif abs(de.delta_m) > 5.0:
                    key = "draught_swing_extreme"
                    pts = draught_cfg.get("draught_swing_extreme", score)
                elif de.risk_score_component > 0:
                    key = "draught_offshore_change"
                    pts = draught_cfg.get("offshore_draught_change_corroboration", score)
                else:
                    continue
                if pts > best_draught_score:
                    best_draught_score = pts
                    best_draught_key = key
            if best_draught_key:
                breakdown[best_draught_key] = best_draught_score
        except Exception as e:
            logger.debug("Draught scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Phase M: Identity fraud scoring ───────────────────────────────────
    if db is not None and vessel is not None:
        id_fraud_cfg = config.get("identity_fraud", {})

        # Stateless MMSI scoring
        if _scoring_settings.STATELESS_MMSI_SCORING_ENABLED:
            stateless = (
                db.query(SpoofingAnomaly)
                .filter(
                    SpoofingAnomaly.vessel_id == vessel.vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STATELESS_MMSI,
                )
                .first()
            )
            if stateless:
                ev = stateless.evidence_json or {}
                tier = ev.get("tier", 1)
                tier_key = f"stateless_mmsi_tier{tier}"
                pts = id_fraud_cfg.get(tier_key, stateless.risk_score_component)
                breakdown["stateless_mmsi"] = pts

        # Flag hopping scoring — FIX: skip if flag_changes_3plus_90d already
        # in breakdown (same 3 flag changes were triggering BOTH +40 and +50).
        if _scoring_settings.FLAG_HOPPING_SCORING_ENABLED:  # noqa: SIM102
            if "flag_changes_3plus_90d" not in breakdown:
                flag_hop = (
                    db.query(SpoofingAnomaly)
                    .filter(
                        SpoofingAnomaly.vessel_id == vessel.vessel_id,
                        SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING,
                    )
                    .first()
                )
                if flag_hop:
                    _fh_recency = _temporal_recency_factor(
                        getattr(flag_hop, "detected_at", None), gap.gap_start_utc
                    )
                    _fh_pts = round(flag_hop.risk_score_component * _fh_recency)
                    breakdown["flag_hopping"] = _fh_pts
                    if _fh_recency != 1.0:
                        breakdown["_temporal_recency_flag_hopping"] = _fh_recency

        # IMO fraud scoring
        if _scoring_settings.IMO_FRAUD_SCORING_ENABLED:
            imo_fraud = (
                db.query(SpoofingAnomaly)
                .filter(
                    SpoofingAnomaly.vessel_id == vessel.vessel_id,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD,
                )
                .first()
            )
            if imo_fraud:
                ev = imo_fraud.evidence_json or {}
                fraud_type = ev.get("type", "simultaneous")
                if fraud_type == "simultaneous":
                    pts = id_fraud_cfg.get("imo_simultaneous_use", 45)
                else:
                    pts = id_fraud_cfg.get("imo_near_miss_qualified", 20)
                breakdown[f"imo_fraud_{fraud_type}"] = pts

    # ── Phase O: Fleet scoring ───────────────────────────────────────────────
    if _scoring_settings.FLEET_SCORING_ENABLED and db is not None and vessel is not None:
        fleet_cfg = config.get("fleet", {})
        try:
            from app.models.fleet_alert import FleetAlert
            from app.models.owner_cluster_member import OwnerClusterMember
            from app.models.vessel_owner import VesselOwner as _VO_fleet

            # Find cluster for this vessel via owner -> cluster member
            owner = db.query(_VO_fleet).filter(_VO_fleet.vessel_id == vessel.vessel_id).first()
            if owner:
                member = (
                    db.query(OwnerClusterMember)
                    .filter(OwnerClusterMember.owner_id == owner.owner_id)
                    .first()
                )
                if member:
                    fleet_alerts = (
                        db.query(FleetAlert)
                        .filter(FleetAlert.owner_cluster_id == member.cluster_id)
                        .all()
                    )
                    for fa in fleet_alerts:
                        key = f"fleet_{fa.alert_type}"
                        if key not in breakdown:
                            pts = fleet_cfg.get(fa.alert_type, fa.risk_score_component)
                            breakdown[key] = pts
        except Exception as e:
            logger.debug("Fleet scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Stage 2-E: ISM/P&I continuity scoring ──────────────────────────────
    if _scoring_settings.ISM_CONTINUITY_SCORING_ENABLED and db is not None and vessel is not None:
        ism_cfg = config.get("ism_continuity", {})
        try:
            from app.models.fleet_alert import FleetAlert as _FA_ism

            ism_alerts = (
                db.query(_FA_ism)
                .filter(
                    _FA_ism.alert_type.in_(["ism_continuity", "pi_continuity"]),
                    _FA_ism.vessel_ids_json.contains(vessel.vessel_id),
                )
                .all()
            )
            for ism_alert in ism_alerts:
                ev = ism_alert.evidence_json or {}
                if (
                    ev.get("ism_manager")
                    and "ism_manager_persistent_across_owners" not in breakdown
                ):
                    breakdown["ism_manager_persistent_across_owners"] = ism_cfg.get(
                        "same_ism_across_owners", 20
                    )
                if ev.get("pi_club") and "pi_club_persistent_across_owners" not in breakdown:
                    breakdown["pi_club_persistent_across_owners"] = ism_cfg.get(
                        "same_pi_across_owners", 15
                    )
        except Exception as e:
            logger.debug("ISM/P&I continuity scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Stage 2-A: P&I validation scoring ──────────────────────────────────
    if _scoring_settings.PI_VALIDATION_SCORING_ENABLED and db is not None and vessel is not None:
        pi_val_cfg = config.get("pi_validation", {})
        try:
            from app.models.vessel_owner import VesselOwner as _VO_pi

            pi_owner = db.query(_VO_pi).filter(_VO_pi.vessel_id == vessel.vessel_id).first()
            pi_club = pi_owner.pi_club_name if pi_owner else None

            if pi_club and isinstance(pi_club, str) and pi_club.strip():
                pi_club_clean = pi_club.strip()
                pi_clubs_data = _load_pi_clubs_config()
                known_fraudulent = pi_clubs_data.get("known_fraudulent", [])
                legitimate_clubs = pi_clubs_data.get("legitimate_clubs", [])

                # Build set of legitimate names (full + short) for matching
                legit_names: set[str] = set()
                for club in legitimate_clubs:
                    legit_names.add(club["name"].lower())
                    legit_names.add(club["short"].lower())

                if pi_club_clean.lower() in {f.lower() for f in known_fraudulent}:
                    breakdown["pi_known_fraudulent"] = pi_val_cfg.get("known_fraudulent", 40)
                elif pi_club_clean.lower() not in legit_names:
                    breakdown["pi_unknown_insurer"] = pi_val_cfg.get("unknown_insurer", 25)
                # else: legitimate club, no points added
            else:
                # Fix 2b: pi_club_name absent because we haven't Equasis-queried this vessel yet,
                # not because it's uninsured. Suppress for low-risk flags.
                if not _suppress_data_absence:
                    breakdown["pi_no_insurer"] = pi_val_cfg.get("no_insurer", 15)
        except Exception as e:
            logger.debug("P&I validation scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Stage 2-B: Fraudulent registry scoring ─────────────────────────────
    if _scoring_settings.FRAUDULENT_REGISTRY_SCORING_ENABLED and vessel is not None:
        fr_cfg = config.get("fraudulent_registry", {})
        vessel_flag = vessel.flag if isinstance(vessel.flag, str) else None
        if vessel_flag:
            vessel_flag_upper = vessel_flag.strip().upper()
            fr_data = _load_fraudulent_registries_config()
            tier_0_codes = {
                entry["country_code"].upper() for entry in fr_data.get("tier_0_fraudulent", [])
            }
            tier_1_codes = {
                entry["country_code"].upper() for entry in fr_data.get("tier_1_high_risk", [])
            }
            if vessel_flag_upper in tier_0_codes:
                breakdown["fraudulent_registry_tier_0"] = fr_cfg.get("tier_0_fraudulent", 40)
            elif vessel_flag_upper in tier_1_codes:
                breakdown["fraudulent_registry_tier_1"] = fr_cfg.get("tier_1_high_risk", 20)

    # ── Stage 2-D: At-sea extended operations (no port call) ─────────────
    # Skip for non-commercial vessels: fishing boats at sea 365d is normal.
    if (
        _scoring_settings.AT_SEA_OPERATIONS_SCORING_ENABLED
        and db is not None
        and vessel is not None
        and not _is_non_commercial
    ):
        at_sea_cfg = config.get("at_sea_operations", {})
        try:
            from app.models.port_call import PortCall as _PC_atsea

            last_port_call = (
                db.query(_PC_atsea)
                .filter(
                    _PC_atsea.vessel_id == gap.vessel_id,
                )
                .order_by(_PC_atsea.departure_utc.desc())
                .first()
            )
            _last_dep = getattr(last_port_call, "departure_utc", None) if last_port_call else None
            if _last_dep is not None and isinstance(_last_dep, datetime):
                _dep_naive = _last_dep.replace(tzinfo=None) if _last_dep.tzinfo else _last_dep
                _days_since = (scoring_date - _dep_naive).days
            else:
                _days_since = 9999  # no port call found
            # Fix 2c: _days_since=9999 when last_port_call is None (sparse port call data, not
            # evidence of extended dark ops). Suppress these thresholds for low-risk flags.
            if not _suppress_data_absence:
                if _days_since >= 365:
                    breakdown["at_sea_no_port_call_365d"] = at_sea_cfg.get("no_port_call_365d", 35)
                elif _days_since >= 180:
                    breakdown["at_sea_no_port_call_180d"] = at_sea_cfg.get("no_port_call_180d", 25)
                elif _days_since >= 90:
                    breakdown["at_sea_no_port_call_90d"] = at_sea_cfg.get("no_port_call_90d", 15)
        except Exception as e:
            logger.debug("At-sea operations scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Stage 2-F: Rename velocity scoring ──────────────────────────────────
    if _scoring_settings.RENAME_VELOCITY_SCORING_ENABLED and db is not None and vessel is not None:
        from app.models.vessel_history import VesselHistory as _VH_rename

        one_year_ago = gap.gap_start_utc - timedelta(days=365)
        rename_changes = (
            db.query(_VH_rename)
            .filter(
                _VH_rename.vessel_id == vessel.vessel_id,
                _VH_rename.field_changed == "name",
                _VH_rename.observed_at >= one_year_ago,
            )
            .all()
        )
        rename_count = len(rename_changes)
        rename_cfg = config.get("rename_velocity", {})
        # Apply temporal recency: most recent rename event drives multiplier
        _latest_rename = max(
            (c.observed_at for c in rename_changes if c.observed_at is not None),
            default=None,
        )
        _rv_recency = _temporal_recency_factor(_latest_rename, gap.gap_start_utc)
        if rename_count >= 3:
            _rv_pts = round(rename_cfg.get("name_changes_3_per_365d", 30) * _rv_recency)
            breakdown["rename_velocity_3_365d"] = _rv_pts
        elif rename_count >= 2:
            _rv_pts = round(rename_cfg.get("name_changes_2_per_365d", 15) * _rv_recency)
            breakdown["rename_velocity_2_365d"] = _rv_pts
        if rename_count >= 2 and _rv_recency != 1.0:
            breakdown["_temporal_recency_rename"] = _rv_recency

    # ── Stage 3-B: STS relay chain scoring ─────────────────────────────────
    if _scoring_settings.STS_CHAIN_SCORING_ENABLED and db is not None and vessel is not None:
        sts_chain_cfg = config.get("sts_chains", {})
        try:
            from app.models.fleet_alert import FleetAlert as _FA_chain

            chain_alerts = (
                db.query(_FA_chain)
                .filter(
                    _FA_chain.alert_type == "sts_relay_chain",
                    _FA_chain.vessel_ids_json.contains(vessel.vessel_id),
                )
                .all()
            )
            for ca in chain_alerts:
                ev = ca.evidence_json or {}
                chain_len = ev.get("chain_length", 0)
                if chain_len >= 4:
                    breakdown["sts_chain_4_plus"] = sts_chain_cfg.get("chain_4_plus_hops", 40)
                elif chain_len >= 3:
                    breakdown["sts_chain_3"] = sts_chain_cfg.get("chain_3_hops", 20)
                # Check if this vessel is an intermediary
                intermediaries = ev.get("intermediary_vessel_ids", [])
                if vessel.vessel_id in intermediaries:
                    breakdown["sts_intermediary"] = sts_chain_cfg.get("intermediary_vessel", 15)
        except Exception as e:
            logger.debug("STS chain scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Stage 3-C: Scrapped vessel registry scoring ──────────────────────────
    if (
        _scoring_settings.SCRAPPED_REGISTRY_SCORING_ENABLED
        and db is not None
        and vessel is not None
    ):
        scrapped_cfg = config.get("scrapped_registry", {})
        scrapped_anomalies = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD,
            )
            .all()
        )
        for sa in scrapped_anomalies:
            ev = sa.evidence_json or {}
            if ev.get("subtype") == "scrapped_imo":
                pts = scrapped_cfg.get("scrapped_imo_reuse", 50)
                breakdown["scrapped_imo_reuse"] = pts

    # ── Stage 3-C: Track replay scoring ──────────────────────────────────────
    if _scoring_settings.TRACK_REPLAY_SCORING_ENABLED and db is not None and vessel is not None:
        replay_cfg = config.get("track_replay", {})
        replay_anomalies = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.TRACK_REPLAY,
            )
            .all()
        )
        for _ra in replay_anomalies:
            pts = replay_cfg.get("high_correlation_replay", 45)
            breakdown["track_replay"] = pts

    # ── Stage 4-A: Merge chain scoring ─────────────────────────────────────
    if _scoring_settings.MERGE_CHAIN_SCORING_ENABLED and db is not None and vessel is not None:
        mc_cfg = config.get("merge_chains", {})
        try:
            from app.models.merge_chain import MergeChain

            chains = db.query(MergeChain).all()
            for chain in chains:
                v_ids = chain.vessel_ids_json or []
                if vessel.vessel_id in v_ids:
                    if chain.chain_length >= 4:
                        pts = mc_cfg.get("chain_4_plus_hops", 25)
                        breakdown["merge_chain_4plus"] = pts
                    elif chain.chain_length >= 3:
                        pts = mc_cfg.get("chain_3_hops", 15)
                        breakdown["merge_chain_3"] = pts
                    # Check for scrapped IMO in chain
                    ev = chain.evidence_json or {}
                    if ev.get("has_scrapped_imo"):
                        breakdown["scrapped_imo_in_chain"] = mc_cfg.get("scrapped_imo_in_chain", 35)
                    break  # one chain match is enough
        except Exception as e:
            logger.debug("Merge chain scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Stage 5-B: Convoy scoring ────────────────────────────────────────────
    if _scoring_settings.CONVOY_SCORING_ENABLED and db is not None and vessel is not None:
        config.get("convoy", {})
        try:
            from sqlalchemy import or_ as _or_convoy

            from app.models.convoy_event import ConvoyEvent

            convoy_events = (
                db.query(ConvoyEvent)
                .filter(
                    _or_convoy(
                        ConvoyEvent.vessel_a_id == vessel.vessel_id,
                        ConvoyEvent.vessel_b_id == vessel.vessel_id,
                    ),
                    ConvoyEvent.start_time_utc >= gap.gap_start_utc - timedelta(days=7),
                    ConvoyEvent.end_time_utc <= gap.gap_end_utc + timedelta(days=7),
                )
                .all()
            )
            if convoy_events:
                best_convoy_score = 0
                best_convoy = None
                for ce in convoy_events:
                    score = ce.risk_score_component or 0
                    if score > best_convoy_score:
                        best_convoy_score = score
                        best_convoy = ce
                if best_convoy:
                    breakdown[f"convoy_{best_convoy.convoy_id}"] = best_convoy_score
        except Exception as e:
            logger.debug("Convoy scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Stage 5-A: Ownership graph scoring ─────────────────────────────────
    if _scoring_settings.OWNERSHIP_GRAPH_SCORING_ENABLED and db is not None and vessel is not None:
        og_cfg = config.get("ownership_graph", {})
        try:
            from app.models.vessel_owner import VesselOwner as _VO_og

            og_owner = db.query(_VO_og).filter(_VO_og.vessel_id == vessel.vessel_id).first()
            if og_owner:
                # Shell chain detection: walk parent_owner_id chain
                parent_id = getattr(og_owner, "parent_owner_id", None)
                if isinstance(parent_id, int):
                    chain_depth = 1
                    visited_ids = {og_owner.owner_id}
                    current_parent = parent_id
                    is_circular = False
                    while current_parent is not None and chain_depth <= 10:
                        if current_parent in visited_ids:
                            is_circular = True
                            break
                        visited_ids.add(current_parent)
                        chain_depth += 1
                        next_owner = (
                            db.query(_VO_og).filter(_VO_og.owner_id == current_parent).first()
                        )
                        if next_owner:
                            current_parent = getattr(next_owner, "parent_owner_id", None)
                            if not isinstance(current_parent, int):
                                current_parent = None
                        else:
                            break
                    if chain_depth > 2:
                        breakdown["ownership_shell_chain"] = og_cfg.get(
                            "shell_chain_depth_3_plus", 20
                        )
                    if is_circular:
                        breakdown["ownership_circular"] = og_cfg.get("circular_ownership", 25)

                # Post-sanction reshuffling: >2 ownership changes in 12 months
                all_vessel_owners = (
                    db.query(_VO_og).filter(_VO_og.vessel_id == vessel.vessel_id).all()
                )
                from datetime import timedelta as _td_og

                _now_og = scoring_date
                _window_og = _now_og - _td_og(days=365)
                recent_changes_og = sum(
                    1
                    for o in all_vessel_owners
                    if isinstance(getattr(o, "verified_at", None), datetime)
                    and o.verified_at >= _window_og
                )
                if recent_changes_og >= 3:
                    breakdown["ownership_reshuffling"] = og_cfg.get("post_sanction_reshuffling", 20)

                # Shared address with sanctioned entity
                if og_owner.country and not og_owner.is_sanctioned:
                    sanctioned_same_country = (
                        db.query(_VO_og)
                        .filter(
                            _VO_og.is_sanctioned,
                            _VO_og.country == og_owner.country,
                            _VO_og.owner_id != og_owner.owner_id,
                        )
                        .first()
                    )
                    if sanctioned_same_country:
                        breakdown["ownership_shared_address_sanctioned"] = og_cfg.get(
                            "shared_address_sanctioned", 35
                        )

                # E5: Sanctions propagation via OwnerCluster
                # If this owner belongs to a sanctioned cluster, propagate the score
                try:
                    from app.models.owner_cluster import OwnerCluster
                    from app.models.owner_cluster_member import OwnerClusterMember

                    cluster_membership = (
                        db.query(OwnerClusterMember)
                        .filter(OwnerClusterMember.owner_id == og_owner.owner_id)
                        .first()
                    )
                    if cluster_membership:
                        cluster = (
                            db.query(OwnerCluster)
                            .filter(OwnerCluster.cluster_id == cluster_membership.cluster_id)
                            .first()
                        )
                        if cluster and cluster.is_sanctioned and not og_owner.is_sanctioned:
                            breakdown["ownership_cluster_sanctioned"] = og_cfg.get(
                                "shared_address_sanctioned", 35
                            )
                except Exception as e:
                    logger.debug(
                        "Owner cluster sanctions propagation failed for vessel %s: %s",
                        vessel.vessel_id,
                        e,
                    )
        except Exception as e:
            logger.debug("Ownership graph scoring failed for vessel %s: %s", vessel.vessel_id, e)

    # ── Stage 5-C: Voyage prediction + cargo inference + weather scoring ────
    if _scoring_settings.VOYAGE_SCORING_ENABLED and db is not None and vessel is not None:
        voyage_cfg = config.get("voyage", {})

        # Route deviation toward STS zone
        if _scoring_settings.VOYAGE_PREDICTION_ENABLED:
            try:
                from app.modules.voyage_predictor import predict_next_destination

                prediction = predict_next_destination(db, vessel.vessel_id)
                if prediction and prediction.get("deviation_score", 0) > 0:
                    breakdown["route_deviation_toward_sts"] = voyage_cfg.get(
                        "route_deviation_toward_sts", 25
                    )
            except Exception as e:
                logger.debug(
                    "Voyage prediction scoring failed for vessel %s: %s", vessel.vessel_id, e
                )

        # Cargo inference: laden from Russian terminal + STS
        if _scoring_settings.CARGO_INFERENCE_ENABLED:
            try:
                from app.modules.cargo_inference import infer_cargo_state

                cargo = infer_cargo_state(db, vessel.vessel_id)
                if cargo.get("russian_terminal_sts"):
                    breakdown["laden_from_russian_terminal_sts"] = voyage_cfg.get(
                        "laden_from_russian_terminal_sts", 15
                    )
            except Exception as e:
                logger.debug(
                    "Cargo inference scoring failed for vessel %s: %s", vessel.vessel_id, e
                )

        # Weather correlation: deduction on speed anomaly ONLY
        if _scoring_settings.WEATHER_CORRELATION_ENABLED:
            try:
                from app.modules.weather_correlator import correlate_weather

                weather = correlate_weather(db, vessel.vessel_id)
                if weather and weather.get("total_deduction", 0) < 0:
                    has_speed_anomaly = any(k.startswith("speed_") for k in breakdown)
                    if has_speed_anomaly:
                        for corr in weather.get("correlations", []):
                            if corr.get("reason") == "storm_conditions":
                                breakdown["weather_speed_correction_storm"] = voyage_cfg.get(
                                    "weather_speed_correction_storm", -15
                                )
                                break
                            elif corr.get("reason") == "high_wind":
                                breakdown["weather_speed_correction_wind"] = voyage_cfg.get(
                                    "weather_speed_correction_wind", -8
                                )
                                break
            except Exception as e:
                logger.debug(
                    "Weather correlation scoring failed for vessel %s: %s", vessel.vessel_id, e
                )

    # ── Stage C: Route laundering scoring ─────────────────────────────────
    if _scoring_settings.ROUTE_LAUNDERING_SCORING_ENABLED and db is not None and vessel is not None:
        rl_cfg = config.get("route_laundering", {})
        rl_anomalies = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING,
            )
            .all()
        )
        if rl_anomalies:
            best = max(rl_anomalies, key=lambda a: a.risk_score_component)
            ev = best.evidence_json or {}
            hop_count = ev.get("hop_count", 2)
            if hop_count >= 3:
                pts = rl_cfg.get("confirmed_3_hop", 35)
            elif hop_count >= 2:
                pts = rl_cfg.get("partial_2_hop", 20)
            else:
                pts = rl_cfg.get("pattern_only", 15)
            breakdown["route_laundering"] = pts

    # ── Stage C: P&I cycling scoring ──────────────────────────────────────
    if _scoring_settings.PI_CYCLING_SCORING_ENABLED and db is not None and vessel is not None:
        pic_cfg = config.get("pi_cycling", {})
        pic_anomalies = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.PI_CYCLING,
            )
            .all()
        )
        if pic_anomalies:
            best = max(pic_anomalies, key=lambda a: a.risk_score_component)
            ev = best.evidence_json or {}
            if ev.get("non_ig_club"):
                pts = pic_cfg.get("non_ig_club", 30)
            else:
                pts = pic_cfg.get("rapid_change_90d", 20)
            breakdown["pi_cycling"] = pts

    # ── Stage C: Sparse transmission scoring ──────────────────────────────
    if (
        _scoring_settings.SPARSE_TRANSMISSION_SCORING_ENABLED
        and db is not None
        and vessel is not None
    ):
        st_cfg = config.get("sparse_transmission", {})
        st_anomalies = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.SPARSE_TRANSMISSION,
            )
            .all()
        )
        if st_anomalies:
            best = max(st_anomalies, key=lambda a: a.risk_score_component)
            ev = best.evidence_json or {}
            severity = ev.get("severity", "moderate")
            if severity == "severe":
                pts = st_cfg.get("severe_sparsity", 25)
            else:
                pts = st_cfg.get("moderate_sparsity", 15)
            breakdown["sparse_transmission"] = pts

    # ── Stage C: Vessel type consistency scoring ──────────────────────────
    if _scoring_settings.TYPE_CONSISTENCY_SCORING_ENABLED and db is not None and vessel is not None:
        vtc_cfg = config.get("vessel_type_consistency", {})
        vtc_anomalies = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vessel.vessel_id,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.TYPE_DWT_MISMATCH,
            )
            .all()
        )
        if vtc_anomalies:
            best = max(vtc_anomalies, key=lambda a: a.risk_score_component)
            ev = best.evidence_json or {}
            if ev.get("recent_type_change"):
                pts = vtc_cfg.get("recent_type_change", 15)
            else:
                pts = vtc_cfg.get("type_dwt_mismatch", 25)
            breakdown["vessel_type_consistency"] = pts

    # ── Phase 4a: Per-vessel behavioral baseline (Windward "Patterns of Life") ─
    # Compare current gap to vessel's own 30-day historical baseline.
    # Windward Behavioral Intelligence + ArXiv 2406.09966: per-vessel Z-score achieves
    # 95% F1 in literature vs 70-80% for fleet-wide absolute thresholds.
    if db is not None and vessel is not None:
        try:
            _baseline_lookback = gap.gap_start_utc - timedelta(days=30)
            _hist_gaps = (
                db.query(AISGapEvent)
                .filter(
                    AISGapEvent.vessel_id == vessel.vessel_id,
                    AISGapEvent.gap_start_utc >= _baseline_lookback,
                    AISGapEvent.gap_start_utc < gap.gap_start_utc,
                    AISGapEvent.gap_id != gap.gap_id,
                )
                .all()
            )
            if len(_hist_gaps) >= 3:  # Minimum sample size for meaningful baseline
                _hist_durations = [g.gap_duration_hours for g in _hist_gaps if g.gap_duration_hours]
                if _hist_durations and len(_hist_durations) >= 3:
                    _mean_dur = sum(_hist_durations) / len(_hist_durations)
                    _var_dur = sum((d - _mean_dur) ** 2 for d in _hist_durations) / len(
                        _hist_durations
                    )
                    _std_dur = _var_dur**0.5
                    _curr_dur = duration_h  # Use already-computed duration_h
                    if _std_dur > 0.5:  # Skip if std is trivially small (all gaps same duration)
                        _z_score = abs(_curr_dur - _mean_dur) / _std_dur
                        if _z_score >= 3.0:
                            breakdown["behavioral_deviation_3sigma"] = 40
                            breakdown["_behavioral_z_score"] = round(_z_score, 2)
                        elif _z_score >= 2.0:
                            breakdown["behavioral_deviation_2sigma"] = 25
                            breakdown["_behavioral_z_score"] = round(_z_score, 2)
        except Exception as e:
            logger.debug(
                "Behavioral baseline scoring failed for vessel %s: %s", vessel.vessel_id, e
            )

    # ── Phase 7: KSE Shadow Fleet Archetype Score ──────────────────────────
    # KSE Institute finding (2023-2024): 92% of new shadow crude tankers are:
    # - >15y old, open registry flag, tanker type, Aframax+ size, RU/AE manager
    # Matching 3+ dimensions triggers composite signal.
    kse_cfg = config.get("kse_profile", {})
    if kse_cfg.get("enabled", True) and vessel is not None:
        _OPEN_REGISTRY_FLAGS = {
            "PA",
            "LR",
            "MH",
            "BS",
            "SG",
            "CM",
            "KM",
            "GQ",
            "PW",
            "SL",
            "HN",
            "GA",
            "TZ",
            "ST",
            "GM",
            "VU",
            "CK",
        }
        _kse_hits = 0
        # Compute age safely — may not be defined if year_built is None
        _kse_age = max(0, current_year - vessel.year_built) if vessel.year_built is not None else 0
        # Dimension 1: age ≥15y
        if _kse_age >= 15:
            _kse_hits += 1
        # Dimension 2: open registry / known shadow fleet flag
        _kse_flag = str(vessel.flag or "").upper()
        if _kse_flag in _OPEN_REGISTRY_FLAGS or _kse_flag == "RU":
            _kse_hits += 1
        # Dimension 3: crude/product tanker type
        _kse_type = str(vessel.vessel_type or "").lower()
        if any(t in _kse_type for t in ("tanker", "crude", "oil")):
            _kse_hits += 1
        # Dimension 4: Aframax+ size (>80k DWT)
        if vessel.deadweight and vessel.deadweight > 80_000:
            _kse_hits += 1
        # Dimension 5: flag is specifically high-risk (shadow fleet convenience flags)
        _kse_flag_risk = str(
            vessel.flag_risk_category.value
            if hasattr(vessel.flag_risk_category, "value")
            else vessel.flag_risk_category or ""
        ).lower()
        if "high_risk" in _kse_flag_risk:
            _kse_hits += 1
        # Score: 4+ hits → strong match, 3 hits → moderate match
        if _kse_hits >= 4:
            breakdown["kse_shadow_profile_strong"] = kse_cfg.get("profile_match_4plus", 35)
        elif _kse_hits >= 3:
            breakdown["kse_shadow_profile_match"] = kse_cfg.get("profile_match_3", 20)

    # ── Phase 2+3: Multiplier composition (asymmetric) ─────────────────────
    # Multipliers amplify ONLY risk signals (positive); legitimacy deductions
    # (negative) are added at face value so they always mean exactly what
    # risk_scoring.yaml says regardless of corridor zone or vessel size.
    risk_signals = sum(v for v in breakdown.values() if isinstance(v, (int, float)) and v > 0)
    legitimacy_signals = sum(v for v in breakdown.values() if isinstance(v, (int, float)) and v < 0)
    additive_subtotal = risk_signals + legitimacy_signals

    corridor_mult, corridor_type = _corridor_multiplier(gap.corridor, config)
    vessel_size_mult, vessel_size_class = _vessel_size_multiplier(gap.vessel, config)

    # Non-commercial vessel type override: reduce corridor multiplier to 1.0
    # and remove STS-specific signals (fishing boats in STS corridors are normal)
    if _is_non_commercial:
        corridor_mult = 1.0
        _sts_keys_to_remove = [
            k
            for k in breakdown
            if k.startswith("sts_event_")
            or k.startswith("repeat_sts")
            or k == "gap_in_sts_tagged_corridor"
            or k.startswith("loiter")
        ]
        for k in _sts_keys_to_remove:
            del breakdown[k]
        # Recalculate risk_signals after STS removal
        risk_signals = sum(v for v in breakdown.values() if isinstance(v, (int, float)) and v > 0)
        legitimacy_signals = sum(
            v for v in breakdown.values() if isinstance(v, (int, float)) and v < 0
        )

    # Fix 3: cap corridor multiplier at 1.0 for EU/NATO flagged vessels.
    # The ×1.5 transit corridor multiplier was designed for shadow fleet vessels going dark
    # in sensitive zones. For EU/NATO flags it over-amplifies legitimate maritime activity.
    # Example without fix: 168 pts × 1.5 − 45 legitimacy = 207 → cap 200 (CRITICAL).
    # Example with fix:    168 pts × 1.0 − 45 legitimacy = 123 (HIGH).
    if _is_low_risk_flag:
        _fp_cfg = config.get("false_positive_suppression", {})
        _lr_cap = _fp_cfg.get("low_risk_flag_corridor_mult_cap", 1.0)
        corridor_mult = min(corridor_mult, _lr_cap)
        breakdown["_low_risk_flag_corridor_cap"] = corridor_mult

    amplified_risk = risk_signals * corridor_mult * vessel_size_mult
    final_score = min(200, max(0, round(amplified_risk + legitimacy_signals)))  # Cap at 200

    # Non-commercial vessel score cap: pilot/SAR/tug/port tender/law enforcement
    # vessels can score high due to gap frequency but are almost never shadow fleet.
    # Cap their maximum score to suppress to LOW tier after all legitimacy discounts.
    if _is_non_commercial:
        _fp_cfg = config.get("false_positive_suppression", {})
        _nc_cap = _fp_cfg.get("non_commercial_score_cap", 30)
        final_score = min(final_score, _nc_cap)
        breakdown["_non_commercial_cap_applied"] = _nc_cap

    # Fix 4: soft cap for ambiguous AIS type codes (90/96/99) on low-risk flag vessels.
    # Types 90/96/99 are NOT added to _NON_COMMERCIAL_AIS_CODES because they are ambiguous —
    # Type 90 ("Other") is used by coast guard AND some shadow fleet vessels with bad type data.
    # A softer cap (50) gated on _is_low_risk_flag protects legitimate vessels while preserving
    # detection for RU/KM/PW flag vessels broadcasting Type 90.
    # Safety: RU + Type 90 → no cap (not low_risk_flag); ZA + Type 90 → cap at 50.
    _AMBIGUOUS_AIS_TYPE_CODES = {"type 90", "type 96", "type 99"}
    if _is_low_risk_flag and _vessel_type_raw in _AMBIGUOUS_AIS_TYPE_CODES:
        _fp_cfg = config.get("false_positive_suppression", {})
        _amb_cap = _fp_cfg.get("ambiguous_type_low_risk_cap", 50)
        final_score = min(final_score, _amb_cap)
        breakdown["_ambiguous_type_low_risk_cap_applied"] = _amb_cap

    # ── Data completeness cap: prevent under-tracked vessels from scoring HIGH/CRITICAL ──
    # New vessels with few AIS points accumulate score from data absence alone.
    # Cap at MEDIUM unless high-confidence signals (spoofing, watchlist, identity fraud) fire.
    _HIGH_CONFIDENCE_KEYS = frozenset(
        {
            "impossible_reappear",
            "speed_spoof_before_gap",
            "speed_impossible",
            "mmsi_reuse_implied_speed_30kn",
            "mmsi_reuse_implied_speed_100kn",
            "circle_pattern",
            "identity_swap",
            "cross_receiver_disagreement",
            "imo_fabricated",
            "imo_simultaneous_use",
            "stateless_mmsi",
            "scrapped_imo_reuse",
            "track_naturalness_high",
        }
    )
    _dc_cfg = config.get("data_completeness", {})
    _dc_max = _dc_cfg.get("max_score_if_incomplete", 50)
    if db is not None and vessel is not None and final_score > _dc_max:
        _dc_min_pts = _dc_cfg.get("min_points", 50)
        _dc_min_days = _dc_cfg.get("min_days", 14)
        _first_seen = getattr(vessel, "mmsi_first_seen_utc", None)
        _tracking_days: int = 0
        if isinstance(_first_seen, datetime):
            _tracking_days = (scoring_date - _first_seen).days
        _is_incomplete = _tracking_days < _dc_min_days
        _pt_count = 0
        if _is_incomplete:
            from sqlalchemy import func as sa_func

            from app.models.ais_point import AISPoint

            try:
                _pt_count = int(
                    db.query(sa_func.count(AISPoint.ais_point_id))
                    .filter(AISPoint.vessel_id == vessel.vessel_id)
                    .scalar()
                    or 0
                )
            except (TypeError, ValueError):
                _pt_count = 0
            _is_incomplete = _pt_count < _dc_min_pts
        if _is_incomplete:
            _has_hc = any(k in _HIGH_CONFIDENCE_KEYS for k in breakdown)
            _has_watchlist = any(k.startswith("watchlist_") for k in breakdown)
            _has_sanctions = "owner_or_manager_on_sanctions_list" in breakdown
            _has_flag_hop = any(k.startswith("flag_hopping") for k in breakdown)
            if not (_has_hc or _has_watchlist or _has_sanctions or _has_flag_hop):
                final_score = min(final_score, _dc_max)
                breakdown["_data_completeness_cap_applied"] = True
                breakdown["_data_completeness_points"] = _pt_count
                breakdown["_data_completeness_days"] = _tracking_days

    # ── Phase 4b: Pole Star MTI-Style Pillar Score Separation ────────────────
    # Splits signals into 3 independent pillars for analyst interpretability.
    # Inspired by Pole Star Maritime Transparency Index methodology.
    # Pillars don't change the total score — they decompose it for the UI.

    _p_vessel = sum(
        v
        for k, v in breakdown.items()
        if not k.startswith("_")
        and k in _VESSEL_PILLAR_KEYS
        and isinstance(v, (int, float))
        and v > 0
    )
    _p_position = sum(
        v
        for k, v in breakdown.items()
        if not k.startswith("_")
        and k in _POSITION_PILLAR_KEYS
        and isinstance(v, (int, float))
        and v > 0
    )
    _p_voyage = sum(
        v
        for k, v in breakdown.items()
        if not k.startswith("_")
        and k in _VOYAGE_PILLAR_KEYS
        and isinstance(v, (int, float))
        and v > 0
    )

    # Metadata (prefixed with _ so UI does not sum them as signal points)
    breakdown["_corridor_type"] = corridor_type
    breakdown["_corridor_multiplier"] = corridor_mult
    breakdown["_vessel_size_class"] = vessel_size_class
    breakdown["_vessel_size_multiplier"] = vessel_size_mult
    breakdown["_additive_subtotal"] = additive_subtotal
    breakdown["_final_score"] = final_score
    breakdown["_pillar_vessel"] = _p_vessel
    breakdown["_pillar_position"] = _p_position
    breakdown["_pillar_voyage"] = _p_voyage

    # ── Phase 4c: Cross-Detector Corroboration Bonus ──────────────────────
    # Skip if data completeness cap was applied — bonus must not override safety caps.
    corr_cfg = config.get("corroboration", {})
    if corr_cfg.get("enabled", True) and "_data_completeness_cap_applied" not in breakdown:
        bonus = _corroboration_bonus(breakdown, corr_cfg)
        if bonus:
            final_score = min(200, final_score + bonus)
            breakdown["_corroboration_bonus"] = bonus

    return final_score, breakdown
