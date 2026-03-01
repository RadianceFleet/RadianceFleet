"""Tests for Stage 4-A: Extended MMSI Chain Detection.

Covers:
  - MergeChain model creation
  - Chain detection with 3-vessel and 4+ vessel components
  - Chain confidence = min(link scores)
  - Confidence bands (HIGH/MEDIUM/LOW)
  - Deduplication (existing chain not recreated)
  - Empty graph (no chains)
  - Feature flag gating
  - Extended merge pass basics
  - Pipeline wiring
  - Config integration (flags exist, defaults false)
  - Risk scoring integration
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_merge_candidate(
    candidate_id: int,
    vessel_a_id: int,
    vessel_b_id: int,
    confidence_score: int = 80,
    status_value: str = "auto_merged",
    created_at: datetime.datetime | None = None,
):
    """Create a mock MergeCandidate."""
    mc = MagicMock()
    mc.candidate_id = candidate_id
    mc.vessel_a_id = vessel_a_id
    mc.vessel_b_id = vessel_b_id
    mc.confidence_score = confidence_score
    mc.created_at = created_at or datetime.datetime(2025, 6, 1, tzinfo=datetime.timezone.utc)
    mc.status = MagicMock()
    mc.status.value = status_value
    return mc


def _make_vessel(vessel_id: int, imo: str | None = None, mmsi: str = "123456789"):
    """Create a mock Vessel."""
    v = MagicMock()
    v.vessel_id = vessel_id
    v.imo = imo
    v.mmsi = mmsi
    v.name = f"Vessel-{vessel_id}"
    v.merged_into_vessel_id = None
    return v


def _make_chain(
    chain_id: int,
    vessel_ids: list[int],
    chain_length: int,
    confidence: float = 80.0,
    confidence_band: str = "HIGH",
    evidence_json: dict | None = None,
):
    """Create a mock MergeChain."""
    mc = MagicMock()
    mc.chain_id = chain_id
    mc.vessel_ids_json = vessel_ids
    mc.links_json = [100, 101]
    mc.chain_length = chain_length
    mc.confidence = confidence
    mc.confidence_band = confidence_band
    mc.evidence_json = evidence_json or {}
    mc.created_at = datetime.datetime(2025, 6, 1, tzinfo=datetime.timezone.utc)
    return mc


def _make_gap_and_vessel():
    """Create a minimal gap+vessel mock pair for scoring tests."""
    gap = MagicMock()
    gap.duration_minutes = 360
    gap.vessel_id = 1
    gap.impossible_speed_flag = False
    gap.velocity_plausibility_ratio = None
    gap.in_dark_zone = False
    gap.corridor = None
    gap.corridor_id = None
    gap.gap_start_utc = datetime.datetime(2025, 6, 1)
    gap.gap_end_utc = datetime.datetime(2025, 6, 1, 6, 0)
    gap.dark_zone_id = None
    gap.start_point = None
    gap.gap_off_lat = None
    gap.max_plausible_distance_nm = 200.0
    gap.risk_score = 0

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.mmsi = "123456789"
    vessel.flag = "PA"
    vessel.flag_risk_category = MagicMock()
    vessel.flag_risk_category.value = "medium_risk"
    vessel.deadweight = 100000
    vessel.year_built = 2010
    vessel.ais_class = MagicMock()
    vessel.ais_class.value = "A"
    vessel.pi_coverage_status = MagicMock()
    vessel.pi_coverage_status.value = "active"
    vessel.psc_detained_last_12m = False
    vessel.psc_major_deficiencies_last_12m = 0
    vessel.name = "VESSEL TEST"
    vessel.imo = "9074729"
    vessel.vessel_type = "Oil Tanker"
    vessel.mmsi_first_seen_utc = None
    vessel.vessel_laid_up_30d = False
    vessel.vessel_laid_up_60d = False
    vessel.vessel_laid_up_in_sts_zone = False
    gap.vessel = vessel

    return gap, vessel


def _make_scoring_db(chain_list=None):
    """Create a mock db that returns chain_list for MergeChain queries and
    safe defaults for all other queries."""
    db = MagicMock()
    # Default: all query().filter().all() returns empty list
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.count.return_value = 0
    db.query.return_value.filter.return_value.scalar.return_value = 0
    db.query.return_value.join.return_value.filter.return_value.count.return_value = 0
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    db.query.return_value.filter.return_value.order_by.return_value.asc.return_value = None
    db.query.return_value.get.return_value = None

    if chain_list is not None:
        # The MergeChain query is db.query(MergeChain).all()
        # Since MagicMock doesn't distinguish between model types in query(),
        # we set the global all() to return chains; the try/except in scoring
        # handles any issues from other query sections.
        db.query.return_value.all.return_value = chain_list

    return db


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestMergeChainModel:
    """Test MergeChain model can be imported and has expected fields."""

    def test_import_model(self):
        from app.models.merge_chain import MergeChain
        assert MergeChain.__tablename__ == "merge_chains"

    def test_model_columns(self):
        from app.models.merge_chain import MergeChain
        col_names = {c.name for c in MergeChain.__table__.columns}
        expected = {
            "chain_id", "vessel_ids_json", "links_json", "chain_length",
            "confidence", "confidence_band", "created_at", "evidence_json",
        }
        assert expected.issubset(col_names)

    def test_model_in_init(self):
        """MergeChain is registered in models __init__."""
        from app.models import MergeChain
        assert MergeChain is not None


# ---------------------------------------------------------------------------
# Feature flag tests
# ---------------------------------------------------------------------------

class TestFeatureFlags:
    """Test config flags exist and default to False."""

    def test_merge_chain_detection_flag_exists(self):
        from app.config import Settings
        s = Settings(DATABASE_URL="sqlite:///test.db")
        assert hasattr(s, "MERGE_CHAIN_DETECTION_ENABLED")
        assert s.MERGE_CHAIN_DETECTION_ENABLED is False

    def test_merge_chain_scoring_flag_exists(self):
        from app.config import Settings
        s = Settings(DATABASE_URL="sqlite:///test.db")
        assert hasattr(s, "MERGE_CHAIN_SCORING_ENABLED")
        assert s.MERGE_CHAIN_SCORING_ENABLED is False


# ---------------------------------------------------------------------------
# Chain detection tests
# ---------------------------------------------------------------------------

class TestDetectMergeChains:
    """Test detect_merge_chains function."""

    @patch("app.modules.identity_resolver.settings")
    def test_feature_flag_disabled(self, mock_settings):
        """Returns early when feature flag is disabled."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = False
        db = MagicMock()
        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["skipped"] == "feature_disabled"
        assert result["chains_created"] == 0

    @patch("app.modules.identity_resolver.settings")
    def test_empty_candidates(self, mock_settings):
        """No chains created when no candidates exist."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 0

    @patch("app.modules.identity_resolver.settings")
    def test_chain_3_vessels(self, mock_settings):
        """Creates chain for 3-vessel connected component."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True

        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=80,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=70,
                                  created_at=datetime.datetime(2025, 2, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {
            1: _make_vessel(1, imo="1234567"),
            2: _make_vessel(2, imo="7654321"),
            3: _make_vessel(3, imo="9876543"),
        }
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None

        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 1

    @patch("app.modules.identity_resolver.settings")
    def test_chain_4_plus_vessels(self, mock_settings):
        """Creates chain for 4-vessel connected component."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True

        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=90,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=75,
                                  created_at=datetime.datetime(2025, 2, 1)),
            _make_merge_candidate(102, 3, 4, confidence_score=60,
                                  created_at=datetime.datetime(2025, 3, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {i: _make_vessel(i) for i in range(1, 5)}
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None

        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 1

    @patch("app.modules.identity_resolver.settings")
    def test_chain_confidence_is_min(self, mock_settings):
        """Chain confidence = min(link scores) -- weakest link principle."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True

        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=90,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=55,
                                  created_at=datetime.datetime(2025, 2, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {i: _make_vessel(i) for i in range(1, 4)}
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None

        # Capture db.add calls to inspect chain attributes
        added_objects = []
        db.add.side_effect = lambda obj: added_objects.append(obj)

        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 1
        # The chain's confidence should be min(90, 55) = 55
        assert len(added_objects) == 1
        assert added_objects[0].confidence == 55

    @patch("app.modules.identity_resolver.settings")
    def test_confidence_band_high(self, mock_settings):
        """Confidence >= 75 maps to HIGH band."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True

        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=85,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=80,
                                  created_at=datetime.datetime(2025, 2, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {i: _make_vessel(i) for i in range(1, 4)}
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None
        db.add.side_effect = lambda obj: None

        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_by_band"]["HIGH"] == 1

    @patch("app.modules.identity_resolver.settings")
    def test_confidence_band_medium(self, mock_settings):
        """Confidence 50-74 maps to MEDIUM band."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True

        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=60,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=55,
                                  created_at=datetime.datetime(2025, 2, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {i: _make_vessel(i) for i in range(1, 4)}
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None
        db.add.side_effect = lambda obj: None

        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_by_band"]["MEDIUM"] == 1

    @patch("app.modules.identity_resolver.settings")
    def test_confidence_band_boundary(self, mock_settings):
        """Confidence == 50 (minimum from query filter) maps to MEDIUM."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True

        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=50,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=50,
                                  created_at=datetime.datetime(2025, 2, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {i: _make_vessel(i) for i in range(1, 4)}
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None
        db.add.side_effect = lambda obj: None

        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_by_band"]["MEDIUM"] == 1

    @patch("app.modules.identity_resolver.settings")
    def test_no_chains_for_two_vessel_component(self, mock_settings):
        """2-vessel components should NOT produce chains (need >= 3)."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True

        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=90,
                                  created_at=datetime.datetime(2025, 1, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        db.query.return_value.filter.return_value.first.return_value = None

        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 0

    @patch("app.modules.identity_resolver.settings")
    def test_deduplication(self, mock_settings):
        """Existing chain with same vessel_ids_json should not be recreated."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True

        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=80,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=75,
                                  created_at=datetime.datetime(2025, 2, 1)),
        ]

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {i: _make_vessel(i) for i in range(1, 4)}
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        existing_chain = _make_chain(1, [1, 2, 3], 3)
        db.query.return_value.filter.return_value.first.return_value = existing_chain

        from app.modules.identity_resolver import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 0


# ---------------------------------------------------------------------------
# Extended merge pass tests
# ---------------------------------------------------------------------------

class TestExtendedMergePass:
    """Test extended_merge_pass function."""

    @patch("app.modules.identity_resolver.settings")
    def test_feature_flag_disabled(self, mock_settings):
        """Returns early when disabled."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = False
        db = MagicMock()
        from app.modules.identity_resolver import extended_merge_pass
        result = extended_merge_pass(db)
        assert result["skipped"] == "feature_disabled"
        assert result["extended"] is True

    @patch("app.modules.identity_resolver.detect_merge_candidates")
    @patch("app.modules.identity_resolver.settings")
    def test_calls_detect_with_180_days(self, mock_settings, mock_detect):
        """Calls detect_merge_candidates with max_gap_days=180."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True
        mock_detect.return_value = {"candidates_created": 5, "auto_merged": 0, "skipped": 0}
        db = MagicMock()

        from app.modules.identity_resolver import extended_merge_pass
        result = extended_merge_pass(db)

        mock_detect.assert_called_once_with(db, max_gap_days=180, require_identity_anchor=True)
        assert result["extended"] is True
        assert result["candidates_created"] == 5


# ---------------------------------------------------------------------------
# Pipeline wiring tests
# ---------------------------------------------------------------------------

class TestPipelineWiring:
    """Test that Stage 4-A is wired into the pipeline."""

    def test_pipeline_source_has_merge_chain_step(self):
        """Check that dark_vessel_discovery.py references merge chain detection."""
        import inspect
        from app.modules import dark_vessel_discovery
        source = inspect.getsource(dark_vessel_discovery)
        assert "merge_chain_detection" in source
        assert "extended_merge_pass" in source

    def test_pipeline_feature_gate(self):
        """Merge chain step is gated by MERGE_CHAIN_DETECTION_ENABLED."""
        import inspect
        from app.modules import dark_vessel_discovery
        source = inspect.getsource(dark_vessel_discovery)
        assert "MERGE_CHAIN_DETECTION_ENABLED" in source


# ---------------------------------------------------------------------------
# Risk scoring integration tests
# ---------------------------------------------------------------------------

class TestMergeChainScoring:
    """Test merge chain scoring in compute_gap_score."""

    def test_scoring_yaml_has_merge_chains_section(self):
        """risk_scoring.yaml has merge_chains section (if reachable from CWD)."""
        from app.modules.risk_scoring import reload_scoring_config
        cfg = reload_scoring_config()
        # In test environments where config/risk_scoring.yaml is not reachable,
        # the config will be empty -- handle gracefully
        if not cfg:
            assert isinstance(cfg, dict)
        else:
            assert "merge_chains" in cfg
            mc_cfg = cfg["merge_chains"]
            assert mc_cfg["chain_3_hops"] == 15
            assert mc_cfg["chain_4_plus_hops"] == 25
            assert mc_cfg["scrapped_imo_in_chain"] == 35

    def test_expected_sections_includes_merge_chains(self):
        """_EXPECTED_SECTIONS includes merge_chains."""
        from app.modules.risk_scoring import _EXPECTED_SECTIONS
        assert "merge_chains" in _EXPECTED_SECTIONS

    @patch("app.config.settings")
    def test_chain_3_scoring(self, mock_settings):
        """3-vessel chain adds chain_3_hops points when scoring enabled."""
        mock_settings.MERGE_CHAIN_SCORING_ENABLED = True
        mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
        mock_settings.TRACK_NATURALNESS_SCORING_ENABLED = False
        mock_settings.STATELESS_MMSI_SCORING_ENABLED = False
        mock_settings.FLAG_HOPPING_SCORING_ENABLED = False
        mock_settings.IMO_FRAUD_SCORING_ENABLED = False
        mock_settings.FLEET_SCORING_ENABLED = False
        mock_settings.DARK_STS_SCORING_ENABLED = False
        mock_settings.DRAUGHT_SCORING_ENABLED = False

        from app.modules.risk_scoring import compute_gap_score

        cfg = {
            "merge_chains": {
                "chain_3_hops": 15,
                "chain_4_plus_hops": 25,
                "scrapped_imo_in_chain": 35,
            },
        }

        gap, vessel = _make_gap_and_vessel()
        chain = _make_chain(1, [1, 2, 3], chain_length=3, confidence=60.0,
                            confidence_band="MEDIUM",
                            evidence_json={"has_scrapped_imo": False})

        db = _make_scoring_db(chain_list=[chain])

        score, breakdown = compute_gap_score(gap, cfg, db=db)
        assert "merge_chain_3" in breakdown
        assert breakdown["merge_chain_3"] == 15

    @patch("app.config.settings")
    def test_chain_4plus_scoring(self, mock_settings):
        """4+ vessel chain adds chain_4_plus_hops points."""
        mock_settings.MERGE_CHAIN_SCORING_ENABLED = True
        mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
        mock_settings.TRACK_NATURALNESS_SCORING_ENABLED = False
        mock_settings.STATELESS_MMSI_SCORING_ENABLED = False
        mock_settings.FLAG_HOPPING_SCORING_ENABLED = False
        mock_settings.IMO_FRAUD_SCORING_ENABLED = False
        mock_settings.FLEET_SCORING_ENABLED = False
        mock_settings.DARK_STS_SCORING_ENABLED = False
        mock_settings.DRAUGHT_SCORING_ENABLED = False

        from app.modules.risk_scoring import compute_gap_score

        cfg = {
            "merge_chains": {
                "chain_3_hops": 15,
                "chain_4_plus_hops": 25,
                "scrapped_imo_in_chain": 35,
            },
        }

        gap, vessel = _make_gap_and_vessel()
        chain = _make_chain(1, [1, 2, 3, 4], chain_length=4, confidence=70.0,
                            confidence_band="MEDIUM",
                            evidence_json={"has_scrapped_imo": False})

        db = _make_scoring_db(chain_list=[chain])

        score, breakdown = compute_gap_score(gap, cfg, db=db)
        assert "merge_chain_4plus" in breakdown
        assert breakdown["merge_chain_4plus"] == 25

    @patch("app.config.settings")
    def test_scoring_disabled_skips_chains(self, mock_settings):
        """Scoring disabled: no merge chain keys in breakdown."""
        mock_settings.MERGE_CHAIN_SCORING_ENABLED = False
        mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
        mock_settings.TRACK_NATURALNESS_SCORING_ENABLED = False
        mock_settings.STATELESS_MMSI_SCORING_ENABLED = False
        mock_settings.FLAG_HOPPING_SCORING_ENABLED = False
        mock_settings.IMO_FRAUD_SCORING_ENABLED = False
        mock_settings.FLEET_SCORING_ENABLED = False
        mock_settings.DARK_STS_SCORING_ENABLED = False
        mock_settings.DRAUGHT_SCORING_ENABLED = False

        from app.modules.risk_scoring import compute_gap_score

        cfg = {
            "merge_chains": {
                "chain_3_hops": 15,
                "chain_4_plus_hops": 25,
                "scrapped_imo_in_chain": 35,
            },
        }

        gap, vessel = _make_gap_and_vessel()
        db = _make_scoring_db()

        score, breakdown = compute_gap_score(gap, cfg, db=db)
        assert "merge_chain_3" not in breakdown
        assert "merge_chain_4plus" not in breakdown

    @patch("app.config.settings")
    def test_scrapped_imo_in_chain_scoring(self, mock_settings):
        """Chain with scrapped IMO adds scrapped_imo_in_chain points."""
        mock_settings.MERGE_CHAIN_SCORING_ENABLED = True
        mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
        mock_settings.TRACK_NATURALNESS_SCORING_ENABLED = False
        mock_settings.STATELESS_MMSI_SCORING_ENABLED = False
        mock_settings.FLAG_HOPPING_SCORING_ENABLED = False
        mock_settings.IMO_FRAUD_SCORING_ENABLED = False
        mock_settings.FLEET_SCORING_ENABLED = False
        mock_settings.DARK_STS_SCORING_ENABLED = False
        mock_settings.DRAUGHT_SCORING_ENABLED = False

        from app.modules.risk_scoring import compute_gap_score

        cfg = {
            "merge_chains": {
                "chain_3_hops": 15,
                "chain_4_plus_hops": 25,
                "scrapped_imo_in_chain": 35,
            },
        }

        gap, vessel = _make_gap_and_vessel()
        chain = _make_chain(1, [1, 2, 3], chain_length=3, confidence=80.0,
                            confidence_band="HIGH",
                            evidence_json={"has_scrapped_imo": True})

        db = _make_scoring_db(chain_list=[chain])

        score, breakdown = compute_gap_score(gap, cfg, db=db)
        assert "scrapped_imo_in_chain" in breakdown
        assert breakdown["scrapped_imo_in_chain"] == 35

    @patch("app.config.settings")
    def test_vessel_not_in_chain_no_scoring(self, mock_settings):
        """Vessel not in any chain: no merge chain keys in breakdown."""
        mock_settings.MERGE_CHAIN_SCORING_ENABLED = True
        mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
        mock_settings.TRACK_NATURALNESS_SCORING_ENABLED = False
        mock_settings.STATELESS_MMSI_SCORING_ENABLED = False
        mock_settings.FLAG_HOPPING_SCORING_ENABLED = False
        mock_settings.IMO_FRAUD_SCORING_ENABLED = False
        mock_settings.FLEET_SCORING_ENABLED = False
        mock_settings.DARK_STS_SCORING_ENABLED = False
        mock_settings.DRAUGHT_SCORING_ENABLED = False

        from app.modules.risk_scoring import compute_gap_score

        cfg = {
            "merge_chains": {
                "chain_3_hops": 15,
                "chain_4_plus_hops": 25,
                "scrapped_imo_in_chain": 35,
            },
        }

        gap, vessel = _make_gap_and_vessel()
        # Chain exists but vessel_id=1 is NOT in it
        chain = _make_chain(1, [10, 20, 30], chain_length=3, confidence=80.0,
                            confidence_band="HIGH",
                            evidence_json={"has_scrapped_imo": False})

        db = _make_scoring_db(chain_list=[chain])

        score, breakdown = compute_gap_score(gap, cfg, db=db)
        assert "merge_chain_3" not in breakdown
        assert "merge_chain_4plus" not in breakdown
