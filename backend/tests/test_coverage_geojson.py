"""Tests for GET /coverage/geojson endpoint."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app

# Resolve actual coverage.yaml regardless of CWD
_COVERAGE_YAML = str(Path(__file__).resolve().parents[2] / "config" / "coverage.yaml")


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def api_client(mock_db, monkeypatch):
    monkeypatch.setenv("COVERAGE_CONFIG", _COVERAGE_YAML)
    # Patch settings object directly so the endpoint picks up the absolute path
    from app.config import settings

    monkeypatch.setattr(settings, "COVERAGE_CONFIG", _COVERAGE_YAML)

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class TestCoverageGeoJSON:
    def test_returns_feature_collection(self, api_client):
        resp = api_client.get("/api/v1/coverage/geojson")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert isinstance(data["features"], list)

    def test_features_have_quality_property(self, api_client):
        resp = api_client.get("/api/v1/coverage/geojson")
        data = resp.json()
        assert len(data["features"]) > 0, "Should load regions from coverage.yaml"
        for feature in data["features"]:
            assert "quality" in feature["properties"]
            assert "description" in feature["properties"]

    def test_feature_count_matches_config(self, api_client):
        """Feature count matches number of regions in coverage.yaml."""
        config_path = Path(_COVERAGE_YAML)
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
        expected = sum(1 for v in raw.values() if isinstance(v, dict))
        resp = api_client.get("/api/v1/coverage/geojson")
        data = resp.json()
        assert len(data["features"]) == expected

    def test_features_have_geometry(self, api_client):
        """Features with geometry WKT should have non-null geometry."""
        resp = api_client.get("/api/v1/coverage/geojson")
        data = resp.json()
        has_geometry = [f for f in data["features"] if f["geometry"] is not None]
        assert len(has_geometry) > 0, "At least some features should have geometry"
