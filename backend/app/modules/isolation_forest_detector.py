"""Isolation Forest Multi-Feature Anomaly Scorer.

Pure-Python Isolation Forest implementation for vessel behavioral anomaly
detection. Builds an ensemble of random binary trees from vessel fingerprint
feature vectors, then scores each vessel by average path length.

Features (13 total):
  10 from VesselFingerprint.feature_vector_json:
    cruise_speed_median, cruise_speed_iqr, sog_max, acceleration_profile,
    turn_rate_median, heading_stability, draught_range, tx_interval_median,
    tx_interval_var, deceleration_profile
  3 augmented:
    gap_frequency_30d, loiter_frequency_30d, sts_count_90d
"""

from __future__ import annotations

import datetime
import logging
import math
import random
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.modules.scoring_config import load_scoring_config

logger = logging.getLogger(__name__)

# ── Feature names ────────────────────────────────────────────────────────────
FINGERPRINT_FEATURES = [
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

AUGMENTED_FEATURES = [
    "gap_frequency_30d",
    "loiter_frequency_30d",
    "sts_count_90d",
]

ALL_FEATURES = FINGERPRINT_FEATURES + AUGMENTED_FEATURES
_NUM_FEATURES = len(ALL_FEATURES)

# ── Algorithm defaults ───────────────────────────────────────────────────────
_DEFAULT_N_TREES = 100
_DEFAULT_SAMPLE_SIZE = 256
_DEFAULT_SEED = 42
_DEFAULT_CONTAMINATION = 0.05

# ── Tier thresholds and risk score mappings ──────────────────────────────────
_TIER_HIGH_THRESHOLD = 0.7
_TIER_MEDIUM_THRESHOLD = 0.6
_TIER_LOW_THRESHOLD = 0.5
_DEFAULT_HIGH_SCORE = 35
_DEFAULT_MEDIUM_SCORE = 20
_DEFAULT_LOW_SCORE = 10

# ── Precomputed exact harmonic numbers H(n) for small n ──────────────────────
# H(n) = sum(1/k for k=1..n)
# The Euler-Mascheroni approximation ln(n) + 0.5772 is 27.6% wrong at n=3.
_HARMONIC = [
    0.0,       # H(0) — unused, placeholder
    1.0,       # H(1)
    1.5,       # H(2)
    1.8333333333333333,  # H(3)
    2.0833333333333335,  # H(4)
    2.283333333333333,   # H(5)
    2.4499999999999997,  # H(6)
    2.5928571428571425,  # H(7)
    2.7178571428571425,  # H(8)
    2.8289682539682537,  # H(9)
    2.9289682539682538,  # H(10)
    3.0198773448773446,  # H(11)
    3.1032106782106783,  # H(12)
    3.180133755133755,   # H(13)
    3.251562326562327,   # H(14)
    3.3182289932289937,  # H(15)
    3.3807289932289937,  # H(16)
    3.439552522640758,   # H(17)
    3.4951080781963135,  # H(18)
    3.547739657143682,   # H(19)
    3.597739657143682,   # H(20)
    3.6454890524088577,  # H(21)
    3.6909435978634033,  # H(22)
    3.7344827240663833,  # H(23)
    3.7761493907330498,  # H(24)
    3.8161493907330495,  # H(25)
    3.854611928195587,   # H(26)
    3.891612965196624,   # H(27)
    3.927272108539467,   # H(28)
    3.9616765429439013,  # H(29)
    3.994943209610568,   # H(30)
    4.027124866130044,   # H(31)
    4.058312366130044,   # H(32)
    4.088615696433375,   # H(33)
    4.117909247832556,   # H(34)
    4.146480676404,      # H(35)
    4.174258454182,      # H(36)
    4.2012854811825,     # H(37)
    4.227601796497785,   # H(38)
    4.253242155138145,   # H(39)
    4.278242155138145,   # H(40)
    4.302630175612533,   # H(41)
    4.326434557807248,   # H(42)
    4.349680312053002,   # H(43)
    4.372389403961094,   # H(44)
    4.394582847736289,   # H(45)
    4.416279517432959,   # H(46)
    4.437535773689215,   # H(47)
    4.458410773689215,   # H(48)
    4.478957519946036,   # H(49)
]


def _harmonic_number(n: int) -> float:
    """Return the n-th harmonic number H(n).

    Uses precomputed exact values for n < 50 and the Euler-Mascheroni
    approximation for n >= 50 where it is sufficiently accurate.
    """
    if n <= 0:
        return 0.0
    if n < len(_HARMONIC):
        return _HARMONIC[n]
    # For large n the approximation error is <0.5%
    return math.log(n) + 0.5772156649015329


def _average_path_length(n: int) -> float:
    """Average path length c(n) of unsuccessful search in a Binary Search Tree.

    c(n) = 2 * H(n-1) - 2*(n-1)/n

    Returns 0 for n <= 1 to prevent division by zero.
    """
    if n <= 1:
        return 0.0
    return 2.0 * _harmonic_number(n - 1) - 2.0 * (n - 1) / n


# ── Isolation Tree ───────────────────────────────────────────────────────────


class _IsolationTreeNode:
    """A node in an isolation tree."""

    __slots__ = ("split_feature", "split_value", "left", "right", "size")

    def __init__(
        self,
        split_feature: int | None = None,
        split_value: float | None = None,
        left: _IsolationTreeNode | None = None,
        right: _IsolationTreeNode | None = None,
        size: int = 0,
    ):
        self.split_feature = split_feature
        self.split_value = split_value
        self.left = left
        self.right = right
        self.size = size  # data size at leaf nodes


def _build_tree(
    data: list[list[float]],
    height_limit: int,
    current_height: int,
    rng: random.Random,
) -> _IsolationTreeNode:
    """Build a single isolation tree recursively.

    Handles constant features by skipping them when selecting split dimensions.
    If all features are constant for the subsample, terminates as leaf node.
    """
    n = len(data)

    if n <= 1 or current_height >= height_limit:
        return _IsolationTreeNode(size=n)

    num_features = len(data[0])

    # Find features that are not constant (min != max)
    non_constant_features = []
    feature_ranges: dict[int, tuple[float, float]] = {}
    for f_idx in range(num_features):
        col_min = data[0][f_idx]
        col_max = data[0][f_idx]
        for row in data[1:]:
            val = row[f_idx]
            if val < col_min:
                col_min = val
            if val > col_max:
                col_max = val
        if col_min < col_max:
            non_constant_features.append(f_idx)
            feature_ranges[f_idx] = (col_min, col_max)

    # If all features are constant, terminate as leaf
    if not non_constant_features:
        return _IsolationTreeNode(size=n)

    # Select random feature from non-constant features
    split_feature = rng.choice(non_constant_features)
    f_min, f_max = feature_ranges[split_feature]
    split_value = rng.uniform(f_min, f_max)

    left_data = [row for row in data if row[split_feature] < split_value]
    right_data = [row for row in data if row[split_feature] >= split_value]

    # Guard against degenerate splits (all data on one side)
    if not left_data or not right_data:
        return _IsolationTreeNode(size=n)

    return _IsolationTreeNode(
        split_feature=split_feature,
        split_value=split_value,
        left=_build_tree(left_data, height_limit, current_height + 1, rng),
        right=_build_tree(right_data, height_limit, current_height + 1, rng),
    )


def _path_length(point: list[float], node: _IsolationTreeNode, current_depth: int) -> float:
    """Compute the path length for a single point in an isolation tree.

    For leaf nodes, adds the expected path length c(node.size) to account
    for the unbuilt portion of the tree.
    """
    # Leaf node
    if node.split_feature is None:
        return current_depth + _average_path_length(node.size)

    if point[node.split_feature] < node.split_value:
        return _path_length(point, node.left, current_depth + 1)
    else:
        return _path_length(point, node.right, current_depth + 1)


# ── Isolation Forest ─────────────────────────────────────────────────────────


class IsolationForest:
    """Pure-Python Isolation Forest ensemble.

    Parameters
    ----------
    n_trees : int
        Number of isolation trees in the ensemble.
    sample_size : int
        Number of data points sampled for each tree.
    seed : int
        Random seed for reproducibility.
    """

    def __init__(
        self,
        n_trees: int = _DEFAULT_N_TREES,
        sample_size: int = _DEFAULT_SAMPLE_SIZE,
        seed: int = _DEFAULT_SEED,
    ):
        self.n_trees = n_trees
        self.sample_size = sample_size
        self.seed = seed
        self.trees: list[_IsolationTreeNode] = []
        self._c_n: float = 0.0  # c(sample_size) for normalization

    def fit(self, data: list[list[float]]) -> None:
        """Build the isolation forest from training data."""
        n = len(data)
        if n == 0:
            return

        actual_sample_size = min(self.sample_size, n)
        self._c_n = _average_path_length(actual_sample_size)

        height_limit = int(math.ceil(math.log2(max(actual_sample_size, 2))))
        rng = random.Random(self.seed)

        self.trees = []
        for _ in range(self.n_trees):
            sample = list(data) if n <= actual_sample_size else rng.sample(data, actual_sample_size)
            tree = _build_tree(sample, height_limit, 0, rng)
            self.trees.append(tree)

    def score_samples(self, data: list[list[float]]) -> list[float]:
        """Compute anomaly scores for each data point.

        Score = 2^(-E[h(x)] / c(n)) where E[h(x)] is the average path length
        across all trees and c(n) is the average BST path length for n points.

        Returns list of scores in [0, 1]. Higher = more anomalous.
        """
        if not self.trees or self._c_n == 0.0:
            return [0.5] * len(data)

        scores = []
        for point in data:
            total_path = 0.0
            for tree in self.trees:
                total_path += _path_length(point, tree, 0)
            avg_path = total_path / len(self.trees)
            score = 2.0 ** (-avg_path / self._c_n)
            scores.append(score)
        return scores

    def average_path_lengths(self, data: list[list[float]]) -> list[float]:
        """Return average path lengths for each data point."""
        if not self.trees:
            return [0.0] * len(data)

        results = []
        for point in data:
            total_path = 0.0
            for tree in self.trees:
                total_path += _path_length(point, tree, 0)
            results.append(total_path / len(self.trees))
        return results


# ── Feature extraction ───────────────────────────────────────────────────────


def _extract_augmented_features(db: Session, vessel_id: int) -> dict[str, float]:
    """Extract the 3 augmented features for a vessel.

    - gap_frequency_30d: number of AIS gaps in the last 30 days
    - loiter_frequency_30d: number of loitering events in the last 30 days
    - sts_count_90d: number of STS transfer events in the last 90 days
    """
    from app.models.gap_event import AISGapEvent
    from app.models.loitering_event import LoiteringEvent
    from app.models.sts_transfer import StsTransferEvent

    now = datetime.datetime.now(datetime.UTC)
    cutoff_30d = now - datetime.timedelta(days=30)
    cutoff_90d = now - datetime.timedelta(days=90)

    gap_count = (
        db.query(AISGapEvent)
        .filter(
            AISGapEvent.vessel_id == vessel_id,
            AISGapEvent.gap_start_utc >= cutoff_30d,
        )
        .count()
    )

    loiter_count = (
        db.query(LoiteringEvent)
        .filter(
            LoiteringEvent.vessel_id == vessel_id,
            LoiteringEvent.start_time_utc >= cutoff_30d,
        )
        .count()
    )

    sts_count = (
        db.query(StsTransferEvent)
        .filter(
            StsTransferEvent.vessel_id == vessel_id,
            StsTransferEvent.start_time_utc >= cutoff_90d,
        )
        .count()
    )

    return {
        "gap_frequency_30d": float(gap_count),
        "loiter_frequency_30d": float(loiter_count),
        "sts_count_90d": float(sts_count),
    }


def _build_feature_vector(
    fingerprint_features: dict[str, float],
    augmented_features: dict[str, float],
) -> list[float]:
    """Build the 13-element feature vector from fingerprint + augmented features."""
    vector = []
    for name in FINGERPRINT_FEATURES:
        vector.append(fingerprint_features.get(name, 0.0))
    for name in AUGMENTED_FEATURES:
        vector.append(augmented_features.get(name, 0.0))
    return vector


def _identify_top_features(
    feature_vector: list[float],
    all_vectors: list[list[float]],
    n_top: int = 3,
) -> list[dict[str, Any]]:
    """Identify the top N most anomalous features for a given vector.

    Uses z-score relative to the population to rank features.
    """
    if not all_vectors or len(all_vectors) < 2:
        return []

    num_features = len(feature_vector)
    feature_scores: list[tuple[int, float]] = []

    for f_idx in range(num_features):
        col_vals = [v[f_idx] for v in all_vectors]
        mean = sum(col_vals) / len(col_vals)
        variance = sum((x - mean) ** 2 for x in col_vals) / len(col_vals)
        std = math.sqrt(variance) if variance > 0 else 0.0

        z = abs(feature_vector[f_idx] - mean) / std if std > 1e-12 else 0.0

        feature_scores.append((f_idx, z))

    # Sort by z-score descending
    feature_scores.sort(key=lambda x: x[1], reverse=True)

    top = []
    for f_idx, z_score in feature_scores[:n_top]:
        top.append({
            "feature": ALL_FEATURES[f_idx],
            "value": round(feature_vector[f_idx], 4),
            "z_score": round(z_score, 4),
        })

    return top


# ── Tier scoring ─────────────────────────────────────────────────────────────


def _score_tier(anomaly_score: float) -> tuple[str, int]:
    """Map anomaly score to tier and risk score component.

    Uses scoring config if available, falls back to defaults.
    """
    config = load_scoring_config()
    section = config.get("isolation_forest", {})

    high_score = section.get("high", _DEFAULT_HIGH_SCORE)
    medium_score = section.get("medium", _DEFAULT_MEDIUM_SCORE)
    low_score = section.get("low", _DEFAULT_LOW_SCORE)

    if anomaly_score >= _TIER_HIGH_THRESHOLD:
        return "high", high_score
    elif anomaly_score >= _TIER_MEDIUM_THRESHOLD:
        return "medium", medium_score
    elif anomaly_score >= _TIER_LOW_THRESHOLD:
        return "low", low_score
    return "low", 0


# ── Public API ───────────────────────────────────────────────────────────────


def run_isolation_forest_detection(db: Session) -> dict[str, Any]:
    """Run Isolation Forest anomaly detection across all vessels with fingerprints.

    Gated by ISOLATION_FOREST_ENABLED feature flag.

    Steps:
      1. Load all VesselFingerprint records
      2. Augment with gap/loiter/STS counts
      3. Fit Isolation Forest ensemble
      4. Score all vessels
      5. Persist IsolationForestAnomaly records for flagged vessels (score >= 0.5)

    Returns statistics dict.
    """
    from app.models.isolation_forest_anomaly import IsolationForestAnomaly
    from app.models.vessel_fingerprint import VesselFingerprint

    stats: dict[str, Any] = {
        "vessels_processed": 0,
        "anomalies_created": 0,
        "anomalies_updated": 0,
        "skipped_below_threshold": 0,
        "errors": [],
    }

    if not settings.ISOLATION_FOREST_ENABLED:
        logger.info("Isolation Forest detection disabled (ISOLATION_FOREST_ENABLED=False)")
        return stats

    # Load scoring config for algorithm parameters
    config = load_scoring_config()
    section = config.get("isolation_forest", {})
    n_trees = section.get("n_trees", _DEFAULT_N_TREES)
    sample_size = section.get("sample_size", _DEFAULT_SAMPLE_SIZE)

    # Load all fingerprints
    fingerprints = db.query(VesselFingerprint).all()

    if len(fingerprints) < sample_size:
        logger.info(
            "Isolation Forest: only %d fingerprints (need %d for sample_size), skipping",
            len(fingerprints),
            sample_size,
        )
        return stats

    # Build feature vectors for all vessels
    vessel_ids: list[int] = []
    feature_vectors: list[list[float]] = []
    fingerprint_map: dict[int, dict[str, float]] = {}

    for fp in fingerprints:
        vid = fp.vessel_id
        fp_features = fp.feature_vector_json or {}

        # Skip if fingerprint has no features
        if not fp_features:
            continue

        augmented = _extract_augmented_features(db, vid)
        vector = _build_feature_vector(fp_features, augmented)

        vessel_ids.append(vid)
        feature_vectors.append(vector)
        fingerprint_map[vid] = {**fp_features, **augmented}

    if len(feature_vectors) < sample_size:
        logger.info(
            "Isolation Forest: only %d valid vectors (need %d), skipping",
            len(feature_vectors),
            sample_size,
        )
        return stats

    # Fit isolation forest
    forest = IsolationForest(n_trees=n_trees, sample_size=sample_size, seed=_DEFAULT_SEED)
    forest.fit(feature_vectors)

    # Score all vessels
    scores = forest.score_samples(feature_vectors)
    path_lengths = forest.average_path_lengths(feature_vectors)

    now = datetime.datetime.now(datetime.UTC)

    for i, (vid, score, avg_path) in enumerate(zip(vessel_ids, scores, path_lengths, strict=False)):
        stats["vessels_processed"] += 1

        tier, risk_component = _score_tier(score)

        if score < _TIER_LOW_THRESHOLD:
            stats["skipped_below_threshold"] += 1
            continue

        # Identify top features
        top_features = _identify_top_features(feature_vectors[i], feature_vectors)

        # Build feature dict for storage
        feature_dict = {}
        for j, name in enumerate(ALL_FEATURES):
            feature_dict[name] = round(feature_vectors[i][j], 6)

        evidence = {
            "n_trees": n_trees,
            "sample_size": sample_size,
            "population_size": len(feature_vectors),
            "avg_path_length": round(avg_path, 4),
        }

        # Dedup: update if existing, create if not
        existing = (
            db.query(IsolationForestAnomaly)
            .filter(IsolationForestAnomaly.vessel_id == vid)
            .first()
        )

        if existing:
            existing.anomaly_score = round(score, 6)
            existing.path_length_mean = round(avg_path, 6)
            existing.risk_score_component = risk_component
            existing.tier = tier
            existing.feature_vector_json = feature_dict
            existing.top_features_json = top_features
            existing.evidence_json = evidence
            existing.created_at = now
            stats["anomalies_updated"] += 1
        else:
            anomaly = IsolationForestAnomaly(
                vessel_id=vid,
                anomaly_score=round(score, 6),
                path_length_mean=round(avg_path, 6),
                risk_score_component=risk_component,
                tier=tier,
                feature_vector_json=feature_dict,
                top_features_json=top_features,
                evidence_json=evidence,
                created_at=now,
            )
            db.add(anomaly)
            stats["anomalies_created"] += 1

    db.commit()
    logger.info(
        "Isolation Forest complete: %d processed, %d created, %d updated, %d below threshold",
        stats["vessels_processed"],
        stats["anomalies_created"],
        stats["anomalies_updated"],
        stats["skipped_below_threshold"],
    )
    return stats


def get_vessel_anomaly(db: Session, vessel_id: int) -> dict[str, Any] | None:
    """Get the Isolation Forest anomaly record for a single vessel."""
    from app.models.isolation_forest_anomaly import IsolationForestAnomaly

    anomaly = (
        db.query(IsolationForestAnomaly)
        .filter(IsolationForestAnomaly.vessel_id == vessel_id)
        .first()
    )

    if anomaly is None:
        return None

    return {
        "anomaly_id": anomaly.anomaly_id,
        "vessel_id": anomaly.vessel_id,
        "anomaly_score": anomaly.anomaly_score,
        "path_length_mean": anomaly.path_length_mean,
        "risk_score_component": anomaly.risk_score_component,
        "tier": anomaly.tier,
        "feature_vector": anomaly.feature_vector_json,
        "top_features": anomaly.top_features_json,
        "evidence": anomaly.evidence_json,
        "created_at": anomaly.created_at.isoformat() if anomaly.created_at else None,
    }
