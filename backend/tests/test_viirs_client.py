"""Tests for VIIRS nighttime boat detection client."""

from __future__ import annotations

import csv
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestDownloadViirsCsv:
    """Tests for download_viirs_csv."""

    def test_download_calls_breaker(self, tmp_path):
        """Downloads via circuit breaker."""
        with (
            patch("app.modules.viirs_client.breakers") as mock_breakers,
            patch("app.modules.viirs_client.settings") as mock_settings,
        ):
            mock_settings.VIIRS_EOG_BASE_URL = "https://example.com/viirs"
            mock_settings.VIIRS_DATA_DIR = str(tmp_path)
            mock_breakers.__getitem__ = MagicMock(return_value=MagicMock())
            mock_breakers["viirs"].call.return_value = tmp_path / "test.csv"

            from app.modules.viirs_client import download_viirs_csv

            result = download_viirs_csv("20260101", str(tmp_path))
            mock_breakers["viirs"].call.assert_called_once()
            assert result == tmp_path / "test.csv"

    def test_download_constructs_correct_url(self, tmp_path):
        """URL is constructed from base + date."""
        with (
            patch("app.modules.viirs_client.breakers") as mock_breakers,
            patch("app.modules.viirs_client.settings") as mock_settings,
        ):
            mock_settings.VIIRS_EOG_BASE_URL = "https://eog.example.com/v23"
            mock_settings.VIIRS_DATA_DIR = str(tmp_path)
            mock_breakers["viirs"].call.return_value = tmp_path / "test.csv"

            from app.modules.viirs_client import download_viirs_csv

            download_viirs_csv("20260313", str(tmp_path))
            call_args = mock_breakers["viirs"].call.call_args
            url_arg = call_args[0][1]
            assert "VBD_npp_20260313.csv" in url_arg

    def test_download_creates_data_dir(self, tmp_path):
        """Creates data directory if it doesn't exist."""
        target = tmp_path / "subdir" / "viirs"
        with (
            patch("app.modules.viirs_client.breakers") as mock_breakers,
            patch("app.modules.viirs_client.settings") as mock_settings,
        ):
            mock_settings.VIIRS_EOG_BASE_URL = "https://example.com/viirs"
            mock_settings.VIIRS_DATA_DIR = str(target)
            mock_breakers["viirs"].call.return_value = target / "test.csv"

            from app.modules.viirs_client import download_viirs_csv

            download_viirs_csv("20260101", str(target))
            assert target.exists()


class TestParseViirsCsv:
    """Tests for parse_viirs_csv."""

    def _write_csv(self, path: Path, rows: list[dict]) -> Path:
        fieldnames = ["Lat_DNB", "Lon_DNB", "Rad_DNB", "Date_Proc", "QF_Detect"]
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def test_filters_to_boats_only(self, tmp_path):
        """Only QF_Detect=1 rows are returned."""
        csv_path = self._write_csv(tmp_path / "test.csv", [
            {"Lat_DNB": "55.5", "Lon_DNB": "18.1", "Rad_DNB": "100", "Date_Proc": "20260313", "QF_Detect": "1"},
            {"Lat_DNB": "56.0", "Lon_DNB": "19.0", "Rad_DNB": "200", "Date_Proc": "20260313", "QF_Detect": "4"},
            {"Lat_DNB": "57.0", "Lon_DNB": "20.0", "Rad_DNB": "300", "Date_Proc": "20260313", "QF_Detect": "8"},
        ])

        from app.modules.viirs_client import parse_viirs_csv

        result = parse_viirs_csv(csv_path)
        assert len(result) == 1
        assert result[0]["lat"] == 55.5
        assert result[0]["qf_detect"] == 1

    def test_scene_id_has_viirs_prefix(self, tmp_path):
        """Scene IDs start with 'viirs-'."""
        csv_path = self._write_csv(tmp_path / "test.csv", [
            {"Lat_DNB": "55.5", "Lon_DNB": "18.1", "Rad_DNB": "100", "Date_Proc": "20260313", "QF_Detect": "1"},
        ])

        from app.modules.viirs_client import parse_viirs_csv

        result = parse_viirs_csv(csv_path)
        assert result[0]["scene_id"].startswith("viirs-")

    def test_empty_csv(self, tmp_path):
        """Empty CSV returns empty list."""
        csv_path = self._write_csv(tmp_path / "test.csv", [])

        from app.modules.viirs_client import parse_viirs_csv

        result = parse_viirs_csv(csv_path)
        assert result == []

    def test_invalid_coordinates_skipped(self, tmp_path):
        """Rows with invalid coordinates are skipped."""
        csv_path = self._write_csv(tmp_path / "test.csv", [
            {"Lat_DNB": "not_a_number", "Lon_DNB": "18.1", "Rad_DNB": "100", "Date_Proc": "20260313", "QF_Detect": "1"},
            {"Lat_DNB": "55.5", "Lon_DNB": "18.1", "Rad_DNB": "100", "Date_Proc": "20260313", "QF_Detect": "1"},
        ])

        from app.modules.viirs_client import parse_viirs_csv

        result = parse_viirs_csv(csv_path)
        assert len(result) == 1

    def test_invalid_qf_detect_skipped(self, tmp_path):
        """Rows with non-integer QF_Detect are skipped."""
        csv_path = self._write_csv(tmp_path / "test.csv", [
            {"Lat_DNB": "55.5", "Lon_DNB": "18.1", "Rad_DNB": "100", "Date_Proc": "20260313", "QF_Detect": "abc"},
        ])

        from app.modules.viirs_client import parse_viirs_csv

        result = parse_viirs_csv(csv_path)
        assert result == []

    def test_detection_time_parsed(self, tmp_path):
        """Date_Proc is parsed into datetime."""
        csv_path = self._write_csv(tmp_path / "test.csv", [
            {"Lat_DNB": "55.5", "Lon_DNB": "18.1", "Rad_DNB": "100", "Date_Proc": "20260313", "QF_Detect": "1"},
        ])

        from app.modules.viirs_client import parse_viirs_csv

        result = parse_viirs_csv(csv_path)
        assert result[0]["detection_time"] == datetime(2026, 3, 13)

    def test_multiple_boats(self, tmp_path):
        """Multiple boat detections are all returned."""
        csv_path = self._write_csv(tmp_path / "test.csv", [
            {"Lat_DNB": "55.5", "Lon_DNB": "18.1", "Rad_DNB": "100", "Date_Proc": "20260313", "QF_Detect": "1"},
            {"Lat_DNB": "56.0", "Lon_DNB": "19.0", "Rad_DNB": "200", "Date_Proc": "20260313", "QF_Detect": "1"},
            {"Lat_DNB": "57.0", "Lon_DNB": "20.0", "Rad_DNB": "300", "Date_Proc": "20260313", "QF_Detect": "1"},
        ])

        from app.modules.viirs_client import parse_viirs_csv

        result = parse_viirs_csv(csv_path)
        assert len(result) == 3

    def test_radiance_stored(self, tmp_path):
        """Radiance value is stored in detection dict."""
        csv_path = self._write_csv(tmp_path / "test.csv", [
            {"Lat_DNB": "55.5", "Lon_DNB": "18.1", "Rad_DNB": "42.5", "Date_Proc": "20260313", "QF_Detect": "1"},
        ])

        from app.modules.viirs_client import parse_viirs_csv

        result = parse_viirs_csv(csv_path)
        assert result[0]["radiance"] == 42.5


class TestImportViirsDet:
    """Tests for import_viirs_detections."""

    def test_imports_new_detections(self):
        """New detections are added to DB."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        detections = [
            {"lat": 55.5, "lon": 18.1, "radiance": 100, "scene_id": "viirs-20260313-55.5000-18.1000", "detection_time": datetime(2026, 3, 13)},
        ]

        from app.modules.viirs_client import import_viirs_detections

        result = import_viirs_detections(db, detections)
        assert result["imported"] == 1
        assert result["skipped"] == 0
        assert db.add.called
        assert db.commit.called

    def test_skips_duplicates(self):
        """Existing detections are skipped."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = MagicMock()  # existing

        detections = [
            {"lat": 55.5, "lon": 18.1, "radiance": 100, "scene_id": "viirs-20260313-55.5000-18.1000", "detection_time": datetime(2026, 3, 13)},
        ]

        from app.modules.viirs_client import import_viirs_detections

        result = import_viirs_detections(db, detections)
        assert result["imported"] == 0
        assert result["skipped"] == 1

    def test_empty_detections(self):
        """Empty list does not commit."""
        db = MagicMock()

        from app.modules.viirs_client import import_viirs_detections

        result = import_viirs_detections(db, [])
        assert result["imported"] == 0
        assert not db.commit.called


class TestCollectViirs:
    """Tests for collect_viirs orchestrator."""

    def test_disabled_returns_zero(self):
        """Returns zero counts when disabled."""
        with patch("app.modules.viirs_client.settings") as mock_settings:
            mock_settings.VIIRS_ENABLED = False

            from app.modules.viirs_client import collect_viirs

            result = collect_viirs(MagicMock())
            assert result["imported"] == 0
            assert result["errors"] == 0

    def test_orchestrates_download_parse_import(self):
        """Calls download, parse, import in sequence."""
        with (
            patch("app.modules.viirs_client.settings") as mock_settings,
            patch("app.modules.viirs_client.download_viirs_csv") as mock_dl,
            patch("app.modules.viirs_client.parse_viirs_csv") as mock_parse,
            patch("app.modules.viirs_client.import_viirs_detections") as mock_import,
        ):
            mock_settings.VIIRS_ENABLED = True
            mock_settings.VIIRS_GAS_FLARING_FILTER_ENABLED = False
            mock_dl.return_value = Path("/tmp/test.csv")
            mock_parse.return_value = [{"lat": 55.5, "lon": 18.1}]
            mock_import.return_value = {"imported": 1, "skipped": 0}

            from app.modules.viirs_client import collect_viirs

            result = collect_viirs(MagicMock())
            assert result["imported"] == 1
            assert result["errors"] == 0
            mock_dl.assert_called_once()
            mock_parse.assert_called_once()
            mock_import.assert_called_once()

    def test_handles_download_error(self):
        """Returns error count on download failure."""
        with (
            patch("app.modules.viirs_client.settings") as mock_settings,
            patch("app.modules.viirs_client.download_viirs_csv") as mock_dl,
        ):
            mock_settings.VIIRS_ENABLED = True
            mock_settings.VIIRS_GAS_FLARING_FILTER_ENABLED = False
            mock_dl.side_effect = Exception("Network error")

            from app.modules.viirs_client import collect_viirs

            result = collect_viirs(MagicMock())
            assert result["errors"] == 1
            assert result["imported"] == 0
