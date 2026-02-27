"""Tests for NOAA historical AIS client."""
import csv
import io
import os
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestURLConstruction:
    def test_pre_2025_zip_url(self):
        from app.modules.noaa_client import _url_for_date
        url = _url_for_date(date(2024, 6, 15))
        assert "AIS_2024_06_15.zip" in url

    def test_2025_zst_url(self):
        from app.modules.noaa_client import _url_for_date
        url = _url_for_date(date(2025, 3, 1))
        assert "ais-2025-03-01.csv.zst" in url


class TestGeoFilter:
    def test_point_in_bbox(self):
        from app.modules.noaa_client import _point_in_bbox
        bbox = (55.0, 20.0, 65.0, 30.0)
        assert _point_in_bbox(60.0, 25.0, bbox) is True
        assert _point_in_bbox(70.0, 25.0, bbox) is False
        assert _point_in_bbox(60.0, 15.0, bbox) is False

    @patch("app.modules.noaa_client._build_corridor_bbox")
    @patch("app.modules.ingest._create_ais_point")
    @patch("app.modules.ingest._get_or_create_vessel")
    def test_geo_filter_rejects_out_of_bbox(self, mock_vessel, mock_point, mock_bbox):
        """CSV with points inside and outside bbox; only in-bbox points imported."""
        from app.modules.noaa_client import import_noaa_file

        # Create a minimal test CSV in a temp ZIP
        csv_content = (
            "MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,VesselType\n"
            "636017000,2025-01-15T12:00:00,60.0,25.0,10.0,180.0,180,TEST VESSEL,,Tanker\n"
            "636017001,2025-01-15T12:00:00,10.0,-80.0,10.0,180.0,180,OUT OF AREA,,Tanker\n"
        )

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            with zipfile.ZipFile(tmp, "w") as zf:
                zf.writestr("test.csv", csv_content)
            tmp_path = Path(tmp.name)

        db = MagicMock()
        # Return bbox covering Baltic (lat 55-65, lon 20-30)
        mock_bbox.return_value = (55.0, 20.0, 65.0, 30.0)
        mock_vessel.return_value = MagicMock(vessel_id=1)
        mock_point.return_value = MagicMock()

        try:
            result = import_noaa_file(tmp_path, db, corridor_filter=True)
            # The point at 10,-80 should be filtered out
            assert result["filtered_geo"] >= 1
        finally:
            os.unlink(tmp_path)


class TestZipDecompression:
    def test_valid_zip_decompresses(self):
        """Valid ZIP file decompresses correctly."""
        from app.modules.noaa_client import _decompress_csv_lines

        csv_content = "MMSI,BaseDateTime,LAT,LON\n636017000,2025-01-15T12:00:00,60.0,25.0\n"

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            with zipfile.ZipFile(tmp, "w") as zf:
                zf.writestr("test.csv", csv_content)
            tmp_path = Path(tmp.name)

        try:
            lines = list(_decompress_csv_lines(tmp_path))
            assert len(lines) >= 2  # Header + data
            assert "MMSI" in lines[0]
        finally:
            os.unlink(tmp_path)

    def test_corrupted_zip_raises(self):
        """Truncated ZIP should raise clear error."""
        from app.modules.noaa_client import download_noaa_file

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp.write(b"PK\x03\x04truncated")
            tmp_path = Path(tmp.name)

        # The validate step in download checks integrity
        # For direct decompression, zipfile will raise
        try:
            with pytest.raises((zipfile.BadZipFile, ValueError)):
                from app.modules.noaa_client import _decompress_csv_lines
                list(_decompress_csv_lines(tmp_path))
        finally:
            os.unlink(tmp_path)


class TestNormalization:
    def test_noaa_column_aliases(self):
        """NOAA columns are normalized to canonical names."""
        from app.modules.normalize import normalize_noaa_row

        row = {
            "MMSI": "636017000",
            "BaseDateTime": "2025-01-15T12:00:00",
            "LAT": "60.0",
            "LON": "25.0",
            "SOG": "10.0",
            "COG": "180.0",
            "VesselName": "TEST SHIP",
            "VesselType": "Tanker",
            "TransceiverClass": "A",
            "IMO": "IMO9876543",
        }
        result = normalize_noaa_row(row)
        assert result is not None
        assert result["vessel_name"] == "TEST SHIP"
        assert result["vessel_type"] == "Tanker"
        assert result["ais_class"] == "A"
        assert result["imo"] == "9876543"  # Prefix stripped

    def test_imo_no_space_prefix(self):
        """IMO prefix 'IMO9876543' (no space) is handled."""
        from app.modules.normalize import normalize_noaa_row

        row = {
            "MMSI": "636017000",
            "BaseDateTime": "2025-01-15T12:00:00",
            "LAT": "60.0",
            "LON": "25.0",
            "IMO": "IMO9876543",
        }
        result = normalize_noaa_row(row)
        assert result["imo"] == "9876543"

    def test_missing_required_returns_none(self):
        from app.modules.normalize import normalize_noaa_row
        assert normalize_noaa_row({"MMSI": "636017000"}) is None  # No lat/lon
        assert normalize_noaa_row({}) is None
