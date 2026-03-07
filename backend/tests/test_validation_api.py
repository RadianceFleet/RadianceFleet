"""Tests for validation admin endpoints."""
import pytest
from unittest.mock import patch, MagicMock

from app.auth import require_admin
from app.main import app


@pytest.fixture(autouse=True)
def _override_admin(api_client):
    """Override require_admin for all tests in this module."""
    app.dependency_overrides[require_admin] = lambda: {"sub": "admin"}
    yield
    # api_client fixture already clears overrides, but ensure admin is removed
    app.dependency_overrides.pop(require_admin, None)


class TestValidateEndpoint:
    @patch("app.modules.validation_harness.run_validation")
    def test_validate_returns_results(self, mock_run, api_client):
        mock_run.return_value = {
            "threshold_band": "high",
            "n_evaluated": 5,
            "precision": 0.8,
            "recall": 0.9,
            "f2_score": 0.88,
            "pr_auc": 0.75,
            "confusion_matrix": {"tp": 4, "fp": 1, "tn": 3, "fn": 0},
            "per_source": {},
            "score_distribution": {"positives": {"n": 4}, "negatives": {"n": 1}},
        }
        response = api_client.get("/api/v1/admin/validate?threshold_band=high")
        assert response.status_code == 200
        data = response.json()
        assert "f2_score" in data
        assert data["threshold_band"] == "high"

    @patch("app.modules.validation_harness.run_validation")
    def test_validate_empty_data(self, mock_run, api_client):
        mock_run.return_value = {"error": "no_data", "n_linked": 0}
        response = api_client.get("/api/v1/admin/validate")
        assert response.status_code == 200
        data = response.json()
        assert data["error"] == "no_data"


class TestValidateSignalsEndpoint:
    @patch("app.modules.validation_harness.signal_effectiveness_report")
    def test_signals_returns_list(self, mock_report, api_client):
        mock_report.return_value = [
            {"signal": "dark_zone", "tp_freq": 0.8, "fp_freq": 0.2, "lift": 4.0, "spurious": False},
        ]
        response = api_client.get("/api/v1/admin/validate/signals")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["signal"] == "dark_zone"


class TestValidateSweepEndpoint:
    @patch("app.modules.validation_harness.sweep_thresholds")
    def test_sweep_returns_list(self, mock_sweep, api_client):
        mock_sweep.return_value = [
            {"threshold": 0, "precision": 0.5, "recall": 1.0, "f2_score": 0.83},
            {"threshold": 50, "precision": 0.8, "recall": 0.7, "f2_score": 0.72},
        ]
        response = api_client.get("/api/v1/admin/validate/sweep")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["threshold"] == 0


class TestAnalystMetricsEndpoint:
    @patch("app.modules.validation_harness.analyst_feedback_metrics")
    def test_analyst_metrics_returns_shape(self, mock_metrics, api_client):
        mock_metrics.return_value = {
            "total_reviewed": 10,
            "confirmed_tp": 7,
            "confirmed_fp": 3,
            "fp_rate": 0.3,
            "by_score_band": {"high": {"tp": 5, "fp": 1}},
            "by_corridor": {"1": {"tp": 3, "fp": 2}},
        }
        response = api_client.get("/api/v1/admin/validate/analyst-metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["total_reviewed"] == 10
        assert data["confirmed_fp"] == 3
        assert "by_score_band" in data
        assert "by_corridor" in data
