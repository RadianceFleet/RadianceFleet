"""Tests for behavioral_baseline_detector — Behavioral Baseline Per-Vessel Profiling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.behavioral_baseline_detector import (
    BASELINE_DAYS,
    CURRENT_WINDOW_DAYS,
    GAP_FREQUENCY_CAP,
    MULTI_SIGNAL_BONUS,
    MULTI_SIGNAL_MIN_COUNT,
    PORT_NOVELTY_THRESHOLD,
    TIER_HIGH_THRESHOLD,
    TIER_MEDIUM_THRESHOLD,
    _z_score_threshold,
    build_vessel_profile,
    compute_deviation_score,
    compute_gap_frequency_ratio,
    compute_gap_pattern,
    compute_port_novelty,
    compute_port_pattern,
    compute_route_deviation,
    compute_route_pattern,
    compute_speed_deviation,
    compute_speed_stats,
    compute_temporal_pattern,
    get_vessel_profile,
    refresh_vessel_profile,
    run_behavioral_baseline,
    _score_to_tier,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_ais_point(sog=12.0, timestamp=None, corridor_id=None):
    """Create a mock AIS point."""
    pt = MagicMock()
    pt.sog = sog
    pt.timestamp_utc = timestamp or datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    pt.corridor_id = corridor_id
    return pt


def _make_gap_event(duration_minutes=120, corridor_id=None):
    """Create a mock gap event."""
    ge = MagicMock()
    ge.duration_minutes = duration_minutes
    ge.corridor_id = corridor_id
    ge.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    return ge


def _make_port_call(port_id, arrival, departure=None):
    """Create a port call dict."""
    return {
        "port_id": port_id,
        "arrival_utc": arrival,
        "departure_utc": departure,
    }


# ── Speed stats tests ───────────────────────────────────────────────────────


class TestSpeedStats:
    """Tests for speed statistics computation."""

    def test_basic_speed_stats(self):
        """Compute median, IQR, and max SOG from a list of values."""
        sog = [5.0, 10.0, 12.0, 14.0, 20.0]
        stats = compute_speed_stats(sog)
        assert stats["median_sog"] == 12.0
        assert stats["max_sog"] == 20.0
        assert stats["iqr_sog"] > 0

    def test_empty_sog_returns_zeros(self):
        """Empty SOG list returns all-zero stats."""
        stats = compute_speed_stats([])
        assert stats["median_sog"] == 0.0
        assert stats["iqr_sog"] == 0.0
        assert stats["max_sog"] == 0.0

    def test_single_value(self):
        """Single SOG value yields median=value, iqr=0."""
        stats = compute_speed_stats([7.5])
        assert stats["median_sog"] == 7.5
        assert stats["max_sog"] == 7.5
        assert stats["iqr_sog"] == 0.0

    def test_even_count_median(self):
        """Even number of values takes average of two middle values."""
        sog = [2.0, 4.0, 6.0, 8.0]
        stats = compute_speed_stats(sog)
        assert stats["median_sog"] == 5.0


# ── Port pattern tests ──────────────────────────────────────────────────────


class TestPortPattern:
    """Tests for port pattern extraction."""

    def test_basic_port_pattern(self):
        """Extract visited ports and dwell times."""
        t1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        calls = [_make_port_call(1, t1, t2), _make_port_call(2, t1, t2)]
        pattern = compute_port_pattern(calls)
        assert sorted(pattern["visited_ports"]) == [1, 2]
        assert "1" in pattern["dwell_times"]
        assert pattern["dwell_times"]["1"] == 12.0

    def test_empty_port_calls(self):
        """Empty port calls returns empty lists."""
        pattern = compute_port_pattern([])
        assert pattern["visited_ports"] == []
        assert pattern["dwell_times"] == {}

    def test_null_port_id_skipped(self):
        """Port calls with null port_id are skipped."""
        t1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        calls = [_make_port_call(None, t1, t1 + timedelta(hours=6))]
        pattern = compute_port_pattern(calls)
        assert pattern["visited_ports"] == []

    def test_no_departure_skips_dwell(self):
        """Port call without departure time has no dwell time computed."""
        t1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        calls = [_make_port_call(5, t1, None)]
        pattern = compute_port_pattern(calls)
        assert 5 in pattern["visited_ports"]
        assert "5" not in pattern["dwell_times"]


# ── Route pattern tests ─────────────────────────────────────────────────────


class TestRoutePattern:
    """Tests for route pattern extraction."""

    def test_top_corridors(self):
        """Top corridors by visit frequency are returned."""
        visits = [1, 1, 1, 2, 2, 3, 4, 4, 4, 4]
        pattern = compute_route_pattern(visits)
        top = pattern["top_corridors"]
        assert len(top) == 3
        assert top[0]["corridor_id"] == 4  # 4 visits
        assert top[1]["corridor_id"] == 1  # 3 visits

    def test_empty_visits(self):
        """Empty visit list returns empty top corridors."""
        pattern = compute_route_pattern([])
        assert pattern["top_corridors"] == []


# ── Gap pattern tests ────────────────────────────────────────────────────────


class TestGapPattern:
    """Tests for gap pattern computation."""

    def test_basic_gap_pattern(self):
        """Compute frequency, mean, and max gap duration."""
        durations = [60.0, 120.0, 180.0]
        pattern = compute_gap_pattern(durations)
        assert pattern["frequency"] == 3
        assert pattern["mean_duration"] == 120.0
        assert pattern["max_duration"] == 180.0

    def test_empty_gaps(self):
        """No gaps returns all-zero pattern."""
        pattern = compute_gap_pattern([])
        assert pattern["frequency"] == 0
        assert pattern["mean_duration"] == 0.0
        assert pattern["max_duration"] == 0.0


# ── Gap frequency ratio tests ───────────────────────────────────────────────


class TestGapFrequencyRatio:
    """Tests for gap frequency ratio with explicit 3-case handling."""

    def test_both_zero(self):
        """Both historical and current zero: ratio = 1.0, no signal."""
        ratio, fired = compute_gap_frequency_ratio(0, 0)
        assert ratio == 1.0
        assert fired is False

    def test_historical_zero_current_nonzero(self):
        """Historical zero + current nonzero: capped at GAP_FREQUENCY_CAP, signal fires."""
        ratio, fired = compute_gap_frequency_ratio(0, 5)
        assert ratio == GAP_FREQUENCY_CAP
        assert fired is True

    def test_current_zero(self):
        """Current zero: ratio = 0.0, no signal (no gaps = good)."""
        ratio, fired = compute_gap_frequency_ratio(3, 0)
        assert ratio == 0.0
        assert fired is False

    def test_normal_ratio(self):
        """Normal ratio when both are nonzero and ratio <= 2."""
        ratio, fired = compute_gap_frequency_ratio(5, 5)
        assert ratio == 1.0
        assert fired is False

    def test_high_ratio_fires_signal(self):
        """Ratio > 2.0 fires the gap signal."""
        ratio, fired = compute_gap_frequency_ratio(2, 6)
        assert ratio == 3.0
        assert fired is True

    def test_cap_prevents_extreme_values(self):
        """Very high ratio is capped at GAP_FREQUENCY_CAP."""
        ratio, fired = compute_gap_frequency_ratio(0.1, 500)
        assert ratio == GAP_FREQUENCY_CAP
        assert fired is True

    def test_division_by_zero_protection(self):
        """Historical=0, current>0 does not raise ZeroDivisionError."""
        # This should not raise
        ratio, fired = compute_gap_frequency_ratio(0, 1)
        assert ratio == GAP_FREQUENCY_CAP
        assert fired is True


# ── Deviation scoring tests ──────────────────────────────────────────────────


class TestDeviationScoring:
    """Tests for composite deviation scoring."""

    def test_no_signals_low_score(self):
        """No fired signals produce a low deviation score."""
        score, signals = compute_deviation_score(
            speed_z=0.5, speed_fired=False,
            port_novelty=0.1, port_fired=False,
            route_deviation=0.0, route_fired=False,
            gap_ratio=1.0, gap_fired=False,
        )
        assert score < TIER_MEDIUM_THRESHOLD
        assert len(signals) == 0

    def test_all_signals_high_score(self):
        """All signals fired produces high deviation score with multi-signal bonus."""
        score, signals = compute_deviation_score(
            speed_z=5.0, speed_fired=True,
            port_novelty=1.0, port_fired=True,
            route_deviation=1.0, route_fired=True,
            gap_ratio=GAP_FREQUENCY_CAP, gap_fired=True,
        )
        assert score >= TIER_HIGH_THRESHOLD
        assert "multi_signal_bonus" in signals
        assert len(signals) == 5  # 4 signals + bonus

    def test_multi_signal_bonus_threshold(self):
        """Multi-signal bonus requires exactly MULTI_SIGNAL_MIN_COUNT signals."""
        # 2 signals: no bonus
        _, signals_2 = compute_deviation_score(
            speed_z=5.0, speed_fired=True,
            port_novelty=1.0, port_fired=True,
            route_deviation=0.0, route_fired=False,
            gap_ratio=1.0, gap_fired=False,
        )
        assert "multi_signal_bonus" not in signals_2

        # 3 signals: bonus
        _, signals_3 = compute_deviation_score(
            speed_z=5.0, speed_fired=True,
            port_novelty=1.0, port_fired=True,
            route_deviation=1.0, route_fired=True,
            gap_ratio=1.0, gap_fired=False,
        )
        assert "multi_signal_bonus" in signals_3

    def test_score_clamped_to_0_1(self):
        """Deviation score is always clamped to [0, 1]."""
        score, _ = compute_deviation_score(
            speed_z=100.0, speed_fired=True,
            port_novelty=1.0, port_fired=True,
            route_deviation=1.0, route_fired=True,
            gap_ratio=GAP_FREQUENCY_CAP, gap_fired=True,
        )
        assert 0.0 <= score <= 1.0


# ── Confidence tier tests ───────────────────────────────────────────────────


class TestConfidenceTiers:
    """Tests for z-score thresholds by data density."""

    def test_sparse_data_high_threshold(self):
        """< 50 points requires z > 3.5."""
        assert _z_score_threshold(30) == 3.5

    def test_moderate_data(self):
        """50-200 points requires z > 3.0."""
        assert _z_score_threshold(100) == 3.0

    def test_good_data(self):
        """200-500 points requires z > 2.5."""
        assert _z_score_threshold(300) == 2.5

    def test_dense_data(self):
        """500+ points requires z > 2.0."""
        assert _z_score_threshold(1000) == 2.0

    def test_boundary_50(self):
        """Exactly 50 points uses the 50-200 threshold."""
        assert _z_score_threshold(50) == 3.0

    def test_boundary_200(self):
        """Exactly 200 points uses the 200-500 threshold."""
        assert _z_score_threshold(200) == 2.5

    def test_boundary_500(self):
        """Exactly 500 points uses the 500+ threshold."""
        assert _z_score_threshold(500) == 2.0


# ── Speed deviation tests ───────────────────────────────────────────────────


class TestSpeedDeviation:
    """Tests for speed z-score deviation."""

    def test_no_deviation(self):
        """Same baseline and current produce z=0."""
        z, fired = compute_speed_deviation(
            {"median_sog": 12.0, "iqr_sog": 2.0},
            {"median_sog": 12.0, "iqr_sog": 2.0},
            data_point_count=100,
        )
        assert z == 0.0
        assert fired is False

    def test_large_deviation_fires(self):
        """Large speed change fires the signal."""
        z, fired = compute_speed_deviation(
            {"median_sog": 12.0, "iqr_sog": 2.0},
            {"median_sog": 22.0, "iqr_sog": 2.0},
            data_point_count=100,
        )
        assert z == 5.0  # (22 - 12) / 2 = 5
        assert fired is True

    def test_zero_iqr_uses_epsilon(self):
        """Zero IQR falls back to epsilon=0.1."""
        z, fired = compute_speed_deviation(
            {"median_sog": 10.0, "iqr_sog": 0.0},
            {"median_sog": 10.5, "iqr_sog": 0.0},
            data_point_count=100,
        )
        assert z == 5.0  # 0.5 / 0.1 = 5.0
        assert fired is True


# ── Port novelty tests ──────────────────────────────────────────────────────


class TestPortNovelty:
    """Tests for port novelty fraction."""

    def test_no_current_ports(self):
        """No current ports returns 0 novelty, no signal."""
        frac, fired = compute_port_novelty([1, 2, 3], [])
        assert frac == 0.0
        assert fired is False

    def test_all_known_ports(self):
        """All current ports in baseline: 0 novelty."""
        frac, fired = compute_port_novelty([1, 2, 3], [1, 2])
        assert frac == 0.0
        assert fired is False

    def test_all_new_ports(self):
        """All current ports are new: novelty = 1.0."""
        frac, fired = compute_port_novelty([1, 2], [5, 6])
        assert frac == 1.0
        assert fired is True

    def test_threshold_boundary(self):
        """Exactly at threshold (0.3) fires the signal."""
        # 3 current ports, 1 new: 1/3 = 0.333 >= 0.3
        frac, fired = compute_port_novelty([1, 2], [1, 2, 5])
        assert frac == pytest.approx(0.3333, abs=0.01)
        assert fired is True


# ── Route deviation tests ───────────────────────────────────────────────────


class TestRouteDeviation:
    """Tests for route deviation fraction."""

    def test_no_current_corridors(self):
        """No current corridors: 0 deviation."""
        frac, fired = compute_route_deviation([1, 2, 3], [])
        assert frac == 0.0
        assert fired is False

    def test_all_in_baseline(self):
        """All current corridors in baseline: 0 deviation."""
        frac, fired = compute_route_deviation([1, 2, 3], [1, 2])
        assert frac == 0.0
        assert fired is False

    def test_all_novel(self):
        """All corridors novel: 1.0 deviation, fires signal."""
        frac, fired = compute_route_deviation([1, 2], [5, 6])
        assert frac == 1.0
        assert fired is True


# ── Tier scoring tests ──────────────────────────────────────────────────────


class TestTierScoring:
    """Tests for tier mapping from deviation score."""

    def test_high_tier(self):
        tier, score = _score_to_tier(0.8)
        assert tier == "high"
        assert score == 30.0

    def test_medium_tier(self):
        tier, score = _score_to_tier(0.5)
        assert tier == "medium"
        assert score == 18.0

    def test_low_tier(self):
        tier, score = _score_to_tier(0.2)
        assert tier == "low"
        assert score == 8.0

    def test_boundary_high(self):
        tier, _ = _score_to_tier(TIER_HIGH_THRESHOLD)
        assert tier == "high"

    def test_boundary_medium(self):
        tier, _ = _score_to_tier(TIER_MEDIUM_THRESHOLD)
        assert tier == "medium"


# ── Integration tests ────────────────────────────────────────────────────────


class TestIntegration:
    """Integration tests for run_behavioral_baseline."""

    @patch("app.modules.behavioral_baseline_detector.settings")
    def test_disabled_flag_returns_empty(self, mock_settings):
        """Detection disabled returns empty stats."""
        mock_settings.BEHAVIORAL_BASELINE_ENABLED = False
        # getattr fallback
        delattr(mock_settings, "BEHAVIORAL_BASELINE_ENABLED")
        db = MagicMock()
        result = run_behavioral_baseline(db)
        assert result["vessels_processed"] == 0
        assert result["profiles_created"] == 0

    @patch("app.modules.behavioral_baseline_detector.settings")
    def test_enabled_no_vessels(self, mock_settings):
        """Enabled with no vessels returns empty stats."""
        mock_settings.BEHAVIORAL_BASELINE_ENABLED = True
        db = MagicMock()
        db.query.return_value.all.return_value = []
        result = run_behavioral_baseline(db)
        assert result["vessels_processed"] == 0

    @patch("app.modules.behavioral_baseline_detector.settings")
    @patch("app.modules.behavioral_baseline_detector.build_vessel_profile")
    def test_insufficient_data_counted(self, mock_build, mock_settings):
        """Vessels with insufficient data are counted in skipped."""
        mock_settings.BEHAVIORAL_BASELINE_ENABLED = True
        mock_build.return_value = None

        db = MagicMock()
        db.query.return_value.all.return_value = [(1,), (2,)]
        # No existing profile
        db.query.return_value.filter.return_value.first.return_value = None

        result = run_behavioral_baseline(db)
        assert result["skipped_insufficient_data"] == 2

    @patch("app.modules.behavioral_baseline_detector.settings")
    @patch("app.modules.behavioral_baseline_detector.build_vessel_profile")
    @patch("app.modules.behavioral_baseline_detector._persist_profile")
    def test_profiles_created(self, mock_persist, mock_build, mock_settings):
        """Successful profiles are counted as created."""
        mock_settings.BEHAVIORAL_BASELINE_ENABLED = True
        mock_build.return_value = {
            "vessel_id": 1,
            "baseline_start": datetime(2025, 10, 1),
            "baseline_end": datetime(2026, 1, 1),
            "speed_stats": {"median_sog": 12.0},
            "port_pattern": {"visited_ports": []},
            "route_pattern": {"top_corridors": []},
            "gap_pattern": {"frequency": 0},
            "temporal_pattern": {"buckets_6h": [0, 0, 0, 0]},
            "deviation_score": 0.3,
            "deviation_signals": [],
            "risk_score_component": 8.0,
            "tier": "low",
        }

        db = MagicMock()
        db.query.return_value.all.return_value = [(1,)]
        db.query.return_value.filter.return_value.first.return_value = None  # no existing

        result = run_behavioral_baseline(db)
        assert result["profiles_created"] == 1
        assert result["vessels_processed"] == 1


# ── Edge case tests ──────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for behavioral baseline profiling."""

    @patch("app.modules.behavioral_baseline_detector._fetch_position_data")
    def test_new_vessel_no_history(self, mock_fetch):
        """New vessel with no position data returns None."""
        mock_fetch.return_value = []
        db = MagicMock()
        result = build_vessel_profile(db, vessel_id=999)
        assert result is None

    @patch("app.modules.behavioral_baseline_detector._fetch_position_data")
    def test_sparse_data_returns_none(self, mock_fetch):
        """Vessel with < 10 baseline points returns None."""
        mock_fetch.return_value = [_make_ais_point() for _ in range(5)]
        db = MagicMock()
        result = build_vessel_profile(db, vessel_id=1)
        assert result is None

    def test_temporal_pattern_buckets(self):
        """Temporal pattern distributes timestamps into 6h buckets."""
        timestamps = [
            datetime(2026, 1, 1, 3, 0, 0),   # bucket 0 (0-6h)
            datetime(2026, 1, 1, 9, 0, 0),   # bucket 1 (6-12h)
            datetime(2026, 1, 1, 15, 0, 0),  # bucket 2 (12-18h)
            datetime(2026, 1, 1, 21, 0, 0),  # bucket 3 (18-24h)
            datetime(2026, 1, 1, 1, 0, 0),   # bucket 0
        ]
        pattern = compute_temporal_pattern(timestamps)
        assert pattern["buckets_6h"] == [2, 1, 1, 1]

    def test_gap_pattern_single_gap(self):
        """Single gap event: frequency=1, mean=max=duration."""
        pattern = compute_gap_pattern([240.0])
        assert pattern["frequency"] == 1
        assert pattern["mean_duration"] == 240.0
        assert pattern["max_duration"] == 240.0


# ── API endpoint tests ───────────────────────────────────────────────────────


class TestAPIEndpoints:
    """Tests for behavioral baseline API endpoints."""

    def test_run_endpoint_disabled(self):
        """POST /detect/behavioral-baseline returns 404 when disabled."""
        from fastapi.testclient import TestClient
        from app.api.routes_behavioral_baseline import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_behavioral_baseline.settings") as mock_settings:
            # Simulate BEHAVIORAL_BASELINE_ENABLED not set (getattr returns False)
            delattr(mock_settings, "BEHAVIORAL_BASELINE_ENABLED")
            response = client.post("/detect/behavioral-baseline")
            assert response.status_code == 404

    def test_get_endpoint_disabled(self):
        """GET /detect/behavioral-baseline/{id} returns 404 when disabled."""
        from fastapi.testclient import TestClient
        from app.api.routes_behavioral_baseline import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_behavioral_baseline.settings") as mock_settings:
            delattr(mock_settings, "BEHAVIORAL_BASELINE_ENABLED")
            response = client.get("/detect/behavioral-baseline/1")
            assert response.status_code == 404

    def test_refresh_endpoint_disabled(self):
        """POST /detect/behavioral-baseline/{id}/refresh returns 404 when disabled."""
        from fastapi.testclient import TestClient
        from app.api.routes_behavioral_baseline import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_behavioral_baseline.settings") as mock_settings:
            delattr(mock_settings, "BEHAVIORAL_BASELINE_ENABLED")
            response = client.post("/detect/behavioral-baseline/1/refresh")
            assert response.status_code == 404

    def test_run_endpoint_enabled(self):
        """POST /detect/behavioral-baseline runs detection when enabled."""
        from fastapi.testclient import TestClient
        from app.api.routes_behavioral_baseline import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("app.api.routes_behavioral_baseline.settings") as mock_settings, \
             patch("app.modules.behavioral_baseline_detector.run_behavioral_baseline") as mock_run:
            mock_settings.BEHAVIORAL_BASELINE_ENABLED = True
            mock_run.return_value = {
                "vessels_processed": 0,
                "profiles_created": 0,
                "profiles_updated": 0,
                "skipped_insufficient_data": 0,
                "errors": [],
            }
            response = client.post("/detect/behavioral-baseline")
            assert response.status_code == 200
            assert response.json()["vessels_processed"] == 0


# ── Get vessel profile tests ────────────────────────────────────────────────


class TestGetVesselProfile:
    """Tests for get_vessel_profile."""

    def test_returns_none_when_not_found(self):
        """Returns None when no profile record exists."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = get_vessel_profile(db, vessel_id=1)
        assert result is None

    def test_returns_profile_dict(self):
        """Returns formatted dict when profile exists."""
        import json

        profile = MagicMock()
        profile.profile_id = 1
        profile.vessel_id = 100
        profile.baseline_start = datetime(2025, 10, 1)
        profile.baseline_end = datetime(2026, 1, 1)
        profile.speed_stats_json = json.dumps({"median_sog": 12.0})
        profile.port_pattern_json = json.dumps({"visited_ports": [1, 2]})
        profile.route_pattern_json = json.dumps({"top_corridors": []})
        profile.gap_pattern_json = json.dumps({"frequency": 3})
        profile.temporal_pattern_json = json.dumps({"buckets_6h": [10, 20, 15, 5]})
        profile.deviation_score = 0.45
        profile.deviation_signals_json = json.dumps(["speed_z_score"])
        profile.risk_score_component = 18.0
        profile.tier = "medium"
        profile.created_at = datetime(2026, 3, 14)
        profile.updated_at = datetime(2026, 3, 14)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = profile
        result = get_vessel_profile(db, vessel_id=100)
        assert result is not None
        assert result["vessel_id"] == 100
        assert result["tier"] == "medium"
        assert result["speed_stats"]["median_sog"] == 12.0


# ── Refresh profile tests ───────────────────────────────────────────────────


class TestRefreshProfile:
    """Tests for refresh_vessel_profile."""

    @patch("app.modules.behavioral_baseline_detector.settings")
    def test_disabled_returns_none(self, mock_settings):
        """Refresh returns None when feature is disabled."""
        delattr(mock_settings, "BEHAVIORAL_BASELINE_ENABLED")
        db = MagicMock()
        result = refresh_vessel_profile(db, vessel_id=1)
        assert result is None

    @patch("app.modules.behavioral_baseline_detector.settings")
    @patch("app.modules.behavioral_baseline_detector.build_vessel_profile")
    def test_insufficient_data_returns_none(self, mock_build, mock_settings):
        """Refresh returns None when vessel has insufficient data."""
        mock_settings.BEHAVIORAL_BASELINE_ENABLED = True
        mock_build.return_value = None
        db = MagicMock()
        result = refresh_vessel_profile(db, vessel_id=999)
        assert result is None
