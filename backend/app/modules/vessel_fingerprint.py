"""Behavioral fingerprinting — candidate ranking for vessel identity corroboration.

This module computes a 10-feature operational fingerprint for each vessel from
its AIS track data, then uses Mahalanobis distance to rank candidate matches.

This is a CORROBORATING signal only (top 5-20 candidates), NOT a 1:1 matcher.
It should never be used for auto-merge on its own.

Features (per 6-hour window):
  1. cruise_speed_median   — median SOG
  2. cruise_speed_iqr      — IQR of SOG
  3. sog_max               — max SOG
  4. acceleration_profile  — std dev of consecutive SOG differences
  5. turn_rate_median      — median of heading differences
  6. heading_stability     — std of heading
  7. draught_range         — max - min draught
  8. tx_interval_median    — median time between transmissions (seconds)
  9. tx_interval_var       — variance of transmission intervals
  10. deceleration_profile — mean of negative SOG differences
"""
from __future__ import annotations

import datetime
import logging
import math
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# ── Feature names (order matters for covariance matrix) ────────────────────────
FEATURE_NAMES = [
    "cruise_speed_median",
    "cruise_speed_iqr",
    "sog_max",
    "acceleration_profile",
    "turn_rate_median",
    "heading_stability",
    "draught_range",
    "tx_interval_median",
    "tx_interval_var",
    "deceleration_profile",
]

_NUM_FEATURES = len(FEATURE_NAMES)

# ── Thresholds ─────────────────────────────────────────────────────────────────
_MIN_POINTS = 300
_MIN_SPAN_HOURS = 24
_ANCHORED_SOG_THRESHOLD = 0.5
_WINDOW_HOURS = 6
_BATCH_CAP = 500
_DWT_TOLERANCE = 0.30  # +/-30%


# ── Pure-Python math helpers ──────────────────────────────────────────────────

def _median(values: list[float]) -> float:
    """Compute median of a list of floats."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _mean(values: list[float]) -> float:
    """Compute arithmetic mean."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _variance(values: list[float]) -> float:
    """Compute sample variance."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return sum((x - m) ** 2 for x in values) / (len(values) - 1)


def _std(values: list[float]) -> float:
    """Compute sample standard deviation."""
    return math.sqrt(_variance(values))


def _iqr(values: list[float]) -> float:
    """Compute interquartile range."""
    if len(values) < 4:
        return 0.0
    s = sorted(values)
    n = len(s)
    q1 = _median(s[: n // 2])
    q3 = _median(s[(n + 1) // 2 :])
    return q3 - q1


def _heading_diff(h1: float, h2: float) -> float:
    """Compute absolute heading difference in degrees, wrapped to [0, 180]."""
    d = abs(h1 - h2) % 360
    return d if d <= 180 else 360 - d


# ── Matrix helpers (pure-Python, no numpy required) ────────────────────────────

def _mat_zeros(n: int, m: int) -> list[list[float]]:
    """Create an n x m zero matrix."""
    return [[0.0] * m for _ in range(n)]


def _mat_transpose(mat: list[list[float]]) -> list[list[float]]:
    """Transpose a matrix."""
    if not mat:
        return []
    rows, cols = len(mat), len(mat[0])
    return [[mat[r][c] for r in range(rows)] for c in range(cols)]


def _mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """Multiply two matrices."""
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = _mat_zeros(rows_a, cols_b)
    for i in range(rows_a):
        for j in range(cols_b):
            s = 0.0
            for k in range(cols_a):
                s += a[i][k] * b[k][j]
            result[i][j] = s
    return result


def _mat_trace(mat: list[list[float]]) -> float:
    """Compute trace of a square matrix."""
    return sum(mat[i][i] for i in range(len(mat)))


def _mat_add(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """Element-wise addition of two matrices."""
    n = len(a)
    m = len(a[0])
    return [[a[i][j] + b[i][j] for j in range(m)] for i in range(n)]


def _mat_scale(mat: list[list[float]], s: float) -> list[list[float]]:
    """Scale a matrix by scalar s."""
    return [[mat[i][j] * s for j in range(len(mat[0]))] for i in range(len(mat))]


def _identity(n: int) -> list[list[float]]:
    """Create n x n identity matrix."""
    mat = _mat_zeros(n, n)
    for i in range(n):
        mat[i][i] = 1.0
    return mat


def _cholesky(mat: list[list[float]]) -> list[list[float]] | None:
    """Cholesky decomposition (lower triangular). Returns None if not positive definite."""
    n = len(mat)
    L = _mat_zeros(n, n)
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                val = mat[i][i] - s
                if val <= 0:
                    return None
                L[i][j] = math.sqrt(val)
            else:
                if L[j][j] == 0:
                    return None
                L[i][j] = (mat[i][j] - s) / L[j][j]
    return L


def _solve_lower(L: list[list[float]], b: list[float]) -> list[float]:
    """Solve L x = b where L is lower triangular."""
    n = len(b)
    x = [0.0] * n
    for i in range(n):
        s = sum(L[i][j] * x[j] for j in range(i))
        x[i] = (b[i] - s) / L[i][i]
    return x


def _mahalanobis_from_cov(
    diff: list[float], cov: list[list[float]], is_diagonal: bool
) -> float:
    """Compute Mahalanobis distance given a difference vector and covariance.

    d = sqrt(diff^T * Sigma^{-1} * diff)

    For diagonal-only covariance, inverts diagonal directly.
    For full covariance, uses Cholesky decomposition.
    """
    n = len(diff)
    if is_diagonal:
        total = 0.0
        for i in range(n):
            v = cov[i][i] if cov[i][i] > 1e-12 else 1e-12
            total += (diff[i] ** 2) / v
        return math.sqrt(total)

    # Full covariance — Cholesky
    L = _cholesky(cov)
    if L is None:
        # Fallback to diagonal
        return _mahalanobis_from_cov(diff, cov, True)

    y = _solve_lower(L, diff)
    return math.sqrt(sum(yi ** 2 for yi in y))


# ── Sample covariance computation ──────────────────────────────────────────────

def _compute_covariance(
    window_vectors: list[list[float]],
) -> tuple[list[list[float]], bool]:
    """Compute sample covariance matrix from per-window feature vectors.

    If >= 10 windows: full covariance with diagonal loading.
    If < 10 windows: diagonal-only (variances).

    Returns (covariance_matrix, is_diagonal_only).
    """
    n_windows = len(window_vectors)
    d = _NUM_FEATURES

    if n_windows < 2:
        # Not enough data — return identity-scaled
        return _identity(d), True

    # Compute column means
    means = [0.0] * d
    for vec in window_vectors:
        for j in range(d):
            means[j] += vec[j]
    means = [m / n_windows for m in means]

    if n_windows < 10:
        # Diagonal only: compute per-feature variance
        cov = _mat_zeros(d, d)
        for j in range(d):
            vals = [vec[j] for vec in window_vectors]
            cov[j][j] = _variance(vals) if _variance(vals) > 1e-12 else 1e-12
        return cov, True

    # Full sample covariance
    # Center the data
    centered = [[vec[j] - means[j] for j in range(d)] for vec in window_vectors]
    # Sigma = (1/(n-1)) * X^T X
    cov = _mat_zeros(d, d)
    for i in range(d):
        for j in range(d):
            s = 0.0
            for k in range(n_windows):
                s += centered[k][i] * centered[k][j]
            cov[i][j] = s / (n_windows - 1)

    # Diagonal loading: lambda = 0.01 * trace(Sigma) / d
    tr = _mat_trace(cov)
    lam = 0.01 * tr / d
    for i in range(d):
        cov[i][i] += lam

    return cov, False


# ── Per-window feature extraction ──────────────────────────────────────────────

def _extract_window_features(points: list[Any]) -> dict[str, float] | None:
    """Extract 10 features from a list of AIS points in a 6h window.

    Points must have: sog, heading (optional), draught (optional),
    timestamp_utc attributes. Returns None if insufficient data.
    """
    if len(points) < 3:
        return None

    sogs = []
    headings = []
    draughts = []
    timestamps = []

    for p in points:
        sog = getattr(p, "sog", None)
        if sog is not None:
            sogs.append(float(sog))
        heading = getattr(p, "heading", None)
        if heading is not None:
            headings.append(float(heading))
        draught = getattr(p, "draught", None)
        if draught is not None:
            draughts.append(float(draught))
        ts = getattr(p, "timestamp_utc", None)
        if ts is not None:
            timestamps.append(ts)

    if len(sogs) < 3:
        return None

    # SOG differences (consecutive)
    sog_diffs = [sogs[i + 1] - sogs[i] for i in range(len(sogs) - 1)]
    negative_diffs = [d for d in sog_diffs if d < 0]

    # Heading differences
    heading_diffs = []
    if len(headings) >= 2:
        heading_diffs = [
            _heading_diff(headings[i], headings[i + 1])
            for i in range(len(headings) - 1)
        ]

    # Transmission intervals (seconds)
    intervals = []
    if len(timestamps) >= 2:
        sorted_ts = sorted(timestamps)
        intervals = [
            (sorted_ts[i + 1] - sorted_ts[i]).total_seconds()
            for i in range(len(sorted_ts) - 1)
        ]

    # Draught range
    draught_range = 0.0
    if len(draughts) >= 2:
        draught_range = max(draughts) - min(draughts)

    return {
        "cruise_speed_median": _median(sogs),
        "cruise_speed_iqr": _iqr(sogs),
        "sog_max": max(sogs),
        "acceleration_profile": _std(sog_diffs) if len(sog_diffs) >= 2 else 0.0,
        "turn_rate_median": _median(heading_diffs) if heading_diffs else 0.0,
        "heading_stability": _std(headings) if len(headings) >= 2 else 0.0,
        "draught_range": draught_range,
        "tx_interval_median": _median(intervals) if intervals else 0.0,
        "tx_interval_var": _variance(intervals) if len(intervals) >= 2 else 0.0,
        "deceleration_profile": _mean(negative_diffs) if negative_diffs else 0.0,
    }


# ── Window segmentation ───────────────────────────────────────────────────────

def _segment_into_windows(
    points: list[Any], window_hours: int = _WINDOW_HOURS
) -> list[list[Any]]:
    """Segment time-ordered AIS points into fixed-duration windows."""
    if not points:
        return []

    # Sort by timestamp
    sorted_pts = sorted(points, key=lambda p: getattr(p, "timestamp_utc"))
    windows: list[list[Any]] = []
    current_window: list[Any] = []
    window_start = getattr(sorted_pts[0], "timestamp_utc")

    for p in sorted_pts:
        ts = getattr(p, "timestamp_utc")
        if (ts - window_start).total_seconds() >= window_hours * 3600:
            if current_window:
                windows.append(current_window)
            current_window = [p]
            window_start = ts
        else:
            current_window.append(p)

    if current_window:
        windows.append(current_window)

    return windows


# ── Public API ────────────────────────────────────────────────────────────────

def compute_fingerprint(
    db: Session, vessel_id: int
) -> Any | None:
    """Compute and store a behavioral fingerprint for a vessel.

    Returns the VesselFingerprint record, or None if insufficient data.
    """
    from app.models.ais_point import AISPoint
    from app.models.vessel_fingerprint import VesselFingerprint

    # Query AIS points, ordered by time
    points = (
        db.query(AISPoint)
        .filter(
            AISPoint.vessel_id == vessel_id,
            AISPoint.sog.isnot(None),
            AISPoint.sog >= _ANCHORED_SOG_THRESHOLD,
        )
        .order_by(AISPoint.timestamp_utc)
        .all()
    )

    if len(points) < _MIN_POINTS:
        logger.debug(
            "Vessel %d: only %d active points (need %d), skipping fingerprint",
            vessel_id, len(points), _MIN_POINTS,
        )
        return None

    # Check time span
    first_ts = points[0].timestamp_utc
    last_ts = points[-1].timestamp_utc
    span_hours = (last_ts - first_ts).total_seconds() / 3600
    if span_hours < _MIN_SPAN_HOURS:
        logger.debug(
            "Vessel %d: only %.1fh span (need %dh), skipping fingerprint",
            vessel_id, span_hours, _MIN_SPAN_HOURS,
        )
        return None

    # Segment into 6h windows
    windows = _segment_into_windows(points, _WINDOW_HOURS)

    # Extract features per window
    window_vectors: list[list[float]] = []
    for w in windows:
        features = _extract_window_features(w)
        if features is not None:
            window_vectors.append([features[name] for name in FEATURE_NAMES])

    if not window_vectors:
        return None

    # Aggregate: median across windows for each feature
    d = _NUM_FEATURES
    final_features: dict[str, float] = {}
    for j in range(d):
        vals = [wv[j] for wv in window_vectors]
        final_features[FEATURE_NAMES[j]] = _median(vals)

    # Covariance
    cov, is_diag = _compute_covariance(window_vectors)

    # Determine operational state (rough heuristic based on draught)
    op_state = "unknown"

    # Upsert fingerprint
    existing = (
        db.query(VesselFingerprint)
        .filter(VesselFingerprint.vessel_id == vessel_id)
        .first()
    )
    now = datetime.datetime.now(datetime.timezone.utc)

    if existing:
        existing.feature_vector_json = final_features
        existing.covariance_json = cov
        existing.sample_count = len(window_vectors)
        existing.point_count = len(points)
        existing.is_diagonal_only = is_diag
        existing.operational_state = op_state
        existing.updated_at = now
        fp = existing
    else:
        fp = VesselFingerprint(
            vessel_id=vessel_id,
            operational_state=op_state,
            feature_vector_json=final_features,
            covariance_json=cov,
            sample_count=len(window_vectors),
            point_count=len(points),
            is_diagonal_only=is_diag,
            created_at=now,
        )
        db.add(fp)

    db.flush()
    return fp


def mahalanobis_distance(fp1: Any, fp2: Any) -> float:
    """Compute symmetric Mahalanobis distance between two fingerprints.

    Returns min(d(fp1->fp2), d(fp2->fp1)) for symmetry.
    """
    vec1 = [fp1.feature_vector_json[name] for name in FEATURE_NAMES]
    vec2 = [fp2.feature_vector_json[name] for name in FEATURE_NAMES]
    diff = [vec1[i] - vec2[i] for i in range(_NUM_FEATURES)]

    # d(fp1 -> fp2) uses fp1's covariance
    d12 = _mahalanobis_from_cov(
        diff, fp1.covariance_json, fp1.is_diagonal_only
    )

    # d(fp2 -> fp1) uses fp2's covariance
    diff_rev = [-d for d in diff]
    d21 = _mahalanobis_from_cov(
        diff_rev, fp2.covariance_json, fp2.is_diagonal_only
    )

    return min(d12, d21)


def rank_candidates(
    db: Session, vessel_id: int, limit: int = 20
) -> list[dict[str, Any]]:
    """Rank candidate vessels by behavioral similarity to a target vessel.

    Steps:
      1. Get/compute target fingerprint
      2. Eliminative filtering: same vessel_type, DWT +/-30%, same ais_class
      3. Get/compute fingerprints for candidates (batch cap: 500)
      4. Compute distances, rank ascending
      5. Return top `limit` with distance bands

    Returns list of dicts with vessel_id, distance, band.
    """
    from app.models.vessel import Vessel
    from app.models.vessel_fingerprint import VesselFingerprint

    # Get or compute target fingerprint
    target_fp = (
        db.query(VesselFingerprint)
        .filter(VesselFingerprint.vessel_id == vessel_id)
        .first()
    )
    if target_fp is None:
        target_fp = compute_fingerprint(db, vessel_id)
    if target_fp is None:
        return []

    # Get target vessel for filtering
    target_vessel = db.query(Vessel).filter(Vessel.vessel_id == vessel_id).first()
    if target_vessel is None:
        return []

    # Eliminative filter
    query = db.query(Vessel).filter(
        Vessel.vessel_id != vessel_id,
        Vessel.merged_into_vessel_id.is_(None),
    )

    if target_vessel.vessel_type:
        query = query.filter(Vessel.vessel_type == target_vessel.vessel_type)
    if target_vessel.ais_class:
        query = query.filter(Vessel.ais_class == target_vessel.ais_class)
    if target_vessel.deadweight:
        dwt_lo = target_vessel.deadweight * (1 - _DWT_TOLERANCE)
        dwt_hi = target_vessel.deadweight * (1 + _DWT_TOLERANCE)
        query = query.filter(
            Vessel.deadweight.isnot(None),
            Vessel.deadweight >= dwt_lo,
            Vessel.deadweight <= dwt_hi,
        )

    candidates = query.limit(_BATCH_CAP).all()

    # Get/compute fingerprints for candidates
    scored: list[tuple[int, float]] = []
    for cand in candidates:
        cand_fp = (
            db.query(VesselFingerprint)
            .filter(VesselFingerprint.vessel_id == cand.vessel_id)
            .first()
        )
        if cand_fp is None:
            cand_fp = compute_fingerprint(db, cand.vessel_id)
        if cand_fp is None:
            continue

        dist = mahalanobis_distance(target_fp, cand_fp)
        scored.append((cand.vessel_id, dist))

    # Sort ascending by distance
    scored.sort(key=lambda x: x[1])

    # Take top `limit`
    top = scored[:limit]

    # Compute distance bands based on the full scored list
    if not scored:
        return []

    all_distances = [d for _, d in scored]
    all_distances.sort()
    n = len(all_distances)
    q1_idx = n // 4
    q2_idx = n // 2
    q3_idx = (3 * n) // 4

    q1_val = all_distances[q1_idx] if q1_idx < n else float("inf")
    q2_val = all_distances[q2_idx] if q2_idx < n else float("inf")
    q3_val = all_distances[q3_idx] if q3_idx < n else float("inf")

    results: list[dict[str, Any]] = []
    for vid, dist in top:
        if dist <= q1_val:
            band = "CLOSE"
        elif dist <= q2_val:
            band = "SIMILAR"
        else:
            band = "DIFFERENT"
        results.append({
            "vessel_id": vid,
            "distance": round(dist, 4),
            "band": band,
        })

    return results


def fingerprint_merge_bonus(
    db: Session, vessel_a_id: int, vessel_b_id: int
) -> int:
    """Compute merge confidence bonus/penalty from fingerprint similarity.

    Bottom quartile distance -> +15
    Bottom half distance     -> +10
    Top quartile distance    -> -5
    Otherwise                -> 0
    """
    from app.models.vessel_fingerprint import VesselFingerprint

    fp_a = (
        db.query(VesselFingerprint)
        .filter(VesselFingerprint.vessel_id == vessel_a_id)
        .first()
    )
    fp_b = (
        db.query(VesselFingerprint)
        .filter(VesselFingerprint.vessel_id == vessel_b_id)
        .first()
    )

    if fp_a is None or fp_b is None:
        return 0

    dist = mahalanobis_distance(fp_a, fp_b)

    # We need the population distribution to determine quartiles.
    # Use fp_a's covariance trace as a scale reference.
    # For a chi-squared(d) distribution with d=10:
    #   Median ~= 9.34, Q1 ~= 6.74, Q3 ~= 12.55
    # Mahalanobis distance is sqrt of chi-squared.
    # Q1 ~= sqrt(6.74) ~= 2.60, Median ~= sqrt(9.34) ~= 3.06, Q3 ~= sqrt(12.55) ~= 3.54
    q1_threshold = 2.60
    median_threshold = 3.06
    q3_threshold = 3.54

    if dist <= q1_threshold:
        return 15
    elif dist <= median_threshold:
        return 10
    elif dist >= q3_threshold:
        return -5
    return 0


def run_fingerprint_computation(db: Session) -> dict[str, Any]:
    """Batch fingerprint computation for all vessels with sufficient AIS data.

    Gated by FINGERPRINT_ENABLED feature flag.
    Returns statistics dict.
    """
    from app.models.vessel import Vessel

    stats: dict[str, Any] = {
        "vessels_processed": 0,
        "fingerprints_created": 0,
        "fingerprints_updated": 0,
        "skipped_insufficient_data": 0,
        "errors": [],
    }

    if not settings.FINGERPRINT_ENABLED:
        logger.info("Fingerprint computation disabled (FINGERPRINT_ENABLED=False)")
        return stats

    vessels = (
        db.query(Vessel)
        .filter(Vessel.merged_into_vessel_id.is_(None))
        .all()
    )

    for vessel in vessels:
        stats["vessels_processed"] += 1
        try:
            from app.models.vessel_fingerprint import VesselFingerprint

            existing = (
                db.query(VesselFingerprint)
                .filter(VesselFingerprint.vessel_id == vessel.vessel_id)
                .first()
            )
            had_existing = existing is not None

            fp = compute_fingerprint(db, vessel.vessel_id)
            if fp is None:
                stats["skipped_insufficient_data"] += 1
            elif had_existing:
                stats["fingerprints_updated"] += 1
            else:
                stats["fingerprints_created"] += 1
        except Exception as exc:
            logger.warning(
                "Fingerprint failed for vessel %d: %s", vessel.vessel_id, exc
            )
            stats["errors"].append(f"vessel_{vessel.vessel_id}: {exc}")

    db.commit()
    logger.info(
        "Fingerprint computation complete: %d processed, %d created, %d updated, %d skipped",
        stats["vessels_processed"],
        stats["fingerprints_created"],
        stats["fingerprints_updated"],
        stats["skipped_insufficient_data"],
    )
    return stats
