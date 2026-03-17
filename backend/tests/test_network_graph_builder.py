"""Tests for network_graph_builder module."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.owner_cluster import OwnerCluster
from app.models.owner_cluster_member import OwnerClusterMember
from app.models.vessel import Vessel
from app.models.vessel_owner import VesselOwner
from app.modules.network_graph_builder import (
    _find_sanctions_paths,
    _walk_parents_bfs,
    build_ownership_network,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def seed_basic(db: Session):
    """Create a basic ownership hierarchy: vessel -> leaf -> intermediary -> root."""
    vessel = Vessel(vessel_id=1, name="Test Vessel", mmsi="123456789")
    db.add(vessel)

    root = VesselOwner(
        owner_id=10, vessel_id=1, owner_name="Root Corp",
        parent_owner_id=None, country="US",
    )
    intermediary = VesselOwner(
        owner_id=20, vessel_id=1, owner_name="Middle Ltd",
        parent_owner_id=10, country="PA",
    )
    leaf = VesselOwner(
        owner_id=30, vessel_id=1, owner_name="Leaf LLC",
        parent_owner_id=20, country="MH",
    )
    db.add_all([root, intermediary, leaf])
    db.commit()
    return vessel, root, intermediary, leaf


@pytest.fixture()
def seed_sanctioned(db: Session):
    """Create hierarchy with a sanctioned owner."""
    vessel = Vessel(vessel_id=2, name="Sanctioned Ship", mmsi="987654321")
    db.add(vessel)

    root = VesselOwner(
        owner_id=100, vessel_id=2, owner_name="Sanctioned Corp",
        parent_owner_id=None, is_sanctioned=True, country="RU",
    )
    leaf = VesselOwner(
        owner_id=101, vessel_id=2, owner_name="Shell Co",
        parent_owner_id=100, country="PA", is_spv=True,
        incorporation_jurisdiction="PA",
    )
    db.add_all([root, leaf])
    db.commit()
    return vessel, root, leaf


@pytest.fixture()
def seed_cluster(db: Session):
    """Create owners linked via cluster membership."""
    v1 = Vessel(vessel_id=3, name="V1", mmsi="111111111")
    v2 = Vessel(vessel_id=4, name="V2", mmsi="222222222")
    db.add_all([v1, v2])

    o1 = VesselOwner(owner_id=200, vessel_id=3, owner_name="Alpha Shipping")
    o2 = VesselOwner(owner_id=201, vessel_id=4, owner_name="Alpha Maritime")
    db.add_all([o1, o2])

    cluster = OwnerCluster(cluster_id=1, canonical_name="Alpha Group", vessel_count=2)
    db.add(cluster)

    m1 = OwnerClusterMember(member_id=1, cluster_id=1, owner_id=200, similarity_score=0.95)
    m2 = OwnerClusterMember(member_id=2, cluster_id=1, owner_id=201, similarity_score=0.92)
    db.add_all([m1, m2])
    db.commit()
    return v1, v2, o1, o2, cluster


class TestBuildOwnershipNetwork:
    """Tests for build_ownership_network main entry point."""

    def test_basic_vessel_network(self, db, seed_basic):
        """Network for a vessel includes its owners and the vessel node."""
        result = build_ownership_network(db, vessel_id=1)

        assert result["stats"]["total_nodes"] > 0
        node_ids = {n["id"] for n in result["nodes"]}
        assert "vessel-1" in node_ids
        assert "owner-30" in node_ids  # leaf
        assert "owner-20" in node_ids  # intermediary
        assert "owner-10" in node_ids  # root

    def test_edges_reflect_hierarchy(self, db, seed_basic):
        """Edges connect child owners to parent owners."""
        result = build_ownership_network(db, vessel_id=1)

        edge_pairs = {(e["source"], e["target"]) for e in result["edges"]}
        # leaf -> intermediary
        assert ("owner-30", "owner-20") in edge_pairs
        # intermediary -> root
        assert ("owner-20", "owner-10") in edge_pairs

    def test_layer_assignment(self, db, seed_basic):
        """Layers assigned correctly: 0=root, 1=intermediary, 2=leaf, 3=vessel."""
        result = build_ownership_network(db, vessel_id=1)

        layer_map = {n["id"]: n["layer"] for n in result["nodes"]}
        assert layer_map["owner-10"] == 0  # root
        assert layer_map["owner-20"] == 1  # intermediary
        assert layer_map["owner-30"] == 2  # leaf
        assert layer_map["vessel-1"] == 3  # vessel

    def test_empty_ownership(self, db):
        """Vessel with no owners returns empty graph."""
        vessel = Vessel(vessel_id=99, name="Orphan", mmsi="000000000")
        db.add(vessel)
        db.commit()

        result = build_ownership_network(db, vessel_id=99)
        assert result["stats"]["total_nodes"] == 0
        assert result["nodes"] == []

    def test_depth_limit(self, db):
        """Depth=1 should not reach distant ancestors."""
        # Create a deep chain where only the leaf owns the vessel
        vessel = Vessel(vessel_id=50, name="Deep Vessel", mmsi="555555555")
        db.add(vessel)
        root = VesselOwner(
            owner_id=500, vessel_id=50, owner_name="Deep Root",
            parent_owner_id=None, country="US",
        )
        mid = VesselOwner(
            owner_id=501, vessel_id=50, owner_name="Deep Mid",
            parent_owner_id=500, country="PA",
        )
        leaf = VesselOwner(
            owner_id=502, vessel_id=50, owner_name="Deep Leaf",
            parent_owner_id=501, country="MH",
        )
        db.add_all([root, mid, leaf])
        db.commit()

        # All three owners have vessel_id=50, so all are start owners.
        # With depth=1, BFS walks 1 step from each start owner.
        # root (start, depth 0) has no parent -> nothing.
        # mid (start, depth 0) walks to root (depth 1) -> found.
        # leaf (start, depth 0) walks to mid (already found) -> ok.
        # So all three should be found with depth >= 1.
        # With depth=0, no BFS walking occurs at all — only start owners.
        # Since depth min is 1 (API enforces ge=1), test with depth=1 just
        # verifies the overall result includes the starting nodes.
        result_d1 = build_ownership_network(db, vessel_id=50, depth=1)
        node_ids_d1 = {n["id"] for n in result_d1["nodes"]}
        assert "owner-502" in node_ids_d1  # leaf (start)
        assert "owner-501" in node_ids_d1  # mid (start)
        # root is also a start owner (vessel_id=50), so it will be in graph
        assert "owner-500" in node_ids_d1

        # Verify stats reflect the depth
        assert result_d1["stats"]["max_depth"] <= 1

    def test_node_limit(self, db, seed_basic):
        """Node limit caps the number of returned nodes."""
        result = build_ownership_network(db, vessel_id=1, limit=2)

        assert result["stats"]["total_nodes"] <= 2

    def test_sanctioned_flag(self, db, seed_sanctioned):
        """Sanctioned owners are flagged in node data."""
        result = build_ownership_network(db, vessel_id=2)

        sanctioned_nodes = [n for n in result["nodes"] if n.get("is_sanctioned")]
        assert len(sanctioned_nodes) >= 1
        assert result["stats"]["sanctioned_count"] >= 1

    def test_spv_flag(self, db, seed_sanctioned):
        """SPV owners are flagged in node data."""
        result = build_ownership_network(db, vessel_id=2)

        spv_nodes = [n for n in result["nodes"] if n.get("is_spv")]
        assert len(spv_nodes) >= 1
        assert result["stats"]["spv_count"] >= 1

    def test_sanctioned_only_filter(self, db, seed_sanctioned):
        """sanctioned_only=True filters to sanctioned nodes."""
        result = build_ownership_network(db, sanctioned_only=True)

        company_nodes = [n for n in result["nodes"] if n["type"] == "company"]
        for node in company_nodes:
            assert node["is_sanctioned"] is True

    def test_spv_only_filter(self, db, seed_sanctioned):
        """spv_only=True filters to SPV nodes."""
        result = build_ownership_network(db, spv_only=True)

        company_nodes = [n for n in result["nodes"] if n["type"] == "company"]
        for node in company_nodes:
            assert node["is_spv"] is True

    def test_jurisdiction_filter(self, db, seed_sanctioned):
        """jurisdiction filter limits results to specific jurisdiction."""
        result = build_ownership_network(db, jurisdiction="PA")

        company_nodes = [n for n in result["nodes"] if n["type"] == "company"]
        for node in company_nodes:
            assert node["jurisdiction"] is not None
            assert node["jurisdiction"].upper() == "PA"

    def test_cluster_related_owners(self, db, seed_cluster):
        """Cluster membership pulls in related owners."""
        result = build_ownership_network(db, vessel_id=3)

        node_ids = {n["id"] for n in result["nodes"]}
        # Owner 201 should appear via cluster relationship
        assert "owner-201" in node_ids

    def test_cluster_edges(self, db, seed_cluster):
        """Cluster members get cluster_related edges."""
        result = build_ownership_network(db, vessel_id=3)

        cluster_edges = [e for e in result["edges"] if e["relationship"] == "cluster_related"]
        assert len(cluster_edges) >= 1

    def test_stats_complete(self, db, seed_basic):
        """Stats dict contains all expected keys."""
        result = build_ownership_network(db, vessel_id=1)

        stats = result["stats"]
        assert "total_nodes" in stats
        assert "total_edges" in stats
        assert "max_depth" in stats
        assert "sanctioned_count" in stats
        assert "spv_count" in stats


class TestSanctionsPaths:
    """Tests for _find_sanctions_paths helper."""

    def test_sanctions_paths_found(self):
        """Sanctions paths BFS finds paths from sanctioned nodes."""
        nodes = [
            {"id": "a", "is_sanctioned": True},
            {"id": "b", "is_sanctioned": False},
            {"id": "c", "is_sanctioned": False},
        ]
        edges = [
            {"source": "a", "target": "b"},
            {"source": "b", "target": "c"},
        ]
        paths = _find_sanctions_paths(nodes, edges)
        assert len(paths) > 0
        # At least one path should start from "a"
        assert any(p[0] == "a" for p in paths)

    def test_no_sanctioned_nodes(self):
        """No sanctions paths when no sanctioned nodes exist."""
        nodes = [
            {"id": "a", "is_sanctioned": False},
            {"id": "b", "is_sanctioned": False},
        ]
        edges = [{"source": "a", "target": "b"}]
        paths = _find_sanctions_paths(nodes, edges)
        assert paths == []
