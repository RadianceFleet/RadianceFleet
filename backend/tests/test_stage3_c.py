"""Tests for Stage 3-C: Scrapped vessel registry + historical track replay.

Covers:
  - Scrapped IMO detection (disabled flag, match, no match, YAML parse)
  - Track replay detection (disabled flag, high correlation, low correlation, insufficient data)
  - Pipeline wiring (steps present, gated by flags)
  - Integration (enums, feature flags, YAML sections, _EXPECTED_SECTIONS)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, mock_open

import pytest


# ── Scrapped IMO detection tests ────────────────────────────────────────────

class TestScrappedImoDisabled:
    """When SCRAPPED_REGISTRY_DETECTION_ENABLED is False, detection returns early."""

    def test_disabled_flag_returns_early(self):
        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = False
            from app.modules.scrapped_registry import detect_scrapped_imo_reuse

            db = MagicMock()
            result = detect_scrapped_imo_reuse(db)
            assert result["status"] == "disabled"
            db.query.assert_not_called()


class TestScrappedImoDetection:
    """Scrapped IMO matched and unmatched vessel scenarios."""

    def test_vessel_with_scrapped_imo_detected(self):
        """A vessel whose IMO matches the scrapped registry gets an anomaly."""
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.imo = "9123456"

        registry = {
            "9123456": {
                "name": "EXAMPLE SCRAPPED TANKER",
                "scrapped_year": 2020,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            mock_q = MagicMock()
            if call_count[0] == 1:
                # query(Vessel).filter(...).all() -> vessels list
                mock_q.filter.return_value.all.return_value = [mock_vessel]
            elif call_count[0] == 2:
                # query(SpoofingAnomaly).filter(...).all() -> no existing anomalies
                mock_q.filter.return_value.all.return_value = []
            return mock_q

        mock_db.query.side_effect = query_side_effect

        with patch("app.modules.scrapped_registry.settings") as mock_settings, \
             patch("app.modules.scrapped_registry._load_scrapped_registry", return_value=registry):
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_scrapped_imo_reuse

            result = detect_scrapped_imo_reuse(mock_db)
            assert result["status"] == "ok"
            assert result["anomalies_created"] == 1
            assert result["matches"] == 1
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    def test_vessel_with_clean_imo_not_flagged(self):
        """A vessel with an IMO not in the scrapped registry is not flagged."""
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 2
        mock_vessel.imo = "9999999"

        registry = {
            "9123456": {
                "name": "EXAMPLE SCRAPPED TANKER",
                "scrapped_year": 2020,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_vessel]

        with patch("app.modules.scrapped_registry.settings") as mock_settings, \
             patch("app.modules.scrapped_registry._load_scrapped_registry", return_value=registry):
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_scrapped_imo_reuse

            result = detect_scrapped_imo_reuse(mock_db)
            assert result["status"] == "ok"
            assert result["anomalies_created"] == 0
            assert result["matches"] == 0
            mock_db.add.assert_not_called()

    def test_imo_prefix_stripped(self):
        """IMO prefix 'IMO' is stripped before matching."""
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 3
        mock_vessel.imo = "IMO9123456"

        registry = {
            "9123456": {
                "name": "EXAMPLE SCRAPPED TANKER",
                "scrapped_year": 2020,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            mock_q = MagicMock()
            if call_count[0] == 1:
                mock_q.filter.return_value.all.return_value = [mock_vessel]
            elif call_count[0] == 2:
                mock_q.filter.return_value.all.return_value = []
            return mock_q

        mock_db.query.side_effect = query_side_effect

        with patch("app.modules.scrapped_registry.settings") as mock_settings, \
             patch("app.modules.scrapped_registry._load_scrapped_registry", return_value=registry):
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_scrapped_imo_reuse

            result = detect_scrapped_imo_reuse(mock_db)
            assert result["matches"] == 1
            assert result["anomalies_created"] == 1

    def test_already_flagged_vessel_skipped(self):
        """Vessel already flagged with scrapped_imo subtype is not re-flagged."""
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 4
        mock_vessel.imo = "9123456"

        existing_anomaly = MagicMock()
        existing_anomaly.evidence_json = {"subtype": "scrapped_imo"}

        registry = {
            "9123456": {
                "name": "EXAMPLE SCRAPPED TANKER",
                "scrapped_year": 2020,
                "notes": "Broken at Alang",
            }
        }

        mock_db = MagicMock()
        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            mock_q = MagicMock()
            if call_count[0] == 1:
                mock_q.filter.return_value.all.return_value = [mock_vessel]
            elif call_count[0] == 2:
                mock_q.filter.return_value.all.return_value = [existing_anomaly]
            return mock_q

        mock_db.query.side_effect = query_side_effect

        with patch("app.modules.scrapped_registry.settings") as mock_settings, \
             patch("app.modules.scrapped_registry._load_scrapped_registry", return_value=registry):
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_scrapped_imo_reuse

            result = detect_scrapped_imo_reuse(mock_db)
            assert result["matches"] == 1
            assert result["anomalies_created"] == 0
            mock_db.add.assert_not_called()


class TestScrappedYamlConfig:
    """YAML config parsing."""

    def test_yaml_config_parses_correctly(self):
        """_load_scrapped_registry parses all fields from YAML."""
        import app.modules.scrapped_registry as mod
        # Reset cache
        mod._SCRAPPED_REGISTRY = None

        yaml_content = (
            'last_updated: "2026-03-01"\n'
            'scrapped_imos:\n'
            '  - imo: "9111111"\n'
            '    name: "TEST VESSEL"\n'
            '    scrapped_year: 2022\n'
            '    notes: "Test note"\n'
        )

        with patch("builtins.open", mock_open(read_data=yaml_content)), \
             patch("pathlib.Path.exists", return_value=True):
            result = mod._load_scrapped_registry()

        assert "9111111" in result
        assert result["9111111"]["name"] == "TEST VESSEL"
        assert result["9111111"]["scrapped_year"] == 2022
        assert result["9111111"]["notes"] == "Test note"

        # Clean up
        mod._SCRAPPED_REGISTRY = None

    def test_missing_yaml_returns_empty(self):
        """Missing YAML file returns empty registry."""
        import app.modules.scrapped_registry as mod
        mod._SCRAPPED_REGISTRY = None

        with patch("pathlib.Path.exists", return_value=False):
            result = mod._load_scrapped_registry()

        assert result == {}
        mod._SCRAPPED_REGISTRY = None

    def test_empty_registry_no_queries(self):
        """Empty registry skips vessel querying entirely."""
        with patch("app.modules.scrapped_registry.settings") as mock_settings, \
             patch("app.modules.scrapped_registry._load_scrapped_registry", return_value={}):
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            from app.modules.scrapped_registry import detect_scrapped_imo_reuse

            db = MagicMock()
            result = detect_scrapped_imo_reuse(db)
            assert result["status"] == "ok"
            assert result["matches"] == 0
            db.query.assert_not_called()


# ── Track replay detection tests ────────────────────────────────────────────

class TestTrackReplayDisabled:
    """When TRACK_REPLAY_DETECTION_ENABLED is False, detection returns early."""

    def test_disabled_flag_returns_early(self):
        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.TRACK_REPLAY_DETECTION_ENABLED = False
            from app.modules.scrapped_registry import detect_track_replay

            db = MagicMock()
            result = detect_track_replay(db)
            assert result["status"] == "disabled"
            db.query.assert_not_called()


class TestTrackReplayDetection:
    """Track replay correlation detection scenarios."""

    def test_high_correlation_creates_anomaly(self):
        """Correlation > 0.9 creates TRACK_REPLAY anomaly."""
        now = datetime.now(timezone.utc)
        recent_points = [
            (50.0 + i * 0.001, 10.0 + i * 0.001, now - timedelta(hours=i))
            for i in range(210)
        ]
        historical_points = [
            (50.0 + i * 0.001, 10.0 + i * 0.001, now - timedelta(days=60, hours=i))
            for i in range(210)
        ]

        with patch("app.modules.scrapped_registry.settings") as mock_settings, \
             patch("app.modules.scrapped_registry._compute_track_correlation", return_value=0.95):
            mock_settings.TRACK_REPLAY_DETECTION_ENABLED = True

            from app.modules.scrapped_registry import detect_track_replay

            mock_db = MagicMock()
            call_count = [0]

            def query_side_effect(*args):
                call_count[0] += 1
                mock_q = MagicMock()
                if call_count[0] == 1:
                    # vessel_ids_with_data: query().filter().group_by().having().all()
                    mock_q.filter.return_value.group_by.return_value.having.return_value.all.return_value = [(1,)]
                elif call_count[0] == 2:
                    # avg_sog: query().filter().scalar()
                    mock_q.filter.return_value.scalar.return_value = 5.0
                elif call_count[0] == 3:
                    # Recent points: query().filter().order_by().all()
                    mock_q.filter.return_value.order_by.return_value.all.return_value = recent_points
                elif call_count[0] == 4:
                    # Historical points: query().filter().order_by().all()
                    mock_q.filter.return_value.order_by.return_value.all.return_value = historical_points
                elif call_count[0] == 5:
                    # Existing anomaly check: query().filter().first()
                    mock_q.filter.return_value.first.return_value = None
                return mock_q

            mock_db.query.side_effect = query_side_effect

            result = detect_track_replay(mock_db)
            assert result["status"] == "ok"
            assert result["anomalies_created"] == 1
            mock_db.add.assert_called_once()
            mock_db.commit.assert_called_once()

    def test_low_correlation_no_anomaly(self):
        """Correlation <= 0.9 does not create anomaly."""
        now = datetime.now(timezone.utc)
        recent_points = [
            (50.0 + i * 0.001, 10.0 + i * 0.001, now - timedelta(hours=i))
            for i in range(210)
        ]
        historical_points = [
            (55.0 + i * 0.002, 15.0 + i * 0.002, now - timedelta(days=60, hours=i))
            for i in range(210)
        ]

        with patch("app.modules.scrapped_registry.settings") as mock_settings, \
             patch("app.modules.scrapped_registry._compute_track_correlation", return_value=0.3):
            mock_settings.TRACK_REPLAY_DETECTION_ENABLED = True

            from app.modules.scrapped_registry import detect_track_replay

            mock_db = MagicMock()
            call_count = [0]

            def query_side_effect(*args):
                call_count[0] += 1
                mock_q = MagicMock()
                if call_count[0] == 1:
                    mock_q.filter.return_value.group_by.return_value.having.return_value.all.return_value = [(1,)]
                elif call_count[0] == 2:
                    mock_q.filter.return_value.scalar.return_value = 5.0
                elif call_count[0] == 3:
                    mock_q.filter.return_value.order_by.return_value.all.return_value = recent_points
                elif call_count[0] == 4:
                    mock_q.filter.return_value.order_by.return_value.all.return_value = historical_points
                return mock_q

            mock_db.query.side_effect = query_side_effect

            result = detect_track_replay(mock_db)
            assert result["status"] == "ok"
            assert result["anomalies_created"] == 0
            mock_db.add.assert_not_called()

    def test_insufficient_data_skipped(self):
        """Vessels with < 200 points are skipped."""
        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.TRACK_REPLAY_DETECTION_ENABLED = True

            from app.modules.scrapped_registry import detect_track_replay

            mock_db = MagicMock()

            # No vessels meet the 200-point threshold
            mock_db.query.return_value.filter.return_value.group_by.return_value.having.return_value.all.return_value = []

            result = detect_track_replay(mock_db)
            assert result["status"] == "ok"
            assert result["vessels_checked"] == 0
            assert result["anomalies_created"] == 0

    def test_anchored_vessel_skipped(self):
        """Vessels with avg SOG < 0.5 are skipped (anchored)."""
        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.TRACK_REPLAY_DETECTION_ENABLED = True

            from app.modules.scrapped_registry import detect_track_replay

            mock_db = MagicMock()
            call_count = [0]

            def query_side_effect(*args):
                call_count[0] += 1
                mock_q = MagicMock()
                if call_count[0] == 1:
                    mock_q.filter.return_value.group_by.return_value.having.return_value.all.return_value = [(1,)]
                elif call_count[0] == 2:
                    # Low avg SOG -> anchored
                    mock_q.filter.return_value.scalar.return_value = 0.2
                return mock_q

            mock_db.query.side_effect = query_side_effect

            result = detect_track_replay(mock_db)
            assert result["status"] == "ok"
            assert result["vessels_checked"] == 0
            assert result["anomalies_created"] == 0


class TestTrackCorrelation:
    """Unit tests for _compute_track_correlation."""

    def test_identical_tracks_high_correlation(self):
        from app.modules.scrapped_registry import _compute_track_correlation

        # Same positions at same times of day
        points = [(50.0, 10.0, h * 3600.0) for h in range(24)]
        corr = _compute_track_correlation(points, points)
        assert corr > 0.9

    def test_different_tracks_low_correlation(self):
        from app.modules.scrapped_registry import _compute_track_correlation

        recent = [(50.0, 10.0, h * 3600.0) for h in range(24)]
        historical = [(60.0, 20.0, h * 3600.0) for h in range(24)]
        corr = _compute_track_correlation(recent, historical)
        assert corr < 0.5

    def test_empty_tracks_zero_correlation(self):
        from app.modules.scrapped_registry import _compute_track_correlation

        assert _compute_track_correlation([], []) == 0.0
        assert _compute_track_correlation([(50.0, 10.0, 0.0)], []) == 0.0

    def test_too_few_common_hours(self):
        from app.modules.scrapped_registry import _compute_track_correlation

        # Only 3 common hours (< 6 required)
        recent = [(50.0, 10.0, h * 3600.0) for h in range(3)]
        historical = [(50.0, 10.0, h * 3600.0) for h in range(3)]
        corr = _compute_track_correlation(recent, historical)
        assert corr == 0.0


# ── Pipeline wiring tests ───────────────────────────────────────────────────

class TestPipelineWiring:
    """Pipeline steps present and gated by flags."""

    def test_scrapped_registry_step_present_in_source(self):
        """Scrapped registry step is wired into discover_dark_vessels."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        assert "scrapped_registry" in source
        assert "SCRAPPED_REGISTRY_DETECTION_ENABLED" in source

    def test_track_replay_step_present_in_source(self):
        """Track replay step is wired into discover_dark_vessels."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        assert "track_replay" in source
        assert "TRACK_REPLAY_DETECTION_ENABLED" in source

    def test_steps_gated_by_flags(self):
        """Both steps are conditional on their feature flags."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        # Verify gating pattern: if settings.FLAG: ... import ... _run_step
        assert "settings.SCRAPPED_REGISTRY_DETECTION_ENABLED" in source
        assert "settings.TRACK_REPLAY_DETECTION_ENABLED" in source
        assert "detect_scrapped_imo_reuse" in source
        assert "detect_track_replay" in source


# ── Integration tests ────────────────────────────────────────────────────────

class TestIntegration:
    """Integration: enums, feature flags, YAML sections, _EXPECTED_SECTIONS."""

    def test_track_replay_enum_exists(self):
        from app.models.base import SpoofingTypeEnum
        assert hasattr(SpoofingTypeEnum, "TRACK_REPLAY")
        assert SpoofingTypeEnum.TRACK_REPLAY.value == "track_replay"

    def test_imo_fraud_enum_exists(self):
        """IMO_FRAUD enum (used by scrapped detection) exists."""
        from app.models.base import SpoofingTypeEnum
        assert hasattr(SpoofingTypeEnum, "IMO_FRAUD")
        assert SpoofingTypeEnum.IMO_FRAUD.value == "imo_fraud"

    def test_feature_flags_exist(self):
        from app.config import Settings
        s = Settings(
            SCRAPPED_REGISTRY_DETECTION_ENABLED=False,
            SCRAPPED_REGISTRY_SCORING_ENABLED=False,
            TRACK_REPLAY_DETECTION_ENABLED=False,
            TRACK_REPLAY_SCORING_ENABLED=False,
        )
        assert s.SCRAPPED_REGISTRY_DETECTION_ENABLED is False
        assert s.SCRAPPED_REGISTRY_SCORING_ENABLED is False
        assert s.TRACK_REPLAY_DETECTION_ENABLED is False
        assert s.TRACK_REPLAY_SCORING_ENABLED is False

    def test_feature_flags_default_false(self):
        from app.config import Settings
        s = Settings()
        assert s.SCRAPPED_REGISTRY_DETECTION_ENABLED is False
        assert s.SCRAPPED_REGISTRY_SCORING_ENABLED is False
        assert s.TRACK_REPLAY_DETECTION_ENABLED is False
        assert s.TRACK_REPLAY_SCORING_ENABLED is False

    def test_yaml_sections_exist(self):
        import yaml
        from pathlib import Path

        # Find the config directory (may be at project root or relative)
        config_path = Path(__file__).parent.parent.parent / "config" / "risk_scoring.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert "scrapped_registry" in config
        assert config["scrapped_registry"]["scrapped_imo_reuse"] == 50
        assert config["scrapped_registry"]["merge_scrapped_imo_bonus"] == 15
        assert "track_replay" in config
        assert config["track_replay"]["high_correlation_replay"] == 45

    def test_scrapped_vessels_yaml_exists(self):
        """scrapped_vessels.yaml config file exists and is valid."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent.parent / "config" / "scrapped_vessels.yaml"
        assert config_path.exists(), f"scrapped_vessels.yaml not found at {config_path}"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "scrapped_imos" in config
        assert len(config["scrapped_imos"]) >= 1
        # Verify structure
        first = config["scrapped_imos"][0]
        assert "imo" in first
        assert "name" in first
        assert "scrapped_year" in first

    def test_expected_sections_includes_both(self):
        from app.modules.risk_scoring import _EXPECTED_SECTIONS
        assert "scrapped_registry" in _EXPECTED_SECTIONS
        assert "track_replay" in _EXPECTED_SECTIONS

    def test_shadow_exclusion_includes_track_replay(self):
        """track_replay shadow exclusion code exists in risk_scoring module."""
        import inspect
        import app.modules.risk_scoring as rs_mod
        source = inspect.getsource(rs_mod)
        assert 'TRACK_REPLAY_SCORING_ENABLED' in source
        assert '"track_replay"' in source
        assert 'SCRAPPED_REGISTRY_SCORING_ENABLED' in source

    def test_database_enum_migration_includes_track_replay(self):
        """track_replay is in the Postgres enum migration loop."""
        import app.database as db_mod
        import inspect
        source = inspect.getsource(db_mod._run_migrations)
        assert "track_replay" in source
