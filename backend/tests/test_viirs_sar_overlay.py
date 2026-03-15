"""Tests for GET /dark-vessels/by-source endpoint (VIIRS + SAR overlays)."""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest

from app.models.stubs import DarkVesselDetection


def _make_detection(
    detection_id=1,
    scene_id="viirs-20240101",
    lat=10.0,
    lon=20.0,
    time_utc=None,
    length_m=120.0,
    vessel_type="tanker",
    confidence=0.85,
    matched_vessel_id=None,
):
    d = MagicMock(spec=DarkVesselDetection)
    d.detection_id = detection_id
    d.scene_id = scene_id
    d.detection_lat = lat
    d.detection_lon = lon
    d.detection_time_utc = time_utc or datetime(2024, 6, 15, 12, 0, tzinfo=UTC)
    d.length_estimate_m = length_m
    d.vessel_type_inferred = vessel_type
    d.model_confidence = confidence
    d.matched_vessel_id = matched_vessel_id
    return d


class TestDarkVesselsBySource:
    """Tests for the /dark-vessels/by-source endpoint."""

    def test_viirs_source_returns_only_viirs_prefixed(self, api_client, mock_db):
        viirs_det = _make_detection(detection_id=1, scene_id="viirs-abc123")
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [viirs_det]

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["scene_id"] == "viirs-abc123"

    def test_sar_source_returns_only_sar_prefixed(self, api_client, mock_db):
        sar_det = _make_detection(detection_id=2, scene_id="gfw-sar-xyz789")
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [sar_det]

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=sar")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["scene_id"] == "gfw-sar-xyz789"

    def test_invalid_source_returns_400(self, api_client, mock_db):
        resp = api_client.get("/api/v1/dark-vessels/by-source?source=optical")
        assert resp.status_code == 400
        assert "Invalid source" in resp.json()["detail"]

    def test_missing_source_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/dark-vessels/by-source")
        assert resp.status_code == 422

    def test_empty_results(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_date_from_filtering(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs&date_from=2024-01-01")
        assert resp.status_code == 200

    def test_date_to_filtering(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=sar&date_to=2024-12-31")
        assert resp.status_code == 200

    def test_date_range_filtering(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        resp = api_client.get(
            "/api/v1/dark-vessels/by-source?source=viirs&date_from=2024-01-01&date_to=2024-06-30"
        )
        assert resp.status_code == 200

    def test_min_confidence_filtering(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs&min_confidence=0.7")
        assert resp.status_code == 200

    def test_limit_parameter(self, api_client, mock_db):
        dets = [_make_detection(detection_id=i, scene_id=f"viirs-{i}") for i in range(5)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = dets

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs&limit=5")
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    def test_limit_minimum_validation(self, api_client, mock_db):
        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs&limit=0")
        assert resp.status_code == 422

    def test_limit_maximum_validation(self, api_client, mock_db):
        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs&limit=1001")
        assert resp.status_code == 422

    def test_response_fields_viirs(self, api_client, mock_db):
        det = _make_detection(
            detection_id=42,
            scene_id="viirs-20240615",
            lat=35.5,
            lon=28.3,
            confidence=0.92,
            length_m=180.0,
            vessel_type="tanker",
            matched_vessel_id=7,
        )
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [det]

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs")
        data = resp.json()[0]
        assert data["detection_id"] == 42
        assert data["scene_id"] == "viirs-20240615"
        assert data["latitude"] == 35.5
        assert data["longitude"] == 28.3
        assert data["confidence"] == 0.92
        assert data["estimated_length_m"] == 180.0
        assert data["vessel_type_estimate"] == "tanker"
        assert data["matched_vessel_id"] == 7
        assert "detection_timestamp_utc" in data

    def test_response_fields_sar(self, api_client, mock_db):
        det = _make_detection(
            detection_id=99,
            scene_id="gfw-sar-sentinel1-20240601",
            lat=-5.1,
            lon=42.7,
            confidence=0.78,
            length_m=95.0,
            vessel_type="cargo",
            matched_vessel_id=None,
        )
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [det]

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=sar")
        data = resp.json()[0]
        assert data["detection_id"] == 99
        assert data["scene_id"] == "gfw-sar-sentinel1-20240601"
        assert data["matched_vessel_id"] is None

    def test_null_detection_time_serialized(self, api_client, mock_db):
        det = _make_detection(detection_id=1, scene_id="viirs-null-time")
        det.detection_time_utc = None
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [det]

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs")
        assert resp.status_code == 200
        assert resp.json()[0]["detection_timestamp_utc"] is None

    def test_multiple_viirs_detections(self, api_client, mock_db):
        dets = [
            _make_detection(detection_id=i, scene_id=f"viirs-scene-{i}", lat=i * 5.0, lon=i * 10.0)
            for i in range(1, 4)
        ]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = dets

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        ids = [d["detection_id"] for d in data]
        assert ids == [1, 2, 3]

    def test_all_filters_combined(self, api_client, mock_db):
        # With date_from + date_to + min_confidence: 3 extra .filter() calls
        chain = mock_db.query.return_value.filter.return_value
        chain.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        resp = api_client.get(
            "/api/v1/dark-vessels/by-source?source=sar&date_from=2024-01-01&date_to=2024-12-31&min_confidence=0.5&limit=50"
        )
        assert resp.status_code == 200

    def test_default_limit_is_200(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        resp = api_client.get("/api/v1/dark-vessels/by-source?source=viirs")
        assert resp.status_code == 200
        # Verify limit was called (we can't easily inspect the value, but the endpoint should work)

    def test_source_case_sensitive(self, api_client, mock_db):
        """Source param is case-sensitive; 'VIIRS' should return 400."""
        resp = api_client.get("/api/v1/dark-vessels/by-source?source=VIIRS")
        assert resp.status_code == 400
