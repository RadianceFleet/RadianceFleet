"""Shared test fixtures for API integration tests."""
import pytest
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
