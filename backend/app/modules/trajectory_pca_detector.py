"""Trajectory PCA Anomaly Detector.

Applies Principal Component Analysis to trajectory segment feature vectors to
detect anomalous vessel movements via reconstruction error in the minor-component
subspace (Squared Prediction Error / SPE).

Algorithm:
1. Extract trajectory segments (reuses extract_segments from DBSCAN module)
2. Compute 8-feature vectors: centroid_lat, centroid_lon, bearing, distance_nm,
   duration_h, mean_sog, straightness, waypoint_count
3. Z-score normalize features (critical: maritime features have wildly different scales)
4. Compute covariance matrix and Jacobi eigendecomposition
5. Project into minor-component subspace (components n_components+1 to 8)
6. Compute SPE (Squared Prediction Error) as anomaly metric
7. Percentile-rank scores and assign tiers
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.modules.dbscan_trajectory_detector import extract_segments

logger = logging.getLogger(__name__)

# ── Feature names ────────────────────────────────────────────────────────────
FEATURE_NAMES = [
    "centroid_lat",
    "centroid_lon",
    "bearing",
    "distance_nm",
    "duration_h",
    "mean_sog",
    "straightness",
    "waypoint_count",
]
_NUM_FEATURES = len(FEATURE_NAMES)

# ── Tier thresholds (percentile-based) ───────────────────────────────────────
_TIER_HIGH_PERCENTILE = 0.95
_TIER_MEDIUM_PERCENTILE = 0.90
_TIER_LOW_PERCENTILE = 0.80

_DEFAULT_HIGH_SCORE = 30.0
_DEFAULT_MEDIUM_SCORE = 20.0
_DEFAULT_LOW_SCORE = 10.0

# ── Jacobi eigendecomposition defaults ───────────────────────────────────────
_JACOBI_MAX_ITERATIONS = 100
_JACOBI_TOLERANCE = 1e-10


# ── Z-score normalization ────────────────────────────────────────────────────


def zscore_normalize(
    data: list[list[float]],
) -> tuple[list[list[float]], list[float], list[float]]:
    """Z-score normalize each feature column.

    Returns (normalized_data, means, stds).
    Features with zero variance are left as zeros.
    """
    if not data:
        return [], [], []

    n = len(data)
    num_features = len(data[0])
    means = [0.0] * num_features
    stds = [0.0] * num_features

    # Compute means
    for row in data:
        for j in range(num_features):
            means[j] += row[j]
    for j in range(num_features):
        means[j] /= n

    # Compute standard deviations
    for row in data:
        for j in range(num_features):
            stds[j] += (row[j] - means[j]) ** 2
    for j in range(num_features):
        stds[j] = math.sqrt(stds[j] / n) if stds[j] > 0 else 0.0

    # Normalize
    normalized = []
    for row in data:
        new_row = []
        for j in range(num_features):
            if stds[j] > 1e-12:
                new_row.append((row[j] - means[j]) / stds[j])
            else:
                new_row.append(0.0)
        normalized.append(new_row)

    return normalized, means, stds


# ── Covariance matrix ───────────────────────────────────────────────────────


def compute_covariance_matrix(data: list[list[float]]) -> list[list[float]]:
    """Compute the covariance matrix from z-score normalized data.

    Assumes data is already centered (mean=0 after z-score normalization).
    Uses 1/n normalization (population covariance).
    """
    n = len(data)
    p = len(data[0]) if data else 0

    cov = [[0.0] * p for _ in range(p)]

    for row in data:
        for i in range(p):
            for j in range(i, p):
                cov[i][j] += row[i] * row[j]

    for i in range(p):
        for j in range(i, p):
            cov[i][j] /= n
            cov[j][i] = cov[i][j]

    return cov


# ── Jacobi eigendecomposition ────────────────────────────────────────────────


def jacobi_eigen(
    matrix: list[list[float]],
    max_iterations: int = _JACOBI_MAX_ITERATIONS,
    tolerance: float = _JACOBI_TOLERANCE,
) -> tuple[list[float], list[list[float]]]:
    """Jacobi eigendecomposition of a symmetric matrix.

    Returns (eigenvalues, eigenvectors) sorted by eigenvalue descending.
    Each column of the eigenvectors matrix is an eigenvector.
    """
    n = len(matrix)

    # Copy matrix to avoid mutation
    a = [row[:] for row in matrix]

    # Initialize eigenvector matrix as identity
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]

    for _ in range(max_iterations):
        # Find largest off-diagonal element
        max_val = 0.0
        p, q = 0, 1
        for i in range(n):
            for j in range(i + 1, n):
                if abs(a[i][j]) > max_val:
                    max_val = abs(a[i][j])
                    p, q = i, j

        if max_val < tolerance:
            break

        # Compute rotation angle
        if abs(a[p][p] - a[q][q]) < 1e-15:
            theta = math.pi / 4.0
        else:
            theta = 0.5 * math.atan2(2.0 * a[p][q], a[p][p] - a[q][q])

        cos_t = math.cos(theta)
        sin_t = math.sin(theta)

        # Apply Givens rotation
        new_a = [row[:] for row in a]

        for i in range(n):
            if i != p and i != q:
                new_a[i][p] = cos_t * a[i][p] + sin_t * a[i][q]
                new_a[p][i] = new_a[i][p]
                new_a[i][q] = -sin_t * a[i][p] + cos_t * a[i][q]
                new_a[q][i] = new_a[i][q]

        new_a[p][p] = cos_t**2 * a[p][p] + 2 * cos_t * sin_t * a[p][q] + sin_t**2 * a[q][q]
        new_a[q][q] = sin_t**2 * a[p][p] - 2 * cos_t * sin_t * a[p][q] + cos_t**2 * a[q][q]
        new_a[p][q] = 0.0
        new_a[q][p] = 0.0

        a = new_a

        # Update eigenvectors
        new_v = [row[:] for row in v]
        for i in range(n):
            new_v[i][p] = cos_t * v[i][p] + sin_t * v[i][q]
            new_v[i][q] = -sin_t * v[i][p] + cos_t * v[i][q]
        v = new_v

    eigenvalues = [a[i][i] for i in range(n)]

    # Sort by eigenvalue descending
    indices = sorted(range(n), key=lambda i: eigenvalues[i], reverse=True)
    sorted_eigenvalues = [eigenvalues[i] for i in indices]
    sorted_eigenvectors = [[v[row][col] for col in indices] for row in range(n)]

    return sorted_eigenvalues, sorted_eigenvectors


# ── SPE computation ──────────────────────────────────────────────────────────


def compute_spe(
    data: list[list[float]],
    eigenvectors: list[list[float]],
    n_components: int,
) -> list[float]:
    """Compute Squared Prediction Error (SPE) in the minor-component subspace.

    Projects each data point onto the minor components (components n_components
    through p-1) and computes the squared norm of the residual.
    """
    n = len(data)
    p = len(data[0]) if data else 0
    spe_values = []

    # Minor components: columns n_components to p-1 of eigenvectors
    for row in data:
        error = 0.0
        for k in range(n_components, p):
            # Project onto minor component k
            projection = sum(row[j] * eigenvectors[j][k] for j in range(p))
            error += projection**2
        spe_values.append(error)

    return spe_values


# ── Percentile ranking ───────────────────────────────────────────────────────


def percentile_rank(values: list[float]) -> list[float]:
    """Compute percentile rank for each value (0-1 scale)."""
    if not values:
        return []
    n = len(values)
    if n == 1:
        return [0.5]

    sorted_vals = sorted(values)
    ranks = []
    for v in values:
        # Count how many values are less than v
        count_below = sum(1 for sv in sorted_vals if sv < v)
        ranks.append(count_below / (n - 1))
    return ranks


# ── Tier assignment ──────────────────────────────────────────────────────────


def assign_tier(anomaly_score: float) -> tuple[str, float]:
    """Map percentile-ranked anomaly score to tier and risk score component."""
    if anomaly_score >= _TIER_HIGH_PERCENTILE:
        return "high", _DEFAULT_HIGH_SCORE
    elif anomaly_score >= _TIER_MEDIUM_PERCENTILE:
        return "medium", _DEFAULT_MEDIUM_SCORE
    elif anomaly_score >= _TIER_LOW_PERCENTILE:
        return "low", _DEFAULT_LOW_SCORE
    return "low", 0.0


# ── Feature extraction from segments ─────────────────────────────────────────


def segment_to_feature_vector(segment: Any) -> list[float]:
    """Extract 8-feature vector from a TrajectorySegment."""
    return [
        segment.centroid_lat,
        segment.centroid_lon,
        segment.bearing,
        segment.total_distance_nm,
        segment.duration_hours,
        segment.mean_sog,
        segment.straightness_ratio,
        float(len(segment.waypoints)),
    ]


# ── Top error features ──────────────────────────────────────────────────────


def identify_top_error_features(
    normalized_row: list[float],
    eigenvectors: list[list[float]],
    n_components: int,
    n_top: int = 3,
) -> list[dict[str, Any]]:
    """Identify which features contribute most to the reconstruction error."""
    p = len(normalized_row)
    feature_contributions = []

    for j in range(p):
        contribution = 0.0
        for k in range(n_components, p):
            projection = sum(normalized_row[f] * eigenvectors[f][k] for f in range(p))
            contribution += (projection * eigenvectors[j][k]) ** 2
        feature_contributions.append((j, contribution))

    feature_contributions.sort(key=lambda x: x[1], reverse=True)

    top = []
    for j, contrib in feature_contributions[:n_top]:
        top.append({
            "feature": FEATURE_NAMES[j],
            "contribution": round(contrib, 6),
            "normalized_value": round(normalized_row[j], 4),
        })

    return top


# ── Main entry point ─────────────────────────────────────────────────────────


def run_pca_detection(
    db: Session,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """Run PCA-based trajectory anomaly detection.

    1. Extract trajectory segments (reuses DBSCAN module's extract_segments)
    2. Compute 8-feature vectors per segment
    3. Z-score normalize
    4. PCA via Jacobi eigendecomposition
    5. Compute SPE in minor-component subspace
    6. Percentile-rank and assign tiers
    7. Persist TrajectoryPcaAnomaly records

    Returns summary statistics.
    """
    enabled = getattr(settings, "TRAJECTORY_PCA_ENABLED", False)
    if not enabled:
        return {
            "segments_processed": 0,
            "anomalies_created": 0,
            "disabled": True,
        }

    n_components = getattr(settings, "TRAJECTORY_PCA_N_COMPONENTS", 4)

    segments = extract_segments(db, date_from=date_from, date_to=date_to)

    if len(segments) < 3:
        logger.info("Too few segments (%d) for PCA detection", len(segments))
        return {
            "segments_processed": len(segments),
            "anomalies_created": 0,
        }

    # Step 2: Feature vectors
    raw_features = [segment_to_feature_vector(seg) for seg in segments]

    # Step 3: Z-score normalize
    normalized, means, stds = zscore_normalize(raw_features)

    # Step 4: Covariance + PCA
    cov_matrix = compute_covariance_matrix(normalized)
    eigenvalues, eigenvectors = jacobi_eigen(cov_matrix)

    # Clamp n_components to available dimensions
    effective_components = min(n_components, _NUM_FEATURES - 1)

    # Step 5: SPE
    spe_values = compute_spe(normalized, eigenvectors, effective_components)

    # Step 6: Percentile rank
    scores = percentile_rank(spe_values)

    # Step 7: Persist
    from app.models.trajectory_pca_anomaly import TrajectoryPcaAnomaly

    # Deduplicate: remove existing records in the same date range
    if date_from or date_to:
        dedup_query = db.query(TrajectoryPcaAnomaly)
        if date_from:
            dedup_query = dedup_query.filter(TrajectoryPcaAnomaly.segment_start >= date_from)
        if date_to:
            dedup_query = dedup_query.filter(TrajectoryPcaAnomaly.segment_end <= date_to)
        dedup_query.delete(synchronize_session=False)

    anomalies_created = 0
    for i, (seg, score, spe_val) in enumerate(zip(segments, scores, spe_values)):
        tier, risk_component = assign_tier(score)

        if score < _TIER_LOW_PERCENTILE:
            continue

        top_features = identify_top_error_features(
            normalized[i], eigenvectors, effective_components
        )

        feature_dict = {}
        for j, name in enumerate(FEATURE_NAMES):
            feature_dict[name] = round(raw_features[i][j], 6)

        evidence = {
            "n_components": effective_components,
            "total_segments": len(segments),
            "eigenvalues": [round(ev, 6) for ev in eigenvalues],
            "spe_raw": round(spe_val, 6),
            "variance_explained": round(
                sum(eigenvalues[:effective_components]) / max(sum(eigenvalues), 1e-12), 4
            ),
        }

        anomaly = TrajectoryPcaAnomaly(
            vessel_id=seg.vessel_id,
            segment_start=seg.window_start,
            segment_end=seg.window_end,
            reconstruction_error=round(spe_val, 6),
            anomaly_score=round(score, 6),
            risk_score_component=risk_component,
            tier=tier,
            feature_vector_json=json.dumps(feature_dict),
            principal_components_json=json.dumps(
                [[round(eigenvectors[r][c], 6) for c in range(len(eigenvalues))]
                 for r in range(_NUM_FEATURES)]
            ),
            top_error_features_json=json.dumps(top_features),
            evidence_json=json.dumps(evidence),
        )
        db.add(anomaly)
        anomalies_created += 1

    db.commit()

    result = {
        "segments_processed": len(segments),
        "anomalies_created": anomalies_created,
        "n_components": effective_components,
        "variance_explained": round(
            sum(eigenvalues[:effective_components]) / max(sum(eigenvalues), 1e-12), 4
        ),
    }
    logger.info("PCA trajectory detection complete: %s", result)
    return result


def get_vessel_pca_anomalies(
    db: Session,
    vessel_id: int,
) -> list[dict[str, Any]]:
    """Get PCA anomaly records for a specific vessel."""
    from app.models.trajectory_pca_anomaly import TrajectoryPcaAnomaly

    anomalies = (
        db.query(TrajectoryPcaAnomaly)
        .filter(TrajectoryPcaAnomaly.vessel_id == vessel_id)
        .order_by(TrajectoryPcaAnomaly.created_at.desc())
        .all()
    )

    return [
        {
            "anomaly_id": a.anomaly_id,
            "vessel_id": a.vessel_id,
            "segment_start": a.segment_start.isoformat() if a.segment_start else None,
            "segment_end": a.segment_end.isoformat() if a.segment_end else None,
            "reconstruction_error": a.reconstruction_error,
            "anomaly_score": a.anomaly_score,
            "risk_score_component": a.risk_score_component,
            "tier": a.tier,
            "feature_vector": json.loads(a.feature_vector_json) if a.feature_vector_json else None,
            "top_error_features": json.loads(a.top_error_features_json) if a.top_error_features_json else None,
            "evidence": json.loads(a.evidence_json) if a.evidence_json else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in anomalies
    ]
