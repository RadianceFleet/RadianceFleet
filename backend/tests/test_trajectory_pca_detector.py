"""Tests for trajectory_pca_detector — PCA-based trajectory anomaly detection."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.trajectory_pca_anomaly import TrajectoryPcaAnomaly
from app.modules.trajectory_pca_detector import (
    FEATURE_NAMES,
    _TIER_HIGH_PERCENTILE,
    _TIER_LOW_PERCENTILE,
    _TIER_MEDIUM_PERCENTILE,
    assign_tier,
    compute_covariance_matrix,
    compute_spe,
    get_vessel_pca_anomalies,
    identify_top_error_features,
    jacobi_eigen,
    percentile_rank,
    run_pca_detection,
    segment_to_feature_vector,
    zscore_normalize,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_segment(
    vessel_id: int = 1,
    centroid_lat: float = 55.0,
    centroid_lon: float = 20.0,
    bearing: float = 90.0,
    total_distance_nm: float = 50.0,
    duration_hours: float = 10.0,
    mean_sog: float = 5.0,
    straightness_ratio: float = 0.8,
    n_waypoints: int = 10,
):
    """Create a mock TrajectorySegment."""
    seg = MagicMock()
    seg.vessel_id = vessel_id
    seg.centroid_lat = centroid_lat
    seg.centroid_lon = centroid_lon
    seg.bearing = bearing
    seg.total_distance_nm = total_distance_nm
    seg.duration_hours = duration_hours
    seg.mean_sog = mean_sog
    seg.straightness_ratio = straightness_ratio
    seg.waypoints = [(None, 0, 0, 0)] * n_waypoints
    seg.window_start = datetime(2026, 1, 1, 0, 0, 0)
    seg.window_end = datetime(2026, 1, 2, 0, 0, 0)
    return seg


def _make_test_db():
    """Create an in-memory SQLite DB with the PCA anomaly table."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    return session_factory()


# ── Z-score normalization tests ──────────────────────────────────────────────


class TestZscoreNormalize:
    """Tests for z-score normalization."""

    def test_normalizes_to_zero_mean(self):
        """Normalized data should have approximately zero mean."""
        data = [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0], [4.0, 40.0]]
        normalized, means, stds = zscore_normalize(data)

        # Check means are correct
        assert abs(means[0] - 2.5) < 1e-10
        assert abs(means[1] - 25.0) < 1e-10

        # Check normalized mean is ~0
        col_mean = sum(row[0] for row in normalized) / len(normalized)
        assert abs(col_mean) < 1e-10

    def test_normalizes_to_unit_variance(self):
        """Normalized data should have approximately unit variance."""
        data = [[1.0, 100.0], [2.0, 200.0], [3.0, 300.0], [4.0, 400.0]]
        normalized, means, stds = zscore_normalize(data)

        for j in range(2):
            col_var = sum(row[j] ** 2 for row in normalized) / len(normalized)
            assert abs(col_var - 1.0) < 1e-10

    def test_constant_feature_becomes_zero(self):
        """Constant features should normalize to all zeros."""
        data = [[5.0, 1.0], [5.0, 2.0], [5.0, 3.0]]
        normalized, means, stds = zscore_normalize(data)

        for row in normalized:
            assert row[0] == 0.0

    def test_empty_input(self):
        """Empty input returns empty results."""
        normalized, means, stds = zscore_normalize([])
        assert normalized == []
        assert means == []
        assert stds == []

    def test_single_row(self):
        """Single row normalizes to zeros."""
        data = [[3.0, 7.0]]
        normalized, means, stds = zscore_normalize(data)
        assert normalized == [[0.0, 0.0]]


# ── Covariance matrix tests ─────────────────────────────────────────────────


class TestCovarianceMatrix:
    """Tests for covariance matrix computation."""

    def test_identity_like_for_normalized(self):
        """Covariance of z-score normalized data should be close to identity."""
        data = [[float(i), float(i * 2 + 1)] for i in range(100)]
        normalized, _, _ = zscore_normalize(data)
        cov = compute_covariance_matrix(normalized)

        # Diagonal should be ~1.0
        assert abs(cov[0][0] - 1.0) < 1e-10
        assert abs(cov[1][1] - 1.0) < 1e-10

    def test_symmetric(self):
        """Covariance matrix must be symmetric."""
        data = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]
        cov = compute_covariance_matrix(data)

        for i in range(3):
            for j in range(3):
                assert abs(cov[i][j] - cov[j][i]) < 1e-10

    def test_known_covariance(self):
        """Test with a known covariance result."""
        # For data [[1, 0], [-1, 0]], centered, cov = [[1, 0], [0, 0]]
        data = [[1.0, 0.0], [-1.0, 0.0]]
        cov = compute_covariance_matrix(data)
        assert abs(cov[0][0] - 1.0) < 1e-10
        assert abs(cov[0][1]) < 1e-10
        assert abs(cov[1][1]) < 1e-10


# ── Jacobi eigendecomposition tests ──────────────────────────────────────────


class TestJacobiEigen:
    """Tests for Jacobi eigendecomposition."""

    def test_known_2x2_matrix(self):
        """Test with known 2x2 symmetric matrix eigenvalues."""
        # [[2, 1], [1, 2]] has eigenvalues 3 and 1
        matrix = [[2.0, 1.0], [1.0, 2.0]]
        eigenvalues, eigenvectors = jacobi_eigen(matrix)

        assert abs(eigenvalues[0] - 3.0) < 1e-8
        assert abs(eigenvalues[1] - 1.0) < 1e-8

    def test_eigenvalues_sorted_descending(self):
        """Eigenvalues should be sorted in descending order."""
        matrix = [[3.0, 1.0, 0.5], [1.0, 2.0, 0.3], [0.5, 0.3, 1.0]]
        eigenvalues, _ = jacobi_eigen(matrix)

        for i in range(len(eigenvalues) - 1):
            assert eigenvalues[i] >= eigenvalues[i + 1]

    def test_identity_matrix(self):
        """Identity matrix should have all eigenvalues equal to 1."""
        matrix = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        eigenvalues, _ = jacobi_eigen(matrix)

        for ev in eigenvalues:
            assert abs(ev - 1.0) < 1e-8

    def test_diagonal_matrix(self):
        """Diagonal matrix eigenvalues should equal the diagonal entries."""
        matrix = [[5.0, 0.0], [0.0, 2.0]]
        eigenvalues, _ = jacobi_eigen(matrix)

        assert abs(eigenvalues[0] - 5.0) < 1e-8
        assert abs(eigenvalues[1] - 2.0) < 1e-8

    def test_eigenvectors_orthogonal(self):
        """Eigenvectors should be orthogonal."""
        matrix = [[4.0, 2.0], [2.0, 3.0]]
        _, eigenvectors = jacobi_eigen(matrix)

        # Dot product of column 0 and column 1 should be ~0
        dot = sum(eigenvectors[i][0] * eigenvectors[i][1] for i in range(2))
        assert abs(dot) < 1e-8

    def test_reconstruction(self):
        """V * diag(eigenvalues) * V^T should reconstruct the original matrix."""
        matrix = [[3.0, 1.0], [1.0, 2.0]]
        eigenvalues, eigenvectors = jacobi_eigen(matrix)
        n = len(matrix)

        # Reconstruct: sum over k of lambda_k * v_k * v_k^T
        reconstructed = [[0.0] * n for _ in range(n)]
        for k in range(n):
            for i in range(n):
                for j in range(n):
                    reconstructed[i][j] += eigenvalues[k] * eigenvectors[i][k] * eigenvectors[j][k]

        for i in range(n):
            for j in range(n):
                assert abs(reconstructed[i][j] - matrix[i][j]) < 1e-8


# ── SPE computation tests ───────────────────────────────────────────────────


class TestComputeSPE:
    """Tests for Squared Prediction Error computation."""

    def test_zero_spe_for_principal_component_direction(self):
        """Data aligned with principal components should have zero SPE."""
        # Identity eigenvectors, 1 principal component
        eigenvectors = [[1.0, 0.0], [0.0, 1.0]]
        data = [[3.0, 0.0]]  # Aligned with first PC
        spe = compute_spe(data, eigenvectors, n_components=1)
        assert abs(spe[0]) < 1e-10

    def test_nonzero_spe_for_minor_component(self):
        """Data with minor component projection should have nonzero SPE."""
        eigenvectors = [[1.0, 0.0], [0.0, 1.0]]
        data = [[0.0, 5.0]]  # Entirely in minor component
        spe = compute_spe(data, eigenvectors, n_components=1)
        assert abs(spe[0] - 25.0) < 1e-10  # 5^2

    def test_spe_increases_with_deviation(self):
        """Larger deviations from PC subspace should give larger SPE."""
        eigenvectors = [[1.0, 0.0], [0.0, 1.0]]
        data = [[1.0, 1.0], [1.0, 5.0]]
        spe = compute_spe(data, eigenvectors, n_components=1)
        assert spe[1] > spe[0]

    def test_all_principal_components_gives_zero_spe(self):
        """Using all components as principal should give zero SPE."""
        eigenvectors = [[1.0, 0.0], [0.0, 1.0]]
        data = [[3.0, 4.0]]
        spe = compute_spe(data, eigenvectors, n_components=2)
        assert abs(spe[0]) < 1e-10


# ── Percentile ranking tests ────────────────────────────────────────────────


class TestPercentileRank:
    """Tests for percentile ranking."""

    def test_ordered_values(self):
        """Ascending values should get ascending ranks."""
        ranks = percentile_rank([1.0, 2.0, 3.0, 4.0, 5.0])
        for i in range(len(ranks) - 1):
            assert ranks[i] < ranks[i + 1]

    def test_min_is_zero(self):
        """Minimum value gets rank 0."""
        ranks = percentile_rank([1.0, 2.0, 3.0])
        assert ranks[0] == 0.0

    def test_max_is_one(self):
        """Maximum value gets rank 1."""
        ranks = percentile_rank([1.0, 2.0, 3.0])
        assert ranks[-1] == 1.0

    def test_single_value(self):
        """Single value gets rank 0.5."""
        ranks = percentile_rank([42.0])
        assert ranks[0] == 0.5

    def test_empty_input(self):
        """Empty input returns empty list."""
        assert percentile_rank([]) == []

    def test_identical_values(self):
        """Identical values all get rank 0."""
        ranks = percentile_rank([5.0, 5.0, 5.0])
        for r in ranks:
            assert r == 0.0


# ── Tier assignment tests ───────────────────────────────────────────────────


class TestAssignTier:
    """Tests for tier assignment based on anomaly score."""

    def test_high_tier(self):
        tier, score = assign_tier(0.96)
        assert tier == "high"
        assert score == 30.0

    def test_medium_tier(self):
        tier, score = assign_tier(0.92)
        assert tier == "medium"
        assert score == 20.0

    def test_low_tier(self):
        tier, score = assign_tier(0.85)
        assert tier == "low"
        assert score == 10.0

    def test_below_threshold(self):
        tier, score = assign_tier(0.5)
        assert tier == "low"
        assert score == 0.0

    def test_boundary_high(self):
        tier, _ = assign_tier(_TIER_HIGH_PERCENTILE)
        assert tier == "high"

    def test_boundary_medium(self):
        tier, _ = assign_tier(_TIER_MEDIUM_PERCENTILE)
        assert tier == "medium"

    def test_boundary_low(self):
        tier, _ = assign_tier(_TIER_LOW_PERCENTILE)
        assert tier == "low"


# ── Feature extraction tests ────────────────────────────────────────────────


class TestFeatureExtraction:
    """Tests for segment to feature vector conversion."""

    def test_correct_feature_count(self):
        seg = _make_segment()
        vec = segment_to_feature_vector(seg)
        assert len(vec) == 8

    def test_feature_values(self):
        seg = _make_segment(
            centroid_lat=60.0, centroid_lon=25.0, bearing=180.0,
            total_distance_nm=100.0, duration_hours=20.0, mean_sog=5.0,
            straightness_ratio=0.9, n_waypoints=15,
        )
        vec = segment_to_feature_vector(seg)
        assert vec[0] == 60.0  # centroid_lat
        assert vec[1] == 25.0  # centroid_lon
        assert vec[2] == 180.0  # bearing
        assert vec[3] == 100.0  # distance_nm
        assert vec[4] == 20.0  # duration_h
        assert vec[5] == 5.0  # mean_sog
        assert vec[6] == 0.9  # straightness
        assert vec[7] == 15.0  # waypoint_count

    def test_feature_names_match(self):
        assert len(FEATURE_NAMES) == 8
        assert "centroid_lat" in FEATURE_NAMES
        assert "waypoint_count" in FEATURE_NAMES


# ── Top error features tests ────────────────────────────────────────────────


class TestTopErrorFeatures:
    """Tests for top error feature identification."""

    def test_returns_top_n(self):
        """Should return n_top features."""
        eigenvectors = [[1.0, 0.0], [0.0, 1.0]]
        # Extend to 8 features for FEATURE_NAMES compat
        ev_8 = [[0.0] * 8 for _ in range(8)]
        for i in range(8):
            ev_8[i][i] = 1.0

        row = [0.0] * 8
        row[3] = 5.0  # distance_nm has high value
        top = identify_top_error_features(row, ev_8, n_components=4, n_top=3)
        assert len(top) == 3

    def test_identifies_dominant_feature(self):
        """Feature with highest error contribution should be first."""
        ev_8 = [[0.0] * 8 for _ in range(8)]
        for i in range(8):
            ev_8[i][i] = 1.0

        row = [0.0] * 8
        row[5] = 10.0  # mean_sog is extreme

        top = identify_top_error_features(row, ev_8, n_components=4, n_top=3)
        assert top[0]["feature"] == "mean_sog"


# ── Integration tests ───────────────────────────────────────────────────────


class TestRunPcaDetection:
    """Integration tests for run_pca_detection."""

    @patch("app.modules.trajectory_pca_detector.settings")
    def test_disabled_returns_early(self, mock_settings):
        """Disabled flag returns empty stats."""
        mock_settings.TRAJECTORY_PCA_ENABLED = False
        db = MagicMock()
        result = run_pca_detection(db)
        assert result["segments_processed"] == 0
        assert result.get("disabled") is True

    @patch("app.modules.trajectory_pca_detector.extract_segments")
    @patch("app.modules.trajectory_pca_detector.settings")
    def test_too_few_segments(self, mock_settings, mock_extract):
        """Too few segments returns without processing."""
        mock_settings.TRAJECTORY_PCA_ENABLED = True
        mock_settings.TRAJECTORY_PCA_N_COMPONENTS = 4
        mock_extract.return_value = [_make_segment()]
        db = MagicMock()
        result = run_pca_detection(db)
        assert result["anomalies_created"] == 0

    @patch("app.modules.trajectory_pca_detector.extract_segments")
    @patch("app.modules.trajectory_pca_detector.settings")
    def test_full_pipeline_with_mock_segments(self, mock_settings, mock_extract):
        """Full pipeline with sufficient segments creates anomalies."""
        mock_settings.TRAJECTORY_PCA_ENABLED = True
        mock_settings.TRAJECTORY_PCA_N_COMPONENTS = 4

        import random
        rng = random.Random(42)

        # Create 30 normal segments + 2 outliers
        segments = []
        for i in range(30):
            segments.append(_make_segment(
                vessel_id=i + 1,
                centroid_lat=55.0 + rng.gauss(0, 0.5),
                centroid_lon=20.0 + rng.gauss(0, 0.5),
                bearing=90.0 + rng.gauss(0, 10),
                total_distance_nm=50.0 + rng.gauss(0, 5),
                duration_hours=10.0 + rng.gauss(0, 1),
                mean_sog=5.0 + rng.gauss(0, 0.5),
                straightness_ratio=0.8 + rng.gauss(0, 0.05),
                n_waypoints=10,
            ))

        # Outliers: very different trajectory
        segments.append(_make_segment(
            vessel_id=100, centroid_lat=10.0, centroid_lon=-50.0,
            bearing=270.0, total_distance_nm=500.0, duration_hours=48.0,
            mean_sog=20.0, straightness_ratio=0.2, n_waypoints=50,
        ))
        segments.append(_make_segment(
            vessel_id=101, centroid_lat=-30.0, centroid_lon=100.0,
            bearing=0.0, total_distance_nm=800.0, duration_hours=72.0,
            mean_sog=25.0, straightness_ratio=0.1, n_waypoints=100,
        ))

        mock_extract.return_value = segments

        db = MagicMock()
        # Make dedup query return no existing records
        db.query.return_value.filter.return_value.delete.return_value = 0

        result = run_pca_detection(db)
        assert result["segments_processed"] == 32
        assert result["anomalies_created"] > 0
        assert "variance_explained" in result

    @patch("app.modules.trajectory_pca_detector.extract_segments")
    @patch("app.modules.trajectory_pca_detector.settings")
    def test_empty_segments(self, mock_settings, mock_extract):
        """No segments returns zero stats."""
        mock_settings.TRAJECTORY_PCA_ENABLED = True
        mock_settings.TRAJECTORY_PCA_N_COMPONENTS = 4
        mock_extract.return_value = []
        db = MagicMock()
        result = run_pca_detection(db)
        assert result["segments_processed"] == 0
        assert result["anomalies_created"] == 0

    @patch("app.modules.trajectory_pca_detector.extract_segments")
    @patch("app.modules.trajectory_pca_detector.settings")
    def test_all_identical_segments(self, mock_settings, mock_extract):
        """All identical segments should produce zero SPE."""
        mock_settings.TRAJECTORY_PCA_ENABLED = True
        mock_settings.TRAJECTORY_PCA_N_COMPONENTS = 4

        segments = [_make_segment(vessel_id=i + 1) for i in range(10)]
        mock_extract.return_value = segments

        db = MagicMock()
        db.query.return_value.filter.return_value.delete.return_value = 0

        result = run_pca_detection(db)
        # All identical → all SPE = 0 → all percentile = 0 → none above threshold
        assert result["anomalies_created"] == 0


# ── Get vessel anomalies tests ──────────────────────────────────────────────


class TestGetVesselPcaAnomalies:
    """Tests for get_vessel_pca_anomalies."""

    def test_returns_empty_list_when_none(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        result = get_vessel_pca_anomalies(db, vessel_id=1)
        assert result == []

    def test_returns_formatted_anomaly(self):
        anomaly = MagicMock()
        anomaly.anomaly_id = 1
        anomaly.vessel_id = 42
        anomaly.segment_start = datetime(2026, 1, 1)
        anomaly.segment_end = datetime(2026, 1, 2)
        anomaly.reconstruction_error = 12.5
        anomaly.anomaly_score = 0.95
        anomaly.risk_score_component = 30.0
        anomaly.tier = "high"
        anomaly.feature_vector_json = json.dumps({"centroid_lat": 55.0})
        anomaly.top_error_features_json = json.dumps([{"feature": "bearing", "contribution": 8.5}])
        anomaly.evidence_json = json.dumps({"n_components": 4})
        anomaly.created_at = datetime(2026, 1, 2, 12, 0, 0)

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [anomaly]

        result = get_vessel_pca_anomalies(db, vessel_id=42)
        assert len(result) == 1
        assert result[0]["vessel_id"] == 42
        assert result[0]["tier"] == "high"
        assert result[0]["feature_vector"]["centroid_lat"] == 55.0


# ── API endpoint tests ──────────────────────────────────────────────────────


class TestAPIEndpoints:
    """Tests for the trajectory PCA API endpoints."""

    def test_post_disabled(self):
        """POST /detect/trajectory-pca returns 503 when disabled."""
        from fastapi.testclient import TestClient

        from app.api.routes_trajectory_pca import router

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_trajectory_pca.settings") as mock_settings:
            mock_settings.TRAJECTORY_PCA_ENABLED = False
            response = client.post("/detect/trajectory-pca")
            assert response.status_code == 503

    def test_get_disabled(self):
        """GET /detect/trajectory-pca/{vessel_id} returns 503 when disabled."""
        from fastapi.testclient import TestClient

        from app.api.routes_trajectory_pca import router

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_trajectory_pca.settings") as mock_settings:
            mock_settings.TRAJECTORY_PCA_ENABLED = False
            response = client.get("/detect/trajectory-pca/1")
            assert response.status_code == 503

    def test_post_enabled_runs_detection(self):
        """POST /detect/trajectory-pca runs detection when enabled."""
        from fastapi.testclient import TestClient

        from app.api.routes_trajectory_pca import router

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_trajectory_pca.settings") as mock_settings, \
             patch("app.modules.trajectory_pca_detector.run_pca_detection") as mock_run:
            mock_settings.TRAJECTORY_PCA_ENABLED = True
            mock_run.return_value = {"segments_processed": 10, "anomalies_created": 2}

            response = client.post("/detect/trajectory-pca")
            assert response.status_code == 200
            assert response.json()["segments_processed"] == 10

    def test_get_enabled_returns_results(self):
        """GET /detect/trajectory-pca/{vessel_id} returns anomaly results."""
        from fastapi.testclient import TestClient

        from app.api.routes_trajectory_pca import router

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_trajectory_pca.settings") as mock_settings, \
             patch("app.modules.trajectory_pca_detector.get_vessel_pca_anomalies") as mock_get:
            mock_settings.TRAJECTORY_PCA_ENABLED = True
            mock_get.return_value = [{"anomaly_id": 1, "vessel_id": 1, "tier": "high"}]

            response = client.get("/detect/trajectory-pca/1")
            assert response.status_code == 200
            assert response.json()[0]["tier"] == "high"

    def test_get_not_found(self):
        """GET /detect/trajectory-pca/{vessel_id} returns 404 when no results."""
        from fastapi.testclient import TestClient

        from app.api.routes_trajectory_pca import router

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_trajectory_pca.settings") as mock_settings, \
             patch("app.modules.trajectory_pca_detector.get_vessel_pca_anomalies") as mock_get:
            mock_settings.TRAJECTORY_PCA_ENABLED = True
            mock_get.return_value = []

            response = client.get("/detect/trajectory-pca/999")
            assert response.status_code == 404
