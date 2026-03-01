"""Tests for Stage E — Operational Tooling & Robustness.

Covers:
- E2: Feed outage anomaly-aware suppression
- E3: CLI evaluate-detector new detector types
- E4: Drift detection warm-up guard
- E4b: Voyage predictor route template dedup
- E5: Ownership graph sanctions propagation scoring
- E6: Default config enables stable detectors
- E7: Feed outage max_outage_ratio / anti-decoy
"""
from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# E2: Feed outage — anomaly-aware suppression
# ---------------------------------------------------------------------------


class TestE2FeedOutageEvasionExclusion:
    """E2: Gaps with SpoofingAnomaly or STS ±6h should NOT be suppressed."""

    def test_has_evasion_signals_function_exists(self):
        """_has_evasion_signals helper exists in feed_outage_detector."""
        from app.modules.feed_outage_detector import _has_evasion_signals
        sig = inspect.signature(_has_evasion_signals)
        params = list(sig.parameters)
        assert "db" in params
        assert "gap" in params

    def test_has_evasion_signals_spoofing_match(self):
        """Gap with a spoofing anomaly within ±6h is evasion-excluded."""
        from app.modules.feed_outage_detector import _has_evasion_signals

        gap = MagicMock()
        gap.vessel_id = 1
        gap.gap_start_utc = datetime(2025, 1, 1, 12, 0)
        gap.gap_end_utc = datetime(2025, 1, 1, 14, 0)

        db = MagicMock()
        # Mock chain: db.query(...).filter(...).count() → 1 (spoofing found)
        db.query.return_value.filter.return_value.count.return_value = 1

        result = _has_evasion_signals(db, gap)
        assert result is True

    def test_has_evasion_signals_no_signals(self):
        """Gap without any evasion signals returns False."""
        from app.modules.feed_outage_detector import _has_evasion_signals

        gap = MagicMock()
        gap.vessel_id = 1
        gap.gap_start_utc = datetime(2025, 1, 1, 12, 0)
        gap.gap_end_utc = datetime(2025, 1, 1, 14, 0)

        db = MagicMock()
        # Both checks return 0
        db.query.return_value.filter.return_value.count.return_value = 0

        result = _has_evasion_signals(db, gap)
        assert result is False

    def test_detect_feed_outages_returns_evasion_excluded_key(self):
        """detect_feed_outages result includes evasion_excluded count."""
        from app.modules.feed_outage_detector import detect_feed_outages

        sig = inspect.signature(detect_feed_outages)
        # The function should accept max_outage_ratio parameter (E7)
        assert "max_outage_ratio" in sig.parameters

        # Call with disabled feature flag to get baseline return shape
        with patch("app.modules.feed_outage_detector.settings") as mock_settings:
            mock_settings.FEED_OUTAGE_DETECTION_ENABLED = False
            result = detect_feed_outages(MagicMock())
            assert "evasion_excluded" in result
            assert "decoy_rejected" in result


# ---------------------------------------------------------------------------
# E3: CLI evaluate-detector — new detector types
# ---------------------------------------------------------------------------


class TestE3EvaluateDetectorTypes:
    """E3: evaluate-detector should support newer detector types."""

    def test_destination_detector_type_exists(self):
        """'destination' is in the evaluate-detector type map."""
        source = inspect.getsource(importlib.import_module("app.cli"))
        assert '"destination"' in source

    def test_sts_chain_detector_type_exists(self):
        """'sts_chain' is in the evaluate-detector type map."""
        source = inspect.getsource(importlib.import_module("app.cli"))
        assert '"sts_chain"' in source

    def test_scrapped_registry_detector_type_exists(self):
        """'scrapped_registry' is in the evaluate-detector type map."""
        source = inspect.getsource(importlib.import_module("app.cli"))
        assert '"scrapped_registry"' in source

    def test_fleet_analyzer_detector_type_exists(self):
        """'fleet_analyzer' is in the evaluate-detector type map."""
        source = inspect.getsource(importlib.import_module("app.cli"))
        assert '"fleet_analyzer"' in source

    def test_convoy_detector_type_exists(self):
        """'convoy' is in the evaluate-detector type map."""
        source = inspect.getsource(importlib.import_module("app.cli"))
        assert '"convoy"' in source

    def test_ownership_graph_detector_type_exists(self):
        """'ownership_graph' is in the evaluate-detector type map."""
        source = inspect.getsource(importlib.import_module("app.cli"))
        assert '"ownership_graph"' in source


# ---------------------------------------------------------------------------
# E4: Drift detection warm-up period
# ---------------------------------------------------------------------------


class TestE4DriftWarmup:
    """E4: Drift detection skipped if PipelineRun count < 3."""

    def test_finalize_pipeline_run_accepts_skip_drift(self):
        """_finalize_pipeline_run accepts skip_drift parameter."""
        from app.modules.dark_vessel_discovery import _finalize_pipeline_run

        sig = inspect.signature(_finalize_pipeline_run)
        assert "skip_drift" in sig.parameters

    def test_discover_dark_vessels_source_has_warmup_guard(self):
        """discover_dark_vessels source references warm_up_period."""
        source = inspect.getsource(
            importlib.import_module("app.modules.dark_vessel_discovery")
        )
        assert "warm_up_period" in source
        assert "run_count < 3" in source


# ---------------------------------------------------------------------------
# E4b: Voyage predictor route template dedup
# ---------------------------------------------------------------------------


class TestE4bRouteTemplateDedup:
    """E4b: build_route_templates should dedup existing templates."""

    def test_find_existing_template_function_exists(self):
        """_find_existing_template helper exists in voyage_predictor."""
        from app.modules.voyage_predictor import _find_existing_template
        sig = inspect.signature(_find_existing_template)
        params = list(sig.parameters)
        assert "db" in params
        assert "vessel_type" in params
        assert "route_ports" in params

    def test_find_existing_template_match(self):
        """_find_existing_template returns existing when match found."""
        from app.modules.voyage_predictor import _find_existing_template

        # Create mock template
        existing_template = MagicMock()
        existing_template.vessel_type = "tanker"
        existing_template.route_ports_json = [1, 2, 3]
        existing_template.frequency = 5

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [existing_template]

        result = _find_existing_template(db, "tanker", [1, 2, 3])
        assert result is existing_template

    def test_find_existing_template_no_match(self):
        """_find_existing_template returns None when no match."""
        from app.modules.voyage_predictor import _find_existing_template

        existing_template = MagicMock()
        existing_template.vessel_type = "tanker"
        existing_template.route_ports_json = [4, 5, 6]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [existing_template]

        result = _find_existing_template(db, "tanker", [1, 2, 3])
        assert result is None

    def test_build_route_templates_returns_templates_updated_key(self):
        """build_route_templates result includes templates_updated."""
        source = inspect.getsource(
            importlib.import_module("app.modules.voyage_predictor")
        )
        assert "templates_updated" in source


# ---------------------------------------------------------------------------
# E5: Ownership graph — sanctions propagation scoring
# ---------------------------------------------------------------------------


class TestE5OwnershipSanctionsPropagation:
    """E5: OwnerCluster.is_sanctioned wired to scoring."""

    def test_ownership_cluster_sanctioned_in_scoring(self):
        """risk_scoring.py references ownership_cluster_sanctioned."""
        source = inspect.getsource(
            importlib.import_module("app.modules.risk_scoring")
        )
        assert "ownership_cluster_sanctioned" in source

    def test_owner_cluster_import_in_scoring(self):
        """risk_scoring.py imports OwnerCluster for sanctions propagation."""
        source = inspect.getsource(
            importlib.import_module("app.modules.risk_scoring")
        )
        assert "OwnerCluster" in source
        assert "OwnerClusterMember" in source


# ---------------------------------------------------------------------------
# E6: Enable core detectors by default
# ---------------------------------------------------------------------------


class TestE6DefaultDetectorFlags:
    """E6: Stable detectors should be enabled by default."""

    _SHOULD_BE_ENABLED = [
        "STALE_AIS_DETECTION_ENABLED",
        "STALE_AIS_SCORING_ENABLED",
        "AT_SEA_OPERATIONS_SCORING_ENABLED",
        "RENAME_VELOCITY_DETECTION_ENABLED",
        "RENAME_VELOCITY_SCORING_ENABLED",
        "FLAG_HOPPING_DETECTION_ENABLED",
        "FLAG_HOPPING_SCORING_ENABLED",
        "IMO_FRAUD_DETECTION_ENABLED",
        "IMO_FRAUD_SCORING_ENABLED",
        "STATELESS_MMSI_DETECTION_ENABLED",
        "STATELESS_MMSI_SCORING_ENABLED",
        "FEED_OUTAGE_DETECTION_ENABLED",
        "ISM_CONTINUITY_DETECTION_ENABLED",
        "ISM_CONTINUITY_SCORING_ENABLED",
        "PI_VALIDATION_DETECTION_ENABLED",
        "PI_VALIDATION_SCORING_ENABLED",
        "FRAUDULENT_REGISTRY_DETECTION_ENABLED",
        "FRAUDULENT_REGISTRY_SCORING_ENABLED",
    ]

    _SHOULD_REMAIN_DISABLED = [
        "TRACK_NATURALNESS_ENABLED",
        "TRACK_NATURALNESS_SCORING_ENABLED",
        "FINGERPRINT_ENABLED",
        "FINGERPRINT_SCORING_ENABLED",
        "SAR_CORRELATION_ENABLED",
        "SAR_CORRELATION_SCORING_ENABLED",
        "WEATHER_CORRELATION_ENABLED",
        "DARK_STS_DETECTION_ENABLED",
        "DARK_STS_SCORING_ENABLED",
        "CARGO_INFERENCE_ENABLED",
        "DESTINATION_DETECTION_ENABLED",
        "DESTINATION_SCORING_ENABLED",
    ]

    def test_stable_detectors_enabled_by_default(self):
        """All stable detectors default to True."""
        from app.config import Settings

        defaults = Settings()
        for flag in self._SHOULD_BE_ENABLED:
            assert getattr(defaults, flag) is True, f"{flag} should default to True"

    def test_experimental_detectors_disabled_by_default(self):
        """Experimental/unstable detectors remain disabled."""
        from app.config import Settings

        defaults = Settings()
        for flag in self._SHOULD_REMAIN_DISABLED:
            assert getattr(defaults, flag) is False, f"{flag} should remain False"


# ---------------------------------------------------------------------------
# E7: Feed outage — anti-decoy (max_outage_ratio)
# ---------------------------------------------------------------------------


class TestE7FeedOutageAntiDecoy:
    """E7: Reject outage clusters with too many high-risk vessels."""

    def test_max_outage_ratio_parameter_exists(self):
        """detect_feed_outages accepts max_outage_ratio parameter."""
        from app.modules.feed_outage_detector import detect_feed_outages

        sig = inspect.signature(detect_feed_outages)
        assert "max_outage_ratio" in sig.parameters
        # Default should be 0.3
        assert sig.parameters["max_outage_ratio"].default == 0.3

    def test_min_vessels_constant_is_5(self):
        """_MIN_VESSELS_FOR_OUTAGE constant is 5."""
        from app.modules.feed_outage_detector import _MIN_VESSELS_FOR_OUTAGE
        assert _MIN_VESSELS_FOR_OUTAGE == 5

    def test_get_high_risk_vessel_ids_function_exists(self):
        """_get_high_risk_vessel_ids helper exists."""
        from app.modules.feed_outage_detector import _get_high_risk_vessel_ids
        sig = inspect.signature(_get_high_risk_vessel_ids)
        assert "db" in sig.parameters

    def test_get_high_risk_vessel_ids_returns_set(self):
        """_get_high_risk_vessel_ids returns a set of vessel IDs."""
        from app.modules.feed_outage_detector import _get_high_risk_vessel_ids

        db = MagicMock()
        db.query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            (1,), (2,), (3,),
        ]

        result = _get_high_risk_vessel_ids(db)
        assert isinstance(result, set)
        assert result == {1, 2, 3}

    def test_detect_feed_outages_returns_decoy_rejected_key(self):
        """detect_feed_outages result includes decoy_rejected."""
        from app.modules.feed_outage_detector import detect_feed_outages

        with patch("app.modules.feed_outage_detector.settings") as mock_settings:
            mock_settings.FEED_OUTAGE_DETECTION_ENABLED = False
            result = detect_feed_outages(MagicMock())
            assert "decoy_rejected" in result

    def test_feed_outage_source_has_anti_decoy_logic(self):
        """Feed outage detector source includes anti-decoy logic."""
        source = inspect.getsource(
            importlib.import_module("app.modules.feed_outage_detector")
        )
        assert "max_outage_ratio" in source
        assert "decoy_rejected" in source
        assert "high_risk_vessel_ids" in source
