"""Tests for AIS data normalization and validation (normalize.py).

Covers:
- SOG/COG/Heading AIS sentinel values
- MMSI type filtering (SAR, AtoN, coast stations, test MMSIs)
- Non-ISO timestamp formats (Unix epoch, US/EU date formats)
- Scientific notation MMSI detection
- MarineTraffic/VesselFinder uppercase column aliases
- Shared helper: is_non_vessel_mmsi
"""
import polars as pl
import pytest


# --- Helpers ---

def _valid_row(**overrides):
    """Return a valid AIS row dict with optional field overrides."""
    row = {
        "mmsi": "241234567",
        "lat": 55.0,
        "lon": 12.0,
        "sog": 10.0,
        "cog": 180.0,
        "heading": 200.0,
        "timestamp_utc": "2025-06-01T00:00:00Z",
    }
    row.update(overrides)
    return row


# =====================================================================
# 1.1: SOG sentinel (102.3 -> None, not rejected)
# =====================================================================

class TestSOGSentinel:
    def test_sog_sentinel_1023_sets_none(self):
        """SOG=102.3 (raw 1023 = 'not available') should set sog to None, not reject."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(sog=102.3)
        error = validate_ais_row(row)
        assert error is None, f"SOG sentinel 102.3 should not reject: {error}"
        assert row["sog"] is None, "SOG should be set to None for sentinel value"

    def test_sog_sentinel_1023_exact(self):
        """Exact 102.3 treated as sentinel."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(sog=102.3)
        error = validate_ais_row(row)
        assert error is None

    def test_sog_sentinel_higher_value(self):
        """Values above 102.2 are sentinel (e.g., 102.4)."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(sog=102.4)
        error = validate_ais_row(row)
        assert error is None
        assert row["sog"] is None

    def test_sog_above_35_below_sentinel_rejected(self):
        """SOG > 35 but < 102.2 should still be rejected as physical limit exceeded."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(sog=40.0)
        error = validate_ais_row(row)
        assert error is not None
        assert "SOG exceeds physical limit" in error

    def test_sog_valid_value_passes(self):
        """Normal SOG value passes validation."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(sog=12.5)
        error = validate_ais_row(row)
        assert error is None
        assert row["sog"] == 12.5

    def test_sog_none_passes(self):
        """SOG=None should pass (optional field)."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(sog=None)
        error = validate_ais_row(row)
        assert error is None


# =====================================================================
# 1.1: COG sentinel (360.0 -> None)
# =====================================================================

class TestCOGSentinel:
    def test_cog_sentinel_360_sets_none(self):
        """COG=360.0 (raw 3600 = 'not available') should set cog to None, not reject."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(cog=360.0)
        error = validate_ais_row(row)
        assert error is None, f"COG sentinel 360 should not reject: {error}"
        assert row["cog"] is None, "COG should be set to None for sentinel value"

    def test_cog_sentinel_above_360(self):
        """COG > 360 also treated as sentinel."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(cog=361.0)
        error = validate_ais_row(row)
        assert error is None
        assert row["cog"] is None

    def test_cog_valid_passes(self):
        """Normal COG value passes and is preserved."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(cog=270.0)
        error = validate_ais_row(row)
        assert error is None
        assert row["cog"] == 270.0

    def test_cog_none_passes(self):
        """COG=None should pass."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(cog=None)
        error = validate_ais_row(row)
        assert error is None


# =====================================================================
# 1.1: Heading sentinel (511 -> None)
# =====================================================================

class TestHeadingSentinel:
    def test_heading_511_sets_none(self):
        """Heading=511 ('not available') should set heading to None."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(heading=511)
        error = validate_ais_row(row)
        assert error is None, f"Heading 511 should not reject: {error}"
        assert row["heading"] is None, "Heading should be set to None for sentinel 511"

    def test_heading_511_float(self):
        """Heading=511.0 (float) also treated as sentinel."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(heading=511.0)
        error = validate_ais_row(row)
        assert error is None
        assert row["heading"] is None

    def test_heading_valid_passes(self):
        """Normal heading value passes."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(heading=180.0)
        error = validate_ais_row(row)
        assert error is None
        assert row["heading"] == 180.0

    def test_heading_out_of_range_rejected(self):
        """Heading > 360 (but not 511) is rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(heading=400.0)
        error = validate_ais_row(row)
        assert error is not None
        assert "Heading out of range" in error

    def test_heading_negative_rejected(self):
        """Negative heading is rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(heading=-10.0)
        error = validate_ais_row(row)
        assert error is not None
        assert "Heading out of range" in error

    def test_heading_none_passes(self):
        """Heading=None should pass."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(heading=None)
        error = validate_ais_row(row)
        assert error is None


# =====================================================================
# 1.5: MMSI type filtering
# =====================================================================

class TestMMSITypes:
    def test_sar_aircraft_970_rejected(self):
        """970xxxxxx = SAR aircraft MMSI — should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="970123456")
        error = validate_ais_row(row)
        assert error is not None
        assert "SAR aircraft" in error

    def test_sar_aircraft_975_rejected(self):
        """975xxxxxx = SAR aircraft MMSI — should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="975000001")
        error = validate_ais_row(row)
        assert error is not None
        assert "SAR aircraft" in error

    def test_aton_99_rejected(self):
        """99xxxxxxx = Aid to Navigation — should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="991234567")
        error = validate_ais_row(row)
        assert error is not None
        assert "Aid to Navigation" in error

    def test_coast_station_00_rejected(self):
        """00xxxxxxx = coast station — should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="001234567")
        error = validate_ais_row(row)
        assert error is not None
        assert "coast station" in error

    def test_test_mmsi_111111111_rejected(self):
        """111111111 = common test MMSI — should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="111111111")
        error = validate_ais_row(row)
        assert error is not None
        assert "Test MMSI" in error

    def test_test_mmsi_123456789_rejected(self):
        """123456789 = common test MMSI — should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="123456789")
        error = validate_ais_row(row)
        assert error is not None
        assert "Test MMSI" in error

    def test_test_mmsi_000000000_rejected(self):
        """000000000 = test MMSI — should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="000000000")
        error = validate_ais_row(row)
        assert error is not None
        assert "coast station" in error  # 00xxxxxxx catches this first

    def test_valid_vessel_mmsi_passes(self):
        """Normal 9-digit vessel MMSI should pass."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="241234567")
        error = validate_ais_row(row)
        assert error is None

    def test_is_non_vessel_mmsi_helper(self):
        """is_non_vessel_mmsi returns error string for non-vessel, None for vessel."""
        from app.modules.normalize import is_non_vessel_mmsi
        assert is_non_vessel_mmsi("970123456") is not None
        assert is_non_vessel_mmsi("991234567") is not None
        assert is_non_vessel_mmsi("001234567") is not None
        assert is_non_vessel_mmsi("111111111") is not None
        assert is_non_vessel_mmsi("241234567") is None  # Valid vessel


# =====================================================================
# 1.6: Non-ISO timestamp formats
# =====================================================================

class TestTimestampFormats:
    def test_iso_format(self):
        """Standard ISO 8601 should pass."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc="2025-06-01T12:30:00Z")
        error = validate_ais_row(row)
        assert error is None

    def test_unix_epoch_integer(self):
        """Unix epoch integer should be parsed successfully."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc=1717243800)  # 2024-06-01 12:30:00 UTC
        error = validate_ais_row(row)
        assert error is None

    def test_unix_epoch_float(self):
        """Unix epoch float should be parsed successfully."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc=1717243800.5)
        error = validate_ais_row(row)
        assert error is None

    def test_us_date_format(self):
        """US date format MM/DD/YYYY HH:MM:SS should be parsed."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc="06/01/2025 12:30:00")
        error = validate_ais_row(row)
        assert error is None

    def test_eu_date_format(self):
        """EU date format DD/MM/YYYY HH:MM:SS should be parsed."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc="01/06/2025 12:30:00")
        error = validate_ais_row(row)
        assert error is None

    def test_slash_date_format(self):
        """YYYY/MM/DD HH:MM:SS should be parsed."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc="2025/06/01 12:30:00")
        error = validate_ais_row(row)
        assert error is None

    def test_dash_eu_format(self):
        """DD-MM-YYYY HH:MM:SS should be parsed."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc="01-06-2025 12:30:00")
        error = validate_ais_row(row)
        assert error is None

    def test_us_format_without_seconds(self):
        """MM/DD/YYYY HH:MM (no seconds) should be parsed."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc="06/01/2025 12:30")
        error = validate_ais_row(row)
        assert error is None

    def test_completely_invalid_timestamp_rejected(self):
        """Completely unparseable timestamp should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(timestamp_utc="not-a-date")
        error = validate_ais_row(row)
        assert error is not None
        assert "Unparseable timestamp" in error

    def test_parse_timestamp_flexible_returns_none_for_garbage(self):
        """parse_timestamp_flexible returns None for garbage input."""
        from app.modules.normalize import parse_timestamp_flexible
        assert parse_timestamp_flexible("garbage") is None
        assert parse_timestamp_flexible("") is None
        assert parse_timestamp_flexible(None) is None

    def test_parse_timestamp_flexible_unix_epoch(self):
        """parse_timestamp_flexible handles Unix epoch."""
        from app.modules.normalize import parse_timestamp_flexible
        from datetime import datetime, timezone
        result = parse_timestamp_flexible(1717243800)
        assert result is not None
        assert isinstance(result, datetime)

    def test_parse_timestamp_flexible_datetime_passthrough(self):
        """parse_timestamp_flexible passes through datetime objects."""
        from app.modules.normalize import parse_timestamp_flexible
        from datetime import datetime, timezone
        dt = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = parse_timestamp_flexible(dt)
        assert result is dt


# =====================================================================
# 4.5: Scientific notation MMSI detection
# =====================================================================

class TestScientificNotationMMSI:
    def test_sci_notation_lowercase_e(self):
        """MMSI like '2.41e+08' should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="2.41e+08")
        error = validate_ais_row(row)
        assert error is not None
        assert "scientific notation" in error.lower()

    def test_sci_notation_uppercase_e(self):
        """MMSI like '2.41E+08' should be rejected."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi="2.41E+08")
        error = validate_ais_row(row)
        assert error is not None
        assert "scientific notation" in error.lower()

    def test_sci_notation_numeric_input(self):
        """Numeric MMSI that gets converted to sci notation string."""
        from app.modules.normalize import validate_ais_row
        row = _valid_row(mmsi=2.41e+08)  # Python float
        error = validate_ais_row(row)
        assert error is not None
        assert "scientific notation" in error.lower() or "must be 9 digits" in error


# =====================================================================
# 4.3: MarineTraffic/VesselFinder uppercase column aliases
# =====================================================================

class TestColumnAliases:
    def test_uppercase_speed_renamed(self):
        """'SPEED' column should be renamed to 'sog'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"SPEED": [12.5], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "sog" in result.columns

    def test_uppercase_lat_renamed(self):
        """'LAT' column should be renamed to 'lat'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"LAT": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "lat" in result.columns

    def test_uppercase_lon_renamed(self):
        """'LON' column should be renamed to 'lon'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"lat": [55.0], "LON": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "lon" in result.columns

    def test_uppercase_heading_renamed(self):
        """'HEADING' column should be renamed to 'heading'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"HEADING": [180.0], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "heading" in result.columns

    def test_uppercase_timestamp_renamed(self):
        """'TIMESTAMP' column should be renamed to 'timestamp'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"TIMESTAMP": ["2025-01-01T00:00:00Z"], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        # After full normalization, it becomes timestamp_utc
        assert "timestamp_utc" in result.columns or "timestamp" in result.columns

    def test_uppercase_course_renamed(self):
        """'COURSE' column should be renamed to 'cog'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"COURSE": [180.0], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "cog" in result.columns

    def test_uppercase_name_renamed(self):
        """'NAME' column should be renamed to 'vessel_name'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"NAME": ["TANKER ONE"], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "vessel_name" in result.columns

    def test_uppercase_mmsi_renamed(self):
        """'MMSI' column should be renamed to 'mmsi'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"MMSI": ["241234567"], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "mmsi" in result.columns

    def test_uppercase_imo_renamed(self):
        """'IMO' column should be renamed to 'imo'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"IMO": ["1234567"], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "imo" in result.columns

    def test_shipname_renamed(self):
        """'SHIPNAME' column should be renamed to 'vessel_name'."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"SHIPNAME": ["TANKER ONE"], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "vessel_name" in result.columns

    def test_lowercase_aliases_still_work(self):
        """Existing lowercase aliases (speed, course, etc.) still work."""
        from app.modules.normalize import normalize_ais_dataframe
        df = pl.DataFrame({"speed": [12.5], "course": [180.0], "shipname": ["X"], "lat": [55.0], "lon": [12.0]})
        result = normalize_ais_dataframe(df)
        assert "sog" in result.columns
        assert "cog" in result.columns
        assert "vessel_name" in result.columns


# =====================================================================
# aisstream sentinel checks
# =====================================================================

class TestAisstreamSentinels:
    def test_sog_sentinel_filtered(self):
        """aisstream SOG=102.3 should be set to None."""
        from app.modules.aisstream_client import _map_position_report
        msg = {
            "MetaData": {"MMSI": "241234567", "latitude": 55.0, "longitude": 12.0, "time_utc": "2025-06-01T00:00:00Z"},
            "Message": {"PositionReport": {"Sog": 102.3, "Cog": 180.0, "TrueHeading": 200, "NavigationalStatus": 0, "Latitude": 55.0, "Longitude": 12.0}},
        }
        result = _map_position_report(msg)
        assert result is not None
        assert result["sog"] is None

    def test_cog_sentinel_filtered(self):
        """aisstream COG=360.0 should be set to None."""
        from app.modules.aisstream_client import _map_position_report
        msg = {
            "MetaData": {"MMSI": "241234567", "latitude": 55.0, "longitude": 12.0, "time_utc": "2025-06-01T00:00:00Z"},
            "Message": {"PositionReport": {"Sog": 10.0, "Cog": 360.0, "TrueHeading": 200, "NavigationalStatus": 0, "Latitude": 55.0, "Longitude": 12.0}},
        }
        result = _map_position_report(msg)
        assert result is not None
        assert result["cog"] is None

    def test_heading_511_filtered(self):
        """aisstream heading=511 should be set to None."""
        from app.modules.aisstream_client import _map_position_report
        msg = {
            "MetaData": {"MMSI": "241234567", "latitude": 55.0, "longitude": 12.0, "time_utc": "2025-06-01T00:00:00Z"},
            "Message": {"PositionReport": {"Sog": 10.0, "Cog": 180.0, "TrueHeading": 511, "NavigationalStatus": 0, "Latitude": 55.0, "Longitude": 12.0}},
        }
        result = _map_position_report(msg)
        assert result is not None
        assert result["heading"] is None

    def test_non_vessel_mmsi_filtered(self):
        """aisstream SAR aircraft MMSI should be filtered."""
        from app.modules.aisstream_client import _map_position_report
        msg = {
            "MetaData": {"MMSI": "970123456", "latitude": 55.0, "longitude": 12.0, "time_utc": "2025-06-01T00:00:00Z"},
            "Message": {"PositionReport": {"Sog": 10.0, "Cog": 180.0, "TrueHeading": 200, "NavigationalStatus": 0}},
        }
        result = _map_position_report(msg)
        assert result is None

    def test_unparseable_timestamp_returns_none(self):
        """aisstream with unparseable timestamp should return None (not fallback to now)."""
        from app.modules.aisstream_client import _map_position_report
        msg = {
            "MetaData": {"MMSI": "241234567", "latitude": 55.0, "longitude": 12.0, "time_utc": "not-a-date"},
            "Message": {"PositionReport": {"Sog": 10.0, "Cog": 180.0, "TrueHeading": 200, "NavigationalStatus": 0}},
        }
        result = _map_position_report(msg)
        assert result is None


# =====================================================================
# aishub sentinel checks
# =====================================================================

class TestAishubSentinels:
    def test_sog_sentinel_after_division(self):
        """AISHub raw SOG=1023 / 10 = 102.3 should be set to None."""
        from app.modules.aishub_client import _map_aishub_position
        pos = {
            "MMSI": "241234567",
            "LATITUDE": 55.0,
            "LONGITUDE": 12.0,
            "TIME": "2025-06-01T00:00:00Z",
            "SOG": 1023,  # raw value / 10 = 102.3
            "COG": 1800,  # raw value / 10 = 180.0
            "HEADING": 200,
        }
        result = _map_aishub_position(pos)
        assert result is not None
        assert result["sog"] is None

    def test_cog_sentinel_after_division(self):
        """AISHub raw COG=3600 / 10 = 360.0 should be set to None."""
        from app.modules.aishub_client import _map_aishub_position
        pos = {
            "MMSI": "241234567",
            "LATITUDE": 55.0,
            "LONGITUDE": 12.0,
            "TIME": "2025-06-01T00:00:00Z",
            "SOG": 100,
            "COG": 3600,  # raw value / 10 = 360.0
            "HEADING": 200,
        }
        result = _map_aishub_position(pos)
        assert result is not None
        assert result["cog"] is None

    def test_non_vessel_mmsi_filtered(self):
        """AISHub SAR aircraft MMSI should be filtered."""
        from app.modules.aishub_client import _map_aishub_position
        pos = {
            "MMSI": "970123456",
            "LATITUDE": 55.0,
            "LONGITUDE": 12.0,
            "TIME": "2025-06-01T00:00:00Z",
            "SOG": 100,
            "COG": 1800,
        }
        result = _map_aishub_position(pos)
        assert result is None

    def test_unparseable_timestamp_returns_none(self):
        """AISHub with unparseable timestamp returns None (not fallback to now)."""
        from app.modules.aishub_client import _map_aishub_position
        pos = {
            "MMSI": "241234567",
            "LATITUDE": 55.0,
            "LONGITUDE": 12.0,
            "TIME": "not-a-date",
            "SOG": 100,
            "COG": 1800,
        }
        result = _map_aishub_position(pos)
        assert result is None
