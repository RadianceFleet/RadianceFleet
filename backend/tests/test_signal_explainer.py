"""Tests for the explainability framework (signal_explainer + API endpoint)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.modules.signal_explainer import (
    ExplainabilityResponse,
    SignalExplanation,
    WaterfallEntry,
    _analyst_category,
    _categorize_key,
    _compute_waterfall,
    _explain_signal,
    _generate_summary,
    _is_multiplier_key,
    _key_to_label,
    explain_alert,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_alert(
    gap_event_id: int = 1,
    vessel_id: int = 10,
    risk_score: int = 65,
    breakdown: dict | None = None,
    duration_minutes: int = 720,
) -> SimpleNamespace:
    """Create a mock AISGapEvent-like object."""
    return SimpleNamespace(
        gap_event_id=gap_event_id,
        vessel_id=vessel_id,
        risk_score=risk_score,
        risk_breakdown_json=breakdown,
        duration_minutes=duration_minutes,
        gap_start_utc=datetime(2025, 6, 1, 0, 0, tzinfo=UTC),
        gap_end_utc=datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
    )


def _make_vessel(
    vessel_id: int = 10,
    name: str = "Test Vessel",
    mmsi: str = "123456789",
) -> SimpleNamespace:
    return SimpleNamespace(vessel_id=vessel_id, name=name, mmsi=mmsi)


# ── Tier-1 template matching ────────────────────────────────────────────────


class TestTier1Templates:
    def test_known_key_gap_duration_24h(self):
        explanation, tier = _explain_signal("gap_duration_24h", 20)
        assert tier == 1
        assert "24 hours" in explanation
        assert "20" in explanation

    def test_known_key_watchlist_ofac_sdn(self):
        explanation, tier = _explain_signal("watchlist_ofac_sdn", 30)
        assert tier == 1
        assert "OFAC SDN" in explanation

    def test_known_key_spoofing_circle(self):
        explanation, tier = _explain_signal("spoofing_circle", 15)
        assert tier == 1
        assert "Circular" in explanation or "circular" in explanation.lower()

    def test_known_key_flag_AND_name_change(self):
        explanation, tier = _explain_signal("flag_AND_name_change", 25)
        assert tier == 1
        assert "flag" in explanation.lower() and "name" in explanation.lower()

    def test_known_key_sts_dark_dark(self):
        explanation, tier = _explain_signal("sts_dark_dark", 20)
        assert tier == 1
        assert "AIS off" in explanation

    def test_known_key_loiter_pre_gap(self):
        explanation, tier = _explain_signal("loiter_pre_gap", 10)
        assert tier == 1
        assert "loitering" in explanation.lower()

    def test_known_key_speed_impossible(self):
        explanation, tier = _explain_signal("speed_impossible", 18)
        assert tier == 1
        assert "impossible" in explanation.lower()


# ── Tier-2 pattern matching ──────────────────────────────────────────────────


class TestTier2Patterns:
    def test_multiplier_suffix(self):
        explanation, tier = _explain_signal("corridor_risk_multiplier", 1.5)
        assert tier == 2
        assert "multiplier" in explanation.lower()

    def test_factor_suffix(self):
        explanation, tier = _explain_signal("age_risk_factor", 2.0)
        assert tier == 2
        assert "factor" in explanation.lower()

    def test_prefix_viirs(self):
        explanation, tier = _explain_signal("viirs_nighttime_match", 12)
        assert tier == 2
        assert "VIIRS" in explanation

    def test_prefix_gap_sar(self):
        explanation, tier = _explain_signal("gap_sar_confirmed", 8)
        assert tier == 2
        assert "Gap-SAR" in explanation

    def test_prefix_fleet(self):
        explanation, tier = _explain_signal("fleet_owner_overlap", 5)
        assert tier == 2
        assert "Fleet" in explanation or "fleet" in explanation.lower()

    def test_prefix_pi(self):
        explanation, tier = _explain_signal("pi_lapsed_coverage", 7)
        assert tier == 2
        assert "P&I" in explanation


# ── Tier-3 fallback ──────────────────────────────────────────────────────────


class TestTier3Fallback:
    def test_unknown_key(self):
        explanation, tier = _explain_signal("completely_unknown_signal", 3)
        assert tier == 3
        assert "completely_unknown_signal" in explanation
        assert "3" in explanation

    def test_another_unknown_key(self):
        explanation, tier = _explain_signal("exotic_metric_xyz", 7.5)
        assert tier == 3
        assert "7.5" in explanation


# ── Waterfall computation ────────────────────────────────────────────────────


class TestWaterfall:
    def test_sorted_descending_by_contribution(self):
        signals = [("a", 5.0), ("b", 15.0), ("c", 10.0)]
        entries = _compute_waterfall(signals)
        # Sorted by absolute value: b(15), c(10), a(5)
        assert entries[0].label == "B"
        assert entries[1].label == "C"
        assert entries[2].label == "A"

    def test_cumulative_totals(self):
        signals = [("x", 10.0), ("y", 5.0)]
        entries = _compute_waterfall(signals)
        # x=10 first (larger), y=5 second
        assert entries[0].value == 10.0
        assert entries[0].cumulative == 10.0
        assert entries[1].value == 5.0
        assert entries[1].cumulative == 15.0

    def test_multiplier_effects(self):
        signals = [("base_score", 20.0), ("risk_multiplier", 1.5)]
        entries = _compute_waterfall(signals)
        assert len(entries) == 2
        assert entries[0].is_multiplier is False
        assert entries[1].is_multiplier is True
        # Multiplier effect: 20 * (1.5 - 1) = 10
        assert entries[1].value == 10.0
        assert entries[1].cumulative == 30.0

    def test_empty_signals(self):
        entries = _compute_waterfall([])
        assert entries == []

    def test_single_signal(self):
        entries = _compute_waterfall([("only", 7.0)])
        assert len(entries) == 1
        assert entries[0].cumulative == 7.0

    def test_all_multipliers(self):
        signals = [("risk_multiplier", 2.0), ("age_factor", 1.3)]
        entries = _compute_waterfall(signals)
        # No additive signals, cumulative starts at 0
        # First multiplier: effect = 0 * (2.0 - 1) = 0 ... but edge case
        # with 0 cumulative, effect = value directly
        assert len(entries) == 2
        for e in entries:
            assert e.is_multiplier is True


# ── Category grouping ────────────────────────────────────────────────────────


class TestCategoryGrouping:
    def test_watchlist_categorized(self):
        assert _categorize_key("watchlist_ofac_sdn") == "WATCHLIST"

    def test_spoofing_categorized(self):
        assert _categorize_key("spoofing_circle") == "SPOOFING"

    def test_sts_categorized(self):
        assert _categorize_key("sts_event_detected") == "STS_TRANSFER"

    def test_identity_categorized(self):
        assert _categorize_key("flag_change_rapid") == "IDENTITY_CHANGE"

    def test_default_categorized(self):
        assert _categorize_key("some_unknown_thing") == "AIS_GAP"

    def test_analyst_temporal_category(self):
        assert _analyst_category("gap_duration_24h") == "temporal"
        assert _analyst_category("gap_frequency_high") == "temporal"

    def test_analyst_spatial_category(self):
        assert _analyst_category("sts_event_detected") == "spatial"
        assert _analyst_category("dark_zone_gap") == "spatial"

    def test_analyst_identity_category(self):
        assert _analyst_category("spoofing_circle") == "identity"
        assert _analyst_category("flag_change_rapid") == "identity"

    def test_analyst_sanctions_category(self):
        assert _analyst_category("watchlist_ofac_sdn") == "sanctions"

    def test_analyst_behavioral_default(self):
        assert _analyst_category("speed_impossible") == "behavioral"


# ── Summary generation ───────────────────────────────────────────────────────


class TestSummaryGeneration:
    def test_summary_with_signals(self):
        alert = _make_alert(breakdown={"gap_duration_24h": 20, "speed_impossible": 15})
        vessel = _make_vessel()
        signals = [
            SignalExplanation(key="gap_duration_24h", value=20, explanation="", category="temporal", tier=1),
            SignalExplanation(key="speed_impossible", value=15, explanation="", category="behavioral", tier=1),
        ]
        summary = _generate_summary(vessel, alert, signals, 65)
        assert "Test Vessel" in summary
        assert "123456789" in summary
        assert "65" in summary

    def test_summary_no_signals(self):
        alert = _make_alert(breakdown={})
        vessel = _make_vessel()
        summary = _generate_summary(vessel, alert, [], 0)
        assert "no contributing risk signals" in summary.lower()

    def test_summary_no_vessel(self):
        alert = _make_alert()
        summary = _generate_summary(None, alert, [], 0)
        assert "Unknown vessel" in summary


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_breakdown(self):
        alert = _make_alert(risk_score=0, breakdown={})
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = explain_alert(alert, db)
        assert isinstance(result, ExplainabilityResponse)
        assert result.signals == []
        assert result.waterfall == []
        assert result.categories == {}

    def test_null_breakdown(self):
        alert = _make_alert(risk_score=0, breakdown=None)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = explain_alert(alert, db)
        assert isinstance(result, ExplainabilityResponse)
        assert result.signals == []

    def test_single_signal_breakdown(self):
        alert = _make_alert(risk_score=20, breakdown={"gap_duration_24h": 20})
        vessel = _make_vessel()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = vessel
        result = explain_alert(alert, db)
        assert len(result.signals) == 1
        assert result.signals[0].tier == 1
        assert result.total_score == 20.0

    def test_non_numeric_values_skipped(self):
        alert = _make_alert(
            risk_score=10,
            breakdown={"gap_duration_24h": 10, "notes": "some text", "flags": ["a", "b"]},
        )
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = explain_alert(alert, db)
        assert len(result.signals) == 1
        assert result.signals[0].key == "gap_duration_24h"

    def test_negative_values_included(self):
        alert = _make_alert(risk_score=5, breakdown={"gap_duration_3h": 10, "feed_outage_deduction": -5})
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = explain_alert(alert, db)
        # Both positive and negative are included
        assert len(result.signals) == 2


# ── Helper functions ─────────────────────────────────────────────────────────


class TestHelpers:
    def test_key_to_label(self):
        assert _key_to_label("gap_duration_24h") == "Gap duration 24h"
        assert _key_to_label("speed_impossible") == "Speed impossible"

    def test_is_multiplier_key(self):
        assert _is_multiplier_key("corridor_risk_multiplier") is True
        assert _is_multiplier_key("age_factor") is True
        assert _is_multiplier_key("damping_coefficient") is True
        assert _is_multiplier_key("gap_duration_24h") is False


# ── API endpoint test ────────────────────────────────────────────────────────


class TestAPIEndpoint:
    def test_get_explain_404(self):
        """Test that a missing alert returns 404."""
        from fastapi.testclient import TestClient

        from app.api.routes_explainability import router

        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")

        # Mock get_db
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        app.dependency_overrides = {}

        from app.database import get_db

        app.dependency_overrides[get_db] = lambda: mock_db

        client = TestClient(app)
        resp = client.get("/api/v1/alerts/999/explain")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_explain_success(self):
        """Test successful explanation response."""
        from fastapi.testclient import TestClient

        from app.api.routes_explainability import router

        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")

        mock_alert = _make_alert(
            breakdown={"gap_duration_24h": 20, "watchlist_ofac_sdn": 30},
        )
        mock_vessel = _make_vessel()

        mock_db = MagicMock()
        # First query: alert lookup
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_alert,
            mock_vessel,
        ]

        from app.database import get_db

        app.dependency_overrides[get_db] = lambda: mock_db

        client = TestClient(app)
        resp = client.get("/api/v1/alerts/1/explain")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alert_id"] == 1
        assert data["total_score"] == 65.0
        assert len(data["signals"]) == 2
        assert len(data["waterfall"]) == 2
        assert "summary" in data
        assert len(data["categories"]) > 0
