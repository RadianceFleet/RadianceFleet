"""Tests for sanctions propagation engine — multi-hop risk propagation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.owner_cluster import OwnerCluster
from app.models.owner_cluster_member import OwnerClusterMember
from app.models.sanctions_propagation import SanctionsPropagation
from app.models.vessel import Vessel
from app.models.vessel_owner import VesselOwner
from app.models.vessel_watchlist import VesselWatchlist
from app.modules.sanctions_propagation import (
    _check_compound_signal,
    _find_shared_manager_vessels,
    _get_vessel_managers,
    _normalize_name,
    get_vessel_propagations,
    propagate_sanctions_multi_hop,
)


@pytest.fixture
def db():
    """Create an in-memory SQLite database with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def _enable_propagation():
    """Enable sanctions propagation for tests."""
    with patch("app.modules.sanctions_propagation.settings") as mock_settings:
        mock_settings.SANCTIONS_PROPAGATION_ENABLED = True
        mock_settings.SANCTIONS_PROPAGATION_SCORING_ENABLED = True
        mock_settings.SANCTIONS_PROPAGATION_MAX_DEPTH = 3
        mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
        yield mock_settings


def _create_vessel(db: Session, vessel_id: int, name: str = "TEST", flag: str = "PA") -> Vessel:
    """Helper to create a vessel."""
    v = Vessel(vessel_id=vessel_id, name=name, mmsi=str(200000000 + vessel_id), flag=flag)
    db.add(v)
    db.flush()
    return v


def _create_owner(
    db: Session,
    vessel_id: int,
    owner_name: str,
    ownership_type: str | None = None,
    ism_manager: str | None = None,
    pi_club_name: str | None = None,
    is_sanctioned: bool = False,
) -> VesselOwner:
    """Helper to create a vessel owner record."""
    o = VesselOwner(
        vessel_id=vessel_id,
        owner_name=owner_name,
        ownership_type=ownership_type,
        ism_manager=ism_manager,
        pi_club_name=pi_club_name,
        is_sanctioned=is_sanctioned,
    )
    db.add(o)
    db.flush()
    return o


def _create_watchlist(db: Session, vessel_id: int, source: str = "OFAC_SDN") -> VesselWatchlist:
    """Helper to create a watchlist entry."""
    w = VesselWatchlist(
        vessel_id=vessel_id,
        watchlist_source=source,
        reason="Test sanctions",
        is_active=True,
    )
    db.add(w)
    db.flush()
    return w


# ── Name normalization tests ─────────────────────────────────────────────


class TestNormalizeName:
    def test_basic_normalization(self):
        assert _normalize_name("  Sovcomflot  ") == "SOVCOMFLOT"

    def test_empty_string(self):
        assert _normalize_name("") == ""

    def test_none_like_empty(self):
        # _normalize_name expects str, empty returns empty
        assert _normalize_name("") == ""

    def test_preserves_spaces_in_middle(self):
        assert _normalize_name("SCF Group") == "SCF GROUP"

    def test_mixed_case(self):
        assert _normalize_name("sOvCoMfLoT") == "SOVCOMFLOT"


# ── Get vessel managers tests ────────────────────────────────────────────


class TestGetVesselManagers:
    def test_ism_manager_from_column(self, db):
        _create_vessel(db, 1)
        _create_owner(db, 1, "Owner Corp", ism_manager="SCF Management")
        db.flush()

        managers = _get_vessel_managers(db, 1)
        assert "shared_ism_manager" in managers
        assert managers["shared_ism_manager"] == "SCF MANAGEMENT"

    def test_ship_manager_from_ownership_type(self, db):
        _create_vessel(db, 1)
        _create_owner(db, 1, "Ship Mgmt Ltd", ownership_type="ship_manager")
        db.flush()

        managers = _get_vessel_managers(db, 1)
        assert "shared_ship_manager" in managers
        assert managers["shared_ship_manager"] == "SHIP MGMT LTD"

    def test_doc_company(self, db):
        _create_vessel(db, 1)
        _create_owner(db, 1, "DOC Corp", ownership_type="doc_company")
        db.flush()

        managers = _get_vessel_managers(db, 1)
        assert "shared_doc_company" in managers

    def test_registered_owner(self, db):
        _create_vessel(db, 1)
        _create_owner(db, 1, "Reg Owner Inc", ownership_type="registered_owner")
        db.flush()

        managers = _get_vessel_managers(db, 1)
        assert "shared_registered_owner" in managers

    def test_no_managers(self, db):
        _create_vessel(db, 1)
        _create_owner(db, 1, "Just Owner")  # no ownership_type, no ism_manager
        db.flush()

        managers = _get_vessel_managers(db, 1)
        assert managers == {}

    def test_multiple_managers(self, db):
        _create_vessel(db, 1)
        _create_owner(db, 1, "Owner A", ownership_type="ship_manager", ism_manager="ISM Corp")
        _create_owner(db, 1, "DOC Inc", ownership_type="doc_company")
        db.flush()

        managers = _get_vessel_managers(db, 1)
        assert len(managers) >= 2


# ── Find shared manager vessels tests ────────────────────────────────────


class TestFindSharedManagerVessels:
    def test_shared_ism_manager(self, db):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="SCF Mgmt")
        _create_owner(db, 2, "Owner B", ism_manager="SCF Mgmt")
        db.flush()

        results = _find_shared_manager_vessels(db, 1, "shared_ism_manager", "SCF Mgmt", set())
        assert len(results) == 1
        assert results[0][0] == 2

    def test_shared_ship_manager(self, db):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Ship Mgr Co", ownership_type="ship_manager")
        _create_owner(db, 2, "Ship Mgr Co", ownership_type="ship_manager")
        db.flush()

        results = _find_shared_manager_vessels(db, 1, "shared_ship_manager", "Ship Mgr Co", set())
        assert len(results) == 1
        assert results[0][0] == 2

    def test_exclude_ids(self, db):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_vessel(db, 3)
        _create_owner(db, 1, "ISM Corp", ism_manager="ISM Corp")
        _create_owner(db, 2, "ISM Corp", ism_manager="ISM Corp")
        _create_owner(db, 3, "ISM Corp", ism_manager="ISM Corp")
        db.flush()

        results = _find_shared_manager_vessels(db, 1, "shared_ism_manager", "ISM Corp", {2})
        assert len(results) == 1
        assert results[0][0] == 3

    def test_no_match(self, db):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="ISM A")
        _create_owner(db, 2, "Owner B", ism_manager="ISM B")
        db.flush()

        results = _find_shared_manager_vessels(db, 1, "shared_ism_manager", "ISM A", set())
        assert len(results) == 0

    def test_self_reference_prevention(self, db):
        _create_vessel(db, 1)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        db.flush()

        results = _find_shared_manager_vessels(db, 1, "shared_ism_manager", "ISM Corp", set())
        assert len(results) == 0

    def test_case_insensitive_match(self, db):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="scf management")
        _create_owner(db, 2, "Owner B", ism_manager="SCF MANAGEMENT")
        db.flush()

        results = _find_shared_manager_vessels(db, 1, "shared_ism_manager", "scf management", set())
        assert len(results) == 1


# ── Compound signal tests ────────────────────────────────────────────────


class TestCompoundSignal:
    def test_compound_signal_detected(self, db):
        _create_vessel(db, 1, flag="PA")
        _create_vessel(db, 2, flag="PA")
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp", pi_club_name="West of England")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp", pi_club_name="West of England")
        db.flush()

        assert _check_compound_signal(db, 2, 1) is True

    def test_compound_signal_different_flags(self, db):
        _create_vessel(db, 1, flag="PA")
        _create_vessel(db, 2, flag="LR")
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp", pi_club_name="West of England")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp", pi_club_name="West of England")
        db.flush()

        assert _check_compound_signal(db, 2, 1) is False

    def test_compound_signal_no_pi(self, db):
        _create_vessel(db, 1, flag="PA")
        _create_vessel(db, 2, flag="PA")
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        db.flush()

        assert _check_compound_signal(db, 2, 1) is False

    def test_compound_signal_no_shared_manager(self, db):
        _create_vessel(db, 1, flag="PA")
        _create_vessel(db, 2, flag="PA")
        _create_owner(db, 1, "Owner A", pi_club_name="West of England")
        _create_owner(db, 2, "Owner B", pi_club_name="West of England")
        db.flush()

        assert _check_compound_signal(db, 2, 1) is False


# ── Depth 1 propagation tests ────────────────────────────────────────────


class TestDepth1Propagation:
    def test_shared_ism_manager_propagation(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="SCF Management")
        _create_owner(db, 2, "Owner B", ism_manager="SCF Management")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        assert len(records) >= 1
        assert any(r.vessel_id == 2 and r.propagation_depth == 1 for r in records)

    def test_shared_ship_manager_propagation(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Ship Mgr Co", ownership_type="ship_manager")
        _create_owner(db, 2, "Ship Mgr Co", ownership_type="ship_manager")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        depth_1 = [r for r in records if r.propagation_depth == 1]
        assert len(depth_1) >= 1
        assert any(r.propagation_type == "shared_ship_manager" for r in depth_1)

    def test_shared_doc_company_propagation(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "DOC Corp", ownership_type="doc_company")
        _create_owner(db, 2, "DOC Corp", ownership_type="doc_company")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        assert any(r.propagation_type == "shared_doc_company" for r in records)

    def test_shared_registered_owner_propagation(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Reg Owner Ltd", ownership_type="registered_owner")
        _create_owner(db, 2, "Reg Owner Ltd", ownership_type="registered_owner")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        assert any(r.propagation_type == "shared_registered_owner" for r in records)

    def test_depth_1_score(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Ship Co", ownership_type="ship_manager")
        _create_owner(db, 2, "Ship Co", ownership_type="ship_manager")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        depth_1 = [r for r in records if r.propagation_depth == 1 and r.propagation_type != "compound_signal"]
        assert all(r.risk_score_component == 40 for r in depth_1)


# ── Depth 2 propagation tests ────────────────────────────────────────────


class TestDepth2Propagation:
    def test_second_hop_propagation(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_vessel(db, 3)
        # Vessel 1 (sanctioned) -> shares ISM with vessel 2
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        # Vessel 2 -> shares ship_manager with vessel 3
        _create_owner(db, 2, "Ship Mgr X", ownership_type="ship_manager")
        _create_owner(db, 3, "Ship Mgr X", ownership_type="ship_manager")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        depth_2 = [r for r in records if r.propagation_depth == 2]
        assert len(depth_2) >= 1
        assert any(r.vessel_id == 3 for r in depth_2)

    def test_depth_2_score(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_vessel(db, 3)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        _create_owner(db, 2, "Ship Mgr X", ownership_type="ship_manager")
        _create_owner(db, 3, "Ship Mgr X", ownership_type="ship_manager")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        depth_2 = [r for r in records if r.propagation_depth == 2]
        assert all(r.risk_score_component == 25 for r in depth_2)


# ── Depth 3 cluster-based propagation tests ──────────────────────────────


class TestDepth3Propagation:
    def test_cluster_based_propagation(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_vessel(db, 3)
        # Vessel 1 (sanctioned) shares ISM with vessel 2
        o1 = _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        o2 = _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        # Vessel 3 is connected via owner cluster
        o3 = _create_owner(db, 3, "Owner C")

        # Create cluster connecting owner B and owner C
        cluster = OwnerCluster(canonical_name="OWNER GROUP", vessel_count=2)
        db.add(cluster)
        db.flush()

        m1 = OwnerClusterMember(cluster_id=cluster.cluster_id, owner_id=o2.owner_id, similarity_score=0.9)
        m2 = OwnerClusterMember(cluster_id=cluster.cluster_id, owner_id=o3.owner_id, similarity_score=0.85)
        db.add_all([m1, m2])
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        depth_3 = [r for r in records if r.propagation_depth == 3]
        assert len(depth_3) >= 1
        assert any(r.vessel_id == 3 for r in depth_3)
        assert all(r.propagation_type == "owner_cluster" for r in depth_3)

    def test_depth_3_score(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_vessel(db, 3)
        o1 = _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        o2 = _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        o3 = _create_owner(db, 3, "Owner C")
        cluster = OwnerCluster(canonical_name="GROUP", vessel_count=2)
        db.add(cluster)
        db.flush()
        db.add_all([
            OwnerClusterMember(cluster_id=cluster.cluster_id, owner_id=o2.owner_id),
            OwnerClusterMember(cluster_id=cluster.cluster_id, owner_id=o3.owner_id),
        ])
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        depth_3 = [r for r in records if r.propagation_depth == 3]
        assert all(r.risk_score_component == 15 for r in depth_3)


# ── Score capping tests ──────────────────────────────────────────────────


class TestScoreCapping:
    def test_max_score_cap(self, db, _enable_propagation):
        """Total score per vessel should not exceed max_score (50)."""
        _create_vessel(db, 1)
        _create_vessel(db, 2, flag="PA")
        # Multiple shared managers to try to exceed cap
        _create_owner(db, 1, "ISM A", ism_manager="ISM A")
        _create_owner(db, 2, "ISM A", ism_manager="ISM A")
        _create_owner(db, 1, "Ship A", ownership_type="ship_manager")
        _create_owner(db, 2, "Ship A", ownership_type="ship_manager")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        vessel_2_total = sum(r.risk_score_component for r in records if r.vessel_id == 2)
        assert vessel_2_total <= 50


# ── DB persistence tests ────────────────────────────────────────────────


class TestDBPersistence:
    def test_records_persisted(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        _create_watchlist(db, 1)
        db.flush()

        propagate_sanctions_multi_hop(db, vessel_id=1)
        # Query directly from DB
        stored = db.query(SanctionsPropagation).filter(SanctionsPropagation.vessel_id == 2).all()
        assert len(stored) >= 1
        assert stored[0].is_active is True

    def test_stale_deactivation(self, db, _enable_propagation):
        """Re-running propagation deactivates old records."""
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        _create_watchlist(db, 1)
        db.flush()

        # First run
        propagate_sanctions_multi_hop(db, vessel_id=1)
        first_count = db.query(SanctionsPropagation).filter(
            SanctionsPropagation.is_active == True  # noqa: E712
        ).count()

        # Second run — should deactivate old and create new
        propagate_sanctions_multi_hop(db, vessel_id=1)
        deactivated = db.query(SanctionsPropagation).filter(
            SanctionsPropagation.is_active == False  # noqa: E712
        ).count()
        assert deactivated >= first_count

    def test_propagation_path_json(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        for r in records:
            path = json.loads(r.propagation_path_json)
            assert isinstance(path, list)
            assert len(path) >= 2


# ── Get vessel propagations tests ────────────────────────────────────────


class TestGetVesselPropagations:
    def test_returns_active_records(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        _create_watchlist(db, 1)
        db.flush()

        propagate_sanctions_multi_hop(db, vessel_id=1)
        results = get_vessel_propagations(db, 2)
        assert len(results) >= 1
        assert all(r["is_active"] for r in results)

    def test_empty_for_unaffected_vessel(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 99)
        _create_watchlist(db, 1)
        db.flush()

        propagate_sanctions_multi_hop(db, vessel_id=1)
        results = get_vessel_propagations(db, 99)
        assert results == []


# ── Multiple sanctioned vessels test ─────────────────────────────────────


class TestMultipleSanctionedVessels:
    def test_propagation_from_all_watchlist(self, db, _enable_propagation):
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_vessel(db, 3)
        _create_vessel(db, 4)
        # Vessel 1 shares ISM with vessel 3
        _create_owner(db, 1, "Owner A", ism_manager="ISM X")
        _create_owner(db, 3, "Owner C", ism_manager="ISM X")
        # Vessel 2 shares ship_manager with vessel 4
        _create_owner(db, 2, "Ship Co", ownership_type="ship_manager")
        _create_owner(db, 4, "Ship Co", ownership_type="ship_manager")
        _create_watchlist(db, 1)
        _create_watchlist(db, 2)
        db.flush()

        records = propagate_sanctions_multi_hop(db)
        affected_ids = {r.vessel_id for r in records}
        assert 3 in affected_ids
        assert 4 in affected_ids


# ── Disabled feature test ────────────────────────────────────────────────


class TestDisabledFeature:
    def test_returns_empty_when_disabled(self, db):
        with patch("app.modules.sanctions_propagation.settings") as mock_settings:
            mock_settings.SANCTIONS_PROPAGATION_ENABLED = False
            result = propagate_sanctions_multi_hop(db)
            assert result == []


# ── Edge cases ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_no_managers(self, db, _enable_propagation):
        """Vessel with no managers should produce no propagation."""
        _create_vessel(db, 1)
        _create_owner(db, 1, "Owner A")  # No ISM, no ownership_type
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        assert records == []

    def test_no_watchlist_entries(self, db, _enable_propagation):
        """No watchlist entries should produce no propagation."""
        _create_vessel(db, 1)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        db.flush()

        records = propagate_sanctions_multi_hop(db)
        assert records == []

    def test_empty_manager_name(self, db):
        assert _normalize_name("") == ""
        results = _find_shared_manager_vessels(db, 1, "shared_ism_manager", "", set())
        assert results == []


# ── API endpoint tests ───────────────────────────────────────────────────


class TestAPIEndpoints:
    def test_propagate_endpoint(self, db, _enable_propagation):
        """Test that the propagation endpoint returns expected format."""
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        _create_watchlist(db, 1)
        db.flush()

        records = propagate_sanctions_multi_hop(db, vessel_id=1)
        # Simulate endpoint response format
        response = {
            "status": "ok",
            "records_created": len(records),
            "vessel_ids_affected": list({r.vessel_id for r in records}),
        }
        assert response["status"] == "ok"
        assert response["records_created"] >= 1
        assert 2 in response["vessel_ids_affected"]

    def test_get_propagation_endpoint(self, db, _enable_propagation):
        """Test that the get endpoint returns expected format."""
        _create_vessel(db, 1)
        _create_vessel(db, 2)
        _create_owner(db, 1, "Owner A", ism_manager="ISM Corp")
        _create_owner(db, 2, "Owner B", ism_manager="ISM Corp")
        _create_watchlist(db, 1)
        db.flush()

        propagate_sanctions_multi_hop(db, vessel_id=1)
        results = get_vessel_propagations(db, 2)
        assert len(results) >= 1
        r = results[0]
        assert "vessel_id" in r
        assert "propagation_depth" in r
        assert "propagation_type" in r
        assert "risk_score_component" in r
        assert "propagation_path" in r
        assert "shared_fields" in r


# ── Watchlist integration trigger test ───────────────────────────────────


class TestWatchlistIntegration:
    def test_watchlist_trigger_calls_propagation(self):
        """Verify watchlist scheduler has the sanctions propagation trigger."""
        import inspect
        from app.modules import watchlist_scheduler

        source = inspect.getsource(watchlist_scheduler.update_source)
        assert "propagate_sanctions_multi_hop" in source
        assert "SANCTIONS_PROPAGATION_ENABLED" in source
