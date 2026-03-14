"""Behavioral Baseline Per-Vessel Profiling.

Builds a 90-day behavioral profile per vessel from AIS position data and gap
events, then detects deviations in a 7-day current window compared to the
historical baseline.

Profile components:
  - Speed stats: median SOG, IQR of SOG, max SOG
  - Port pattern: visited ports, dwell times per port
  - Route pattern: top-3 corridors by visit count
  - Gap pattern: gap frequency, mean duration, max duration
  - Temporal pattern: activity distribution in 6-hour buckets

Deviation signals:
  - Speed z-score (median SOG shift)
  - Port novelty fraction (new ports / total ports visited)
  - Route deviation fraction (corridors not in baseline)
  - Gap frequency ratio (current / historical rate)

Confidence tiers scale z-score thresholds by data density:
  - 50-200 points: z > 3.0
  - 200-500 points: z > 2.5
  - 500+ points: z > 2.0

Multi-signal bonus: +0.15 when 3+ signals fire simultaneously.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

BASELINE_DAYS = 90
CURRENT_WINDOW_DAYS = 7
TEMPORAL_BUCKETS = 4  # 6-hour buckets: 0-6, 6-12, 12-18, 18-24
TOP_CORRIDORS = 3

# Deviation thresholds
PORT_NOVELTY_THRESHOLD = 0.3
MULTI_SIGNAL_BONUS = 0.15
MULTI_SIGNAL_MIN_COUNT = 3
GAP_FREQUENCY_CAP = 10.0

# Tier thresholds (deviation_score)
TIER_HIGH_THRESHOLD = 0.7
TIER_MEDIUM_THRESHOLD = 0.4

# Risk score component mappings
RISK_SCORE_HIGH = 30.0
RISK_SCORE_MEDIUM = 18.0
RISK_SCORE_LOW = 8.0


# ── Statistics helpers ───────────────────────────────────────────────────────


def _median(values: list[float]) -> float:
    """Compute the median of a sorted-capable list of floats."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _iqr(values: list[float]) -> float:
    """Compute the interquartile range (Q3 - Q1)."""
    if len(values) < 4:
        return 0.0
    s = sorted(values)
    n = len(s)
    q1_idx = n // 4
    q3_idx = (3 * n) // 4
    return s[q3_idx] - s[q1_idx]


def _std(values: list[float]) -> float:
    """Compute standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


# ── Profile building ─────────────────────────────────────────────────────────


def compute_speed_stats(sog_values: list[float]) -> dict[str, float]:
    """Compute speed statistics from SOG values.

    Returns dict with median_sog, iqr_sog, max_sog.
    """
    if not sog_values:
        return {"median_sog": 0.0, "iqr_sog": 0.0, "max_sog": 0.0}
    return {
        "median_sog": round(_median(sog_values), 4),
        "iqr_sog": round(_iqr(sog_values), 4),
        "max_sog": round(max(sog_values), 4),
    }


def compute_port_pattern(
    port_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute port visit pattern from port call records.

    Each port_call dict should have: port_id, arrival_utc, departure_utc.
    Returns dict with visited_ports list and dwell_times dict.
    """
    visited: set[int] = set()
    dwell_times: dict[int, list[float]] = {}

    for pc in port_calls:
        pid = pc.get("port_id")
        if pid is None:
            continue
        visited.add(pid)
        arrival = pc.get("arrival_utc")
        departure = pc.get("departure_utc")
        if arrival and departure:
            hours = (departure - arrival).total_seconds() / 3600.0
            if hours > 0:
                dwell_times.setdefault(pid, []).append(round(hours, 2))

    # Average dwell times per port
    avg_dwell: dict[str, float] = {}
    for pid, times in dwell_times.items():
        avg_dwell[str(pid)] = round(sum(times) / len(times), 2)

    return {
        "visited_ports": sorted(visited),
        "dwell_times": avg_dwell,
    }


def compute_route_pattern(
    corridor_visits: list[int],
) -> dict[str, Any]:
    """Compute route pattern from corridor visit counts.

    Args:
        corridor_visits: list of corridor_ids visited (may have repeats).

    Returns dict with top_corridors (top-3 by frequency).
    """
    freq: dict[int, int] = {}
    for cid in corridor_visits:
        freq[cid] = freq.get(cid, 0) + 1

    sorted_corridors = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    top = [{"corridor_id": cid, "count": cnt} for cid, cnt in sorted_corridors[:TOP_CORRIDORS]]
    return {"top_corridors": top}


def compute_gap_pattern(
    gap_durations: list[float],
) -> dict[str, float]:
    """Compute gap pattern from gap event durations (in minutes).

    Returns dict with frequency (count), mean_duration, max_duration.
    """
    if not gap_durations:
        return {"frequency": 0, "mean_duration": 0.0, "max_duration": 0.0}
    return {
        "frequency": len(gap_durations),
        "mean_duration": round(sum(gap_durations) / len(gap_durations), 2),
        "max_duration": round(max(gap_durations), 2),
    }


def compute_temporal_pattern(
    timestamps: list[datetime],
) -> dict[str, list[int]]:
    """Compute temporal activity pattern in 6-hour buckets.

    Buckets: [0-6h, 6-12h, 12-18h, 18-24h] counts.
    """
    buckets = [0] * TEMPORAL_BUCKETS
    for ts in timestamps:
        hour = ts.hour
        bucket_idx = hour // 6
        buckets[bucket_idx] += 1
    return {"buckets_6h": buckets}


# ── Deviation detection ──────────────────────────────────────────────────────


def _z_score_threshold(data_point_count: int) -> float:
    """Return the z-score threshold based on data density.

    50-200 points: z > 3.0
    200-500 points: z > 2.5
    500+ points: z > 2.0
    <50 points: z > 3.5 (very conservative)
    """
    if data_point_count >= 500:
        return 2.0
    if data_point_count >= 200:
        return 2.5
    if data_point_count >= 50:
        return 3.0
    return 3.5


def compute_speed_deviation(
    baseline_stats: dict[str, float],
    current_stats: dict[str, float],
    data_point_count: int,
) -> tuple[float, bool]:
    """Compute speed z-score deviation between baseline and current window.

    Returns (z_score, signal_fired).
    """
    baseline_median = baseline_stats.get("median_sog", 0.0)
    current_median = current_stats.get("median_sog", 0.0)
    baseline_iqr = baseline_stats.get("iqr_sog", 0.0)

    # Use IQR as proxy for spread; fall back to small epsilon
    spread = baseline_iqr if baseline_iqr > 0 else 0.1
    z = abs(current_median - baseline_median) / spread
    threshold = _z_score_threshold(data_point_count)
    return round(z, 4), z > threshold


def compute_port_novelty(
    baseline_ports: list[int],
    current_ports: list[int],
) -> tuple[float, bool]:
    """Compute port novelty fraction: new ports / total current ports.

    Returns (novelty_fraction, signal_fired).
    Threshold >= 0.3 to avoid single-port false positives.
    """
    if not current_ports:
        return 0.0, False
    baseline_set = set(baseline_ports)
    new_ports = [p for p in current_ports if p not in baseline_set]
    fraction = len(new_ports) / len(current_ports)
    return round(fraction, 4), fraction >= PORT_NOVELTY_THRESHOLD


def compute_route_deviation(
    baseline_corridors: list[int],
    current_corridors: list[int],
) -> tuple[float, bool]:
    """Compute route deviation fraction: corridors not in baseline / total current.

    Returns (deviation_fraction, signal_fired).
    """
    if not current_corridors:
        return 0.0, False
    baseline_set = set(baseline_corridors)
    novel = [c for c in current_corridors if c not in baseline_set]
    fraction = len(novel) / len(current_corridors)
    return round(fraction, 4), fraction > 0.5


def compute_gap_frequency_ratio(
    historical_frequency: float,
    current_frequency: float,
) -> tuple[float, bool]:
    """Compute gap frequency ratio with explicit 3-case handling.

    Cases:
    - Both zero: ratio = 1.0 (normal, no signal)
    - Historical zero + current nonzero: ratio capped at GAP_FREQUENCY_CAP (auto signal)
    - Current zero: ratio = 0.0 (no gaps = good, no signal)

    Returns (ratio, signal_fired).
    """
    if historical_frequency == 0 and current_frequency == 0:
        return 1.0, False
    if historical_frequency == 0 and current_frequency > 0:
        return GAP_FREQUENCY_CAP, True
    if current_frequency == 0:
        return 0.0, False
    ratio = current_frequency / historical_frequency
    return round(min(ratio, GAP_FREQUENCY_CAP), 4), ratio > 2.0


def compute_deviation_score(
    speed_z: float,
    speed_fired: bool,
    port_novelty: float,
    port_fired: bool,
    route_deviation: float,
    route_fired: bool,
    gap_ratio: float,
    gap_fired: bool,
) -> tuple[float, list[str]]:
    """Compute composite deviation score from individual signals.

    Each signal contributes a weighted component. Multi-signal bonus
    of +0.15 applied when 3+ signals fire simultaneously.

    Returns (deviation_score in [0, 1], list of fired signal names).
    """
    signals: list[str] = []
    score = 0.0

    # Speed component: normalize z-score to 0-0.3 range (z of 5+ = max)
    speed_component = min(speed_z / 5.0, 1.0) * 0.3
    score += speed_component
    if speed_fired:
        signals.append("speed_z_score")

    # Port novelty component: 0-0.25 range
    port_component = port_novelty * 0.25
    score += port_component
    if port_fired:
        signals.append("port_novelty")

    # Route deviation component: 0-0.2 range
    route_component = route_deviation * 0.2
    score += route_component
    if route_fired:
        signals.append("route_deviation")

    # Gap frequency component: normalize ratio to 0-0.25 range
    gap_component = min(gap_ratio / GAP_FREQUENCY_CAP, 1.0) * 0.25
    score += gap_component
    if gap_fired:
        signals.append("gap_frequency_anomaly")

    # Multi-signal bonus
    if len(signals) >= MULTI_SIGNAL_MIN_COUNT:
        score += MULTI_SIGNAL_BONUS
        signals.append("multi_signal_bonus")

    # Clamp to [0, 1]
    score = max(0.0, min(1.0, score))
    return round(score, 4), signals


def _score_to_tier(deviation_score: float) -> tuple[str, float]:
    """Map deviation score to tier and risk score component.

    Returns (tier, risk_score_component).
    """
    if deviation_score >= TIER_HIGH_THRESHOLD:
        return "high", RISK_SCORE_HIGH
    if deviation_score >= TIER_MEDIUM_THRESHOLD:
        return "medium", RISK_SCORE_MEDIUM
    return "low", RISK_SCORE_LOW


# ── Data extraction helpers ──────────────────────────────────────────────────


def _fetch_position_data(
    db: Session, vessel_id: int, start: datetime, end: datetime
) -> list[Any]:
    """Fetch AIS points for a vessel in a time range."""
    from app.models.ais_point import AISPoint

    return (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel_id,
            AISPoint.timestamp_utc >= start,
            AISPoint.timestamp_utc <= end,
        )
        .order_by(AISPoint.timestamp_utc)
        .all()
    )


def _fetch_gap_events(
    db: Session, vessel_id: int, start: datetime, end: datetime
) -> list[Any]:
    """Fetch gap events for a vessel in a time range."""
    from app.models.gap_event import AISGapEvent

    return (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.vessel_id == vessel_id,
            AISGapEvent.gap_start_utc >= start,
            AISGapEvent.gap_start_utc <= end,
        )
        .all()
    )


def _fetch_port_calls(
    db: Session, vessel_id: int, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Fetch port calls for a vessel in a time range."""
    from app.models.port_call import PortCall

    calls = (
        db.query(PortCall)
        .filter(
            PortCall.vessel_id == vessel_id,
            PortCall.arrival_utc >= start,
            PortCall.arrival_utc <= end,
        )
        .all()
    )
    return [
        {
            "port_id": c.port_id,
            "arrival_utc": c.arrival_utc,
            "departure_utc": c.departure_utc,
        }
        for c in calls
    ]


def _extract_corridor_visits(gap_events: list[Any]) -> list[int]:
    """Extract corridor IDs from gap events (non-null corridor_id)."""
    return [g.corridor_id for g in gap_events if g.corridor_id is not None]


# ── Profile building pipeline ────────────────────────────────────────────────


def build_vessel_profile(
    db: Session, vessel_id: int, now: datetime | None = None
) -> dict[str, Any] | None:
    """Build a complete behavioral profile for a vessel.

    Returns profile dict or None if insufficient data (< 10 positions).
    """
    if now is None:
        now = datetime.now(UTC)

    baseline_start = now - timedelta(days=BASELINE_DAYS)
    baseline_end = now - timedelta(days=CURRENT_WINDOW_DAYS)
    current_start = now - timedelta(days=CURRENT_WINDOW_DAYS)
    current_end = now

    # Fetch baseline data
    baseline_positions = _fetch_position_data(db, vessel_id, baseline_start, baseline_end)
    if len(baseline_positions) < 10:
        logger.debug("Vessel %d: insufficient baseline data (%d points)", vessel_id, len(baseline_positions))
        return None

    # Fetch current window data
    current_positions = _fetch_position_data(db, vessel_id, current_start, current_end)

    # Speed stats
    baseline_sog = [p.sog for p in baseline_positions if p.sog is not None]
    current_sog = [p.sog for p in current_positions if p.sog is not None]
    baseline_speed = compute_speed_stats(baseline_sog)
    current_speed = compute_speed_stats(current_sog)

    # Port patterns
    baseline_port_calls = _fetch_port_calls(db, vessel_id, baseline_start, baseline_end)
    current_port_calls = _fetch_port_calls(db, vessel_id, current_start, current_end)
    baseline_port_pattern = compute_port_pattern(baseline_port_calls)
    current_port_pattern = compute_port_pattern(current_port_calls)

    # Gap patterns
    baseline_gaps = _fetch_gap_events(db, vessel_id, baseline_start, baseline_end)
    current_gaps = _fetch_gap_events(db, vessel_id, current_start, current_end)
    baseline_gap_durations = [g.duration_minutes for g in baseline_gaps]
    current_gap_durations = [g.duration_minutes for g in current_gaps]
    baseline_gap_pattern = compute_gap_pattern(baseline_gap_durations)
    current_gap_pattern = compute_gap_pattern(current_gap_durations)

    # Route patterns (corridor visits from gap events)
    baseline_corridor_visits = _extract_corridor_visits(baseline_gaps)
    current_corridor_visits = _extract_corridor_visits(current_gaps)
    baseline_route_pattern = compute_route_pattern(baseline_corridor_visits)

    # Temporal pattern
    baseline_timestamps = [p.timestamp_utc for p in baseline_positions]
    baseline_temporal = compute_temporal_pattern(baseline_timestamps)

    # Deviation detection
    total_points = len(baseline_positions) + len(current_positions)
    speed_z, speed_fired = compute_speed_deviation(baseline_speed, current_speed, total_points)
    port_novelty, port_fired = compute_port_novelty(
        baseline_port_pattern["visited_ports"],
        current_port_pattern["visited_ports"],
    )
    route_dev, route_fired = compute_route_deviation(
        [c["corridor_id"] for c in baseline_route_pattern["top_corridors"]],
        current_corridor_visits,
    )

    # Normalize gap frequency to per-day rate for comparable ratio
    baseline_days = max((baseline_end - baseline_start).days, 1)
    current_days = max((current_end - current_start).days, 1)
    hist_freq_per_day = baseline_gap_pattern["frequency"] / baseline_days
    curr_freq_per_day = current_gap_pattern["frequency"] / current_days
    gap_ratio, gap_fired = compute_gap_frequency_ratio(hist_freq_per_day, curr_freq_per_day)

    deviation_score, signals = compute_deviation_score(
        speed_z, speed_fired,
        port_novelty, port_fired,
        route_dev, route_fired,
        gap_ratio, gap_fired,
    )

    tier, risk_component = _score_to_tier(deviation_score)

    return {
        "vessel_id": vessel_id,
        "baseline_start": baseline_start,
        "baseline_end": baseline_end,
        "speed_stats": baseline_speed,
        "port_pattern": baseline_port_pattern,
        "route_pattern": baseline_route_pattern,
        "gap_pattern": baseline_gap_pattern,
        "temporal_pattern": baseline_temporal,
        "deviation_score": deviation_score,
        "deviation_signals": signals,
        "risk_score_component": risk_component,
        "tier": tier,
        "data_point_count": total_points,
        "current_speed_stats": current_speed,
        "current_gap_pattern": current_gap_pattern,
    }


# ── Persistence ──────────────────────────────────────────────────────────────


def _persist_profile(db: Session, profile: dict[str, Any]) -> None:
    """Persist or update a VesselBehavioralProfile record."""
    from app.models.vessel_behavioral_profile import VesselBehavioralProfile

    existing = (
        db.query(VesselBehavioralProfile)
        .filter(VesselBehavioralProfile.vessel_id == profile["vessel_id"])
        .first()
    )

    if existing:
        existing.baseline_start = profile["baseline_start"]
        existing.baseline_end = profile["baseline_end"]
        existing.speed_stats_json = json.dumps(profile["speed_stats"])
        existing.port_pattern_json = json.dumps(profile["port_pattern"])
        existing.route_pattern_json = json.dumps(profile["route_pattern"])
        existing.gap_pattern_json = json.dumps(profile["gap_pattern"])
        existing.temporal_pattern_json = json.dumps(profile["temporal_pattern"])
        existing.deviation_score = profile["deviation_score"]
        existing.deviation_signals_json = json.dumps(profile["deviation_signals"])
        existing.risk_score_component = profile["risk_score_component"]
        existing.tier = profile["tier"]
    else:
        record = VesselBehavioralProfile(
            vessel_id=profile["vessel_id"],
            baseline_start=profile["baseline_start"],
            baseline_end=profile["baseline_end"],
            speed_stats_json=json.dumps(profile["speed_stats"]),
            port_pattern_json=json.dumps(profile["port_pattern"]),
            route_pattern_json=json.dumps(profile["route_pattern"]),
            gap_pattern_json=json.dumps(profile["gap_pattern"]),
            temporal_pattern_json=json.dumps(profile["temporal_pattern"]),
            deviation_score=profile["deviation_score"],
            deviation_signals_json=json.dumps(profile["deviation_signals"]),
            risk_score_component=profile["risk_score_component"],
            tier=profile["tier"],
        )
        db.add(record)


# ── Public API ───────────────────────────────────────────────────────────────


def run_behavioral_baseline(db: Session) -> dict[str, Any]:
    """Run behavioral baseline profiling for all vessels.

    Gated by BEHAVIORAL_BASELINE_ENABLED feature flag.

    Returns statistics dict.
    """
    from app.models.vessel import Vessel

    stats: dict[str, Any] = {
        "vessels_processed": 0,
        "profiles_created": 0,
        "profiles_updated": 0,
        "skipped_insufficient_data": 0,
        "errors": [],
    }

    if not getattr(settings, "BEHAVIORAL_BASELINE_ENABLED", False):
        logger.info("Behavioral baseline disabled (BEHAVIORAL_BASELINE_ENABLED=False)")
        return stats

    vessels = db.query(Vessel.vessel_id).all()
    logger.info("Behavioral baseline: processing %d vessels", len(vessels))

    for (vessel_id,) in vessels:
        try:
            profile = build_vessel_profile(db, vessel_id)
            if profile is None:
                stats["skipped_insufficient_data"] += 1
                continue

            # Check if updating or creating
            from app.models.vessel_behavioral_profile import VesselBehavioralProfile

            existing = (
                db.query(VesselBehavioralProfile)
                .filter(VesselBehavioralProfile.vessel_id == vessel_id)
                .first()
            )
            _persist_profile(db, profile)
            if existing:
                stats["profiles_updated"] += 1
            else:
                stats["profiles_created"] += 1
            stats["vessels_processed"] += 1
        except Exception as exc:
            logger.warning("Behavioral baseline error for vessel %d: %s", vessel_id, exc)
            stats["errors"].append({"vessel_id": vessel_id, "error": str(exc)})

    db.commit()
    logger.info(
        "Behavioral baseline complete: %d processed, %d created, %d updated, %d skipped",
        stats["vessels_processed"],
        stats["profiles_created"],
        stats["profiles_updated"],
        stats["skipped_insufficient_data"],
    )
    return stats


def get_vessel_profile(db: Session, vessel_id: int) -> dict[str, Any] | None:
    """Get the behavioral baseline profile for a single vessel."""
    from app.models.vessel_behavioral_profile import VesselBehavioralProfile

    profile = (
        db.query(VesselBehavioralProfile)
        .filter(VesselBehavioralProfile.vessel_id == vessel_id)
        .first()
    )

    if profile is None:
        return None

    return {
        "profile_id": profile.profile_id,
        "vessel_id": profile.vessel_id,
        "baseline_start": profile.baseline_start.isoformat() if profile.baseline_start else None,
        "baseline_end": profile.baseline_end.isoformat() if profile.baseline_end else None,
        "speed_stats": json.loads(profile.speed_stats_json) if profile.speed_stats_json else None,
        "port_pattern": json.loads(profile.port_pattern_json) if profile.port_pattern_json else None,
        "route_pattern": json.loads(profile.route_pattern_json) if profile.route_pattern_json else None,
        "gap_pattern": json.loads(profile.gap_pattern_json) if profile.gap_pattern_json else None,
        "temporal_pattern": json.loads(profile.temporal_pattern_json) if profile.temporal_pattern_json else None,
        "deviation_score": profile.deviation_score,
        "deviation_signals": json.loads(profile.deviation_signals_json) if profile.deviation_signals_json else None,
        "risk_score_component": profile.risk_score_component,
        "tier": profile.tier,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def refresh_vessel_profile(db: Session, vessel_id: int) -> dict[str, Any] | None:
    """Refresh the behavioral baseline profile for a single vessel.

    Returns the updated profile dict or None if insufficient data.
    """
    if not getattr(settings, "BEHAVIORAL_BASELINE_ENABLED", False):
        return None

    profile = build_vessel_profile(db, vessel_id)
    if profile is None:
        return None

    _persist_profile(db, profile)
    db.commit()

    return get_vessel_profile(db, vessel_id)
