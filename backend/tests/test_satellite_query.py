"""Tests for satellite check generation module.

Tests the prepare_satellite_check() function and its helpers:
  - compute_bounding_box
  - build_copernicus_url
  - SatelliteCheck record creation
  - "Already exists" dedup
  - Alert not found handling

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

import pytest


class TestComputeBoundingBox:
    """Test satellite_query.compute_bounding_box helper."""

    def test_bbox_returns_four_keys(self):
        from app.modules.satellite_query import compute_bounding_box

        bbox = compute_bounding_box(55.0, 15.0, 50.0)
        assert "min_lon" in bbox
        assert "min_lat" in bbox
        assert "max_lon" in bbox
        assert "max_lat" in bbox

    def test_bbox_symmetric_latitude(self):
        from app.modules.satellite_query import compute_bounding_box

        bbox = compute_bounding_box(55.0, 15.0, 60.0)
        center_lat = 55.0
        lat_delta = 60.0 / 60.0  # radius_nm / 60
        assert abs(bbox["min_lat"] - (center_lat - lat_delta)) < 0.001
        assert abs(bbox["max_lat"] - (center_lat + lat_delta)) < 0.001

    def test_bbox_center_is_inside(self):
        from app.modules.satellite_query import compute_bounding_box

        bbox = compute_bounding_box(36.5, 22.8, 30.0)
        assert bbox["min_lat"] < 36.5 < bbox["max_lat"]
        assert bbox["min_lon"] < 22.8 < bbox["max_lon"]

    def test_bbox_zero_radius(self):
        from app.modules.satellite_query import compute_bounding_box

        bbox = compute_bounding_box(55.0, 15.0, 0.0)
        assert abs(bbox["min_lat"] - 55.0) < 0.001
        assert abs(bbox["max_lat"] - 55.0) < 0.001


class TestBuildCopernicusUrl:
    """Test satellite_query.build_copernicus_url helper."""

    def test_url_contains_coordinates(self):
        from app.modules.satellite_query import build_copernicus_url

        url = build_copernicus_url(55.0, 15.0, "2026-01-01", "2026-01-02")
        assert "55.0000" in url
        assert "15.0000" in url

    def test_url_contains_dates(self):
        from app.modules.satellite_query import build_copernicus_url

        url = build_copernicus_url(55.0, 15.0, "2026-01-01", "2026-01-02")
        assert "2026-01-01" in url
        assert "2026-01-02" in url

    def test_url_starts_with_copernicus_base(self):
        from app.modules.satellite_query import build_copernicus_url, COPERNICUS_BROWSER_BASE

        url = build_copernicus_url(55.0, 15.0, "2026-01-01", "2026-01-02")
        assert url.startswith(COPERNICUS_BROWSER_BASE)


class TestPrepareSatelliteCheck:
    """Test prepare_satellite_check — the main function."""

    def test_alert_not_found_returns_error(self):
        """When alert doesn't exist, returns {error: ...}."""
        from app.modules.satellite_query import prepare_satellite_check

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = prepare_satellite_check(99999, db)
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_existing_check_returns_message(self):
        """When a SatelliteCheck already exists, returns existing ID."""
        from app.modules.satellite_query import prepare_satellite_check

        db = MagicMock()
        gap = MagicMock()
        gap.gap_event_id = 1
        gap.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0)
        gap.gap_end_utc = datetime(2026, 1, 15, 18, 0, 0)

        existing_check = MagicMock()
        existing_check.sat_check_id = 42

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # AISGapEvent query
                result.filter.return_value.first.return_value = gap
            elif call_count[0] == 2:
                # SatelliteCheck existing query
                result.filter.return_value.first.return_value = existing_check
            return result

        db.query.side_effect = query_side_effect

        result = prepare_satellite_check(1, db)
        assert "message" in result
        assert "already exists" in result["message"].lower()
        assert result["sat_check_id"] == 42

    def test_new_check_creates_record(self):
        """When no existing check, creates new SatelliteCheck and returns full data."""
        from app.modules.satellite_query import prepare_satellite_check

        db = MagicMock()

        gap = MagicMock()
        gap.gap_event_id = 1
        gap.vessel_id = 10
        gap.gap_start_utc = datetime(2026, 1, 15, 12, 0, 0)
        gap.gap_end_utc = datetime(2026, 1, 15, 18, 0, 0)
        gap.max_plausible_distance_nm = 50.0
        gap.start_point_id = 100
        gap.end_point_id = 101
        gap.corridor = MagicMock()
        gap.corridor.name = "Baltic Transit"
        gap.vessel = MagicMock()
        gap.vessel.ais_source = "dma"

        start_pt = MagicMock()
        start_pt.lat = 55.0
        start_pt.lon = 15.0

        end_pt = MagicMock()
        end_pt.lat = 55.5
        end_pt.lon = 15.5

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = gap
            elif call_count[0] == 2:
                result.filter.return_value.first.return_value = None  # No existing check
            else:
                result.filter.return_value.first.return_value = None
            return result

        db.query.side_effect = query_side_effect
        db.get = MagicMock(side_effect=lambda model, pid: start_pt if pid == 100 else end_pt)
        db.add = MagicMock()
        db.commit = MagicMock()

        result = prepare_satellite_check(1, db)
        assert "copernicus_url" in result
        assert "bounding_box" in result
        assert "time_window" in result
        assert "sensor_preference" in result

    def test_api_endpoint_calls_function(self, api_client, mock_db):
        """POST /api/v1/alerts/{id}/satellite-check calls the function."""
        with patch(
            "app.modules.satellite_query.prepare_satellite_check",
            return_value={"status": "prepared", "copernicus_url": "https://example.com"},
        ):
            resp = api_client.post("/api/v1/alerts/1/satellite-check")
            assert resp.status_code == 200


class TestSatelliteCheckModel:
    """Verify SatelliteCheck model structure."""

    def test_satellite_check_has_required_fields(self):
        from app.models.satellite_check import SatelliteCheck

        columns = {c.name for c in SatelliteCheck.__table__.columns}
        assert "sat_check_id" in columns
        assert "gap_event_id" in columns
        assert "provider" in columns
        assert "review_status" in columns
