"""Phase K: Statistical Track Naturalness Detector.

Detects pre-programmed route spoofing by analysing Kalman-filter residuals.
Real tracks exhibit GPS noise, wind/current perturbation, and human steering
variation that synthetic tracks lack. Algorithm adapted from Pohontu et al.
(2024, >98% accuracy on AIS data alone).

Five statistical features of innovation residuals discriminate real from
synthetic tracks:

1. Mean absolute residual (real ~50-200 m, synthetic <20 m)
2. Residual standard deviation (real: high, synthetic: near-zero)
3. Speed-change autocorrelation at lag-1 (real: positive, synthetic: ~0)
4. Heading-change entropy (real: ~2.5-3.5 bits, synthetic: too regular)
5. Course-change kurtosis (real: leptokurtic, synthetic: normal)

If >=3 of 5 features fall outside natural bounds the track is flagged as
SYNTHETIC_TRACK with confidence tiers: 5/5 -> HIGH, 4/5 -> MED, 3/5 -> LOW.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ais_point import AISPoint
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.base import SpoofingTypeEnum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load thresholds from risk_scoring.yaml (with hardcoded fallbacks)
# ---------------------------------------------------------------------------
try:
    from app.modules.risk_scoring import load_scoring_config
    _cfg = load_scoring_config()
    _tn_cfg = _cfg.get("track_naturalness", {})
except Exception:
    _tn_cfg = {}

SCORE_HIGH = int(_tn_cfg.get("synthetic_track_high", 45))
SCORE_MEDIUM = int(_tn_cfg.get("synthetic_track_medium", 35))
SCORE_LOW = int(_tn_cfg.get("synthetic_track_low", 25))

# Processing guardrails
BATCH_SIZE = 500       # max vessels per run
MAX_POINTS = 500       # subsample to cap Kalman iterations
MIN_POINTS = 15        # minimum for meaningful statistics
WINDOW_HOURS = 48      # look-back window
ANCHORED_SOG_KN = 0.5  # median SOG below which vessel is anchored

# Natural bounds for the 5 features (calibrated from research)
NATURAL_BOUNDS = {
    "mean_abs_residual_m":  (20.0, None),   # real >= 20m; synthetic < 20m
    "residual_std_m":       (15.0, None),   # real >= 15m
    "speed_autocorr_lag1":  (0.05, None),   # real >= 0.05; synthetic ~0
    "heading_entropy_bits": (1.5, 4.5),     # real 1.5-4.5; synthetic outside
    "course_kurtosis":      (3.5, None),    # real leptokurtic > 3.5
}


# ---------------------------------------------------------------------------
# Core algorithm
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _kalman_residuals(
    points: list[tuple[float, float, float, float]],
) -> list[float]:
    """Lightweight constant-velocity Kalman filter returning position residuals.

    *points*: list of (timestamp_epoch, lat, lon, sog_kn).
    Returns per-point residual distances in metres.

    The filter uses a simplified 2D constant-velocity model:
    - State: [lat, lon, vlat, vlon]
    - Prediction: constant velocity between observations
    - Update: simple gain blending (alpha = 0.3)
    """
    if len(points) < 2:
        return []

    alpha = 0.3  # Kalman gain (simplified)
    residuals: list[float] = []

    # Initialise state from first two points
    t0, lat0, lon0, _ = points[0]
    t1, lat1, lon1, _ = points[1]
    dt0 = max(t1 - t0, 1.0)
    vlat = (lat1 - lat0) / dt0
    vlon = (lon1 - lon0) / dt0
    state_lat, state_lon = lat1, lon1
    residuals.append(0.0)  # first point has no prediction
    residuals.append(0.0)  # second point initialises

    for i in range(2, len(points)):
        t_prev = points[i - 1][0]
        t_cur, obs_lat, obs_lon, _ = points[i]
        dt = max(t_cur - t_prev, 1.0)

        # Predict
        pred_lat = state_lat + vlat * dt
        pred_lon = state_lon + vlon * dt

        # Innovation (residual)
        res_m = _haversine_m(pred_lat, pred_lon, obs_lat, obs_lon)
        residuals.append(res_m)

        # Update
        state_lat = pred_lat + alpha * (obs_lat - pred_lat)
        state_lon = pred_lon + alpha * (obs_lon - pred_lon)
        vlat = vlat + alpha * ((obs_lat - pred_lat) / dt)
        vlon = vlon + alpha * ((obs_lon - pred_lon) / dt)

    return residuals


def _compute_features(
    points: list[tuple[float, float, float, float]],
    residuals: list[float],
) -> dict[str, Optional[float]]:
    """Compute the 5 statistical features over Kalman residuals and track data."""
    features: dict[str, Optional[float]] = {}

    # Feature 1: Mean absolute residual
    valid_res = [r for r in residuals[2:] if r is not None]
    features["mean_abs_residual_m"] = (
        sum(valid_res) / len(valid_res) if valid_res else None
    )

    # Feature 2: Residual standard deviation
    if len(valid_res) >= 2:
        mean_r = sum(valid_res) / len(valid_res)
        var = sum((r - mean_r) ** 2 for r in valid_res) / (len(valid_res) - 1)
        features["residual_std_m"] = math.sqrt(var)
    else:
        features["residual_std_m"] = None

    # Feature 3: Speed-change autocorrelation at lag-1
    sogs = [p[3] for p in points if p[3] is not None]
    if len(sogs) >= 3:
        diffs = [sogs[i + 1] - sogs[i] for i in range(len(sogs) - 1)]
        if len(diffs) >= 2:
            mean_d = sum(diffs) / len(diffs)
            var_d = sum((d - mean_d) ** 2 for d in diffs)
            if var_d > 1e-12:
                cov = sum(
                    (diffs[i] - mean_d) * (diffs[i + 1] - mean_d)
                    for i in range(len(diffs) - 1)
                )
                features["speed_autocorr_lag1"] = cov / var_d
            else:
                features["speed_autocorr_lag1"] = 0.0
        else:
            features["speed_autocorr_lag1"] = None
    else:
        features["speed_autocorr_lag1"] = None

    # Compute bearings from consecutive lat/lon (shared by Features 4 & 5)
    bearings: list[float] = []
    if len(points) >= 2:
        for i in range(1, len(points)):
            lat1, lon1 = math.radians(points[i - 1][1]), math.radians(points[i - 1][2])
            lat2, lon2 = math.radians(points[i][1]), math.radians(points[i][2])
            dlon = lon2 - lon1
            x = math.sin(dlon) * math.cos(lat2)
            y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
            bearing = math.degrees(math.atan2(x, y)) % 360
            bearings.append(bearing)

    # Compute bearing changes (shared by Features 4 & 5)
    bearing_changes: list[float] = []
    if len(bearings) >= 2:
        for i in range(1, len(bearings)):
            dc = (bearings[i] - bearings[i - 1] + 180) % 360 - 180
            bearing_changes.append(dc)

    # Feature 4: Heading-change entropy (binned into 36 bins of 10°)
    if len(bearing_changes) >= 1:
        n_bins = 36
        bins = [0] * n_bins
        for dc in bearing_changes:
            idx = int(((dc + 180) % 360) / 10) % n_bins
            bins[idx] += 1
        total = sum(bins)
        if total > 0:
            entropy = 0.0
            for count in bins:
                if count > 0:
                    p = count / total
                    entropy -= p * math.log2(p)
            features["heading_entropy_bits"] = entropy
        else:
            features["heading_entropy_bits"] = None
    else:
        features["heading_entropy_bits"] = None

    # Feature 5: Course-change kurtosis
    if len(bearing_changes) >= 4:
        n = len(bearing_changes)
        mean_bc = sum(bearing_changes) / n
        var_bc = sum((bc - mean_bc) ** 2 for bc in bearing_changes) / n
        if var_bc > 1e-12:
            m4 = sum((bc - mean_bc) ** 4 for bc in bearing_changes) / n
            features["course_kurtosis"] = m4 / (var_bc ** 2)
        else:
            features["course_kurtosis"] = None
    else:
        features["course_kurtosis"] = None

    return features


def _count_outside_bounds(features: dict[str, Optional[float]]) -> int:
    """Count how many of the 5 features fall outside natural bounds."""
    outside = 0
    for key, (low, high) in NATURAL_BOUNDS.items():
        val = features.get(key)
        if val is None:
            continue
        if low is not None and val < low:
            outside += 1
        elif high is not None and val > high:
            outside += 1
    return outside


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_track_naturalness_detection(db: Session) -> dict:
    """Run track naturalness detection across all vessels.

    Returns dict with keys: checked, skipped, flagged, status.
    """
    if not settings.TRACK_NATURALNESS_ENABLED:
        return {"status": "disabled"}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)

    # Get vessels with enough AIS data in the window
    vessel_ids = (
        db.query(AISPoint.vessel_id)
        .filter(AISPoint.timestamp_utc >= cutoff)
        .group_by(AISPoint.vessel_id)
        .having(func.count(AISPoint.ais_point_id) >= MIN_POINTS)
        .limit(BATCH_SIZE)
        .all()
    )
    vessel_ids = [v[0] for v in vessel_ids]

    checked = 0
    skipped = 0
    flagged = 0

    for vid in vessel_ids:
        points_q = (
            db.query(
                AISPoint.timestamp_utc,
                AISPoint.lat,
                AISPoint.lon,
                AISPoint.sog,
            )
            .filter(
                AISPoint.vessel_id == vid,
                AISPoint.timestamp_utc >= cutoff,
            )
            .order_by(AISPoint.timestamp_utc)
            .all()
        )

        if len(points_q) < MIN_POINTS:
            skipped += 1
            continue

        # Subsample if too many points
        if len(points_q) > MAX_POINTS:
            step = len(points_q) // MAX_POINTS
            points_q = points_q[::step][:MAX_POINTS]

        # Check if anchored (median SOG < threshold)
        sogs = [p.sog for p in points_q if p.sog is not None]
        if sogs:
            sorted_sogs = sorted(sogs)
            median_sog = sorted_sogs[len(sorted_sogs) // 2]
            if median_sog < ANCHORED_SOG_KN:
                skipped += 1
                continue

        # Convert to tuples for processing
        point_tuples = []
        for p in points_q:
            ts_epoch = p.timestamp_utc.timestamp() if hasattr(p.timestamp_utc, 'timestamp') else float(p.timestamp_utc)
            sog_val = p.sog if p.sog is not None else 0.0
            point_tuples.append((ts_epoch, p.lat, p.lon, sog_val))

        # Run Kalman filter
        residuals = _kalman_residuals(point_tuples)
        if len(residuals) < MIN_POINTS:
            skipped += 1
            continue

        # Compute features
        features = _compute_features(point_tuples, residuals)

        # Count features outside natural bounds
        outside_count = _count_outside_bounds(features)

        checked += 1

        if outside_count >= 3:
            # Determine tier
            if outside_count >= 5:
                score = SCORE_HIGH
                tier = "high"
            elif outside_count >= 4:
                score = SCORE_MEDIUM
                tier = "medium"
            else:
                score = SCORE_LOW
                tier = "low"

            # Check for existing anomaly (dedup)
            existing = (
                db.query(SpoofingAnomaly)
                .filter(
                    SpoofingAnomaly.vessel_id == vid,
                    SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.SYNTHETIC_TRACK,
                    SpoofingAnomaly.start_time_utc >= cutoff,
                )
                .first()
            )
            if existing:
                continue

            now = datetime.now(timezone.utc)
            anomaly = SpoofingAnomaly(
                vessel_id=vid,
                anomaly_type=SpoofingTypeEnum.SYNTHETIC_TRACK,
                start_time_utc=points_q[0].timestamp_utc if hasattr(points_q[0], 'timestamp_utc') else now,
                end_time_utc=points_q[-1].timestamp_utc if hasattr(points_q[-1], 'timestamp_utc') else now,
                risk_score_component=score,
                evidence_json={
                    "tier": tier,
                    "features_outside_bounds": outside_count,
                    "features": {
                        k: round(v, 4) if v is not None else None
                        for k, v in features.items()
                    },
                    "points_analysed": len(point_tuples),
                    "natural_bounds": {
                        k: {"min": lo, "max": hi}
                        for k, (lo, hi) in NATURAL_BOUNDS.items()
                    },
                },
            )
            db.add(anomaly)
            flagged += 1

    if flagged > 0:
        db.commit()

    logger.info(
        "Track naturalness: checked=%d skipped=%d flagged=%d",
        checked, skipped, flagged,
    )

    return {
        "status": "ok",
        "checked": checked,
        "skipped": skipped,
        "flagged": flagged,
    }
