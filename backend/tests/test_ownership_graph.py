"""Tests for corporate ownership graph — shell chains, circular ownership, sanctions."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app.modules.ownership_graph import (
    _build_parent_chain,
    _detect_circular_ownership,
    _normalize_name,
)

# ── Tests: _normalize_name ───────────────────────────────────────────


class TestNormalizeName:
    def test_strips_and_lowercases(self):
        assert _normalize_name("  ACME Corp  ") == "acme corp"

    def test_empty_string(self):
        assert _normalize_name("") == ""

    def test_none_returns_empty(self):
        assert _normalize_name(None) == ""


# ── Tests: _build_parent_chain ───────────────────────────────────────


class TestBuildParentChain:
    def test_simple_chain(self):
        parent_map = {1: 2, 2: 3, 3: None}
        chain = _build_parent_chain(1, parent_map)
        assert chain == [1, 2, 3]

    def test_single_owner_no_parent(self):
        parent_map = {1: None}
        chain = _build_parent_chain(1, parent_map)
        assert chain == [1]

    def test_cycle_detection(self):
        parent_map = {1: 2, 2: 3, 3: 1}
        chain = _build_parent_chain(1, parent_map)
        assert 1 in chain
        # Cycle detected — chain should end at repeat
        assert len(chain) <= 4

    def test_max_depth_limit(self):
        parent_map = {i: i + 1 for i in range(1, 20)}
        parent_map[20] = None
        chain = _build_parent_chain(1, parent_map, max_depth=5)
        assert len(chain) <= 6  # max_depth iterations + initial

    def test_parent_not_in_map(self):
        parent_map = {1: 99}
        chain = _build_parent_chain(1, parent_map)
        assert chain == [1]


# ── Tests: _detect_circular_ownership ────────────────────────────────


class TestDetectCircularOwnership:
    def test_no_circles(self):
        parent_map = {1: 2, 2: 3, 3: None}
        circles = _detect_circular_ownership(parent_map)
        assert circles == []

    def test_detects_simple_circle(self):
        parent_map = {1: 2, 2: 3, 3: 1}
        circles = _detect_circular_ownership(parent_map)
        assert len(circles) >= 1

    def test_detects_self_loop(self):
        parent_map = {1: 1}
        circles = _detect_circular_ownership(parent_map)
        assert len(circles) >= 1

    def test_empty_map(self):
        circles = _detect_circular_ownership({})
        assert circles == []

    def test_multiple_chains_one_circle(self):
        parent_map = {1: 2, 2: 3, 3: None, 4: 5, 5: 4}
        circles = _detect_circular_ownership(parent_map)
        assert len(circles) >= 1


# ── Tests: build_ownership_graph ─────────────────────────────────────


class TestBuildOwnershipGraph:
    def test_disabled_returns_status(self):
        from app.modules.ownership_graph import build_ownership_graph

        db = MagicMock()
        with patch("app.modules.ownership_graph.settings") as mock_settings:
            mock_settings.OWNERSHIP_GRAPH_ENABLED = False
            result = build_ownership_graph(db)
            assert result["status"] == "disabled"

    def test_no_owners(self):
        from app.modules.ownership_graph import build_ownership_graph

        db = MagicMock()
        with patch("app.modules.ownership_graph.settings") as mock_settings:
            mock_settings.OWNERSHIP_GRAPH_ENABLED = True
            db.query.return_value.all.return_value = []
            result = build_ownership_graph(db)
            assert result["clusters_found"] == 0
            assert result["shell_chains"] == 0

    def test_counts_clusters(self):
        from app.modules.ownership_graph import build_ownership_graph

        db = MagicMock()
        owner1 = MagicMock()
        owner1.owner_id = 1
        owner1.owner_name = "ACME CORP"
        owner1.vessel_id = 1
        owner1.parent_owner_id = None
        owner1.is_sanctioned = False
        owner1.country = None
        owner1.verified_at = None

        owner2 = MagicMock()
        owner2.owner_id = 2
        owner2.owner_name = "ACME CORP"
        owner2.vessel_id = 2
        owner2.parent_owner_id = None
        owner2.is_sanctioned = False
        owner2.country = None
        owner2.verified_at = None

        with patch("app.modules.ownership_graph.settings") as mock_settings:
            mock_settings.OWNERSHIP_GRAPH_ENABLED = True
            db.query.return_value.all.return_value = [owner1, owner2]
            result = build_ownership_graph(db)
            assert result["clusters_found"] == 1

    def test_detects_shell_chain(self):
        from app.modules.ownership_graph import build_ownership_graph

        db = MagicMock()
        owners = []
        for i in range(1, 4):
            o = MagicMock()
            o.owner_id = i
            o.owner_name = f"Owner {i}"
            o.vessel_id = i
            o.parent_owner_id = i + 1 if i < 3 else None
            o.is_sanctioned = False
            o.country = None
            o.verified_at = None
            owners.append(o)

        with patch("app.modules.ownership_graph.settings") as mock_settings:
            mock_settings.OWNERSHIP_GRAPH_ENABLED = True
            db.query.return_value.all.return_value = owners
            result = build_ownership_graph(db)
            assert result["shell_chains"] >= 1

    def test_detects_reshuffling(self):
        from app.modules.ownership_graph import build_ownership_graph

        db = MagicMock()
        now = datetime.now()
        owners = []
        for i in range(1, 5):
            o = MagicMock()
            o.owner_id = i
            o.owner_name = f"Owner {i}"
            o.vessel_id = 1  # same vessel, multiple owners
            o.parent_owner_id = None
            o.is_sanctioned = False
            o.country = None
            o.verified_at = now - timedelta(days=30 * i)
            owners.append(o)

        with patch("app.modules.ownership_graph.settings") as mock_settings:
            mock_settings.OWNERSHIP_GRAPH_ENABLED = True
            db.query.return_value.all.return_value = owners
            result = build_ownership_graph(db)
            assert result["reshuffling_detected"] >= 1

    def test_shared_address_sanctioned(self):
        from app.modules.ownership_graph import build_ownership_graph

        db = MagicMock()
        o1 = MagicMock()
        o1.owner_id = 1
        o1.owner_name = "Sanctioned Co"
        o1.vessel_id = 1
        o1.parent_owner_id = None
        o1.is_sanctioned = True
        o1.country = "Russia"
        o1.verified_at = None

        o2 = MagicMock()
        o2.owner_id = 2
        o2.owner_name = "Clean Co"
        o2.vessel_id = 2
        o2.parent_owner_id = None
        o2.is_sanctioned = False
        o2.country = "Russia"
        o2.verified_at = None

        with patch("app.modules.ownership_graph.settings") as mock_settings:
            mock_settings.OWNERSHIP_GRAPH_ENABLED = True
            db.query.return_value.all.return_value = [o1, o2]
            result = build_ownership_graph(db)
            assert result["shared_address_sanctioned"] >= 1


# ── Tests: propagate_sanctions ───────────────────────────────────────


class TestPropagateSanctions:
    def test_disabled_returns_status(self):
        from app.modules.ownership_graph import propagate_sanctions

        db = MagicMock()
        with patch("app.modules.ownership_graph.settings") as mock_settings:
            mock_settings.OWNERSHIP_GRAPH_ENABLED = False
            result = propagate_sanctions(db)
            assert result["status"] == "disabled"

    def test_no_sanctioned_owners(self):
        from app.modules.ownership_graph import propagate_sanctions

        db = MagicMock()
        with patch("app.modules.ownership_graph.settings") as mock_settings:
            mock_settings.OWNERSHIP_GRAPH_ENABLED = True
            db.query.return_value.filter.return_value.all.return_value = []
            result = propagate_sanctions(db)
            assert result["vessels_flagged"] == 0
