"""Shared test fixtures and mock factories."""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db


@pytest.fixture
def mock_db():
    """MagicMock database session — returns None for all queries by default."""
    session = MagicMock()
    # Default: query().filter().first() returns None (not found)
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    return session


@pytest.fixture
def api_client(mock_db):
    """TestClient with DB dependency overridden to use a MagicMock session."""
    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ── Shared mock factories ────────────────────────────────────────────────────
# These are plain functions so tests can import and call them directly.
# They are also exposed as pytest fixtures (returning the factory function)
# for tests that prefer fixture injection.


def make_mock_vessel(vessel_id=1, mmsi="123456789", **kwargs):
    """Create a mock Vessel object. Any kwarg becomes an attribute."""
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi
    defaults = {
        "name": "TEST",
        "deadweight": 100000.0,
        "merged_into_vessel_id": None,
    }
    for key, val in {**defaults, **kwargs}.items():
        setattr(v, key, val)
    return v


def make_mock_point(vessel_id=1, lat=0.0, lon=0.0, ts=None, **kwargs):
    """Create a mock AIS point object. Any kwarg becomes an attribute."""
    p = MagicMock()
    p.vessel_id = vessel_id
    p.lat = lat
    p.lon = lon
    p.timestamp_utc = ts or datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    defaults = {
        "sog": 0.0,
        "cog": 0.0,
        "draught": None,
    }
    for key, val in {**defaults, **kwargs}.items():
        setattr(p, key, val)
    return p


def make_mock_port(port_id=1, name="Test Port", geometry="POINT(55.0 25.0)",
                   is_offshore_terminal=False, **kwargs):
    """Create a mock Port object. Any kwarg becomes an attribute."""
    p = MagicMock()
    p.port_id = port_id
    p.name = name
    p.geometry = geometry
    p.is_offshore_terminal = is_offshore_terminal
    for key, val in kwargs.items():
        setattr(p, key, val)
    return p


def make_mock_gap(gap_event_id=1, vessel_id=1, gap_start=None, gap_end=None,
                  **kwargs):
    """Create a mock GapEvent object. Any kwarg becomes an attribute."""
    g = MagicMock()
    g.gap_event_id = gap_event_id
    g.vessel_id = vessel_id
    g.gap_start_utc = gap_start or datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    g.gap_end_utc = gap_end or datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    for key, val in kwargs.items():
        setattr(g, key, val)
    return g


# Fixture wrappers — return the factory function for fixture-style usage.

@pytest.fixture
def make_vessel():
    return make_mock_vessel


@pytest.fixture
def make_point():
    return make_mock_point


@pytest.fixture
def make_port():
    return make_mock_port


@pytest.fixture
def make_gap():
    return make_mock_gap
