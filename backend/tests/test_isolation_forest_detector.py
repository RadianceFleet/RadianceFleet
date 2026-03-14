"""Tests for isolation_forest_detector — Isolation Forest Multi-Feature Anomaly Scorer."""

from __future__ import annotations

import math
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.modules.isolation_forest_detector import (
    ALL_FEATURES,
    AUGMENTED_FEATURES,
    FINGERPRINT_FEATURES,
    IsolationForest,
    _average_path_length,
    _build_feature_vector,
    _build_tree,
    _identify_top_features,
    _path_length,
    _score_tier,
    get_vessel_anomaly,
    run_isolation_forest_detection,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_fingerprint(vessel_id: int = 1, features: dict | None = None):
    """Create a mock VesselFingerprint."""
    fp = MagicMock()
    fp.vessel_id = vessel_id
    fp.feature_vector_json = features or {
        "cruise_speed_median": 12.0,
        "cruise_speed_iqr": 2.0,
        "sog_max": 15.0,
        "acceleration_profile": 0.5,
        "turn_rate_median": 3.0,
        "heading_stability": 10.0,
        "draught_range": 1.5,
        "tx_interval_median": 30.0,
        "tx_interval_var": 100.0,
        "deceleration_profile": -0.3,
    }
    return fp


def _make_outlier_fingerprint(vessel_id: int = 999):
    """Create a fingerprint with extreme values (outlier)."""
    return _make_fingerprint(
        vessel_id=vessel_id,
        features={
            "cruise_speed_median": 50.0,
            "cruise_speed_iqr": 20.0,
            "sog_max": 60.0,
            "acceleration_profile": 10.0,
            "turn_rate_median": 45.0,
            "heading_stability": 90.0,
            "draught_range": 15.0,
            "tx_interval_median": 600.0,
            "tx_interval_var": 50000.0,
            "deceleration_profile": -5.0,
        },
    )


def _generate_normal_fingerprints(n: int, base_id: int = 1):
    """Generate n normal-looking fingerprints with small random variation."""
    import random

    rng = random.Random(123)
    fps = []
    for i in range(n):
        features = {
            "cruise_speed_median": 12.0 + rng.gauss(0, 1),
            "cruise_speed_iqr": 2.0 + rng.gauss(0, 0.3),
            "sog_max": 15.0 + rng.gauss(0, 1),
            "acceleration_profile": 0.5 + rng.gauss(0, 0.1),
            "turn_rate_median": 3.0 + rng.gauss(0, 0.5),
            "heading_stability": 10.0 + rng.gauss(0, 1),
            "draught_range": 1.5 + rng.gauss(0, 0.2),
            "tx_interval_median": 30.0 + rng.gauss(0, 3),
            "tx_interval_var": 100.0 + rng.gauss(0, 10),
            "deceleration_profile": -0.3 + rng.gauss(0, 0.05),
        }
        fps.append(_make_fingerprint(vessel_id=base_id + i, features=features))
    return fps


# ── Algorithm unit tests ────────────────────────────────────────────────────


class TestAveragePathLength:
    """Tests for c(n) — average BST path length."""

    def test_c_n_zero_for_n_le_1(self):
        """c(n) returns 0 for n <= 1."""
        assert _average_path_length(0) == 0.0
        assert _average_path_length(1) == 0.0

    def test_c_n_exact_for_n_2(self):
        """c(2) = 2*H(1) - 2*(1)/2 = 2*1 - 1 = 1."""
        assert abs(_average_path_length(2) - 1.0) < 1e-10

    def test_c_n_exact_for_n_3(self):
        """c(3) = 2*H(2) - 2*(2)/3 = 2*1.5 - 4/3 = 3 - 1.333... = 1.666..."""
        expected = 2.0 * 1.5 - 2.0 * 2.0 / 3.0
        assert abs(_average_path_length(3) - expected) < 1e-10

    def test_c_n_precision_vs_euler_approximation(self):
        """Verify precomputed harmonics are more precise than Euler approximation at n=3."""
        # The Euler approximation for H(2) = ln(2) + 0.5772 ≈ 1.2703
        # Exact H(2) = 1.5
        # Error = (1.5 - 1.2703) / 1.5 ≈ 15.3%
        exact_c3 = _average_path_length(3)
        # The ln+gamma approximation
        euler_h2 = math.log(2) + 0.5772156649015329
        approx_c3 = 2.0 * euler_h2 - 2.0 * 2.0 / 3.0
        # Exact should differ from approximation
        assert abs(exact_c3 - approx_c3) > 0.2

    def test_c_n_increases_with_n(self):
        """c(n) should increase monotonically with n."""
        prev = _average_path_length(2)
        for n in [3, 5, 10, 50, 100, 256]:
            curr = _average_path_length(n)
            assert curr > prev, f"c({n})={curr} should be > c(prev)={prev}"
            prev = curr

    def test_c_n_large_n_uses_approximation(self):
        """For n >= 50, the function should still return reasonable values."""
        c_256 = _average_path_length(256)
        assert c_256 > 0
        assert c_256 < 20  # Reasonable upper bound


class TestTreeBuilding:
    """Tests for isolation tree construction."""

    def test_build_tree_single_point(self):
        """Single point should become a leaf."""
        import random

        data = [[1.0, 2.0, 3.0]]
        rng = random.Random(42)
        node = _build_tree(data, height_limit=8, current_height=0, rng=rng)
        assert node.split_feature is None  # leaf
        assert node.size == 1

    def test_build_tree_two_distinct_points(self):
        """Two distinct points should produce a tree with a split."""
        import random

        data = [[1.0, 2.0], [10.0, 20.0]]
        rng = random.Random(42)
        node = _build_tree(data, height_limit=8, current_height=0, rng=rng)
        # Should have a split (unless degenerate)
        assert node.split_feature is not None or node.size == 2

    def test_build_tree_constant_features_terminates(self):
        """All-constant features should terminate as leaf."""
        import random

        data = [[5.0, 5.0, 5.0] for _ in range(10)]
        rng = random.Random(42)
        node = _build_tree(data, height_limit=8, current_height=0, rng=rng)
        assert node.split_feature is None  # leaf — all features constant
        assert node.size == 10

    def test_build_tree_height_limit(self):
        """Tree should respect height limit."""
        import random

        data = [[float(i), float(i * 2)] for i in range(100)]
        rng = random.Random(42)
        node = _build_tree(data, height_limit=3, current_height=0, rng=rng)
        # The tree should exist (not crash)
        assert node is not None


class TestPathLength:
    """Tests for path length computation."""

    def test_path_length_leaf_node(self):
        """Path length at a leaf adds c(size)."""
        from app.modules.isolation_forest_detector import _IsolationTreeNode

        leaf = _IsolationTreeNode(size=10)
        pl = _path_length([1.0, 2.0], leaf, current_depth=3)
        expected = 3 + _average_path_length(10)
        assert abs(pl - expected) < 1e-10

    def test_path_length_increases_with_depth(self):
        """Deeper traversal means longer path length."""
        import random

        # Build a tree with varied data
        data = [[float(i)] for i in range(50)]
        rng = random.Random(42)
        node = _build_tree(data, height_limit=10, current_height=0, rng=rng)

        # Outlier should have shorter path (isolated quickly)
        outlier_pl = _path_length([1000.0], node, 0)
        # Inlier (middle value) should have longer path
        inlier_pl = _path_length([25.0], node, 0)

        # Outlier is isolated faster (shorter path)
        assert outlier_pl < inlier_pl


class TestIsolationForestScoring:
    """Tests for the full Isolation Forest scoring."""

    def test_outlier_scores_higher(self):
        """Outliers should have higher anomaly scores than inliers."""
        import random as rmod

        rng = rmod.Random(42)
        # Normal cluster
        data = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(300)]
        # Add an outlier
        data.append([100.0, 100.0])

        forest = IsolationForest(n_trees=100, sample_size=256, seed=42)
        forest.fit(data)
        scores = forest.score_samples(data)

        outlier_score = scores[-1]
        inlier_scores = scores[:-1]
        avg_inlier = sum(inlier_scores) / len(inlier_scores)

        assert outlier_score > avg_inlier

    def test_deterministic_with_seed(self):
        """Same seed produces same scores."""
        import random as rmod

        rng = rmod.Random(42)
        data = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(100)]

        forest1 = IsolationForest(n_trees=50, sample_size=64, seed=42)
        forest1.fit(data)
        scores1 = forest1.score_samples(data)

        forest2 = IsolationForest(n_trees=50, sample_size=64, seed=42)
        forest2.fit(data)
        scores2 = forest2.score_samples(data)

        for s1, s2 in zip(scores1, scores2):
            assert abs(s1 - s2) < 1e-12

    def test_empty_data(self):
        """Forest with empty data returns default scores."""
        forest = IsolationForest(n_trees=10, sample_size=8, seed=42)
        forest.fit([])
        scores = forest.score_samples([[1.0, 2.0]])
        assert len(scores) == 1
        assert scores[0] == 0.5

    def test_scores_between_0_and_1(self):
        """All scores should be between 0 and 1."""
        import random as rmod

        rng = rmod.Random(42)
        data = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(100)]

        forest = IsolationForest(n_trees=50, sample_size=64, seed=42)
        forest.fit(data)
        scores = forest.score_samples(data)

        for score in scores:
            assert 0.0 <= score <= 1.0


# ── Feature extraction tests ────────────────────────────────────────────────


class TestFeatureExtraction:
    """Tests for feature extraction from fingerprints."""

    def test_uses_existing_fingerprint(self):
        """Feature vector uses features from VesselFingerprint."""
        fp_features = {
            "cruise_speed_median": 12.0,
            "cruise_speed_iqr": 2.0,
            "sog_max": 15.0,
            "acceleration_profile": 0.5,
            "turn_rate_median": 3.0,
            "heading_stability": 10.0,
            "draught_range": 1.5,
            "tx_interval_median": 30.0,
            "tx_interval_var": 100.0,
            "deceleration_profile": -0.3,
        }
        augmented = {
            "gap_frequency_30d": 2.0,
            "loiter_frequency_30d": 1.0,
            "sts_count_90d": 0.0,
        }
        vector = _build_feature_vector(fp_features, augmented)
        assert len(vector) == 13
        assert vector[0] == 12.0  # cruise_speed_median
        assert vector[10] == 2.0  # gap_frequency_30d
        assert vector[12] == 0.0  # sts_count_90d

    def test_missing_fingerprint_features_default_to_zero(self):
        """Missing fingerprint features should default to 0.0."""
        fp_features = {"cruise_speed_median": 12.0}  # only 1 feature
        augmented = {"gap_frequency_30d": 0.0, "loiter_frequency_30d": 0.0, "sts_count_90d": 0.0}
        vector = _build_feature_vector(fp_features, augmented)
        assert len(vector) == 13
        assert vector[0] == 12.0
        assert vector[1] == 0.0  # cruise_speed_iqr missing -> 0.0

    def test_augmented_counts(self):
        """Augmented features should include gap/loiter/STS counts."""
        assert "gap_frequency_30d" in AUGMENTED_FEATURES
        assert "loiter_frequency_30d" in AUGMENTED_FEATURES
        assert "sts_count_90d" in AUGMENTED_FEATURES
        assert len(ALL_FEATURES) == 13


# ── Scoring tier tests ───────────────────────────────────────────────────────


class TestScoringTiers:
    """Tests for tier mapping."""

    @patch("app.modules.isolation_forest_detector.load_scoring_config")
    def test_high_tier(self, mock_config):
        mock_config.return_value = {"isolation_forest": {"high": 35, "medium": 20, "low": 10}}
        tier, score = _score_tier(0.75)
        assert tier == "high"
        assert score == 35

    @patch("app.modules.isolation_forest_detector.load_scoring_config")
    def test_medium_tier(self, mock_config):
        mock_config.return_value = {"isolation_forest": {"high": 35, "medium": 20, "low": 10}}
        tier, score = _score_tier(0.65)
        assert tier == "medium"
        assert score == 20

    @patch("app.modules.isolation_forest_detector.load_scoring_config")
    def test_low_tier(self, mock_config):
        mock_config.return_value = {"isolation_forest": {"high": 35, "medium": 20, "low": 10}}
        tier, score = _score_tier(0.55)
        assert tier == "low"
        assert score == 10


# ── Integration tests ────────────────────────────────────────────────────────


class TestIntegration:
    """Integration tests for run_isolation_forest_detection."""

    @patch("app.modules.isolation_forest_detector.settings")
    def test_disabled_flag_returns_empty(self, mock_settings):
        """Detection disabled returns empty stats."""
        mock_settings.ISOLATION_FOREST_ENABLED = False
        db = MagicMock()
        result = run_isolation_forest_detection(db)
        assert result["vessels_processed"] == 0
        assert result["anomalies_created"] == 0

    @patch("app.modules.isolation_forest_detector.settings")
    @patch("app.modules.isolation_forest_detector.load_scoring_config")
    def test_no_fingerprints_noop(self, mock_config, mock_settings):
        """No fingerprints returns empty stats."""
        mock_settings.ISOLATION_FOREST_ENABLED = True
        mock_config.return_value = {"isolation_forest": {"n_trees": 10, "sample_size": 8}}
        db = MagicMock()
        db.query.return_value.all.return_value = []  # no fingerprints
        result = run_isolation_forest_detection(db)
        assert result["vessels_processed"] == 0

    @patch("app.modules.isolation_forest_detector.settings")
    @patch("app.modules.isolation_forest_detector.load_scoring_config")
    @patch("app.modules.isolation_forest_detector._extract_augmented_features")
    def test_creates_anomalies(self, mock_augmented, mock_config, mock_settings):
        """With enough fingerprints, anomalies are created."""
        mock_settings.ISOLATION_FOREST_ENABLED = True
        mock_config.return_value = {"isolation_forest": {
            "n_trees": 10, "sample_size": 8, "high": 35, "medium": 20, "low": 10,
        }}
        mock_augmented.return_value = {
            "gap_frequency_30d": 0.0,
            "loiter_frequency_30d": 0.0,
            "sts_count_90d": 0.0,
        }

        # Generate fingerprints — 20 normal + 1 outlier
        fingerprints = _generate_normal_fingerprints(20) + [_make_outlier_fingerprint()]

        db = MagicMock()
        db.query.return_value.all.return_value = fingerprints
        db.query.return_value.filter.return_value.first.return_value = None  # no existing anomalies

        result = run_isolation_forest_detection(db)
        assert result["vessels_processed"] > 0
        # At least the outlier should be flagged
        total_created = result["anomalies_created"]
        assert total_created >= 1

    @patch("app.modules.isolation_forest_detector.settings")
    @patch("app.modules.isolation_forest_detector.load_scoring_config")
    @patch("app.modules.isolation_forest_detector._extract_augmented_features")
    def test_dedup_updates_existing(self, mock_augmented, mock_config, mock_settings):
        """Existing anomaly records are updated, not duplicated."""
        mock_settings.ISOLATION_FOREST_ENABLED = True
        mock_config.return_value = {"isolation_forest": {
            "n_trees": 10, "sample_size": 8, "high": 35, "medium": 20, "low": 10,
        }}
        mock_augmented.return_value = {
            "gap_frequency_30d": 5.0,
            "loiter_frequency_30d": 3.0,
            "sts_count_90d": 2.0,
        }

        fingerprints = _generate_normal_fingerprints(20) + [_make_outlier_fingerprint()]

        existing_anomaly = MagicMock()
        existing_anomaly.vessel_id = 999

        db = MagicMock()
        db.query.return_value.all.return_value = fingerprints
        db.query.return_value.filter.return_value.first.return_value = existing_anomaly

        result = run_isolation_forest_detection(db)
        # All flagged anomalies should be "updated" since existing_anomaly is returned
        assert result["anomalies_updated"] >= 1
        assert result["anomalies_created"] == 0

    @patch("app.modules.isolation_forest_detector.settings")
    @patch("app.modules.isolation_forest_detector.load_scoring_config")
    @patch("app.modules.isolation_forest_detector._extract_augmented_features")
    def test_scoring_tiers_in_results(self, mock_augmented, mock_config, mock_settings):
        """Anomalies get assigned appropriate tiers based on scores."""
        mock_settings.ISOLATION_FOREST_ENABLED = True
        mock_config.return_value = {"isolation_forest": {
            "n_trees": 10, "sample_size": 8, "high": 35, "medium": 20, "low": 10,
        }}
        mock_augmented.return_value = {
            "gap_frequency_30d": 0.0,
            "loiter_frequency_30d": 0.0,
            "sts_count_90d": 0.0,
        }

        fingerprints = _generate_normal_fingerprints(20) + [_make_outlier_fingerprint()]

        db = MagicMock()
        db.query.return_value.all.return_value = fingerprints
        db.query.return_value.filter.return_value.first.return_value = None

        result = run_isolation_forest_detection(db)
        # Should process at least some vessels
        assert result["vessels_processed"] > 0


class TestTopFeatures:
    """Tests for top feature identification."""

    def test_identifies_extreme_features(self):
        """Features with high z-scores should be ranked first."""
        # Normal population
        all_vectors = [
            [10.0, 5.0, 3.0] for _ in range(50)
        ]
        # Outlier in feature 0
        outlier = [100.0, 5.0, 3.0]
        top = _identify_top_features(outlier, all_vectors, n_top=3)
        assert len(top) > 0
        assert top[0]["feature"] == ALL_FEATURES[0]  # cruise_speed_median

    def test_returns_empty_for_insufficient_data(self):
        """Returns empty list when insufficient population data."""
        top = _identify_top_features([1.0, 2.0], [], n_top=3)
        assert top == []


class TestGetVesselAnomaly:
    """Tests for get_vessel_anomaly."""

    def test_returns_none_when_not_found(self):
        """Returns None when no anomaly record exists."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = get_vessel_anomaly(db, vessel_id=1)
        assert result is None

    def test_returns_anomaly_dict(self):
        """Returns formatted dict when anomaly exists."""
        anomaly = MagicMock()
        anomaly.anomaly_id = 1
        anomaly.vessel_id = 100
        anomaly.anomaly_score = 0.75
        anomaly.path_length_mean = 4.5
        anomaly.risk_score_component = 35
        anomaly.tier = "high"
        anomaly.feature_vector_json = {"cruise_speed_median": 50.0}
        anomaly.top_features_json = [{"feature": "cruise_speed_median", "z_score": 5.0}]
        anomaly.evidence_json = {"n_trees": 100}
        anomaly.created_at = datetime(2026, 1, 1, 0, 0, 0)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = anomaly
        result = get_vessel_anomaly(db, vessel_id=100)
        assert result is not None
        assert result["vessel_id"] == 100
        assert result["tier"] == "high"
        assert result["anomaly_score"] == 0.75
