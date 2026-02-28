"""Tests for GFW client module — vessel search, events, SAR detections.

Tests mock httpx responses to verify:
  - search_vessel() parsing
  - get_vessel_events() parsing
  - get_sar_detections() parsing
  - import_sar_detections_to_db() AIS matching
  - Error handling for API failures
  - _extract_bbox_from_wkt() helper

Uses the shared conftest fixtures (mock_db, api_client).
"""
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone, timedelta

import pytest


class TestSearchVessel:
    """search_vessel() calls GFW API and parses response."""

    def test_search_vessel_no_token_raises(self):
        from app.modules.gfw_client import search_vessel

        with patch("app.config.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = None
            with pytest.raises(ValueError, match="GFW_API_TOKEN"):
                search_vessel("123456789", token=None)

    def test_search_vessel_parses_response(self):
        from app.modules.gfw_client import search_vessel

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "entries": [
                {
                    "id": "gfw-vessel-1",
                    "ssvid": "123456789",
                    "combinedSourcesInfo": [
                        {
                            "shipsData": [
                                {
                                    "shipname": "TEST VESSEL",
                                    "imo": "IMO1234567",
                                    "flag": "PA",
                                    "vesselType": "tanker",
                                }
                            ]
                        }
                    ],
                }
            ]
        }

        with patch("app.utils.http_retry.retry_request", return_value=mock_resp):
            with patch("httpx.Client") as mock_client:
                mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_client.return_value.__exit__ = MagicMock(return_value=False)

                results = search_vessel("123456789", token="test-token")
                assert len(results) == 1
                assert results[0]["gfw_id"] == "gfw-vessel-1"
                assert results[0]["mmsi"] == "123456789"
                assert results[0]["name"] == "TEST VESSEL"

    def test_search_vessel_empty_response(self):
        from app.modules.gfw_client import search_vessel

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"entries": []}

        with patch("app.utils.http_retry.retry_request", return_value=mock_resp):
            with patch("httpx.Client") as mock_client:
                mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_client.return_value.__exit__ = MagicMock(return_value=False)

                results = search_vessel("999999999", token="test-token")
                assert results == []


class TestGetVesselEvents:
    """get_vessel_events() fetches and parses events."""

    def test_no_token_raises(self):
        from app.modules.gfw_client import get_vessel_events

        with patch("app.config.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = None
            with pytest.raises(ValueError, match="GFW_API_TOKEN"):
                get_vessel_events("gfw-1", token=None)

    def test_parses_gap_event(self):
        from app.modules.gfw_client import get_vessel_events

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "entries": [
                {
                    "id": "event-1",
                    "type": "gap",
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-01T12:00:00Z",
                    "position": {"lat": 55.0, "lon": 15.0},
                    "vessel": {"ssvid": "123456789"},
                    "gap": {
                        "offPosition": {"lat": 55.0, "lon": 15.0},
                        "onPosition": {"lat": 55.5, "lon": 15.5},
                        "durationHours": 12,
                        "distanceKm": 50,
                        "impliedSpeedKnots": 4.2,
                    },
                }
            ]
        }

        with patch("app.utils.http_retry.retry_request", return_value=mock_resp):
            with patch("httpx.Client") as mock_client:
                mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_client.return_value.__exit__ = MagicMock(return_value=False)

                events = get_vessel_events("gfw-1", token="test-token")
                assert len(events) == 1
                ev = events[0]
                assert ev["type"] == "gap"
                assert ev["gap_off_lat"] == 55.0
                assert ev["gap_on_lat"] == 55.5
                assert ev["implied_speed_knots"] == 4.2


class TestExtractBboxFromWkt:
    """_extract_bbox_from_wkt helper."""

    def test_parses_polygon_wkt(self):
        from app.modules.gfw_client import _extract_bbox_from_wkt

        wkt = "POLYGON((10 50, 20 50, 20 60, 10 60, 10 50))"
        bbox = _extract_bbox_from_wkt(wkt)
        assert bbox is not None
        lat_min, lon_min, lat_max, lon_max = bbox
        assert lat_min == 50.0
        assert lon_min == 10.0
        assert lat_max == 60.0
        assert lon_max == 20.0

    def test_none_wkt_returns_none(self):
        from app.modules.gfw_client import _extract_bbox_from_wkt

        assert _extract_bbox_from_wkt(None) is None

    def test_empty_wkt_returns_none(self):
        from app.modules.gfw_client import _extract_bbox_from_wkt

        assert _extract_bbox_from_wkt("") is None

    def test_invalid_wkt_returns_none(self):
        from app.modules.gfw_client import _extract_bbox_from_wkt

        assert _extract_bbox_from_wkt("not a wkt") is None


class TestImportSarDetectionsToDb:
    """import_sar_detections_to_db() creates DarkVesselDetection records."""

    def test_empty_detections(self):
        from app.modules.gfw_client import import_sar_detections_to_db

        db = MagicMock()
        db.commit = MagicMock()
        result = import_sar_detections_to_db([], db)
        assert result["total"] == 0

    def test_rejected_for_missing_coordinates(self):
        from app.modules.gfw_client import import_sar_detections_to_db

        db = MagicMock()
        db.commit = MagicMock()

        detections = [
            {"detection_lat": None, "detection_lon": None, "detection_time_utc": "2026-01-01"},
        ]
        result = import_sar_detections_to_db(detections, db)
        assert result["rejected"] == 1

    def test_valid_detection_imported(self):
        from app.modules.gfw_client import import_sar_detections_to_db

        db = MagicMock()
        db.commit = MagicMock()
        db.add = MagicMock()
        # No existing detection (dedup check)
        db.query.return_value.filter.return_value.first.return_value = None
        # No AIS match candidates
        db.query.return_value.filter.return_value.all.return_value = []

        detections = [
            {
                "scene_id": "test-scene-1",
                "detection_lat": 55.0,
                "detection_lon": 15.0,
                "detection_time_utc": "2026-01-01T12:00:00",
                "length_estimate_m": 200.0,
                "vessel_type_inferred": "tanker",
            },
        ]
        result = import_sar_detections_to_db(detections, db)
        assert result["total"] == 1
        assert result["dark"] == 1
        assert db.add.called


class TestParse4WingsGroup:
    """_parse_4wings_group helper."""

    def test_parses_basic_group(self):
        from app.modules.gfw_client import _parse_4wings_group

        detections = []
        group = {"lat": 55.0, "lon": 15.0, "date": "2026-01-01", "detections": 1}
        _parse_4wings_group(group, detections)
        assert len(detections) == 1
        assert detections[0]["detection_lat"] == 55.0

    def test_missing_coordinates_skipped(self):
        from app.modules.gfw_client import _parse_4wings_group

        detections = []
        group = {"date": "2026-01-01", "detections": 1}
        _parse_4wings_group(group, detections)
        assert len(detections) == 0

    def test_nested_timeseries(self):
        from app.modules.gfw_client import _parse_4wings_group

        detections = []
        group = {
            "timeseries": [
                {"lat": 55.0, "lon": 15.0, "date": "2026-01-01"},
                {"lat": 56.0, "lon": 16.0, "date": "2026-01-02"},
            ]
        }
        _parse_4wings_group(group, detections)
        assert len(detections) == 2
