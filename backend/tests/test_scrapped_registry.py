"""Tests for scrapped vessel registry and track replay detection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.modules.scrapped_registry import (
    _compute_track_correlation,
    _load_scrapped_registry,
    reload_scrapped_registry,
)

# ── Tests: _compute_track_correlation ────────────────────────────────


class TestComputeTrackCorrelation:
    def test_identical_tracks(self):
        points = [(25.0 + i * 0.01, 55.0 + i * 0.01, i * 3600.0) for i in range(12)]
        correlation = _compute_track_correlation(points, points)
        assert correlation > 0.95

    def test_completely_different_tracks(self):
        recent = [(25.0, 55.0, i * 3600.0) for i in range(12)]
        historical = [(40.0, 10.0, i * 3600.0) for i in range(12)]
        correlation = _compute_track_correlation(recent, historical)
        assert correlation < 0.1

    def test_empty_tracks(self):
        assert _compute_track_correlation([], []) == 0.0

    def test_empty_recent(self):
        historical = [(25.0, 55.0, 0.0)]
        assert _compute_track_correlation([], historical) == 0.0

    def test_empty_historical(self):
        recent = [(25.0, 55.0, 0.0)]
        assert _compute_track_correlation(recent, []) == 0.0

    def test_too_few_common_hours(self):
        recent = [(25.0, 55.0, 0.0), (25.1, 55.1, 3600.0)]
        historical = [(25.0, 55.0, 7200.0), (25.1, 55.1, 10800.0)]
        # Different hours — not enough in common
        correlation = _compute_track_correlation(recent, historical)
        assert correlation == 0.0

    def test_correlation_range(self):
        recent = [(25.0 + i * 0.005, 55.0, i * 3600.0) for i in range(12)]
        historical = [(25.0 + i * 0.005 + 0.002, 55.0, i * 3600.0) for i in range(12)]
        correlation = _compute_track_correlation(recent, historical)
        assert 0.0 <= correlation <= 1.0


# ── Tests: _load_scrapped_registry ───────────────────────────────────


class TestLoadScrappedRegistry:
    def test_loads_from_yaml(self):
        import app.modules.scrapped_registry as sr

        sr._SCRAPPED_REGISTRY = None  # Force reload

        yaml_content = {
            "scrapped_imos": [
                {
                    "imo": "1234567",
                    "name": "OLD TANKER",
                    "scrapped_year": 2020,
                    "notes": "demolished",
                },
                {"imo": "7654321", "name": "RUSTY SHIP", "scrapped_year": 2019},
            ]
        }

        with patch("app.modules.scrapped_registry.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = True
            MockPath.return_value = mock_path

            with (
                patch("builtins.open", MagicMock()),
                patch("app.modules.scrapped_registry.yaml.safe_load", return_value=yaml_content),
            ):
                registry = _load_scrapped_registry()
                assert "1234567" in registry
                assert "7654321" in registry
                assert registry["1234567"]["name"] == "OLD TANKER"

        sr._SCRAPPED_REGISTRY = None

    def test_missing_file_returns_empty(self):
        import app.modules.scrapped_registry as sr

        sr._SCRAPPED_REGISTRY = None

        with patch("app.modules.scrapped_registry.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            MockPath.return_value = mock_path

            registry = _load_scrapped_registry()
            assert registry == {}

        sr._SCRAPPED_REGISTRY = None

    def test_caches_result(self):
        import app.modules.scrapped_registry as sr

        sr._SCRAPPED_REGISTRY = {"cached": {"name": "test"}}

        result = _load_scrapped_registry()
        assert "cached" in result

        sr._SCRAPPED_REGISTRY = None


class TestReloadScrappedRegistry:
    def test_clears_cache(self):
        import app.modules.scrapped_registry as sr

        sr._SCRAPPED_REGISTRY = {"old": {"name": "old"}}

        with patch("app.modules.scrapped_registry.Path") as MockPath:
            mock_path = MagicMock()
            mock_path.exists.return_value = False
            MockPath.return_value = mock_path

            result = reload_scrapped_registry()
            assert result == {}

        sr._SCRAPPED_REGISTRY = None


# ── Tests: detect_scrapped_imo_reuse ─────────────────────────────────


class TestDetectScrappedImoReuse:
    def test_disabled_returns_status(self):
        from app.modules.scrapped_registry import detect_scrapped_imo_reuse

        db = MagicMock()
        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = False
            result = detect_scrapped_imo_reuse(db)
            assert result["status"] == "disabled"

    def test_no_matches(self):
        import app.modules.scrapped_registry as sr
        from app.modules.scrapped_registry import detect_scrapped_imo_reuse

        sr._SCRAPPED_REGISTRY = {
            "9999999": {"name": "DEAD SHIP", "scrapped_year": 2020, "notes": ""}
        }

        db = MagicMock()
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1111111"

        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            db.query.return_value.filter.return_value.all.return_value = [vessel]
            result = detect_scrapped_imo_reuse(db)
            assert result["matches"] == 0

        sr._SCRAPPED_REGISTRY = None

    def test_detects_match(self):
        import app.modules.scrapped_registry as sr
        from app.modules.scrapped_registry import detect_scrapped_imo_reuse

        sr._SCRAPPED_REGISTRY = {
            "1234567": {"name": "DEAD SHIP", "scrapped_year": 2020, "notes": ""}
        }

        db = MagicMock()
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"

        # No existing anomaly
        db.query.return_value.filter.return_value.all.return_value = [vessel]

        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True

            # Override query for specific models
            def query_side_effect(model):
                q = MagicMock()
                model_name = model.__name__ if hasattr(model, "__name__") else str(model)
                if model_name == "Vessel":
                    q.filter.return_value.all.return_value = [vessel]
                elif model_name == "SpoofingAnomaly":
                    q.filter.return_value.all.return_value = []
                return q

            db.query.side_effect = query_side_effect

            result = detect_scrapped_imo_reuse(db)
            assert result["matches"] == 1
            assert result["anomalies_created"] == 1
            assert db.add.called

        sr._SCRAPPED_REGISTRY = None

    def test_strips_imo_prefix(self):
        import app.modules.scrapped_registry as sr
        from app.modules.scrapped_registry import detect_scrapped_imo_reuse

        sr._SCRAPPED_REGISTRY = {
            "1234567": {"name": "DEAD SHIP", "scrapped_year": 2020, "notes": ""}
        }

        db = MagicMock()
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "IMO1234567"

        def query_side_effect(model):
            q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "Vessel":
                q.filter.return_value.all.return_value = [vessel]
            elif model_name == "SpoofingAnomaly":
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = query_side_effect

        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            result = detect_scrapped_imo_reuse(db)
            assert result["matches"] == 1

        sr._SCRAPPED_REGISTRY = None

    def test_skips_already_flagged(self):
        import app.modules.scrapped_registry as sr
        from app.modules.scrapped_registry import detect_scrapped_imo_reuse

        sr._SCRAPPED_REGISTRY = {
            "1234567": {"name": "DEAD SHIP", "scrapped_year": 2020, "notes": ""}
        }

        db = MagicMock()
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"

        existing_anomaly = MagicMock()
        existing_anomaly.evidence_json = {"subtype": "scrapped_imo"}

        def query_side_effect(model):
            q = MagicMock()
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if model_name == "Vessel":
                q.filter.return_value.all.return_value = [vessel]
            elif model_name == "SpoofingAnomaly":
                q.filter.return_value.all.return_value = [existing_anomaly]
            return q

        db.query.side_effect = query_side_effect

        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            result = detect_scrapped_imo_reuse(db)
            assert result["matches"] == 1
            assert result["anomalies_created"] == 0

        sr._SCRAPPED_REGISTRY = None

    def test_empty_registry(self):
        import app.modules.scrapped_registry as sr
        from app.modules.scrapped_registry import detect_scrapped_imo_reuse

        sr._SCRAPPED_REGISTRY = {}

        db = MagicMock()
        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.SCRAPPED_REGISTRY_DETECTION_ENABLED = True
            result = detect_scrapped_imo_reuse(db)
            assert result["matches"] == 0

        sr._SCRAPPED_REGISTRY = None


# ── Tests: detect_track_replay ───────────────────────────────────────


class TestDetectTrackReplay:
    def test_disabled_returns_status(self):
        from app.modules.scrapped_registry import detect_track_replay

        db = MagicMock()
        with patch("app.modules.scrapped_registry.settings") as mock_settings:
            mock_settings.TRACK_REPLAY_DETECTION_ENABLED = False
            result = detect_track_replay(db)
            assert result["status"] == "disabled"
