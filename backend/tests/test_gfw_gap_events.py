"""Tests for GFW gap event import and SAR corridor sweep."""
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest


# --- Helpers ---

def _make_vessel(vessel_id, mmsi, merged_into=None):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi
    v.merged_into_vessel_id = merged_into
    return v


def _make_gap_event(vessel_id, gap_start, gap_off_lat=None, gap_off_lon=None):
    g = MagicMock()
    g.vessel_id = vessel_id
    g.gap_start_utc = gap_start
    g.gap_end_utc = gap_start + timedelta(hours=12)
    g.gap_off_lat = gap_off_lat
    g.gap_off_lon = gap_off_lon
    return g


def _make_gfw_event(start_iso, end_iso, off_lat=60.0, off_lon=25.0, on_lat=61.0, on_lon=26.0):
    return {
        "id": f"evt-{start_iso}",
        "type": "gap",
        "start": start_iso,
        "end": end_iso,
        "position": {"lat": off_lat, "lon": off_lon},
        "vessel": {"ssvid": "636017000"},
        "gap": {
            "offPosition": {"lat": off_lat, "lon": off_lon},
            "onPosition": {"lat": on_lat, "lon": on_lon},
            "durationHours": 24.0,
            "distanceKm": 150.0,
            "impliedSpeedKnots": 3.4,
        },
        "regions": {},
        "distances": {},
    }


# --- GFW gap event type parsing ---

class TestGFWGapEventType:
    def test_gap_dataset_constant_exists(self):
        from app.modules.gfw_client import _GAP_EVENTS_DATASET
        assert _GAP_EVENTS_DATASET == "public-global-gaps-events:latest"

    @patch("app.utils.http_retry.retry_request")
    def test_get_vessel_events_gap_type(self, mock_retry):
        from app.modules.gfw_client import get_vessel_events

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "entries": [_make_gfw_event("2025-12-01T00:00:00Z", "2025-12-02T00:00:00Z")],
        }
        mock_retry.return_value = mock_resp

        events = get_vessel_events("test-gfw-id", token="test", event_types=["gap"])
        assert len(events) == 1
        assert events[0]["gap_off_lat"] == 60.0
        assert events[0]["gap_on_lon"] == 26.0
        assert events[0]["ssvid"] == "636017000"


# --- Import idempotency ---

class TestImportIdempotent:
    @patch("app.modules.gfw_client.get_vessel_events")
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_import_idempotent_rerun(self, mock_sleep, mock_search, mock_events):
        """Run import twice — second run should import 0 new events (dedup)."""
        from app.modules.gfw_client import import_gfw_gap_events

        mock_search.return_value = [{"gfw_id": "gfw-123", "mmsi": "636017000"}]
        mock_events.return_value = [
            _make_gfw_event("2025-12-01T00:00:00Z", "2025-12-02T00:00:00Z"),
        ]

        vessel = _make_vessel(1, "636017000")

        db = MagicMock()
        # First query: vessels list
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [vessel]
        # Dedup query: no existing on first run
        db.query.return_value.filter.return_value.first.return_value = None

        result1 = import_gfw_gap_events(db, "2025-12-01", "2025-12-31", token="test")
        assert result1["imported"] >= 1

        # Second run: dedup query returns existing
        db.query.return_value.filter.return_value.first.return_value = MagicMock()
        result2 = import_gfw_gap_events(db, "2025-12-01", "2025-12-31", token="test")
        assert result2["skipped_dup"] >= 1


# --- Partial failure and resume ---

class TestPartialFailureResume:
    @patch("app.modules.gfw_client.search_vessel")
    @patch("time.sleep")
    def test_import_partial_failure_resume(self, mock_sleep, mock_search):
        """API failure on 3rd consecutive call → partial=True + last_vessel_id."""
        from app.modules.gfw_client import import_gfw_gap_events

        mock_search.side_effect = Exception("429 rate limited")

        vessels = [_make_vessel(i, f"63601700{i}") for i in range(5)]

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = vessels

        result = import_gfw_gap_events(db, "2025-12-01", "2025-12-31", token="test")
        assert result["partial"] is True


# --- SAR sweep ---

class TestSweepCorridorsSAR:
    @patch("app.modules.gfw_client.import_sar_detections_to_db")
    @patch("app.modules.gfw_client.get_sar_detections")
    def test_sweep_queries_corridors(self, mock_sar, mock_import):
        """Sweep should query each corridor with geometry."""
        from app.modules.gfw_client import sweep_corridors_sar

        mock_sar.return_value = [{"scene_id": "s1", "detection_lat": 60.0, "detection_lon": 25.0}]
        mock_import.return_value = {"total": 1, "dark": 1, "matched": 0, "rejected": 0}

        corridor = MagicMock()
        corridor.name = "Test Corridor"
        corridor.geometry = "POLYGON((20 55, 30 55, 30 65, 20 65, 20 55))"
        corridor.corridor_id = 1

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [corridor]

        result = sweep_corridors_sar(db, "2025-12-01", "2025-12-31", token="test")
        assert result["corridors_queried"] == 1
        assert result["dark_vessels"] == 1


# --- Bbox extraction ---

class TestExtractBbox:
    def test_extract_bbox_from_wkt(self):
        from app.modules.gfw_client import _extract_bbox_from_wkt

        wkt = "POLYGON((20 55, 30 55, 30 65, 20 65, 20 55))"
        bbox = _extract_bbox_from_wkt(wkt)
        assert bbox is not None
        assert bbox == (55.0, 20.0, 65.0, 30.0)  # (lat_min, lon_min, lat_max, lon_max)

    def test_extract_bbox_none(self):
        from app.modules.gfw_client import _extract_bbox_from_wkt
        assert _extract_bbox_from_wkt(None) is None
        assert _extract_bbox_from_wkt("") is None
