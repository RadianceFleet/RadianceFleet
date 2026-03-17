"""Tests for ownership network API endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes_ownership_network import router
from app.auth import require_auth
from app.database import get_db
from app.models.base import Base
from app.models.owner_cluster import OwnerCluster  # noqa: F401
from app.models.owner_cluster_member import OwnerClusterMember  # noqa: F401
from app.models.vessel import Vessel
from app.models.vessel_owner import VesselOwner

@pytest.fixture(scope="module")
def _engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(_engine):
    _TestSession = sessionmaker(bind=_engine)
    session = _TestSession()
    try:
        yield session
    finally:
        session.rollback()
        # Clean up all data
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()


@pytest.fixture()
def client(db):
    app = FastAPI()
    app.include_router(router)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_auth] = lambda: {"analyst_id": 1, "username": "test", "role": "admin"}
    return TestClient(app)


@pytest.fixture()
def seed_vessel(db):
    vessel = Vessel(vessel_id=1, name="Test Vessel", mmsi="123456789")
    db.add(vessel)

    owner = VesselOwner(
        owner_id=10, vessel_id=1, owner_name="Owner Corp",
        parent_owner_id=None, country="US",
    )
    db.add(owner)
    db.commit()
    return vessel, owner


class TestVesselOwnershipNetworkEndpoint:
    """Tests for GET /detect/ownership-network/{vessel_id}."""

    def test_returns_network(self, client, seed_vessel):
        resp = client.get("/detect/ownership-network/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert "sanctions_paths" in data
        assert "stats" in data

    def test_vessel_not_found(self, client):
        resp = client.get("/detect/ownership-network/9999")
        assert resp.status_code == 404
        assert "Vessel not found" in resp.json()["detail"]

    def test_depth_parameter(self, client, seed_vessel):
        resp = client.get("/detect/ownership-network/1?depth=1")
        assert resp.status_code == 200

    def test_limit_parameter(self, client, seed_vessel):
        resp = client.get("/detect/ownership-network/1?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats"]["total_nodes"] <= 5

    def test_depth_validation(self, client, seed_vessel):
        resp = client.get("/detect/ownership-network/1?depth=0")
        assert resp.status_code == 422  # validation error

    def test_limit_validation(self, client, seed_vessel):
        resp = client.get("/detect/ownership-network/1?limit=0")
        assert resp.status_code == 422


class TestFleetOwnershipNetworkEndpoint:
    """Tests for GET /detect/ownership-network."""

    def test_returns_fleet_network(self, client, seed_vessel):
        resp = client.get("/detect/ownership-network")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "stats" in data

    def test_sanctioned_only_filter(self, client, db):
        vessel = Vessel(vessel_id=2, name="Sanctioned Ship", mmsi="987654321")
        db.add(vessel)
        owner = VesselOwner(
            owner_id=20, vessel_id=2, owner_name="Bad Corp",
            is_sanctioned=True, country="RU",
        )
        db.add(owner)
        db.commit()

        resp = client.get("/detect/ownership-network?sanctioned_only=true")
        assert resp.status_code == 200

    def test_spv_only_filter(self, client, db):
        vessel = Vessel(vessel_id=3, name="SPV Ship", mmsi="111111111")
        db.add(vessel)
        owner = VesselOwner(
            owner_id=30, vessel_id=3, owner_name="Shell Co",
            is_spv=True, country="MH",
            incorporation_jurisdiction="MH",
        )
        db.add(owner)
        db.commit()

        resp = client.get("/detect/ownership-network?spv_only=true")
        assert resp.status_code == 200

    def test_jurisdiction_filter(self, client, seed_vessel):
        resp = client.get("/detect/ownership-network?jurisdiction=US")
        assert resp.status_code == 200
