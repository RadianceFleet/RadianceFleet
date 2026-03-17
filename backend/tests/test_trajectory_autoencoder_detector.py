"""Tests for trajectory autoencoder anomaly detector."""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.modules.trajectory_autoencoder_detector import (
    ARCHITECTURE,
    DEFAULT_HIGH_SCORE,
    MIN_SEGMENTS,
    Autoencoder,
    assign_tier,
    compute_min_max,
    extract_feature_vector,
    mat_mul,
    normalize,
    sigmoid,
    sigmoid_derivative,
    transpose,
    xavier_init,
)

# ── Matrix operations ────────────────────────────────────────────────────────


class TestMatMul:
    def test_identity_multiplication(self):
        identity = [[1, 0], [0, 1]]
        m = [[3, 4], [5, 6]]
        result = mat_mul(identity, m)
        assert result == [[3, 4], [5, 6]]

    def test_basic_multiplication(self):
        a = [[1, 2], [3, 4]]
        b = [[5, 6], [7, 8]]
        result = mat_mul(a, b)
        assert result == [[19, 22], [43, 50]]

    def test_non_square_multiplication(self):
        a = [[1, 2, 3]]  # 1x3
        b = [[4], [5], [6]]  # 3x1
        result = mat_mul(a, b)
        assert result == [[32]]

    def test_zero_matrix(self):
        a = [[0, 0], [0, 0]]
        b = [[1, 2], [3, 4]]
        result = mat_mul(a, b)
        assert result == [[0, 0], [0, 0]]


class TestTranspose:
    def test_square_matrix(self):
        m = [[1, 2], [3, 4]]
        result = transpose(m)
        assert result == [[1, 3], [2, 4]]

    def test_rectangular_matrix(self):
        m = [[1, 2, 3], [4, 5, 6]]
        result = transpose(m)
        assert result == [[1, 4], [2, 5], [3, 6]]

    def test_empty_matrix(self):
        assert transpose([]) == []

    def test_single_row(self):
        m = [[1, 2, 3]]
        result = transpose(m)
        assert result == [[1], [2], [3]]


# ── Sigmoid ──────────────────────────────────────────────────────────────────


class TestSigmoid:
    def test_zero_input(self):
        assert sigmoid(0.0) == 0.5

    def test_large_positive_clamped(self):
        result = sigmoid(1000.0)
        assert result == pytest.approx(1.0, abs=1e-10)

    def test_large_negative_clamped(self):
        result = sigmoid(-1000.0)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_no_overflow_at_boundary(self):
        # Should not raise OverflowError
        result = sigmoid(500.0)
        assert result == pytest.approx(1.0, abs=1e-10)
        result = sigmoid(-500.0)
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_positive_input(self):
        result = sigmoid(2.0)
        expected = 1.0 / (1.0 + math.exp(-2.0))
        assert result == pytest.approx(expected, rel=1e-6)

    def test_negative_input(self):
        result = sigmoid(-2.0)
        expected = math.exp(-2.0) / (1.0 + math.exp(-2.0))
        assert result == pytest.approx(expected, rel=1e-6)

    def test_derivative_at_half(self):
        assert sigmoid_derivative(0.5) == pytest.approx(0.25)


# ── Xavier initialization ────────────────────────────────────────────────────


class TestXavierInit:
    def test_shape(self):
        rng = random.Random(42)
        w = xavier_init(7, 4, rng)
        assert len(w) == 7
        assert all(len(row) == 4 for row in w)

    def test_values_within_limit(self):
        rng = random.Random(42)
        rows, cols = 7, 4
        w = xavier_init(rows, cols, rng)
        limit = math.sqrt(6.0 / (rows + cols))
        for row in w:
            for val in row:
                assert -limit <= val <= limit

    def test_deterministic_with_seed(self):
        w1 = xavier_init(3, 2, random.Random(42))
        w2 = xavier_init(3, 2, random.Random(42))
        assert w1 == w2


# ── Normalization ────────────────────────────────────────────────────────────


class TestNormalization:
    def test_basic_normalization(self):
        data = [[0, 10], [5, 20], [10, 30]]
        mins, maxs = compute_min_max(data)
        assert mins == [0, 10]
        assert maxs == [10, 30]
        normed = normalize(data, mins, maxs)
        assert normed[0] == pytest.approx([0.0, 0.0])
        assert normed[1] == pytest.approx([0.5, 0.5])
        assert normed[2] == pytest.approx([1.0, 1.0])

    def test_zero_variance_feature(self):
        """When all values are the same, normalized value should be 0.5."""
        data = [[5, 10], [5, 20], [5, 30]]
        mins, maxs = compute_min_max(data)
        normed = normalize(data, mins, maxs)
        for row in normed:
            assert row[0] == 0.5  # zero-variance feature

    def test_epsilon_guard(self):
        """Very small range (< 1e-6) should produce 0.5."""
        data = [[1.0, 10], [1.0 + 1e-8, 20]]
        mins, maxs = compute_min_max(data)
        normed = normalize(data, mins, maxs)
        assert normed[0][0] == 0.5
        assert normed[1][0] == 0.5

    def test_single_row(self):
        data = [[3, 5, 7]]
        mins, maxs = compute_min_max(data)
        normed = normalize(data, mins, maxs)
        assert normed[0] == [0.5, 0.5, 0.5]


# ── Feature extraction ───────────────────────────────────────────────────────


class TestFeatureExtraction:
    def test_feature_vector_length(self):
        seg = MagicMock()
        seg.centroid_lat = 55.0
        seg.centroid_lon = 20.0
        seg.bearing = 90.0
        seg.total_distance_nm = 50.0
        seg.straightness_ratio = 0.8
        seg.mean_sog = 12.0
        fv = extract_feature_vector(seg)
        assert len(fv) == 7

    def test_bearing_sin_cos(self):
        seg = MagicMock()
        seg.centroid_lat = 0.0
        seg.centroid_lon = 0.0
        seg.bearing = 90.0
        seg.total_distance_nm = 0.0
        seg.straightness_ratio = 1.0
        seg.mean_sog = 0.0
        fv = extract_feature_vector(seg)
        assert fv[2] == pytest.approx(math.sin(math.radians(90.0)), rel=1e-6)
        assert fv[3] == pytest.approx(math.cos(math.radians(90.0)), rel=1e-6)

    def test_zero_bearing(self):
        seg = MagicMock()
        seg.centroid_lat = 0.0
        seg.centroid_lon = 0.0
        seg.bearing = 0.0
        seg.total_distance_nm = 0.0
        seg.straightness_ratio = 1.0
        seg.mean_sog = 0.0
        fv = extract_feature_vector(seg)
        assert fv[2] == pytest.approx(0.0, abs=1e-10)  # sin(0)
        assert fv[3] == pytest.approx(1.0, abs=1e-10)  # cos(0)


# ── Autoencoder ──────────────────────────────────────────────────────────────


class TestAutoencoder:
    def test_init_layer_shapes(self):
        ae = Autoencoder([7, 4, 3, 4, 7])
        assert len(ae.weights) == 4
        assert len(ae.biases) == 4
        # Check weight shapes
        assert len(ae.weights[0]) == 7
        assert len(ae.weights[0][0]) == 4
        assert len(ae.weights[1]) == 4
        assert len(ae.weights[1][0]) == 3
        assert len(ae.weights[2]) == 3
        assert len(ae.weights[2][0]) == 4
        assert len(ae.weights[3]) == 4
        assert len(ae.weights[3][0]) == 7

    def test_forward_output_shape(self):
        ae = Autoencoder([7, 4, 3, 4, 7])
        x = [0.5] * 7
        output, bottleneck = ae.predict(x)
        assert len(output) == 7
        assert len(bottleneck) == 3  # bottleneck layer

    def test_output_values_between_0_and_1(self):
        ae = Autoencoder([7, 4, 3, 4, 7])
        x = [0.1, 0.9, 0.5, 0.3, 0.7, 0.2, 0.8]
        output, bottleneck = ae.predict(x)
        for val in output:
            assert 0.0 <= val <= 1.0
        for val in bottleneck:
            assert 0.0 <= val <= 1.0

    def test_reconstruction_error_nonnegative(self):
        ae = Autoencoder([7, 4, 3, 4, 7])
        x = [0.5] * 7
        error = ae.reconstruction_error(x)
        assert error >= 0.0

    def test_train_reduces_error(self):
        """Training should reduce reconstruction error over time."""
        rng = random.Random(42)
        data = [[rng.random() for _ in range(7)] for _ in range(20)]
        ae = Autoencoder([7, 4, 3, 4, 7], epochs=50, learning_rate=0.5, seed=42)

        # Error before training
        errors_before = [ae.reconstruction_error(x) for x in data]
        avg_before = sum(errors_before) / len(errors_before)

        ae.train(data)

        errors_after = [ae.reconstruction_error(x) for x in data]
        avg_after = sum(errors_after) / len(errors_after)

        assert avg_after < avg_before

    def test_deterministic_with_seed(self):
        data = [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]] * 10
        ae1 = Autoencoder([7, 4, 3, 4, 7], epochs=10, seed=42)
        ae1.train(data)
        e1 = ae1.reconstruction_error(data[0])

        ae2 = Autoencoder([7, 4, 3, 4, 7], epochs=10, seed=42)
        ae2.train(data)
        e2 = ae2.reconstruction_error(data[0])

        assert e1 == pytest.approx(e2, rel=1e-10)

    def test_empty_data_train(self):
        ae = Autoencoder([7, 4, 3, 4, 7])
        losses = ae.train([])
        assert losses == []

    def test_full_batch_mode(self):
        """When data < batch_size, should use full-batch mode."""
        data = [[0.5] * 7 for _ in range(5)]
        ae = Autoencoder([7, 4, 3, 4, 7], batch_size=32, epochs=5, seed=42)
        losses = ae.train(data)
        assert len(losses) == 5  # One loss per epoch


# ── Tier assignment ──────────────────────────────────────────────────────────


class TestTierAssignment:
    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    def test_high_tier(self, mock_config):
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}
        tier, score = assign_tier(0.75)
        assert tier == "HIGH"
        assert score == 30

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    def test_medium_tier(self, mock_config):
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}
        tier, score = assign_tier(0.65)
        assert tier == "MEDIUM"
        assert score == 18

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    def test_low_tier(self, mock_config):
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}
        tier, score = assign_tier(0.55)
        assert tier == "LOW"
        assert score == 8

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    def test_below_threshold(self, mock_config):
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}
        tier, score = assign_tier(0.3)
        assert tier is None
        assert score == 0

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    def test_exact_high_boundary(self, mock_config):
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}
        tier, score = assign_tier(0.7)
        assert tier == "HIGH"

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    def test_exact_medium_boundary(self, mock_config):
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}
        tier, score = assign_tier(0.6)
        assert tier == "MEDIUM"

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    def test_exact_low_boundary(self, mock_config):
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}
        tier, score = assign_tier(0.5)
        assert tier == "LOW"

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    def test_default_scores_when_config_missing(self, mock_config):
        mock_config.return_value = {}
        tier, score = assign_tier(0.75)
        assert tier == "HIGH"
        assert score == DEFAULT_HIGH_SCORE


# ── Integration tests ────────────────────────────────────────────────────────


def _make_segment(
    vessel_id: int,
    lat: float = 55.0,
    lon: float = 20.0,
    bearing: float = 90.0,
    distance: float = 50.0,
    straightness: float = 0.8,
    sog: float = 12.0,
    start_offset_days: int = 0,
):
    """Create a mock TrajectorySegment."""
    seg = MagicMock()
    seg.vessel_id = vessel_id
    seg.centroid_lat = lat
    seg.centroid_lon = lon
    seg.bearing = bearing
    seg.total_distance_nm = distance
    seg.straightness_ratio = straightness
    seg.mean_sog = sog
    seg.window_start = datetime(2025, 1, 1) + timedelta(days=start_offset_days)
    seg.window_end = datetime(2025, 1, 1) + timedelta(days=start_offset_days + 1)
    seg.waypoints = [(seg.window_start, lat, lon, sog)]
    return seg


class TestDetectAnomalies:
    @patch("app.modules.trajectory_autoencoder_detector.settings")
    def test_disabled_returns_empty(self, mock_settings):
        mock_settings.TRAJECTORY_AUTOENCODER_ENABLED = False
        from app.modules.trajectory_autoencoder_detector import (
            detect_trajectory_autoencoder_anomalies,
        )
        db = MagicMock()
        result = detect_trajectory_autoencoder_anomalies(db, vessel_id=1)
        assert result == []

    @patch("app.modules.trajectory_autoencoder_detector.settings")
    @patch("app.modules.dbscan_trajectory_detector.extract_segments")
    def test_too_few_segments_skipped(self, mock_extract, mock_settings):
        mock_settings.TRAJECTORY_AUTOENCODER_ENABLED = True
        mock_extract.return_value = [_make_segment(1, start_offset_days=i) for i in range(5)]

        from app.modules.trajectory_autoencoder_detector import (
            detect_trajectory_autoencoder_anomalies,
        )
        db = MagicMock()
        result = detect_trajectory_autoencoder_anomalies(db, vessel_id=1)
        assert result == []

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    @patch("app.modules.trajectory_autoencoder_detector.settings")
    @patch("app.modules.dbscan_trajectory_detector.extract_segments")
    def test_normal_segments_produces_results(self, mock_extract, mock_settings, mock_config):
        mock_settings.TRAJECTORY_AUTOENCODER_ENABLED = True
        mock_settings.TRAJECTORY_AUTOENCODER_EPOCHS = 50
        mock_settings.TRAJECTORY_AUTOENCODER_LEARNING_RATE = 0.5
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}

        # Create 10 similar segments + 1 outlier
        segments = [_make_segment(1, lat=55 + i * 0.1, start_offset_days=i) for i in range(10)]
        # Add outlier
        segments.append(_make_segment(
            1, lat=10.0, lon=100.0, bearing=270.0, distance=500.0,
            straightness=0.1, sog=2.0, start_offset_days=10
        ))
        mock_extract.return_value = segments

        db = MagicMock()
        # Mock the delete query
        db.query.return_value.filter.return_value.delete.return_value = 0

        from app.modules.trajectory_autoencoder_detector import (
            detect_trajectory_autoencoder_anomalies,
        )
        detect_trajectory_autoencoder_anomalies(db, vessel_id=1)

        # Should have called db.add for anomalies and db.commit
        assert db.commit.called

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    @patch("app.modules.trajectory_autoencoder_detector.settings")
    @patch("app.modules.dbscan_trajectory_detector.extract_segments")
    def test_empty_segments(self, mock_extract, mock_settings, mock_config):
        mock_settings.TRAJECTORY_AUTOENCODER_ENABLED = True
        mock_extract.return_value = []
        mock_config.return_value = {}

        from app.modules.trajectory_autoencoder_detector import (
            detect_trajectory_autoencoder_anomalies,
        )
        db = MagicMock()
        result = detect_trajectory_autoencoder_anomalies(db, vessel_id=1)
        assert result == []

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    @patch("app.modules.trajectory_autoencoder_detector.settings")
    @patch("app.modules.dbscan_trajectory_detector.extract_segments")
    def test_all_identical_segments(self, mock_extract, mock_settings, mock_config):
        """All identical segments should train well and have low reconstruction error."""
        mock_settings.TRAJECTORY_AUTOENCODER_ENABLED = True
        mock_settings.TRAJECTORY_AUTOENCODER_EPOCHS = 100
        mock_settings.TRAJECTORY_AUTOENCODER_LEARNING_RATE = 0.5
        mock_config.return_value = {"trajectory_autoencoder": {"high": 30, "medium": 18, "low": 8}}

        segments = [_make_segment(1, start_offset_days=i) for i in range(10)]
        mock_extract.return_value = segments

        db = MagicMock()
        db.query.return_value.filter.return_value.delete.return_value = 0

        from app.modules.trajectory_autoencoder_detector import (
            detect_trajectory_autoencoder_anomalies,
        )
        detect_trajectory_autoencoder_anomalies(db, vessel_id=1)

        # All identical => all normalized to 0.5 => low reconstruction error
        # Should have few or no anomalies
        assert db.commit.called

    @patch("app.modules.trajectory_autoencoder_detector.load_scoring_config")
    @patch("app.modules.trajectory_autoencoder_detector.settings")
    @patch("app.modules.dbscan_trajectory_detector.extract_segments")
    def test_single_segment_below_minimum(self, mock_extract, mock_settings, mock_config):
        mock_settings.TRAJECTORY_AUTOENCODER_ENABLED = True
        mock_extract.return_value = [_make_segment(1)]
        mock_config.return_value = {}

        from app.modules.trajectory_autoencoder_detector import (
            detect_trajectory_autoencoder_anomalies,
        )
        db = MagicMock()
        result = detect_trajectory_autoencoder_anomalies(db, vessel_id=1)
        assert result == []


class TestGetVesselAnomalies:
    def test_returns_formatted_results(self):
        from app.modules.trajectory_autoencoder_detector import get_vessel_autoencoder_anomalies

        mock_anomaly = MagicMock()
        mock_anomaly.id = 1
        mock_anomaly.vessel_id = 42
        mock_anomaly.segment_start = datetime(2025, 1, 1)
        mock_anomaly.segment_end = datetime(2025, 1, 2)
        mock_anomaly.reconstruction_error = 0.75
        mock_anomaly.anomaly_score = 0.75
        mock_anomaly.tier = "HIGH"
        mock_anomaly.feature_vector_json = json.dumps([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        mock_anomaly.reconstructed_vector_json = json.dumps([1.1, 2.1, 3.1, 4.1, 5.1, 6.1, 7.1])
        mock_anomaly.bottleneck_json = json.dumps([0.5, 0.6, 0.7])
        mock_anomaly.evidence_json = json.dumps({"tier": "HIGH"})
        mock_anomaly.created_at = datetime(2025, 1, 1, 12, 0)

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_anomaly]

        result = get_vessel_autoencoder_anomalies(db, vessel_id=42)
        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["vessel_id"] == 42
        assert result[0]["tier"] == "HIGH"
        assert result[0]["feature_vector"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]

    def test_empty_results(self):
        from app.modules.trajectory_autoencoder_detector import get_vessel_autoencoder_anomalies

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = get_vessel_autoencoder_anomalies(db, vessel_id=999)
        assert result == []


# ── API endpoint tests ───────────────────────────────────────────────────────


class TestAPIEndpoints:
    @patch("app.modules.trajectory_autoencoder_detector.settings")
    def test_post_endpoint_disabled(self, mock_settings):
        mock_settings.TRAJECTORY_AUTOENCODER_ENABLED = False
        from app.modules.trajectory_autoencoder_detector import (
            detect_trajectory_autoencoder_anomalies,
        )
        db = MagicMock()
        result = detect_trajectory_autoencoder_anomalies(db, vessel_id=1)
        assert result == []

    def test_get_endpoint_returns_results(self):
        from app.modules.trajectory_autoencoder_detector import get_vessel_autoencoder_anomalies

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        result = get_vessel_autoencoder_anomalies(db, vessel_id=1)
        assert isinstance(result, list)


# ── Architecture tests ───────────────────────────────────────────────────────


class TestArchitecture:
    def test_architecture_is_symmetric(self):
        assert ARCHITECTURE == [7, 4, 3, 4, 7]
        assert list(reversed(ARCHITECTURE)) == ARCHITECTURE

    def test_bottleneck_is_smallest(self):
        assert min(ARCHITECTURE) == 3
        assert ARCHITECTURE[2] == 3

    def test_min_segments_constant(self):
        assert MIN_SEGMENTS == 8
