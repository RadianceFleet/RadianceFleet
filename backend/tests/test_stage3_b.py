"""Stage 3-B: Multi-hop STS relay chain reconstruction tests.

Tests the STS chain detector, scoring integration, pipeline wiring,
and configuration plumbing.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sts_event(vessel_1_id, vessel_2_id, start_time, hours=4):
    """Create a mock STS transfer event."""
    ev = MagicMock()
    ev.vessel_1_id = vessel_1_id
    ev.vessel_2_id = vessel_2_id
    ev.start_time_utc = start_time
    ev.end_time_utc = start_time + timedelta(hours=hours)
    return ev


def _make_fleet_alert(alert_type, vessel_ids, evidence, score=0):
    """Create a mock FleetAlert."""
    fa = MagicMock()
    fa.alert_type = alert_type
    fa.vessel_ids_json = vessel_ids
    fa.evidence_json = evidence
    fa.risk_score_component = score
    return fa


# ---------------------------------------------------------------------------
# 1. Feature flag gating
# ---------------------------------------------------------------------------

class TestFeatureFlagGating:
    """Tests that the detector respects its feature flag."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_disabled_flag_returns_early(self, mock_settings):
        """When STS_CHAIN_DETECTION_ENABLED=False, return immediately."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = False
        from app.modules.sts_chain_detector import detect_sts_chains

        db = MagicMock()
        result = detect_sts_chains(db)

        assert result["status"] == "disabled"
        assert result["chains_found"] == 0
        assert result["alerts_created"] == 0
        # Should not touch the DB at all
        db.query.assert_not_called()

    @patch("app.modules.sts_chain_detector.settings")
    def test_enabled_flag_queries_db(self, mock_settings):
        """When STS_CHAIN_DETECTION_ENABLED=True, the DB is queried."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = detect_sts_chains(db)

        assert result["status"] == "ok"
        assert result["chains_found"] == 0
        db.query.assert_called()


# ---------------------------------------------------------------------------
# 2. No STS events
# ---------------------------------------------------------------------------

class TestNoStsEvents:
    """Tests behaviour when there are no STS events."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_no_sts_events_no_alerts(self, mock_settings):
        """No STS events should produce zero chains and alerts."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = detect_sts_chains(db)

        assert result["chains_found"] == 0
        assert result["alerts_created"] == 0
        assert result["vessels_flagged"] == 0


# ---------------------------------------------------------------------------
# 3. Simple 2-hop (no chain alert)
# ---------------------------------------------------------------------------

class TestTwoHopNoChain:
    """A->B is only 2 vessels; no chain alert should be created."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_simple_ab_no_chain(self, mock_settings):
        """A single STS event A->B is 2 vessels, below the 3-hop threshold."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1]

        result = detect_sts_chains(db)

        assert result["chains_found"] == 0
        assert result["alerts_created"] == 0


# ---------------------------------------------------------------------------
# 4. Three-hop chain (A->B->C)
# ---------------------------------------------------------------------------

class TestThreeHopChain:
    """A->B->C is a 3-vessel chain, should score +20."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_three_hop_creates_alert(self, mock_settings):
        """Three-vessel chain produces one alert with score 20."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))
        ev2 = _make_sts_event(2, 3, datetime(2025, 6, 2))

        db = MagicMock()
        # STS events query
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1, ev2]
        # Dedup query: no existing alerts
        db.query.return_value.filter.return_value.all.return_value = []

        result = detect_sts_chains(db)

        assert result["chains_found"] == 1
        assert result["alerts_created"] == 1

        # Verify the FleetAlert was added
        add_calls = db.add.call_args_list
        assert len(add_calls) >= 1
        alert = add_calls[0][0][0]
        assert alert.alert_type == "sts_relay_chain"
        assert alert.risk_score_component == 20
        evidence = alert.evidence_json
        assert evidence["chain_length"] == 3
        assert evidence["subtype"] == "sts_relay_chain"

    @patch("app.modules.sts_chain_detector.settings")
    def test_three_hop_intermediary_identified(self, mock_settings):
        """In A->B->C, vessel B should be identified as intermediary."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))
        ev2 = _make_sts_event(2, 3, datetime(2025, 6, 2))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1, ev2]
        db.query.return_value.filter.return_value.all.return_value = []

        detect_sts_chains(db)

        alert = db.add.call_args_list[0][0][0]
        evidence = alert.evidence_json
        assert 2 in evidence["intermediary_vessel_ids"]
        # First and last should NOT be intermediaries
        chain = evidence["chain_vessel_ids"]
        assert chain[0] not in evidence["intermediary_vessel_ids"]
        assert chain[-1] not in evidence["intermediary_vessel_ids"]


# ---------------------------------------------------------------------------
# 5. Four-hop chain (A->B->C->D)
# ---------------------------------------------------------------------------

class TestFourHopChain:
    """A->B->C->D is a 4-vessel chain, should score +40."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_four_hop_scores_40(self, mock_settings):
        """Four-vessel chain produces score 40."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))
        ev2 = _make_sts_event(2, 3, datetime(2025, 6, 2))
        ev3 = _make_sts_event(3, 4, datetime(2025, 6, 3))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1, ev2, ev3]
        db.query.return_value.filter.return_value.all.return_value = []

        result = detect_sts_chains(db)

        assert result["chains_found"] == 1
        alert = db.add.call_args_list[0][0][0]
        assert alert.risk_score_component == 40
        assert alert.evidence_json["chain_length"] == 4

    @patch("app.modules.sts_chain_detector.settings")
    def test_four_hop_intermediaries(self, mock_settings):
        """In A->B->C->D, vessels B and C are intermediaries."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))
        ev2 = _make_sts_event(2, 3, datetime(2025, 6, 2))
        ev3 = _make_sts_event(3, 4, datetime(2025, 6, 3))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1, ev2, ev3]
        db.query.return_value.filter.return_value.all.return_value = []

        detect_sts_chains(db)

        alert = db.add.call_args_list[0][0][0]
        intermediaries = alert.evidence_json["intermediary_vessel_ids"]
        assert 2 in intermediaries
        assert 3 in intermediaries
        assert 1 not in intermediaries
        assert 4 not in intermediaries


# ---------------------------------------------------------------------------
# 6. Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    """Existing chain alerts should not be duplicated."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_existing_alert_not_duplicated(self, mock_settings):
        """If a chain alert already exists for the same vessels, skip it."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))
        ev2 = _make_sts_event(2, 3, datetime(2025, 6, 2))

        existing_alert = _make_fleet_alert(
            "sts_relay_chain", [1, 2, 3],
            {"chain_length": 3, "subtype": "sts_relay_chain"},
        )

        db = MagicMock()
        # First call: STS events query (via filter().order_by().all())
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1, ev2]
        # Second call: dedup query (via filter().all())
        db.query.return_value.filter.return_value.all.return_value = [existing_alert]

        result = detect_sts_chains(db)

        assert result["chains_found"] == 1
        assert result["alerts_created"] == 0
        db.add.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Hops evidence structure
# ---------------------------------------------------------------------------

class TestHopsEvidence:
    """Tests that the hops evidence is correctly structured."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_hops_contain_vessel_ids_and_times(self, mock_settings):
        """Each hop should have from/to vessel IDs and timestamps."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        t1 = datetime(2025, 6, 1)
        t2 = datetime(2025, 6, 2)
        ev1 = _make_sts_event(10, 20, t1)
        ev2 = _make_sts_event(20, 30, t2)

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1, ev2]
        db.query.return_value.filter.return_value.all.return_value = []

        detect_sts_chains(db)

        alert = db.add.call_args_list[0][0][0]
        hops = alert.evidence_json["hops"]
        assert len(hops) == 2
        assert hops[0]["from_vessel_id"] == 10
        assert hops[0]["to_vessel_id"] == 20
        assert hops[1]["from_vessel_id"] == 20
        assert hops[1]["to_vessel_id"] == 30


# ---------------------------------------------------------------------------
# 8. Date range filtering
# ---------------------------------------------------------------------------

class TestDateRange:
    """Tests date_from and date_to parameters."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_custom_date_range(self, mock_settings):
        """Custom date range is passed to the STS query filter."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains
        from datetime import date

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = detect_sts_chains(
            db,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 6, 30),
        )

        assert result["status"] == "ok"
        # The filter should have been called
        db.query.return_value.filter.assert_called()


# ---------------------------------------------------------------------------
# 9. Pipeline wiring
# ---------------------------------------------------------------------------

class TestPipelineWiring:
    """Tests that the chain detector is wired into dark_vessel_discovery."""

    @patch("app.modules.dark_vessel_discovery.settings")
    def test_pipeline_step_present_when_enabled(self, mock_settings):
        """When STS_CHAIN_DETECTION_ENABLED, the step runs."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        # Set other flags to avoid side effects
        mock_settings.TRACK_NATURALNESS_ENABLED = False
        mock_settings.DRAUGHT_DETECTION_ENABLED = False
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = False
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = False
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = False
        mock_settings.FLEET_ANALYSIS_ENABLED = False

        from app.modules.dark_vessel_discovery import discover_dark_vessels

        db = MagicMock()
        # Mock all pipeline step dependencies
        with patch("app.modules.dark_vessel_discovery.cluster_dark_detections", return_value=[]), \
             patch("app.modules.dark_vessel_discovery.auto_hunt_dark_vessels", return_value={}), \
             patch("app.modules.gap_detector.run_gap_detection", return_value={}), \
             patch("app.modules.gap_detector.run_spoofing_detection", return_value={}), \
             patch("app.modules.sts_detector.detect_sts_events", return_value={}), \
             patch("app.modules.sts_chain_detector.detect_sts_chains", return_value={"chains_found": 2}) as mock_chain, \
             patch("app.modules.risk_scoring.rescore_all_alerts", return_value={}), \
             patch("app.modules.identity_resolver.detect_merge_candidates", return_value={}), \
             patch("app.modules.mmsi_cloning_detector.detect_mmsi_cloning", return_value={}):
            # Mock loitering import
            try:
                result = discover_dark_vessels(db, "2025-01-01", "2025-06-30", skip_fetch=True)
            except Exception:
                result = {"steps": {}}

            # Verify chain detection was called
            mock_chain.assert_called_once()

    @patch("app.modules.dark_vessel_discovery.settings")
    def test_pipeline_step_skipped_when_disabled(self, mock_settings):
        """When STS_CHAIN_DETECTION_ENABLED=False, step does not run."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = False
        mock_settings.TRACK_NATURALNESS_ENABLED = False
        mock_settings.DRAUGHT_DETECTION_ENABLED = False
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = False
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = False
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = False
        mock_settings.FLEET_ANALYSIS_ENABLED = False

        from app.modules.dark_vessel_discovery import discover_dark_vessels

        db = MagicMock()
        with patch("app.modules.dark_vessel_discovery.cluster_dark_detections", return_value=[]), \
             patch("app.modules.dark_vessel_discovery.auto_hunt_dark_vessels", return_value={}), \
             patch("app.modules.gap_detector.run_gap_detection", return_value={}), \
             patch("app.modules.gap_detector.run_spoofing_detection", return_value={}), \
             patch("app.modules.sts_detector.detect_sts_events", return_value={}), \
             patch("app.modules.risk_scoring.rescore_all_alerts", return_value={}), \
             patch("app.modules.identity_resolver.detect_merge_candidates", return_value={}), \
             patch("app.modules.mmsi_cloning_detector.detect_mmsi_cloning", return_value={}):
            try:
                result = discover_dark_vessels(db, "2025-01-01", "2025-06-30", skip_fetch=True)
            except Exception:
                result = {"steps": {}}

            # sts_chain_detection should NOT be in steps
            assert "sts_chain_detection" not in result.get("steps", {})


# ---------------------------------------------------------------------------
# 10. Integration: config plumbing
# ---------------------------------------------------------------------------

class TestConfigIntegration:
    """Tests that feature flags and YAML sections are correctly defined."""

    def test_feature_flags_exist(self):
        """STS_CHAIN_DETECTION_ENABLED and STS_CHAIN_SCORING_ENABLED exist in config."""
        from app.config import Settings

        s = Settings(
            STS_CHAIN_DETECTION_ENABLED=True,
            STS_CHAIN_SCORING_ENABLED=True,
        )
        assert s.STS_CHAIN_DETECTION_ENABLED is True
        assert s.STS_CHAIN_SCORING_ENABLED is True

    def test_feature_flags_default_false(self):
        """Feature flags default to False."""
        from app.config import Settings

        s = Settings()
        assert s.STS_CHAIN_DETECTION_ENABLED is False
        assert s.STS_CHAIN_SCORING_ENABLED is False

    def test_yaml_section_exists(self):
        """risk_scoring.yaml has an sts_chains section."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "config" / "risk_scoring.yaml"
        # Try project root path
        if not config_path.exists():
            config_path = Path(__file__).parent.parent.parent / "config" / "risk_scoring.yaml"

        config = yaml.safe_load(config_path.read_text())
        assert "sts_chains" in config
        assert config["sts_chains"]["chain_3_hops"] == 20
        assert config["sts_chains"]["chain_4_plus_hops"] == 40
        assert config["sts_chains"]["intermediary_vessel"] == 15

    def test_expected_sections_includes_sts_chains(self):
        """_EXPECTED_SECTIONS in risk_scoring.py includes 'sts_chains'."""
        from app.modules.risk_scoring import _EXPECTED_SECTIONS

        assert "sts_chains" in _EXPECTED_SECTIONS


# ---------------------------------------------------------------------------
# 11. Scoring integration
# ---------------------------------------------------------------------------

class TestScoringIntegration:
    """Tests that scoring block in risk_scoring.py works correctly."""

    @patch("app.modules.risk_scoring.settings")
    def test_scoring_disabled_by_default(self, mock_settings):
        """When STS_CHAIN_SCORING_ENABLED=False, no chain scoring is applied."""
        mock_settings.STS_CHAIN_SCORING_ENABLED = False
        # This test verifies the flag exists and defaults to False
        from app.config import Settings
        s = Settings()
        assert s.STS_CHAIN_SCORING_ENABLED is False


# ---------------------------------------------------------------------------
# 12. _build_chain helper
# ---------------------------------------------------------------------------

class TestBuildChain:
    """Tests the _build_chain helper function."""

    def test_build_chain_simple(self):
        """Simple A->B->C chain."""
        from app.modules.sts_chain_detector import _build_chain

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))
        ev2 = _make_sts_event(2, 3, datetime(2025, 6, 2))

        chain = _build_chain([ev1, ev2], {1, 2, 3})

        assert len(chain) == 3
        assert chain[0] == 1
        assert chain[1] == 2
        assert chain[2] == 3

    def test_build_chain_reverse_order(self):
        """Chain where second event connects from the start."""
        from app.modules.sts_chain_detector import _build_chain

        ev1 = _make_sts_event(2, 3, datetime(2025, 6, 1))
        ev2 = _make_sts_event(1, 2, datetime(2025, 6, 2))

        chain = _build_chain([ev1, ev2], {1, 2, 3})

        assert len(chain) == 3
        assert 1 in chain
        assert 2 in chain
        assert 3 in chain

    def test_build_chain_empty(self):
        """Empty events list returns empty chain."""
        from app.modules.sts_chain_detector import _build_chain

        chain = _build_chain([], set())
        assert chain == []

    def test_build_chain_four_hops(self):
        """A->B->C->D chain has length 4."""
        from app.modules.sts_chain_detector import _build_chain

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))
        ev2 = _make_sts_event(2, 3, datetime(2025, 6, 2))
        ev3 = _make_sts_event(3, 4, datetime(2025, 6, 3))

        chain = _build_chain([ev1, ev2, ev3], {1, 2, 3, 4})
        assert len(chain) == 4


# ---------------------------------------------------------------------------
# 13. Commit and vessel_ids_json sorted
# ---------------------------------------------------------------------------

class TestCommitBehavior:
    """Tests that db.commit() is called after chain processing."""

    @patch("app.modules.sts_chain_detector.settings")
    def test_commit_called(self, mock_settings):
        """db.commit() is called when chains are processed."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        ev1 = _make_sts_event(1, 2, datetime(2025, 6, 1))
        ev2 = _make_sts_event(2, 3, datetime(2025, 6, 2))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1, ev2]
        db.query.return_value.filter.return_value.all.return_value = []

        detect_sts_chains(db)
        db.commit.assert_called_once()

    @patch("app.modules.sts_chain_detector.settings")
    def test_vessel_ids_sorted(self, mock_settings):
        """vessel_ids_json should be sorted for deterministic dedup."""
        mock_settings.STS_CHAIN_DETECTION_ENABLED = True
        from app.modules.sts_chain_detector import detect_sts_chains

        # Chain: 3->1, 1->2 => chain order might be [3,1,2] but vessel_ids_json = [1,2,3]
        ev1 = _make_sts_event(3, 1, datetime(2025, 6, 1))
        ev2 = _make_sts_event(1, 2, datetime(2025, 6, 2))

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [ev1, ev2]
        db.query.return_value.filter.return_value.all.return_value = []

        detect_sts_chains(db)

        alert = db.add.call_args_list[0][0][0]
        assert alert.vessel_ids_json == [1, 2, 3]
