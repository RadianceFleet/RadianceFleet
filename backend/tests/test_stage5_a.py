"""Tests for Stage 5-A: Corporate Ownership Graph.

Covers:
- build_ownership_graph with basic clusters
- Shell chain detection (depth > 2)
- Post-sanction reshuffling (>2 changes in 12mo)
- Sanctions propagation
- Feature flag gating
- Pipeline wiring test
- Config integration tests
- New migration columns exist
- YAML section exists
- Max depth guard (10)
- Empty owners (no crash)
- Circular ownership detection
- Shared address with sanctioned entity
- Scoring integration
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_owner(
    owner_id: int,
    vessel_id: int,
    owner_name: str = "Acme Shipping",
    country: str | None = None,
    is_sanctioned: bool = False,
    parent_owner_id: int | None = None,
    verified_at: datetime | None = None,
    ownership_type: str | None = None,
    ownership_pct: float | None = None,
):
    """Build a MagicMock owner with explicit numeric attributes."""
    owner = MagicMock()
    owner.owner_id = owner_id
    owner.vessel_id = vessel_id
    owner.owner_name = owner_name
    owner.country = country
    owner.is_sanctioned = is_sanctioned
    owner.parent_owner_id = parent_owner_id
    owner.verified_at = verified_at
    owner.ownership_type = ownership_type
    owner.ownership_pct = ownership_pct
    owner.ism_manager = None
    owner.pi_club_name = None
    owner.verified_by = None
    owner.source_url = None
    owner.verification_notes = None
    return owner


def _make_cluster(cluster_id: int, canonical_name: str, is_sanctioned: bool = False):
    cluster = MagicMock()
    cluster.cluster_id = cluster_id
    cluster.canonical_name = canonical_name
    cluster.is_sanctioned = is_sanctioned
    cluster.vessel_count = 0
    return cluster


def _make_member(member_id: int, cluster_id: int, owner_id: int):
    member = MagicMock()
    member.member_id = member_id
    member.cluster_id = cluster_id
    member.owner_id = owner_id
    member.similarity_score = 100.0
    return member


# ---------------------------------------------------------------------------
# 1. Feature flag gating — disabled
# ---------------------------------------------------------------------------

class TestFeatureFlagGating:
    @patch("app.modules.ownership_graph.settings")
    def test_build_ownership_graph_disabled(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = False
        from app.modules.ownership_graph import build_ownership_graph

        db = MagicMock()
        result = build_ownership_graph(db)
        assert result["status"] == "disabled"
        assert result["clusters_found"] == 0
        assert result["shell_chains"] == 0
        db.query.assert_not_called()

    @patch("app.modules.ownership_graph.settings")
    def test_propagate_sanctions_disabled(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = False
        from app.modules.ownership_graph import propagate_sanctions

        db = MagicMock()
        result = propagate_sanctions(db)
        assert result["status"] == "disabled"
        assert result["vessels_flagged"] == 0
        db.query.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Empty owners — no crash
# ---------------------------------------------------------------------------

class TestEmptyOwners:
    @patch("app.modules.ownership_graph.settings")
    def test_build_graph_empty(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        db = MagicMock()
        db.query.return_value.all.return_value = []
        result = build_ownership_graph(db)
        assert result["status"] == "ok"
        assert result["clusters_found"] == 0

    @patch("app.modules.ownership_graph.settings")
    def test_propagate_sanctions_no_sanctioned(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import propagate_sanctions

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = propagate_sanctions(db)
        assert result["status"] == "ok"
        assert result["vessels_flagged"] == 0


# ---------------------------------------------------------------------------
# 3. Basic cluster detection
# ---------------------------------------------------------------------------

class TestBasicClusters:
    @patch("app.modules.ownership_graph.settings")
    def test_clusters_found_count(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        owners = [
            _make_owner(1, 100, "Acme Shipping"),
            _make_owner(2, 200, "Acme Shipping"),  # same name -> cluster
            _make_owner(3, 300, "Beta Corp"),       # different name -> no cluster
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)
        assert result["status"] == "ok"
        # "acme shipping" has 2 vessels -> 1 cluster
        assert result["clusters_found"] == 1


# ---------------------------------------------------------------------------
# 4. Shell chain detection (depth > 2)
# ---------------------------------------------------------------------------

class TestShellChains:
    @patch("app.modules.ownership_graph.settings")
    def test_shell_chain_depth_3(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        # Chain: owner 3 -> owner 2 -> owner 1 (depth 3)
        owners = [
            _make_owner(1, 100, "Root Corp", parent_owner_id=None),
            _make_owner(2, 200, "Mid Corp", parent_owner_id=1),
            _make_owner(3, 300, "Leaf LLC", parent_owner_id=2),
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)
        assert result["shell_chains"] >= 1

    @patch("app.modules.ownership_graph.settings")
    def test_shell_chain_depth_2_not_flagged(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        # Chain: owner 2 -> owner 1 (depth 2, not > 2)
        owners = [
            _make_owner(1, 100, "Root Corp", parent_owner_id=None),
            _make_owner(2, 200, "Sub Corp", parent_owner_id=1),
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)
        assert result["shell_chains"] == 0


# ---------------------------------------------------------------------------
# 5. Max depth guard (10)
# ---------------------------------------------------------------------------

class TestMaxDepthGuard:
    @patch("app.modules.ownership_graph.settings")
    def test_max_depth_does_not_infinite_loop(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        # Create a linear chain of 15 owners — should stop at depth 10
        owners = []
        for i in range(1, 16):
            parent = i - 1 if i > 1 else None
            owners.append(_make_owner(i, i * 100, f"Corp {i}", parent_owner_id=parent))

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        # Should not hang or raise
        result = build_ownership_graph(db)
        assert result["status"] == "ok"
        assert result["shell_chains"] >= 1


# ---------------------------------------------------------------------------
# 6. Post-sanction reshuffling (>2 changes in 12 months)
# ---------------------------------------------------------------------------

class TestReshuffling:
    @patch("app.modules.ownership_graph.settings")
    def test_reshuffling_detected(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        now = datetime.utcnow()
        owners = [
            _make_owner(1, 100, "Owner A", verified_at=now - timedelta(days=30)),
            _make_owner(2, 100, "Owner B", verified_at=now - timedelta(days=60)),
            _make_owner(3, 100, "Owner C", verified_at=now - timedelta(days=90)),
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)
        assert result["reshuffling_detected"] >= 1

    @patch("app.modules.ownership_graph.settings")
    def test_no_reshuffling_old_changes(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        now = datetime.utcnow()
        owners = [
            _make_owner(1, 100, "Owner A", verified_at=now - timedelta(days=400)),
            _make_owner(2, 100, "Owner B", verified_at=now - timedelta(days=500)),
            _make_owner(3, 100, "Owner C", verified_at=now - timedelta(days=600)),
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)
        assert result["reshuffling_detected"] == 0


# ---------------------------------------------------------------------------
# 7. Circular ownership detection
# ---------------------------------------------------------------------------

class TestCircularOwnership:
    @patch("app.modules.ownership_graph.settings")
    def test_circular_detected(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        # owner 1 -> owner 2 -> owner 3 -> owner 1 (cycle)
        owners = [
            _make_owner(1, 100, "Corp A", parent_owner_id=3),
            _make_owner(2, 200, "Corp B", parent_owner_id=1),
            _make_owner(3, 300, "Corp C", parent_owner_id=2),
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)
        assert result["circular_ownership"] >= 1

    @patch("app.modules.ownership_graph.settings")
    def test_no_circular_linear_chain(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        owners = [
            _make_owner(1, 100, "Corp A", parent_owner_id=None),
            _make_owner(2, 200, "Corp B", parent_owner_id=1),
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)
        assert result["circular_ownership"] == 0


# ---------------------------------------------------------------------------
# 8. Shared address with sanctioned entity
# ---------------------------------------------------------------------------

class TestSharedAddress:
    @patch("app.modules.ownership_graph.settings")
    def test_shared_address_detected(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        owners = [
            _make_owner(1, 100, "Sanctioned Co", country="RU", is_sanctioned=True),
            _make_owner(2, 200, "Shell Co", country="RU", is_sanctioned=False),
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)
        assert result["shared_address_sanctioned"] >= 1


# ---------------------------------------------------------------------------
# 9. Sanctions propagation
# ---------------------------------------------------------------------------

class TestSanctionsPropagation:
    @patch("app.modules.ownership_graph.settings")
    def test_propagation_flags_cluster(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import propagate_sanctions

        db = MagicMock()

        sanctioned_owner = _make_owner(1, 100, "Sanctioned Co", is_sanctioned=True)
        non_sanctioned_owner = _make_owner(2, 200, "Clean Co", is_sanctioned=False)

        member1 = _make_member(1, 10, 1)
        member2 = _make_member(2, 10, 2)

        cluster = _make_cluster(10, "Test Cluster", is_sanctioned=False)

        # Mock query chain for sanctioned owners
        def query_side_effect(model):
            q = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if model_name == "VesselOwner":
                q.filter.return_value.all.return_value = [sanctioned_owner]
                q.filter.return_value.first.return_value = non_sanctioned_owner
            elif model_name == "OwnerClusterMember":
                q.filter.return_value.all.return_value = [member1, member2]
            elif model_name == "OwnerCluster":
                q.filter.return_value.first.return_value = cluster
            return q

        db.query.side_effect = query_side_effect
        result = propagate_sanctions(db)
        assert result["status"] == "ok"
        assert result["clusters_propagated"] >= 1


# ---------------------------------------------------------------------------
# 10. Config integration — feature flags exist
# ---------------------------------------------------------------------------

class TestConfig:
    def test_ownership_graph_flags_exist(self):
        from app.config import Settings

        s = Settings()
        assert hasattr(s, "OWNERSHIP_GRAPH_ENABLED")
        assert hasattr(s, "OWNERSHIP_GRAPH_SCORING_ENABLED")
        assert s.OWNERSHIP_GRAPH_ENABLED is False
        assert s.OWNERSHIP_GRAPH_SCORING_ENABLED is False


# ---------------------------------------------------------------------------
# 11. Migration columns exist in database.py
# ---------------------------------------------------------------------------

class TestMigrationColumns:
    def test_column_migrations_include_ownership_graph(self):
        """Verify the 3 new columns are in the migration list."""
        import app.database as db_module

        # Re-read the source to check the column_migrations list
        import inspect
        source = inspect.getsource(db_module._run_migrations)
        assert "parent_owner_id" in source
        assert "ownership_type" in source
        assert "ownership_pct" in source


# ---------------------------------------------------------------------------
# 12. YAML section exists
# ---------------------------------------------------------------------------

class TestYAMLSection:
    def test_ownership_graph_yaml_section(self):
        from pathlib import Path

        yaml_path = Path(__file__).parent.parent.parent / "config" / "risk_scoring.yaml"
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        assert "ownership_graph" in config
        og = config["ownership_graph"]
        assert og["shell_chain_depth_3_plus"] == 20
        assert og["post_sanction_reshuffling"] == 20
        assert og["shared_address_sanctioned"] == 35
        assert og["circular_ownership"] == 25


# ---------------------------------------------------------------------------
# 13. Expected sections includes ownership_graph
# ---------------------------------------------------------------------------

class TestExpectedSections:
    def test_ownership_graph_in_expected_sections(self):
        from app.modules.risk_scoring import _EXPECTED_SECTIONS

        assert "ownership_graph" in _EXPECTED_SECTIONS


# ---------------------------------------------------------------------------
# 14. Pipeline wiring
# ---------------------------------------------------------------------------

class TestPipelineWiring:
    def test_pipeline_calls_ownership_graph_when_enabled(self):
        """Verify discover_dark_vessels calls ownership graph when enabled."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels

        source = inspect.getsource(discover_dark_vessels)
        assert "ownership_graph" in source
        assert "OWNERSHIP_GRAPH_ENABLED" in source
        assert "build_ownership_graph" in source
        assert "propagate_sanctions" in source


# ---------------------------------------------------------------------------
# 15. VesselOwner model has new columns
# ---------------------------------------------------------------------------

class TestVesselOwnerModel:
    def test_new_columns_on_model(self):
        from app.models.vessel_owner import VesselOwner

        # Check that the ORM class defines the new columns
        mapper = VesselOwner.__table__
        col_names = {c.name for c in mapper.columns}
        assert "parent_owner_id" in col_names
        assert "ownership_type" in col_names
        assert "ownership_pct" in col_names


# ---------------------------------------------------------------------------
# 16. Scoring integration — ownership graph scoring section
# ---------------------------------------------------------------------------

class TestScoringIntegration:
    def test_scoring_reads_ownership_graph_config(self):
        """Verify risk_scoring.py has ownership graph scoring block."""
        import inspect
        from app.modules.risk_scoring import compute_gap_score

        source = inspect.getsource(compute_gap_score)
        assert "OWNERSHIP_GRAPH_SCORING_ENABLED" in source
        assert "ownership_graph" in source
        assert "ownership_shell_chain" in source


# ---------------------------------------------------------------------------
# 17. Internal helper tests
# ---------------------------------------------------------------------------

class TestInternalHelpers:
    def test_normalize_name(self):
        from app.modules.ownership_graph import _normalize_name

        assert _normalize_name("  Acme Shipping  ") == "acme shipping"
        assert _normalize_name("BETA CORP") == "beta corp"
        assert _normalize_name("") == ""
        assert _normalize_name(None) == ""

    def test_build_parent_chain_linear(self):
        from app.modules.ownership_graph import _build_parent_chain

        parent_map = {1: None, 2: 1, 3: 2, 4: 3}
        chain = _build_parent_chain(4, parent_map)
        assert len(chain) == 4
        assert chain[0] == 4
        assert chain[-1] == 1

    def test_build_parent_chain_circular(self):
        from app.modules.ownership_graph import _build_parent_chain

        parent_map = {1: 3, 2: 1, 3: 2}
        chain = _build_parent_chain(1, parent_map)
        # Should detect circle and stop, last element repeats
        assert len(chain) >= 3

    def test_detect_circular_ownership(self):
        from app.modules.ownership_graph import _detect_circular_ownership

        parent_map = {1: 3, 2: 1, 3: 2}
        circles = _detect_circular_ownership(parent_map)
        assert len(circles) >= 1

    def test_detect_no_circular(self):
        from app.modules.ownership_graph import _detect_circular_ownership

        parent_map = {1: None, 2: 1, 3: 2}
        circles = _detect_circular_ownership(parent_map)
        assert len(circles) == 0


# ---------------------------------------------------------------------------
# 18. Mixed scenario — multiple patterns detected
# ---------------------------------------------------------------------------

class TestMixedScenario:
    @patch("app.modules.ownership_graph.settings")
    def test_multiple_patterns_in_one_graph(self, mock_settings):
        mock_settings.OWNERSHIP_GRAPH_ENABLED = True
        from app.modules.ownership_graph import build_ownership_graph

        now = datetime.utcnow()
        owners = [
            # Cluster of same-name owners
            _make_owner(1, 100, "Shell Corp", country="RU", is_sanctioned=True),
            _make_owner(2, 200, "Shell Corp", country="RU", is_sanctioned=False),
            # Chain: 5 -> 4 -> 3 (depth 3)
            _make_owner(3, 300, "Root Inc", parent_owner_id=None),
            _make_owner(4, 400, "Mid Inc", parent_owner_id=3),
            _make_owner(5, 500, "Leaf Inc", parent_owner_id=4),
            # Reshuffling on vessel 600 (3 changes in 12 months)
            _make_owner(6, 600, "Owner X", verified_at=now - timedelta(days=10)),
            _make_owner(7, 600, "Owner Y", verified_at=now - timedelta(days=50)),
            _make_owner(8, 600, "Owner Z", verified_at=now - timedelta(days=90)),
        ]

        db = MagicMock()
        db.query.return_value.all.return_value = owners
        result = build_ownership_graph(db)

        assert result["status"] == "ok"
        assert result["clusters_found"] >= 1  # "shell corp" x2
        assert result["shell_chains"] >= 1    # depth-3 chain
        assert result["reshuffling_detected"] >= 1  # vessel 600
        assert result["shared_address_sanctioned"] >= 1  # RU shared with sanctioned
