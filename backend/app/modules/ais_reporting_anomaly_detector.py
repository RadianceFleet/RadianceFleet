"""AIS Reporting Rate Anomaly Detector.

Detects transponder manipulation by analysing AIS reporting patterns.
Three complementary signals:

1. **Transmission interval irregularity**: Coefficient of variation (CV) of
   inter-message intervals.  Normal Class A: CV ~0.3-0.5.  Manipulated
   transponders show CV > 1.5.

2. **Pre-gap rate decay**: Gradual reporting rate decrease in the hour before
   an AIS gap indicates deliberate transponder shutdown (vs sudden cutoff from
   equipment failure or coverage loss).

3. **Position-dependent transmission changes**: Reporting rate drops
   correlating with entry into STS corridors or dark zones from corridors.yaml.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.base import CorridorTypeEnum, SpoofingTypeEnum
from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent
from app.models.spoofing_anomaly import SpoofingAnomaly

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default thresholds (overridden by risk_scoring.yaml at call time)
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "interval_cv_high": 25,
    "interval_cv_medium": 15,
    "pre_gap_rate_decay": 20,
    "corridor_rate_drop": 20,
    "min_points_for_analysis": 50,
}


def _load_thresholds() -> dict:
    """Load thresholds from risk_scoring.yaml, falling back to defaults."""
    try:
        from app.modules.scoring_config import load_scoring_config

        cfg = load_scoring_config().get("ais_reporting_anomaly", {})
        return {k: int(cfg.get(k, v)) for k, v in _DEFAULTS.items()}
    except Exception:
        return dict(_DEFAULTS)

# Processing constants
BATCH_SIZE = 500
WINDOW_HOURS = 168  # 7 days look-back for baseline
PRE_GAP_WINDOW_HOURS = 1  # 1 hour before gap
BASELINE_DAYS = 7
CORRIDOR_TYPES_MONITORED = {
    CorridorTypeEnum.STS_ZONE,
    CorridorTypeEnum.DARK_ZONE,
    CorridorTypeEnum.EXPORT_ROUTE,
}


# ---------------------------------------------------------------------------
# Signal 1: Transmission interval irregularity
# ---------------------------------------------------------------------------


def compute_interval_cv(timestamps: list[datetime]) -> float | None:
    """Compute coefficient of variation of inter-message intervals.

    Returns None if fewer than 2 timestamps.
    """
    if len(timestamps) < 2:
        return None

    intervals: list[float] = []
    for i in range(1, len(timestamps)):
        dt = (timestamps[i] - timestamps[i - 1]).total_seconds()
        intervals.append(dt)

    if not intervals:
        return None

    mean_interval = sum(intervals) / len(intervals)
    if mean_interval <= 0:
        return None

    variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
    std_interval = math.sqrt(variance)

    return std_interval / mean_interval


def _score_interval_cv(cv: float, thresholds: dict | None = None) -> int:
    """Return risk score points based on CV threshold."""
    t = thresholds or _load_thresholds()
    if cv > 2.0:
        return t["interval_cv_high"]
    elif cv > 1.5:
        return t["interval_cv_medium"]
    return 0


# ---------------------------------------------------------------------------
# Signal 2: Pre-gap rate decay
# ---------------------------------------------------------------------------


def compute_pre_gap_decay(
    timestamps: list[datetime],
    gap_start: datetime,
    baseline_rate: float,
) -> dict | None:
    """Analyse reporting rate in the hour before a gap.

    Returns dict with pre_gap_rate, baseline_rate, ratio, is_decay
    or None if insufficient data.
    """
    if baseline_rate <= 0:
        return None

    pre_gap_window_start = gap_start - timedelta(hours=PRE_GAP_WINDOW_HOURS)
    pre_gap_points = [t for t in timestamps if pre_gap_window_start <= t <= gap_start]

    if len(pre_gap_points) < 2:
        return None

    # Messages per hour in pre-gap window
    pre_gap_rate = len(pre_gap_points) / PRE_GAP_WINDOW_HOURS
    ratio = pre_gap_rate / baseline_rate

    return {
        "pre_gap_rate": round(pre_gap_rate, 2),
        "baseline_rate": round(baseline_rate, 2),
        "ratio": round(ratio, 4),
        "is_decay": ratio < 0.25,
    }


def compute_baseline_rate(timestamps: list[datetime]) -> float:
    """Compute baseline reporting rate (messages/hour) as median over window.

    Uses median of hourly rates across available data.
    """
    if len(timestamps) < 2:
        return 0.0

    # Total messages / total hours
    span_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
    if span_seconds <= 0:
        return 0.0

    span_hours = span_seconds / 3600.0
    return len(timestamps) / span_hours


# ---------------------------------------------------------------------------
# Signal 3: Position-dependent transmission changes
# ---------------------------------------------------------------------------


def _load_corridor_geometries(db: Session) -> list[tuple[int, str, object]]:
    """Load monitored corridor geometries as Shapely objects.

    Returns list of (corridor_id, corridor_name, shapely_geometry).
    """
    from app.modules.corridor_correlator import _geometry_wkt
    from app.utils.geo import load_geometry

    corridors = (
        db.query(Corridor)
        .filter(
            Corridor.corridor_type.in_([ct.value for ct in CORRIDOR_TYPES_MONITORED]),
            Corridor.geometry.isnot(None),
        )
        .all()
    )

    result = []
    for c in corridors:
        wkt = _geometry_wkt(c.geometry)
        if wkt is None:
            continue
        try:
            shape = load_geometry(wkt)
            if shape is not None:
                result.append((c.corridor_id, c.name, shape))
        except Exception:
            logger.debug("Failed to parse geometry for corridor %s", c.name)
    return result


def detect_corridor_rate_change(
    points: list[tuple[datetime, float, float]],
    corridor_geometries: list[tuple[int, str, object]],
    baseline_rate: float,
) -> list[dict]:
    """Detect reporting rate changes correlated with corridor entry/exit.

    *points*: list of (timestamp, lat, lon) sorted by time.
    Returns list of detected rate-change events.
    """
    from shapely.geometry import Point

    if baseline_rate <= 0 or len(points) < 10:
        return []

    detections: list[dict] = []

    for corridor_id, corridor_name, shape in corridor_geometries:
        # Classify each point as inside or outside the corridor
        inside_flags = []
        for _ts, lat, lon in points:
            pt = Point(lon, lat)
            inside_flags.append(shape.buffer(0.1).contains(pt))

        # Find transitions: outside -> inside
        for i in range(1, len(inside_flags)):
            if not inside_flags[i - 1] and inside_flags[i]:
                # Entry detected — compute rate before and after
                entry_time = points[i][0]

                # Rate in 1h before entry
                before_start = entry_time - timedelta(hours=1)
                before_points = [
                    p for p in points if before_start <= p[0] < entry_time
                ]

                # Rate in 1h after entry
                after_end = entry_time + timedelta(hours=1)
                after_points = [
                    p for p in points if entry_time <= p[0] <= after_end
                ]

                if len(before_points) < 3 or len(after_points) < 1:
                    continue

                rate_before = len(before_points)  # msgs in 1h
                rate_after = len(after_points)  # msgs in 1h

                if rate_before > 0:
                    drop_ratio = rate_after / rate_before
                    if drop_ratio < 0.5:
                        detections.append({
                            "corridor_id": corridor_id,
                            "corridor_name": corridor_name,
                            "entry_time": entry_time.isoformat(),
                            "rate_before": rate_before,
                            "rate_after": rate_after,
                            "drop_ratio": round(drop_ratio, 4),
                        })

    return detections


# ---------------------------------------------------------------------------
# Main detection entry point
# ---------------------------------------------------------------------------


def analyse_vessel_reporting(
    db: Session,
    vessel_id: int,
) -> dict:
    """Analyse AIS reporting patterns for a single vessel.

    Returns dict with analysis results for all three signals.
    """
    thresholds = _load_thresholds()
    cutoff = datetime.now(UTC) - timedelta(hours=WINDOW_HOURS)

    # Load AIS points
    points_q = (
        db.query(
            AISPoint.timestamp_utc,
            AISPoint.lat,
            AISPoint.lon,
        )
        .filter(
            AISPoint.vessel_id == vessel_id,
            AISPoint.timestamp_utc >= cutoff,
            AISPoint.timestamp_utc.isnot(None),
        )
        .order_by(AISPoint.timestamp_utc)
        .all()
    )

    if len(points_q) < thresholds["min_points_for_analysis"]:
        return {
            "vessel_id": vessel_id,
            "status": "insufficient_data",
            "points_count": len(points_q),
        }

    timestamps = [p.timestamp_utc for p in points_q]
    point_tuples = [(p.timestamp_utc, p.lat, p.lon) for p in points_q]

    result: dict = {
        "vessel_id": vessel_id,
        "status": "analysed",
        "points_count": len(points_q),
        "signals": {},
    }

    # Signal 1: Interval CV
    cv = compute_interval_cv(timestamps)
    if cv is not None:
        result["signals"]["interval_cv"] = {
            "cv": round(cv, 4),
            "score": _score_interval_cv(cv, thresholds),
        }

    # Signal 2: Pre-gap decay
    baseline_rate = compute_baseline_rate(timestamps)
    gaps = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.vessel_id == vessel_id,
            AISGapEvent.gap_start_utc >= cutoff,
        )
        .order_by(AISGapEvent.gap_start_utc)
        .all()
    )

    decay_detections = []
    for gap in gaps:
        decay = compute_pre_gap_decay(timestamps, gap.gap_start_utc, baseline_rate)
        if decay and decay["is_decay"]:
            decay["gap_event_id"] = gap.gap_event_id
            decay_detections.append(decay)

    if decay_detections:
        result["signals"]["pre_gap_decay"] = {
            "detections": decay_detections,
            "score": thresholds["pre_gap_rate_decay"],
        }

    # Signal 3: Corridor rate change
    corridor_geometries = _load_corridor_geometries(db)
    if corridor_geometries:
        corridor_detections = detect_corridor_rate_change(
            point_tuples, corridor_geometries, baseline_rate
        )
        if corridor_detections:
            result["signals"]["corridor_rate_drop"] = {
                "detections": corridor_detections,
                "score": thresholds["corridor_rate_drop"],
            }

    return result


def run_reporting_anomaly_detection(db: Session) -> dict:
    """Run reporting anomaly detection across all vessels.

    Returns dict with keys: checked, skipped, flagged, status.
    """
    if not settings.AIS_REPORTING_ANOMALY_ENABLED:
        return {"status": "disabled"}

    thresholds = _load_thresholds()
    cutoff = datetime.now(UTC) - timedelta(hours=WINDOW_HOURS)

    # Get vessels with enough AIS data
    vessel_ids = (
        db.query(AISPoint.vessel_id)
        .filter(AISPoint.timestamp_utc >= cutoff)
        .group_by(AISPoint.vessel_id)
        .having(func.count(AISPoint.ais_point_id) >= thresholds["min_points_for_analysis"])
        .limit(BATCH_SIZE)
        .all()
    )
    vessel_ids = [v[0] for v in vessel_ids]

    checked = 0
    skipped = 0
    flagged = 0

    for vid in vessel_ids:
        analysis = analyse_vessel_reporting(db, vid)

        if analysis["status"] != "analysed":
            skipped += 1
            continue

        checked += 1
        signals = analysis.get("signals", {})

        # Determine max score from all signals
        max_score = 0
        evidence: dict = {"signals": {}}

        if "interval_cv" in signals:
            sig = signals["interval_cv"]
            if sig["score"] > 0:
                evidence["signals"]["interval_cv"] = sig
                max_score = max(max_score, sig["score"])

        if "pre_gap_decay" in signals:
            sig = signals["pre_gap_decay"]
            evidence["signals"]["pre_gap_decay"] = sig
            max_score = max(max_score, sig["score"])

        if "corridor_rate_drop" in signals:
            sig = signals["corridor_rate_drop"]
            evidence["signals"]["corridor_rate_drop"] = sig
            max_score = max(max_score, sig["score"])

        if max_score <= 0:
            continue

        # Check for existing anomaly (dedup)
        existing = (
            db.query(SpoofingAnomaly)
            .filter(
                SpoofingAnomaly.vessel_id == vid,
                SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.REPORTING_RATE_ANOMALY,
                SpoofingAnomaly.start_time_utc >= cutoff,
            )
            .first()
        )
        if existing:
            continue

        now = datetime.now(UTC)
        evidence["points_analysed"] = analysis["points_count"]

        anomaly = SpoofingAnomaly(
            vessel_id=vid,
            anomaly_type=SpoofingTypeEnum.REPORTING_RATE_ANOMALY,
            start_time_utc=cutoff,
            end_time_utc=now,
            risk_score_component=max_score,
            evidence_json=evidence,
        )
        db.add(anomaly)
        flagged += 1

    if flagged > 0:
        db.commit()

    logger.info(
        "Reporting anomaly: checked=%d skipped=%d flagged=%d",
        checked,
        skipped,
        flagged,
    )

    return {
        "status": "ok",
        "checked": checked,
        "skipped": skipped,
        "flagged": flagged,
    }
