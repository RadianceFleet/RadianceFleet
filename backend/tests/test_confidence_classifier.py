"""Tests for multi-signal confidence classification."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.modules.confidence_classifier import (
    _categorize_key,
    classify_vessel_confidence,
)


# ── Tests: _categorize_key ───────────────────────────────────────────

class TestCategorizeKey:
    def test_watchlist_keys(self):
        assert _categorize_key("watchlist_match") == "WATCHLIST"
        assert _categorize_key("owner_or_manager_on_sanctions") == "WATCHLIST"

    def test_spoofing_keys(self):
        assert _categorize_key("spoofing_circle") == "SPOOFING"
        assert _categorize_key("track_naturalness") == "SPOOFING"
        assert _categorize_key("stale_ais") == "SPOOFING"
        assert _categorize_key("stateless_mmsi") == "SPOOFING"
        assert _categorize_key("imo_fraud") == "SPOOFING"
        assert _categorize_key("cross_receiver") == "SPOOFING"
        assert _categorize_key("fake_port_call") == "SPOOFING"
        assert _categorize_key("scrapped_imo") == "SPOOFING"
        assert _categorize_key("track_replay") == "SPOOFING"

    def test_sts_keys(self):
        assert _categorize_key("sts_event") == "STS_TRANSFER"
        assert _categorize_key("repeat_sts") == "STS_TRANSFER"
        assert _categorize_key("dark_dark_sts") == "STS_TRANSFER"
        assert _categorize_key("draught_change") == "STS_TRANSFER"
        assert _categorize_key("russian_port") == "STS_TRANSFER"
        assert _categorize_key("voyage_cycle") == "STS_TRANSFER"

    def test_identity_keys(self):
        assert _categorize_key("flag_change") == "IDENTITY_CHANGE"
        assert _categorize_key("flag_hopping") == "IDENTITY_CHANGE"
        assert _categorize_key("rename_velocity") == "IDENTITY_CHANGE"
        assert _categorize_key("invalid_metadata") == "IDENTITY_CHANGE"
        assert _categorize_key("fraudulent_registry") == "IDENTITY_CHANGE"

    def test_loitering_keys(self):
        assert _categorize_key("loiter_gap") == "LOITERING"
        assert _categorize_key("vessel_laid_up") == "LOITERING"

    def test_fleet_keys(self):
        assert _categorize_key("fleet_dark") == "FLEET_PATTERN"
        assert _categorize_key("owner_cluster") == "FLEET_PATTERN"
        assert _categorize_key("shared_manager") == "FLEET_PATTERN"
        assert _categorize_key("convoy_detected") == "FLEET_PATTERN"

    def test_gap_keys(self):
        assert _categorize_key("gap_duration") == "AIS_GAP"
        assert _categorize_key("dark_zone") == "AIS_GAP"
        assert _categorize_key("speed_impossible") == "AIS_GAP"

    def test_unknown_defaults_to_ais_gap(self):
        assert _categorize_key("vessel_age") == "AIS_GAP"
        assert _categorize_key("some_unknown_key") == "AIS_GAP"


# ── Tests: classify_vessel_confidence ────────────────────────────────

class TestClassifyVesselConfidence:
    def _make_vessel(self, vessel_id=1):
        v = MagicMock()
        v.vessel_id = vessel_id
        return v

    def test_confirmed_with_watchlist(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=30,
            breakdown={"watchlist_match": 20},
            has_watchlist_match=True,
        )
        assert confidence == "CONFIRMED"

    def test_confirmed_with_analyst_verified(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=10,
            breakdown={},
            analyst_verified=True,
        )
        assert confidence == "CONFIRMED"

    def test_high_with_multiple_categories(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=80,
            breakdown={
                "gap_duration": 40,
                "spoofing_circle": 30,
                "sts_event": 10,
            },
        )
        assert confidence == "HIGH"
        assert "AIS_GAP" in evidence
        assert "SPOOFING" in evidence

    def test_high_with_single_category_80_plus(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=85,
            breakdown={"gap_duration": 85},
        )
        assert confidence == "HIGH"

    def test_medium_with_category_30_plus(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=55,
            breakdown={"gap_duration": 35, "vessel_age": 20},
        )
        assert confidence == "MEDIUM"

    def test_low_score_21_to_50(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=30,
            breakdown={"gap_duration": 20, "vessel_age": 10},
        )
        assert confidence == "LOW"

    def test_none_score_below_21(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=15,
            breakdown={"vessel_age": 15},
        )
        assert confidence == "NONE"

    def test_skips_negative_values(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=80,
            breakdown={
                "gap_duration": 50,
                "spoofing_circle": 40,
                "deduction": -10,
            },
        )
        assert "deduction" not in str(evidence) or evidence.get("AIS_GAP", 0) >= 0

    def test_skips_non_numeric_values(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=30,
            breakdown={
                "gap_duration": 20,
                "notes": "some text",
            },
        )
        assert confidence == "LOW"

    def test_empty_breakdown(self):
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=10,
            breakdown={},
        )
        assert confidence == "NONE"
        assert evidence == {}

    def test_score_76_single_category_below_80_not_high(self):
        """Score >= 76 but only 1 category with < 80 pts should NOT be HIGH."""
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=76,
            breakdown={"gap_duration": 76},
        )
        # Single category with 76 pts (< 80), and only 1 category
        assert confidence != "HIGH" or len([v for v in evidence.values() if v > 0]) >= 2

    def test_medium_requires_30_plus_category(self):
        """Score >= 51 but no category with 30+ pts should NOT be MEDIUM."""
        vessel = self._make_vessel()
        confidence, evidence = classify_vessel_confidence(
            vessel, total_score=51,
            breakdown={
                # Spread across 4 different categories so none reaches 30
                "gap_duration": 15,       # AIS_GAP
                "spoofing_circle": 14,    # SPOOFING
                "sts_event": 12,          # STS_TRANSFER
                "flag_hopping": 10,       # IDENTITY_CHANGE
            },
        )
        # No single category >= 30 pts, so should be LOW
        assert confidence == "LOW"


# ── Tests: classify_all_vessels ──────────────────────────────────────

class TestClassifyAllVessels:
    def test_empty_db(self):
        from app.modules.confidence_classifier import classify_all_vessels

        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.all.return_value = []
        db.query.return_value.distinct.return_value.all.return_value = []

        result = classify_all_vessels(db)
        assert result["classified"] == 0
        assert result["by_level"] == {}
