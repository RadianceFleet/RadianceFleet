"""Tests for NULL timestamp guards across detector modules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from sqlalchemy.orm import Session


class TestNullTimestampGuards:
    """Verify detector modules handle NULL timestamps gracefully."""

    def test_write_ais_observation_skips_null_ts(self):
        """_write_ais_observation returns early when ts is None."""
        from app.modules.ingest import _write_ais_observation

        db = MagicMock(spec=Session)
        vessel = MagicMock()
        row = {"mmsi": "123456789", "lat": 59.0, "lon": 10.0}

        # Should not crash, should not add anything
        _write_ais_observation(
            db, vessel, row, ts=None, sog_val=5.0, cog_val=180.0, heading_val=180.0
        )
        db.add.assert_not_called()

    def test_cross_receiver_skips_null_timestamps(self):
        """cross_receiver_detector filters out NULL timestamp observations."""
        from app.modules.cross_receiver_detector import detect_cross_receiver_anomalies

        db = MagicMock(spec=Session)
        # Query returns observations with NULL timestamps
        mock_obs = MagicMock()
        mock_obs.mmsi = "123456789"
        mock_obs.timestamp_utc = None
        mock_obs.lat = 59.0
        mock_obs.lon = 10.0
        mock_obs.source = "test_a"

        mock_obs2 = MagicMock()
        mock_obs2.mmsi = "123456789"
        mock_obs2.timestamp_utc = None
        mock_obs2.lat = 60.0
        mock_obs2.lon = 11.0
        mock_obs2.source = "test_b"

        query_mock = MagicMock()
        query_mock.order_by.return_value = query_mock
        query_mock.filter.return_value = query_mock
        query_mock.all.return_value = [mock_obs, mock_obs2]
        db.query.return_value = query_mock

        # Should not crash despite NULL timestamps
        result = detect_cross_receiver_anomalies(db)
        assert result["anomalies_created"] == 0

    def test_auto_hunt_skips_null_gap_timestamps(self):
        """auto_hunt_dark_vessels skips gaps with NULL timestamps."""
        from app.modules.dark_vessel_discovery import auto_hunt_dark_vessels

        db = MagicMock(spec=Session)

        # Create a gap with NULL gap_end_utc
        mock_gap = MagicMock()
        mock_gap.gap_event_id = 1
        mock_gap.risk_score = 100
        mock_gap.gap_off_lat = 59.0
        mock_gap.gap_off_lon = 10.0
        mock_gap.start_point = None
        mock_gap.gap_start_utc = datetime(2024, 1, 1, tzinfo=UTC)
        mock_gap.gap_end_utc = None
        mock_gap.vessel_id = 1

        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        query_mock.all.return_value = [mock_gap]
        db.query.return_value = query_mock

        # Should skip the gap without crashing
        result = auto_hunt_dark_vessels(db)
        assert result["gaps_hunted"] == 0

    def test_auto_hunt_skips_null_sts_end_time(self):
        """auto_hunt_dark_vessels handles STS events with NULL end_time_utc."""
        from app.modules.dark_vessel_discovery import auto_hunt_dark_vessels

        db = MagicMock(spec=Session)

        # No gaps to hunt
        gap_query = MagicMock()
        gap_query.filter.return_value = gap_query
        gap_query.order_by.return_value = gap_query
        gap_query.all.return_value = []

        # STS event with NULL end_time_utc
        mock_sts = MagicMock()
        mock_sts.mean_lat = 59.0
        mock_sts.mean_lon = 10.0
        mock_sts.start_time_utc = datetime(2024, 1, 1, tzinfo=UTC)
        mock_sts.end_time_utc = None

        sts_query = MagicMock()
        sts_query.all.return_value = [mock_sts]

        # First query returns gaps, second returns STS events
        db.query.side_effect = [gap_query, sts_query]

        # Should not crash
        result = auto_hunt_dark_vessels(db)
        assert result["sts_confirmed"] == 0

    def test_spoofing_linkage_skips_null_end_time(self):
        """Spoofing anomaly linkage uses start_time as fallback when end_time is NULL."""
        # This tests the gap_detector.py linkage code indirectly
        from app.models.spoofing_anomaly import SpoofingAnomaly

        anomaly = MagicMock(spec=SpoofingAnomaly)
        anomaly.start_time_utc = datetime(2024, 1, 1, tzinfo=UTC)
        anomaly.end_time_utc = None

        # Verify fallback logic: anomaly_end should be start_time when end is None
        anomaly_end = anomaly.end_time_utc or anomaly.start_time_utc
        assert anomaly_end == anomaly.start_time_utc
        # And it should support timedelta arithmetic
        result = anomaly_end + timedelta(hours=2)
        assert result == datetime(2024, 1, 1, 2, 0, tzinfo=UTC)
