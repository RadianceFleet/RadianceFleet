"""Tests for 5C: AIS Source Timestamp Priority.

Ensures satellite AIS (30-60min old) never overwrites newer terrestrial positions.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

import pytest


def _make_mock_vessel(vessel_id=1, mmsi="211234567", last_ais=None):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi
    v.deadweight = None
    v.name = None
    v.flag = None
    v.flag_risk_category = None
    v.ais_class = None
    v.callsign = None
    v.last_ais_received_utc = last_ais
    return v


def _make_mock_db(vessel):
    """Mock DB where query().filter().first() returns vessel, no near-dup, no prev point."""
    db = MagicMock()
    # First .filter().first() → vessel lookup
    # Subsequent .filter().first() → near_dup check (None = no dup)
    # .filter().order_by().first() → prev_point (None)
    db.query.return_value.filter.return_value.first.return_value = vessel
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    return db


class TestSourceTimestampPriority:
    def test_newer_terrestrial_overwrites_older_satellite(self):
        """Newer terrestrial data should update vessel.last_ais_received_utc."""
        from app.modules.ingest import ingest_ais_csv

        old_ts = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
        new_ts = datetime(2026, 3, 1, 11, 0, 0, tzinfo=timezone.utc)

        vessel = _make_mock_vessel(last_ais=old_ts)
        db = _make_mock_db(vessel)

        csv = (
            "mmsi,timestamp,lat,lon,sog,cog,source\n"
            "211234567,2026-03-01T11:00:00Z,55.0,12.0,10.0,180.0,terrestrial\n"
        )
        ingest_ais_csv(io.BytesIO(csv.encode()), db)

        assert vessel.last_ais_received_utc == new_ts

    def test_older_satellite_does_not_overwrite_newer_terrestrial(self):
        """Satellite data with older source_timestamp must NOT update vessel.last_ais_received_utc."""
        from app.modules.ingest import ingest_ais_csv

        newer_ts = datetime(2026, 3, 1, 11, 0, 0, tzinfo=timezone.utc)
        older_source_ts = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)

        vessel = _make_mock_vessel(last_ais=newer_ts)
        db = _make_mock_db(vessel)

        # source_timestamp is when the AIS message was generated (older)
        csv = (
            "mmsi,timestamp,lat,lon,sog,cog,source,source_timestamp\n"
            "211234567,2026-03-01T10:00:00Z,55.0,12.0,8.0,90.0,satellite,2026-03-01T10:00:00Z\n"
        )
        ingest_ais_csv(io.BytesIO(csv.encode()), db)

        # last_ais_received_utc should remain the newer terrestrial timestamp
        assert vessel.last_ais_received_utc == newer_ts

    def test_ais_point_created_even_when_position_not_updated(self):
        """AISPoint record should always be created for historical track, even with stale data."""
        from app.modules.ingest import ingest_ais_csv

        newer_ts = datetime(2026, 3, 1, 11, 0, 0, tzinfo=timezone.utc)

        vessel = _make_mock_vessel(last_ais=newer_ts)
        db = _make_mock_db(vessel)

        # Stale satellite data with old source_timestamp
        csv = (
            "mmsi,timestamp,lat,lon,sog,cog,source,source_timestamp\n"
            "211234567,2026-03-01T10:00:00Z,55.0,12.0,8.0,90.0,satellite,2026-03-01T10:00:00Z\n"
        )
        ingest_ais_csv(io.BytesIO(csv.encode()), db)

        # db.add should still be called (AISPoint and/or AISObservation records)
        assert db.add.called

    def test_none_source_timestamp_treated_as_point_timestamp(self):
        """When source_timestamp is absent, use point timestamp (backwards compatible)."""
        from app.modules.ingest import ingest_ais_csv

        old_ts = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        point_ts = datetime(2026, 3, 1, 11, 0, 0, tzinfo=timezone.utc)

        vessel = _make_mock_vessel(last_ais=old_ts)
        db = _make_mock_db(vessel)

        csv = (
            "mmsi,timestamp,lat,lon,sog,cog,source\n"
            "211234567,2026-03-01T11:00:00Z,55.0,12.0,10.0,180.0,terrestrial\n"
        )
        ingest_ais_csv(io.BytesIO(csv.encode()), db)

        assert vessel.last_ais_received_utc == point_ts


class TestSourceTimestampOnAISPoint:
    def test_source_timestamp_stored_on_ais_point(self):
        """source_timestamp_utc should be stored on the created AISPoint."""
        from app.modules.ingest import _create_ais_point

        source_ts = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
        vessel = _make_mock_vessel()
        db = _make_mock_db(vessel)
        # No near_dup
        db.query.return_value.filter.return_value.first.return_value = None

        row = {
            "mmsi": "211234567",
            "timestamp": "2026-03-01T10:30:00Z",
            "lat": 55.0,
            "lon": 12.0,
            "sog": 10.0,
            "cog": 180.0,
            "source": "satellite",
        }
        point = _create_ais_point(db, vessel, row, source_timestamp=source_ts)

        assert point is not None
        assert point != "replaced"
        assert point.source_timestamp_utc == source_ts

    def test_none_source_timestamp_on_ais_point(self):
        """When no source_timestamp, the field should be None."""
        from app.modules.ingest import _create_ais_point

        vessel = _make_mock_vessel()
        db = _make_mock_db(vessel)
        db.query.return_value.filter.return_value.first.return_value = None

        row = {
            "mmsi": "211234567",
            "timestamp": "2026-03-01T10:30:00Z",
            "lat": 55.0,
            "lon": 12.0,
            "sog": 10.0,
            "cog": 180.0,
            "source": "terrestrial",
        }
        point = _create_ais_point(db, vessel, row)

        assert point is not None
        assert point.source_timestamp_utc is None
