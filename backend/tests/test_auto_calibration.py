"""Tests for the auto-calibration workflow (Phase 5).

Covers: config settings, per-signal suggestion generation, apply/preview,
run_scheduled_calibration, and API endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import require_auth, require_senior_or_admin
from app.database import get_db
from app.main import app
from app.models.calibration_event import CalibrationEvent
from app.models.corridor import Corridor
from app.modules.fp_rate_tracker import (
    CorridorFPRate,
    generate_per_signal_suggestions,
    run_scheduled_calibration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_corridor(corridor_id=1, name="Test Corridor", corridor_type="export_route"):
    c = MagicMock(spec=Corridor)
    c.corridor_id = corridor_id
    c.name = name
    ct = MagicMock()
    ct.value = corridor_type
    c.corridor_type = ct
    return c


def _make_fp_rate(corridor_id=1, name="Test Corridor", fp_rate=0.25, total_alerts=20):
    return CorridorFPRate(
        corridor_id=corridor_id,
        corridor_name=name,
        total_alerts=total_alerts,
        false_positives=int(total_alerts * fp_rate),
        fp_rate=fp_rate,
    )


def _scoring_config():
    return {
        "gap_duration": {"2h_to_6h": 5, "6h_to_12h": 10, "24h_plus": 20},
        "spoofing": {"mmsi_conflict": 15, "position_jump": 12},
        "dark_zone": {"entry_penalty": 8},
        "sts": {"proximity_bonus": 10},
    }


@pytest.fixture
def mock_db():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.filter.return_value.first.return_value = None
    return session


@pytest.fixture
def api_client(mock_db):
    def override_get_db():
        yield mock_db

    def override_auth():
        return {"analyst_id": 1, "username": "test_admin", "role": "admin"}

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_auth] = override_auth
    app.dependency_overrides[require_senior_or_admin] = override_auth
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_auto_calibration_disabled_by_default(self):
        from app.config import Settings

        s = Settings(
            DATABASE_URL="sqlite:///test.db",
            _env_file=None,
        )
        assert s.AUTO_CALIBRATION_ENABLED is False

    def test_auto_calibration_settings_parsed(self):
        from app.config import Settings

        s = Settings(
            DATABASE_URL="sqlite:///test.db",
            AUTO_CALIBRATION_ENABLED=True,
            AUTO_CALIBRATION_MAX_ADJUSTMENT_PCT=20,
            AUTO_CALIBRATION_COOLDOWN_DAYS=14,
            _env_file=None,
        )
        assert s.AUTO_CALIBRATION_ENABLED is True
        assert s.AUTO_CALIBRATION_MAX_ADJUSTMENT_PCT == 20
        assert s.AUTO_CALIBRATION_COOLDOWN_DAYS == 14


# ---------------------------------------------------------------------------
# Per-signal suggestion tests
# ---------------------------------------------------------------------------


class TestPerSignalSuggestions:
    @patch("app.modules.scoring_config.load_scoring_config")
    @patch("app.modules.fp_rate_tracker.compute_fp_rates")
    def test_generates_suggestions_for_high_fp(self, mock_rates, mock_config, mock_db):
        mock_rates.return_value = [_make_fp_rate(fp_rate=0.25, total_alerts=20)]
        mock_config.return_value = _scoring_config()
        # No recent calibration event (cooldown check)
        mock_db.query.return_value.filter.return_value.first.return_value = None

        results = generate_per_signal_suggestions(mock_db)
        assert len(results) == 1
        assert results[0]["corridor_id"] == 1
        assert results[0]["fp_rate"] == 0.25
        assert len(results[0]["signal_suggestions"]) > 0
        # Each suggestion should have current, proposed, adjustment_pct
        for key, info in results[0]["signal_suggestions"].items():
            assert "current" in info
            assert "proposed" in info
            assert "adjustment_pct" in info
            assert info["proposed"] < info["current"]  # Should reduce for high FP

    @patch("app.modules.scoring_config.load_scoring_config")
    @patch("app.modules.fp_rate_tracker.compute_fp_rates")
    def test_respects_cooldown(self, mock_rates, mock_config, mock_db):
        mock_rates.return_value = [_make_fp_rate(fp_rate=0.30, total_alerts=50)]
        mock_config.return_value = _scoring_config()
        # Recent calibration event within cooldown
        recent_event = MagicMock(spec=CalibrationEvent)
        recent_event.created_at = datetime.now(UTC) - timedelta(days=2)
        mock_db.query.return_value.filter.return_value.first.return_value = recent_event

        results = generate_per_signal_suggestions(mock_db)
        assert len(results) == 0

    @patch("app.modules.scoring_config.load_scoring_config")
    @patch("app.modules.fp_rate_tracker.compute_fp_rates")
    def test_respects_max_adjustment_cap(self, mock_rates, mock_config, mock_db):
        """Even with very high FP rate, raw adjustment is capped at max_adj (15%).

        Note: the reported adjustment_pct may exceed 15% when bounds clamping
        further reduces the proposed value (e.g. gap_duration values capped at 3.0x).
        We verify the raw adjustment factor is correctly capped.
        """
        mock_rates.return_value = [_make_fp_rate(fp_rate=0.90, total_alerts=100)]
        # Use values within bounds so clamping doesn't distort the pct
        mock_config.return_value = {
            "gap_duration": {"6h_to_12h": 1.5},  # Within (0.5, 3.0) bounds
            "spoofing": {"mmsi_conflict": 2.0},   # Within (0.3, 5.0) bounds
            "dark_zone": {"entry_penalty": 2.0},   # Within (0.3, 5.0) bounds
            "sts": {"proximity_bonus": 2.0},       # Within (0.3, 5.0) bounds
        }
        mock_db.query.return_value.filter.return_value.first.return_value = None

        results = generate_per_signal_suggestions(mock_db)
        assert len(results) == 1
        for key, info in results[0]["signal_suggestions"].items():
            # Raw adjustment should be capped near 15% (rounding of proposed
            # value to 2 decimal places can cause up to ~0.5% deviation)
            assert abs(info["adjustment_pct"]) <= 16.0

    @patch("app.modules.scoring_config.load_scoring_config")
    @patch("app.modules.fp_rate_tracker.compute_fp_rates")
    def test_no_suggestions_for_low_fp(self, mock_rates, mock_config, mock_db):
        mock_rates.return_value = [_make_fp_rate(fp_rate=0.10, total_alerts=50)]
        mock_config.return_value = _scoring_config()

        results = generate_per_signal_suggestions(mock_db)
        assert len(results) == 0

    @patch("app.modules.scoring_config.load_scoring_config")
    @patch("app.modules.fp_rate_tracker.compute_fp_rates")
    def test_no_suggestions_for_few_alerts(self, mock_rates, mock_config, mock_db):
        mock_rates.return_value = [_make_fp_rate(fp_rate=0.50, total_alerts=5)]
        mock_config.return_value = _scoring_config()

        results = generate_per_signal_suggestions(mock_db)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Apply suggestion tests
# ---------------------------------------------------------------------------


class TestApplySuggestion:
    @patch("app.modules.fp_rate_tracker.generate_per_signal_suggestions")
    def test_apply_creates_override_and_event(self, mock_suggest, mock_db):
        """Applying a suggestion should create CorridorScoringOverride and CalibrationEvent."""
        mock_suggest.return_value = [{
            "corridor_id": 1,
            "corridor_name": "Test",
            "fp_rate": 0.25,
            "total_alerts": 20,
            "signal_suggestions": {
                "gap_duration.2h_to_6h": {"current": 5, "proposed": 4.25, "adjustment_pct": -15.0},
            },
            "reason": "FP rate 25% exceeds 15% threshold with 20 reviewed alerts",
        }]
        # No existing override
        mock_db.query.return_value.filter.return_value.first.return_value = None

        from app.api.routes_fp_tuning import apply_calibration_suggestion

        # Mock corridor lookup
        corridor = _make_corridor()
        with patch("app.api.routes_fp_tuning._get_corridor_or_404", return_value=corridor):
            with patch("app.api.routes_fp_tuning._check_enabled"):
                result = apply_calibration_suggestion(
                    corridor_id=1, preview=False, db=mock_db,
                    auth={"analyst_id": 1, "role": "admin"},
                )

        assert result["applied"] is True
        assert result["corridor_id"] == 1
        assert "gap_duration.2h_to_6h" in result["overrides"]
        # Should have called db.add twice (override + event) and commit
        assert mock_db.add.call_count == 2
        assert mock_db.commit.called

    @patch("app.modules.fp_rate_tracker.generate_per_signal_suggestions")
    def test_apply_records_calibration_event(self, mock_suggest, mock_db):
        """CalibrationEvent should be recorded with correct type and values."""
        mock_suggest.return_value = [{
            "corridor_id": 1,
            "corridor_name": "Test",
            "fp_rate": 0.20,
            "total_alerts": 30,
            "signal_suggestions": {
                "spoofing.mmsi_conflict": {"current": 15, "proposed": 14.1, "adjustment_pct": -6.0},
            },
            "reason": "FP rate 20% exceeds 15% threshold with 30 reviewed alerts",
        }]
        mock_db.query.return_value.filter.return_value.first.return_value = None

        from app.api.routes_fp_tuning import apply_calibration_suggestion

        corridor = _make_corridor()
        with patch("app.api.routes_fp_tuning._get_corridor_or_404", return_value=corridor):
            with patch("app.api.routes_fp_tuning._check_enabled"):
                apply_calibration_suggestion(
                    corridor_id=1, preview=False, db=mock_db,
                    auth={"analyst_id": 1, "role": "admin"},
                )

        # Check CalibrationEvent was added
        added_objects = [call.args[0] for call in mock_db.add.call_args_list]
        cal_events = [o for o in added_objects if isinstance(o, CalibrationEvent)]
        assert len(cal_events) == 1
        assert cal_events[0].event_type == "suggestion_accepted"
        assert cal_events[0].corridor_id == 1

    @patch("app.modules.fp_rate_tracker.generate_per_signal_suggestions")
    def test_preview_does_not_apply(self, mock_suggest, mock_db):
        mock_suggest.return_value = [{
            "corridor_id": 1,
            "corridor_name": "Test",
            "fp_rate": 0.25,
            "total_alerts": 20,
            "signal_suggestions": {
                "gap_duration.2h_to_6h": {"current": 5, "proposed": 4.25, "adjustment_pct": -15.0},
            },
            "reason": "FP rate 25% exceeds 15% threshold",
        }]

        from app.api.routes_fp_tuning import apply_calibration_suggestion

        corridor = _make_corridor()
        with patch("app.api.routes_fp_tuning._get_corridor_or_404", return_value=corridor):
            with patch("app.api.routes_fp_tuning._check_enabled"):
                result = apply_calibration_suggestion(
                    corridor_id=1, preview=True, db=mock_db,
                    auth={"analyst_id": 1, "role": "admin"},
                )

        assert result["preview"] is True
        assert "suggestion" in result
        assert not mock_db.commit.called

    @patch("app.modules.fp_rate_tracker.generate_per_signal_suggestions")
    def test_apply_nonexistent_suggestion_returns_404(self, mock_suggest, mock_db):
        mock_suggest.return_value = []  # No suggestions

        from app.api.routes_fp_tuning import apply_calibration_suggestion

        corridor = _make_corridor()
        with patch("app.api.routes_fp_tuning._get_corridor_or_404", return_value=corridor):
            with patch("app.api.routes_fp_tuning._check_enabled"):
                with pytest.raises(Exception) as exc_info:
                    apply_calibration_suggestion(
                        corridor_id=99, preview=False, db=mock_db,
                        auth={"analyst_id": 1, "role": "admin"},
                    )
                assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Run scheduled calibration tests
# ---------------------------------------------------------------------------


class TestRunScheduled:
    @patch("app.config.settings")
    def test_returns_disabled_when_off(self, mock_settings, mock_db):
        mock_settings.AUTO_CALIBRATION_ENABLED = False
        result = run_scheduled_calibration(mock_db)
        assert result["status"] == "disabled"
        assert result["suggestions"] == []

    @patch("app.modules.fp_rate_tracker.generate_per_signal_suggestions")
    @patch("app.config.settings")
    def test_returns_suggestions_when_enabled(self, mock_settings, mock_suggest, mock_db):
        mock_settings.AUTO_CALIBRATION_ENABLED = True
        mock_suggest.return_value = [{"corridor_id": 1, "signal_suggestions": {}}]
        result = run_scheduled_calibration(mock_db)
        assert result["status"] == "ok"
        assert result["suggestion_count"] == 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestAPIEndpoints:
    @patch("app.modules.fp_rate_tracker.generate_per_signal_suggestions")
    def test_get_per_signal_suggestions_endpoint(self, mock_suggest, api_client):
        mock_suggest.return_value = [
            {
                "corridor_id": 1,
                "corridor_name": "Test",
                "fp_rate": 0.25,
                "total_alerts": 20,
                "signal_suggestions": {},
                "reason": "test",
            }
        ]
        resp = api_client.get("/api/v1/corridors/calibration-suggestions/per-signal")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["corridor_id"] == 1

    @patch("app.modules.fp_rate_tracker.generate_per_signal_suggestions")
    def test_apply_suggestion_endpoint_404(self, mock_suggest, api_client, mock_db):
        """POST apply-suggestion returns 404 when no suggestion exists."""
        mock_suggest.return_value = []
        # Mock corridor lookup to succeed
        corridor = _make_corridor(corridor_id=99)
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        resp = api_client.post("/api/v1/corridors/99/apply-suggestion")
        assert resp.status_code == 404

    @patch("app.modules.fp_rate_tracker.run_scheduled_calibration")
    def test_run_auto_calibration_endpoint(self, mock_run, api_client):
        mock_run.return_value = {"status": "ok", "suggestion_count": 0, "suggestions": []}
        resp = api_client.post("/api/v1/corridors/auto-calibration/run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
