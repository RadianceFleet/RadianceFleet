"""Trajectory Autoencoder Anomaly Detector.

Pure-Python unsupervised autoencoder (7->4->3->4->7) that flags trajectory
segments with high reconstruction error as anomalous.

Feature vector (7 features):
  centroid_lat, centroid_lon, sin(bearing), cos(bearing),
  distance_nm, straightness_ratio, mean_sog

Bearing is encoded as sin/cos pair to handle circular discontinuity.

Segments are extracted by reusing extract_segments() from
dbscan_trajectory_detector (24h windows, 30-min downsample).
"""

from __future__ import annotations

import json
import logging
import math
import random
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.modules.scoring_config import load_scoring_config

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

ARCHITECTURE = [7, 4, 3, 4, 7]
MIN_SEGMENTS = 8
DEFAULT_EPOCHS = 200
DEFAULT_LEARNING_RATE = 0.1
DEFAULT_BATCH_SIZE = 32
SEED = 42

# Tier thresholds on reconstruction error
TIER_HIGH_THRESHOLD = 0.7
TIER_MEDIUM_THRESHOLD = 0.6
TIER_LOW_THRESHOLD = 0.5

DEFAULT_HIGH_SCORE = 30
DEFAULT_MEDIUM_SCORE = 18
DEFAULT_LOW_SCORE = 8


# ── Matrix operations (pure Python) ─────────────────────────────────────────


def mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """Multiply two matrices represented as list-of-lists."""
    rows_a = len(a)
    cols_a = len(a[0])
    cols_b = len(b[0])
    result = [[0.0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for k in range(cols_a):
            a_ik = a[i][k]
            if a_ik == 0.0:
                continue
            for j in range(cols_b):
                result[i][j] += a_ik * b[k][j]
    return result


def transpose(m: list[list[float]]) -> list[list[float]]:
    """Transpose a matrix."""
    if not m:
        return []
    rows = len(m)
    cols = len(m[0])
    return [[m[i][j] for i in range(rows)] for j in range(cols)]


def sigmoid(x: float) -> float:
    """Sigmoid activation with clamping to avoid OverflowError."""
    x = max(-500.0, min(500.0, x))
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def sigmoid_derivative(output: float) -> float:
    """Derivative of sigmoid given sigmoid output value."""
    return output * (1.0 - output)


# ── Xavier initialization ────────────────────────────────────────────────────


def xavier_init(
    rows: int, cols: int, rng: random.Random
) -> list[list[float]]:
    """Xavier/Glorot uniform initialization."""
    limit = math.sqrt(6.0 / (rows + cols))
    return [[rng.uniform(-limit, limit) for _ in range(cols)] for _ in range(rows)]


# ── Normalization ────────────────────────────────────────────────────────────


def compute_min_max(
    data: list[list[float]],
) -> tuple[list[float], list[float]]:
    """Compute per-feature min and max from training data."""
    n_features = len(data[0])
    mins = [float("inf")] * n_features
    maxs = [float("-inf")] * n_features
    for row in data:
        for j in range(n_features):
            if row[j] < mins[j]:
                mins[j] = row[j]
            if row[j] > maxs[j]:
                maxs[j] = row[j]
    return mins, maxs


def normalize(
    data: list[list[float]],
    mins: list[float],
    maxs: list[float],
) -> list[list[float]]:
    """Min-max normalize data to [0, 1]. Zero-variance features set to 0.5."""
    n_features = len(mins)
    result = []
    for row in data:
        norm_row = []
        for j in range(n_features):
            if maxs[j] - mins[j] < 1e-6:
                norm_row.append(0.5)
            else:
                norm_row.append((row[j] - mins[j]) / (maxs[j] - mins[j]))
        result.append(norm_row)
    return result


# ── Autoencoder ──────────────────────────────────────────────────────────────


class Autoencoder:
    """Pure-Python autoencoder with SGD backpropagation.

    Architecture is defined by a list of layer sizes, e.g. [7, 4, 3, 4, 7].
    Uses sigmoid activations throughout.
    """

    def __init__(
        self,
        layer_sizes: list[int],
        learning_rate: float = DEFAULT_LEARNING_RATE,
        epochs: int = DEFAULT_EPOCHS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        seed: int = SEED,
    ):
        self.layer_sizes = layer_sizes
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.batch_size = batch_size
        self.rng = random.Random(seed)

        # Initialize weights and biases
        self.weights: list[list[list[float]]] = []
        self.biases: list[list[float]] = []
        for i in range(len(layer_sizes) - 1):
            w = xavier_init(layer_sizes[i], layer_sizes[i + 1], self.rng)
            b = [0.0] * layer_sizes[i + 1]
            self.weights.append(w)
            self.biases.append(b)

        # Bottleneck layer index (middle of architecture)
        self.bottleneck_idx = len(layer_sizes) // 2 - 1  # index into activations

    def _forward(self, x: list[float]) -> list[list[float]]:
        """Forward pass. Returns list of activations for each layer (including input)."""
        activations = [x]
        current = x
        for layer_idx in range(len(self.weights)):
            w = self.weights[layer_idx]
            b = self.biases[layer_idx]
            n_out = len(b)
            n_in = len(current)
            new_layer = []
            for j in range(n_out):
                z = b[j]
                for i in range(n_in):
                    z += current[i] * w[i][j]
                new_layer.append(sigmoid(z))
            activations.append(new_layer)
            current = new_layer
        return activations

    def _backward(
        self, activations: list[list[float]], target: list[float]
    ) -> tuple[list[list[list[float]]], list[list[float]]]:
        """Backward pass. Returns weight and bias gradients."""
        n_layers = len(self.weights)
        # Output error
        output = activations[-1]
        output_error = [
            (output[j] - target[j]) * sigmoid_derivative(output[j])
            for j in range(len(output))
        ]

        # Build deltas from output back to first hidden layer
        deltas: list[list[float]] = [[] for _ in range(n_layers)]
        deltas[-1] = output_error

        for layer_idx in range(n_layers - 2, -1, -1):
            w = self.weights[layer_idx + 1]
            next_delta = deltas[layer_idx + 1]
            act = activations[layer_idx + 1]
            n_current = len(act)
            n_next = len(next_delta)
            delta = []
            for i in range(n_current):
                err = 0.0
                for j in range(n_next):
                    err += w[i][j] * next_delta[j]
                delta.append(err * sigmoid_derivative(act[i]))
            deltas[layer_idx] = delta

        # Compute gradients
        weight_grads: list[list[list[float]]] = []
        bias_grads: list[list[float]] = []
        for layer_idx in range(n_layers):
            act_in = activations[layer_idx]
            d = deltas[layer_idx]
            n_in = len(act_in)
            n_out = len(d)
            wg = [[act_in[i] * d[j] for j in range(n_out)] for i in range(n_in)]
            weight_grads.append(wg)
            bias_grads.append(list(d))

        return weight_grads, bias_grads

    def train(self, data: list[list[float]]) -> list[float]:
        """Train the autoencoder (input == target). Returns per-epoch losses."""
        n = len(data)
        if n == 0:
            return []

        effective_batch = min(self.batch_size, n)
        epoch_losses: list[float] = []

        for epoch in range(self.epochs):
            # Shuffle data
            indices = list(range(n))
            self.rng.shuffle(indices)

            total_loss = 0.0

            for batch_start in range(0, n, effective_batch):
                batch_end = min(batch_start + effective_batch, n)
                batch_indices = indices[batch_start:batch_end]
                batch_len = len(batch_indices)

                # Accumulate gradients
                acc_wg: list[list[list[float]]] | None = None
                acc_bg: list[list[float]] | None = None

                for idx in batch_indices:
                    x = data[idx]
                    activations = self._forward(x)
                    output = activations[-1]

                    # MSE loss contribution
                    for j in range(len(x)):
                        total_loss += (output[j] - x[j]) ** 2

                    wg, bg = self._backward(activations, x)

                    if acc_wg is None:
                        acc_wg = wg
                        acc_bg = bg
                    else:
                        for li in range(len(wg)):
                            for i in range(len(wg[li])):
                                for j in range(len(wg[li][i])):
                                    acc_wg[li][i][j] += wg[li][i][j]
                            for j in range(len(bg[li])):
                                acc_bg[li][j] += bg[li][j]  # type: ignore[index]

                # Update weights
                lr_scaled = self.learning_rate / batch_len
                for li in range(len(self.weights)):
                    for i in range(len(self.weights[li])):
                        for j in range(len(self.weights[li][i])):
                            self.weights[li][i][j] -= lr_scaled * acc_wg[li][i][j]  # type: ignore[index]
                    for j in range(len(self.biases[li])):
                        self.biases[li][j] -= lr_scaled * acc_bg[li][j]  # type: ignore[index]

            avg_loss = total_loss / (n * len(data[0])) if n > 0 else 0.0
            epoch_losses.append(avg_loss)

        return epoch_losses

    def predict(self, x: list[float]) -> tuple[list[float], list[float]]:
        """Forward pass returning (reconstructed_output, bottleneck_values)."""
        activations = self._forward(x)
        # bottleneck is at index bottleneck_idx + 1 in activations
        # (activations[0] is input, activations[1] is first hidden, etc.)
        bottleneck = activations[self.bottleneck_idx + 1]
        return activations[-1], bottleneck

    def reconstruction_error(self, x: list[float]) -> float:
        """Compute mean squared reconstruction error for a single sample."""
        output, _ = self.predict(x)
        n = len(x)
        return sum((x[j] - output[j]) ** 2 for j in range(n)) / n


# ── Feature extraction ───────────────────────────────────────────────────────

FEATURE_NAMES = [
    "centroid_lat",
    "centroid_lon",
    "sin_bearing",
    "cos_bearing",
    "distance_nm",
    "straightness_ratio",
    "mean_sog",
]


def extract_feature_vector(segment: Any) -> list[float]:
    """Extract 7-feature vector from a TrajectorySegment."""
    bearing_rad = math.radians(segment.bearing)
    return [
        segment.centroid_lat,
        segment.centroid_lon,
        math.sin(bearing_rad),
        math.cos(bearing_rad),
        segment.total_distance_nm,
        segment.straightness_ratio,
        segment.mean_sog,
    ]


# ── Tier assignment ──────────────────────────────────────────────────────────


def assign_tier(
    reconstruction_error: float,
) -> tuple[str | None, int]:
    """Assign anomaly tier based on reconstruction error.

    Returns (tier, risk_score_component) or (None, 0) if below threshold.
    """
    config = load_scoring_config()
    section = config.get("trajectory_autoencoder", {})

    high_score = section.get("high", DEFAULT_HIGH_SCORE)
    medium_score = section.get("medium", DEFAULT_MEDIUM_SCORE)
    low_score = section.get("low", DEFAULT_LOW_SCORE)

    if reconstruction_error >= TIER_HIGH_THRESHOLD:
        return "HIGH", high_score
    elif reconstruction_error >= TIER_MEDIUM_THRESHOLD:
        return "MEDIUM", medium_score
    elif reconstruction_error >= TIER_LOW_THRESHOLD:
        return "LOW", low_score
    return None, 0


# ── Main entry point ─────────────────────────────────────────────────────────


def detect_trajectory_autoencoder_anomalies(
    db: Session,
    vessel_id: int,
) -> list:
    """Run autoencoder anomaly detection on trajectory segments for a vessel.

    Steps:
      1. Extract segments via dbscan_trajectory_detector.extract_segments
      2. Compute 7-feature vectors
      3. Min-max normalize
      4. Train autoencoder (7->4->3->4->7)
      5. Score reconstruction errors
      6. Assign tiers and persist anomalies above threshold

    Returns list of TrajectoryAutoencoderAnomaly objects created.
    """
    from app.models.trajectory_autoencoder_anomaly import TrajectoryAutoencoderAnomaly
    from app.modules.dbscan_trajectory_detector import extract_segments

    if not settings.TRAJECTORY_AUTOENCODER_ENABLED:
        logger.info("Trajectory autoencoder detection disabled")
        return []

    # Extract segments for this vessel
    segments = extract_segments(db, vessel_ids=[vessel_id])

    if len(segments) < MIN_SEGMENTS:
        logger.warning(
            "Vessel %d has only %d segments (minimum %d required) — skipping",
            vessel_id,
            len(segments),
            MIN_SEGMENTS,
        )
        return []

    # Compute feature vectors
    feature_vectors = [extract_feature_vector(seg) for seg in segments]

    # Normalize
    mins, maxs = compute_min_max(feature_vectors)
    normalized = normalize(feature_vectors, mins, maxs)

    # Train autoencoder
    epochs = getattr(settings, "TRAJECTORY_AUTOENCODER_EPOCHS", DEFAULT_EPOCHS)
    lr = getattr(settings, "TRAJECTORY_AUTOENCODER_LEARNING_RATE", DEFAULT_LEARNING_RATE)

    ae = Autoencoder(
        layer_sizes=list(ARCHITECTURE),
        learning_rate=lr,
        epochs=epochs,
        seed=SEED,
    )
    ae.train(normalized)

    # Score each segment
    now = datetime.now(UTC)

    anomalies: list[TrajectoryAutoencoderAnomaly] = []

    # Delete existing anomalies for this vessel
    db.query(TrajectoryAutoencoderAnomaly).filter(
        TrajectoryAutoencoderAnomaly.vessel_id == vessel_id
    ).delete(synchronize_session=False)

    for i, seg in enumerate(segments):
        error = ae.reconstruction_error(normalized[i])
        reconstructed, bottleneck = ae.predict(normalized[i])

        tier, risk_score = assign_tier(error)
        if tier is None:
            continue  # Below threshold

        evidence = {
            "reconstruction_error": round(error, 6),
            "risk_score_component": risk_score,
            "tier": tier,
            "feature_names": FEATURE_NAMES,
            "segment_waypoint_count": len(seg.waypoints) if hasattr(seg, "waypoints") else None,
        }

        anomaly = TrajectoryAutoencoderAnomaly(
            vessel_id=vessel_id,
            segment_start=seg.window_start,
            segment_end=seg.window_end,
            reconstruction_error=round(error, 6),
            anomaly_score=round(min(1.0, error), 6),
            tier=tier,
            feature_vector_json=json.dumps(feature_vectors[i]),
            reconstructed_vector_json=json.dumps([round(v, 6) for v in reconstructed]),
            bottleneck_json=json.dumps([round(v, 6) for v in bottleneck]),
            evidence_json=json.dumps(evidence),
            created_at=now,
        )
        db.add(anomaly)
        anomalies.append(anomaly)

    db.commit()
    logger.info(
        "Trajectory autoencoder detection for vessel %d: %d segments, %d anomalies",
        vessel_id,
        len(segments),
        len(anomalies),
    )
    return anomalies


def get_vessel_autoencoder_anomalies(
    db: Session,
    vessel_id: int,
) -> list[dict[str, Any]]:
    """Get trajectory autoencoder anomaly results for a vessel."""
    from app.models.trajectory_autoencoder_anomaly import TrajectoryAutoencoderAnomaly

    anomalies = (
        db.query(TrajectoryAutoencoderAnomaly)
        .filter(TrajectoryAutoencoderAnomaly.vessel_id == vessel_id)
        .order_by(TrajectoryAutoencoderAnomaly.segment_start.desc())
        .all()
    )

    return [
        {
            "id": a.id,
            "vessel_id": a.vessel_id,
            "segment_start": a.segment_start.isoformat() if a.segment_start else None,
            "segment_end": a.segment_end.isoformat() if a.segment_end else None,
            "reconstruction_error": a.reconstruction_error,
            "anomaly_score": a.anomaly_score,
            "tier": a.tier,
            "feature_vector": json.loads(a.feature_vector_json) if a.feature_vector_json else None,
            "reconstructed_vector": json.loads(a.reconstructed_vector_json) if a.reconstructed_vector_json else None,
            "bottleneck": json.loads(a.bottleneck_json) if a.bottleneck_json else None,
            "evidence": json.loads(a.evidence_json) if a.evidence_json else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in anomalies
    ]
