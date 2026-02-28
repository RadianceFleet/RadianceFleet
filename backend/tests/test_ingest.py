"""Ingest module tests — VesselHistory deduplication, data integrity fixes."""
import io
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock


def test_vessel_history_dedup_within_24h():
    """Calling _track_field_change twice with same data within 24h creates only one record."""
    from app.modules.ingest import _track_field_change

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.mmsi = "211234567"

    # First call: no existing record -> should add
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    _track_field_change(mock_db, vessel, "flag", "PA", "LR", datetime(2026, 1, 15), "csv")
    assert mock_db.add.called, "First call should add a record"

    # Second call: simulate existing record found -> should skip (dedup)
    mock_db2 = MagicMock()
    mock_db2.query.return_value.filter.return_value.first.return_value = MagicMock()

    _track_field_change(mock_db2, vessel, "flag", "PA", "LR", datetime(2026, 1, 15, 6, 0), "csv")
    assert not mock_db2.add.called, "Second call should skip (dedup)"


def test_track_field_change_skips_none_values():
    """_track_field_change returns early when old_val or new_val is None."""
    from app.modules.ingest import _track_field_change

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.mmsi = "211234567"

    mock_db = MagicMock()

    _track_field_change(mock_db, vessel, "flag", None, "LR", datetime(2026, 1, 15), "csv")
    assert not mock_db.add.called, "Should skip when old_val is None"

    _track_field_change(mock_db, vessel, "flag", "PA", None, datetime(2026, 1, 15), "csv")
    assert not mock_db.add.called, "Should skip when new_val is None"


def test_negative_sog_rejected():
    from app.modules.normalize import validate_ais_row
    row = {"mmsi": "211234567", "lat": 55.0, "lon": 12.0, "sog": -1.0, "timestamp_utc": "2025-06-01T00:00:00Z"}
    error = validate_ais_row(row)
    assert error is not None
    assert "Negative SOG" in error


def test_boundary_lat_valid():
    from app.modules.normalize import validate_ais_row
    row = {"mmsi": "211234567", "lat": 90.0, "lon": 180.0, "sog": 5.0, "timestamp_utc": "2025-06-01T00:00:00Z"}
    error = validate_ais_row(row)
    assert error is None


def test_lat_out_of_bounds():
    from app.modules.normalize import validate_ais_row
    row = {"mmsi": "211234567", "lat": 91.0, "lon": 12.0, "sog": 5.0, "timestamp_utc": "2025-06-01T00:00:00Z"}
    error = validate_ais_row(row)
    assert error is not None
    assert "Latitude out of range" in error


def test_boundary_negative_coords_valid():
    from app.modules.normalize import validate_ais_row
    row = {"mmsi": "211234567", "lat": -90.0, "lon": -180.0, "sog": 0.0, "timestamp_utc": "2025-06-01T00:00:00Z"}
    error = validate_ais_row(row)
    assert error is None


def test_track_field_change_skips_same_value():
    """_track_field_change skips when old and new values are identical (case-insensitive)."""
    from app.modules.ingest import _track_field_change

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.mmsi = "211234567"

    mock_db = MagicMock()

    _track_field_change(mock_db, vessel, "name", "TANKER ONE", "tanker one", datetime(2026, 1, 15), "csv")
    assert not mock_db.add.called, "Same value (case-insensitive) should not create a record"


# =====================================================================
# 1.2: SOG/COG default to None, not 0
# =====================================================================

class TestSOGCOGNoneDefaults:
    def test_create_ais_point_missing_sog_is_none(self):
        """When SOG is missing from row, it should be stored as None, not 0."""
        from app.modules.ingest import _create_ais_point

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "211234567"
        vessel.deadweight = None

        mock_db = MagicMock()
        # No near_dup found, no prev_point
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        row = {
            "mmsi": "211234567",
            "lat": 55.0,
            "lon": 12.0,
            "timestamp_utc": "2025-06-01T00:00:00Z",
            # sog and cog deliberately missing
        }
        result = _create_ais_point(mock_db, vessel, row)
        assert result is not None
        assert result != "replaced"
        # Check the AISPoint was created with None sog/cog
        added_point = mock_db.add.call_args_list[0][0][0]
        assert added_point.sog is None, f"SOG should be None, got {added_point.sog}"
        assert added_point.cog is None, f"COG should be None, got {added_point.cog}"

    def test_create_ais_point_explicit_none_sog(self):
        """When SOG is explicitly None, it should be stored as None."""
        from app.modules.ingest import _create_ais_point

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "211234567"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        row = {
            "mmsi": "211234567",
            "lat": 55.0,
            "lon": 12.0,
            "sog": None,
            "cog": None,
            "timestamp_utc": "2025-06-01T00:00:00Z",
        }
        result = _create_ais_point(mock_db, vessel, row)
        assert result is not None
        added_point = mock_db.add.call_args_list[0][0][0]
        assert added_point.sog is None
        assert added_point.cog is None

    def test_sog_delta_none_when_sog_missing(self):
        """sog_delta should be None when current sog is None, even if prev_point has sog."""
        from app.modules.ingest import _create_ais_point

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "211234567"

        prev_point = MagicMock()
        prev_point.sog = 10.0
        prev_point.cog = 180.0

        mock_db = MagicMock()
        # No near_dup, but prev_point exists
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = prev_point

        row = {
            "mmsi": "211234567",
            "lat": 55.0,
            "lon": 12.0,
            "sog": None,
            "cog": None,
            "timestamp_utc": "2025-06-01T00:00:00Z",
        }
        result = _create_ais_point(mock_db, vessel, row)
        added_point = mock_db.add.call_args_list[0][0][0]
        assert added_point.sog_delta is None, "sog_delta should be None when current sog is None"
        assert added_point.cog_delta is None, "cog_delta should be None when current cog is None"


# =====================================================================
# 1.1: Heading 511 -> None in CSV ingest path
# =====================================================================

class TestHeading511InIngest:
    def test_heading_511_set_to_none(self):
        """Heading=511 should be stored as None in _create_ais_point."""
        from app.modules.ingest import _create_ais_point

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "211234567"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        row = {
            "mmsi": "211234567",
            "lat": 55.0,
            "lon": 12.0,
            "sog": 10.0,
            "cog": 180.0,
            "heading": 511,
            "timestamp_utc": "2025-06-01T00:00:00Z",
        }
        result = _create_ais_point(mock_db, vessel, row)
        added_point = mock_db.add.call_args_list[0][0][0]
        assert added_point.heading is None, f"Heading 511 should be None, got {added_point.heading}"

    def test_heading_normal_value_preserved(self):
        """Normal heading value should be preserved."""
        from app.modules.ingest import _create_ais_point

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "211234567"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        row = {
            "mmsi": "211234567",
            "lat": 55.0,
            "lon": 12.0,
            "sog": 10.0,
            "cog": 180.0,
            "heading": 200.0,
            "timestamp_utc": "2025-06-01T00:00:00Z",
        }
        result = _create_ais_point(mock_db, vessel, row)
        added_point = mock_db.add.call_args_list[0][0][0]
        assert added_point.heading == 200.0


# =====================================================================
# 1.3: Timestamp rejection (not fallback to now())
# =====================================================================

class TestTimestampRejection:
    def test_parse_timestamp_returns_none_for_garbage(self):
        """_parse_timestamp should return None for unparseable timestamps, not datetime.now()."""
        from app.modules.ingest import _parse_timestamp
        result = _parse_timestamp({"timestamp_utc": "not-a-date"})
        assert result is None, "_parse_timestamp should return None for garbage input"

    def test_parse_timestamp_returns_none_for_empty(self):
        """_parse_timestamp should return None for empty/missing timestamps."""
        from app.modules.ingest import _parse_timestamp
        result = _parse_timestamp({})
        assert result is None

    def test_parse_timestamp_accepts_iso(self):
        """_parse_timestamp should accept valid ISO timestamps."""
        from app.modules.ingest import _parse_timestamp
        result = _parse_timestamp({"timestamp_utc": "2025-06-01T12:00:00Z"})
        assert result is not None
        assert isinstance(result, datetime)

    def test_parse_timestamp_accepts_unix_epoch(self):
        """_parse_timestamp should accept Unix epoch timestamps."""
        from app.modules.ingest import _parse_timestamp
        result = _parse_timestamp({"timestamp_utc": 1717243800})
        assert result is not None
        assert isinstance(result, datetime)


# =====================================================================
# 1.4: UTF-8 BOM handling in CSV import
# =====================================================================

class TestBOMHandling:
    def test_bom_csv_bytes_stripped(self):
        """CSV with UTF-8 BOM should be processed correctly."""
        from app.modules.ingest import ingest_ais_csv

        # Create CSV content with BOM
        csv_content = "mmsi,timestamp,lat,lon,sog,cog\n211234567,2025-06-01T00:00:00Z,55.0,12.0,10.0,180.0\n"
        bom_bytes = b"\xef\xbb\xbf" + csv_content.encode("utf-8")

        mock_db = MagicMock()
        # _get_or_create_vessel needs vessel query
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.mmsi = "211234567"
        mock_vessel.deadweight = None
        mock_vessel.name = None
        mock_vessel.flag = None
        mock_vessel.flag_risk_category = None
        mock_vessel.ais_class = None
        mock_vessel.callsign = None

        # Chain: query(Vessel).filter(...).first() returns mock_vessel
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel
        # For prev_point and near_dup queries
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        file = io.BytesIO(bom_bytes)
        result = ingest_ais_csv(file, mock_db)
        # Should not raise and should process the row
        assert result["rejected"] == 0, f"BOM CSV should not cause rejections: {result['errors']}"

    def test_non_bom_csv_still_works(self):
        """CSV without BOM should still work."""
        from app.modules.ingest import ingest_ais_csv

        csv_content = "mmsi,timestamp,lat,lon,sog,cog\n211234567,2025-06-01T00:00:00Z,55.0,12.0,10.0,180.0\n"

        mock_db = MagicMock()
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.mmsi = "211234567"
        mock_vessel.deadweight = None
        mock_vessel.name = None
        mock_vessel.flag = None
        mock_vessel.flag_risk_category = None
        mock_vessel.ais_class = None
        mock_vessel.callsign = None

        mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        file = io.BytesIO(csv_content.encode("utf-8"))
        result = ingest_ais_csv(file, mock_db)
        assert result["rejected"] == 0


# =====================================================================
# 4.4: Multi-receiver AIS dedup (10s window)
# =====================================================================

class TestMultiReceiverDedup:
    def test_near_duplicate_within_10s_skipped(self):
        """A point within 10 seconds of an existing point should be skipped."""
        from app.modules.ingest import _create_ais_point

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "211234567"

        # Simulate an existing point 5 seconds away
        existing_point = MagicMock()
        existing_point.timestamp_utc = datetime(2025, 6, 1, 0, 0, 5, tzinfo=timezone.utc)
        existing_point.source = "csv_import"

        mock_db = MagicMock()
        # near_dup query returns a match
        mock_db.query.return_value.filter.return_value.first.return_value = existing_point

        row = {
            "mmsi": "211234567",
            "lat": 55.0,
            "lon": 12.0,
            "sog": 10.0,
            "cog": 180.0,
            "timestamp_utc": "2025-06-01T00:00:00Z",
        }
        result = _create_ais_point(mock_db, vessel, row)
        assert result is None, "Should skip near-duplicate within 10s window"

    def test_no_near_duplicate_creates_point(self):
        """A point with no nearby duplicates should be created."""
        from app.modules.ingest import _create_ais_point

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "211234567"

        mock_db = MagicMock()
        # No near_dup, no prev_point
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        row = {
            "mmsi": "211234567",
            "lat": 55.0,
            "lon": 12.0,
            "sog": 10.0,
            "cog": 180.0,
            "timestamp_utc": "2025-06-01T00:00:00Z",
        }
        result = _create_ais_point(mock_db, vessel, row)
        assert result is not None
        assert result != "replaced"
        assert mock_db.add.called
