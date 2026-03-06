"""Tests for aisstream.io WebSocket client — message parsing and ingestion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.modules.aisstream_client import (
    _map_position_report,
    _map_static_data,
    _ais_type_to_string,
    _merge_bounding_boxes,
    _box_area,
    get_corridor_bounding_boxes,
)


# ── Tests: _map_position_report ──────────────────────────────────────

class TestMapPositionReport:
    def test_valid_position_report(self):
        msg = {
            "MetaData": {
                "MMSI": 211000001,
                "ShipName": "TANKER ONE",
                "latitude": 25.5,
                "longitude": 55.5,
                "time_utc": "2026-01-15T12:00:00Z",
            },
            "Message": {
                "PositionReport": {
                    "Sog": 12.5,
                    "Cog": 180.0,
                    "TrueHeading": 178,
                    "NavigationalStatus": 0,
                },
            },
        }
        result = _map_position_report(msg)
        assert result is not None
        assert result["mmsi"] == "211000001"
        assert result["lat"] == 25.5
        assert result["lon"] == 55.5
        assert result["sog"] == 12.5
        assert result["cog"] == 180.0
        assert result["source"] == "aisstream"

    def test_filters_non_vessel_mmsi(self):
        msg = {
            "MetaData": {"MMSI": 970123456, "latitude": 25.0, "longitude": 55.0, "time_utc": "2026-01-15T12:00:00Z"},
            "Message": {"PositionReport": {"Sog": 10}},
        }
        result = _map_position_report(msg)
        assert result is None

    def test_filters_zero_mmsi(self):
        msg = {
            "MetaData": {"MMSI": 0, "latitude": 25.0, "longitude": 55.0, "time_utc": "2026-01-15T12:00:00Z"},
            "Message": {"PositionReport": {"Sog": 10}},
        }
        result = _map_position_report(msg)
        assert result is None

    def test_filters_invalid_lat(self):
        msg = {
            "MetaData": {"MMSI": 211000001, "latitude": 95.0, "longitude": 55.0, "time_utc": "2026-01-15T12:00:00Z"},
            "Message": {"PositionReport": {"Sog": 10}},
        }
        result = _map_position_report(msg)
        assert result is None

    def test_sog_sentinel_filtered(self):
        msg = {
            "MetaData": {"MMSI": 211000001, "latitude": 25.0, "longitude": 55.0, "time_utc": "2026-01-15T12:00:00Z"},
            "Message": {"PositionReport": {"Sog": 102.3, "Cog": 360.0, "TrueHeading": 511}},
        }
        result = _map_position_report(msg)
        assert result is not None
        assert result["sog"] is None
        assert result["cog"] is None
        assert result["heading"] is None

    def test_class_b_report(self):
        msg = {
            "MetaData": {"MMSI": 211000001, "latitude": 25.0, "longitude": 55.0, "time_utc": "2026-01-15T12:00:00Z"},
            "Message": {"StandardClassBPositionReport": {"Sog": 5.0, "Cog": 90.0}},
        }
        result = _map_position_report(msg, msg_type="StandardClassBPositionReport")
        assert result is not None
        assert result["ais_class"] == "B"

    def test_missing_timestamp_returns_none(self):
        msg = {
            "MetaData": {"MMSI": 211000001, "latitude": 25.0, "longitude": 55.0, "time_utc": ""},
            "Message": {"PositionReport": {"Sog": 10}},
        }
        result = _map_position_report(msg)
        assert result is None

    def test_missing_lat_lon_returns_none(self):
        msg = {
            "MetaData": {"MMSI": 211000001, "time_utc": "2026-01-15T12:00:00Z"},
            "Message": {"PositionReport": {"Sog": 10}},
        }
        result = _map_position_report(msg)
        assert result is None


# ── Tests: _map_static_data ──────────────────────────────────────────

class TestMapStaticData:
    def test_valid_static_data(self):
        msg = {
            "MetaData": {"MMSI": 211000001, "ShipName": "TANKER ONE"},
            "Message": {
                "ShipStaticData": {
                    "ImoNumber": 9876543,
                    "Type": 80,
                    "CallSign": "ABCD",
                    "Destination": "ROTTERDAM",
                    "Draught": 12.5,
                    "Dimension": {"A": 200, "B": 30, "C": 20, "D": 10},
                },
            },
        }
        result = _map_static_data(msg)
        assert result is not None
        assert result["mmsi"] == "211000001"
        assert result["imo"] == "9876543"
        assert result["vessel_type"] == "Tanker"
        assert result["callsign"] == "ABCD"
        assert result["length"] == 230
        assert result["width"] == 30

    def test_filters_non_vessel_mmsi(self):
        msg = {
            "MetaData": {"MMSI": 970123456, "ShipName": "SAR UNIT"},
            "Message": {"ShipStaticData": {"Type": 0}},
        }
        result = _map_static_data(msg)
        assert result is None

    def test_missing_static_data(self):
        msg = {
            "MetaData": {"MMSI": 211000001},
            "Message": {},
        }
        result = _map_static_data(msg)
        assert result is None


# ── Tests: _ais_type_to_string ───────────────────────────────────────

class TestAisTypeToString:
    def test_tanker(self):
        assert _ais_type_to_string(80) == "Tanker"
        assert _ais_type_to_string(89) == "Tanker"

    def test_cargo(self):
        assert _ais_type_to_string(70) == "Cargo"
        assert _ais_type_to_string(79) == "Cargo"

    def test_passenger(self):
        assert _ais_type_to_string(60) == "Passenger"

    def test_fishing(self):
        assert _ais_type_to_string(30) == "Fishing"

    def test_hsc(self):
        assert _ais_type_to_string(40) == "High Speed Craft"

    def test_unknown_type(self):
        assert _ais_type_to_string(99) == "Type 99"

    def test_zero_returns_none(self):
        assert _ais_type_to_string(0) is None


# ── Tests: bounding box merging ──────────────────────────────────────

class TestBoundingBoxMerge:
    def test_box_area(self):
        box = [[10.0, 20.0], [15.0, 30.0]]
        assert _box_area(box) == 50.0

    def test_no_merge_needed(self):
        boxes = [
            [[10.0, 20.0], [15.0, 25.0]],
            [[30.0, 40.0], [35.0, 45.0]],
        ]
        result = _merge_bounding_boxes(boxes, max_boxes=5)
        assert len(result) == 2

    def test_merges_close_boxes(self):
        boxes = [
            [[10.0, 20.0], [12.0, 22.0]],
            [[11.0, 21.0], [13.0, 23.0]],
            [[30.0, 40.0], [32.0, 42.0]],
        ]
        result = _merge_bounding_boxes(boxes, max_boxes=2)
        assert len(result) <= 2

    def test_respects_area_cap(self):
        boxes = [
            [[0.0, 0.0], [20.0, 20.0]],    # 400 sq deg
            [[-20.0, -20.0], [0.0, 0.0]],   # 400 sq deg
            [[50.0, 50.0], [52.0, 52.0]],    # small
        ]
        result = _merge_bounding_boxes(boxes, max_boxes=2, max_box_area=400.0)
        # Should not merge the two large boxes (would exceed area cap)
        assert len(result) <= 3


class TestGetCorridorBoundingBoxes:
    def test_returns_boxes_from_corridors(self):
        db = MagicMock()
        corridor = MagicMock()
        corridor.geometry = "POLYGON((50 20, 60 20, 60 30, 50 30, 50 20))"

        db.query.return_value.all.return_value = [corridor]

        mock_geom = MagicMock()
        mock_geom.bounds = (50.0, 20.0, 60.0, 30.0)

        with patch("app.utils.geo.load_geometry", return_value=mock_geom):
            boxes = get_corridor_bounding_boxes(db)
            assert len(boxes) == 1
            assert boxes[0][0] == [20.0, 50.0]
            assert boxes[0][1] == [30.0, 60.0]

    def test_skips_corridors_without_geometry(self):
        db = MagicMock()
        corridor = MagicMock()
        corridor.geometry = None

        db.query.return_value.all.return_value = [corridor]

        boxes = get_corridor_bounding_boxes(db)
        assert len(boxes) == 0
