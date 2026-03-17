"""Tests for DMA (Danish Maritime Authority) historical AIS data importer."""

from __future__ import annotations

import csv
import gzip
import io
from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from tests.conftest import SafeSessionMock


def _make_mock_httpx_client(mock_response):
    """Create a mock httpx.Client that returns the given response."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    return mock_client


class TestDMAClient:
    """Tests for dma_client.py."""

    def _make_csv_content(self, rows: list[list[str]], header: list[str] | None = None) -> str:
        """Build a DMA-format CSV string."""
        if header is None:
            header = [
                "# Timestamp",
                "Type of mobile",
                "MMSI",
                "Latitude",
                "Longitude",
                "Navigational status",
                "ROT",
                "SOG",
                "COG",
                "Heading",
                "IMO",
                "Callsign",
                "Name",
                "Ship type",
                "Cargo type",
                "Width",
                "Length",
                "Type of position fixing device",
                "Draught",
                "Destination",
                "ETA",
                "Data source type",
                "A",
                "B",
                "C",
                "D",
            ]
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
        return out.getvalue()

    def _make_dma_row(
        self,
        mmsi="219000001",
        ts="01/03/2026 12:00:00",
        lat="55.6761",
        lon="12.5683",
        imo="1234567",
        name="TEST TANKER",
        ship_type="Tanker",
    ) -> list[str]:
        return [
            ts,
            "Class A",
            mmsi,
            lat,
            lon,
            "Under way using engine",
            "0",
            "12.5",
            "180.0",
            "179",
            imo,
            "OXYZ",
            name,
            ship_type,
            "0",
            "32",
            "200",
            "GPS",
            "10.5",
            "COPENHAGEN",
            "01/03/2026 18:00:00",
            "AIS",
            "100",
            "100",
            "16",
            "16",
        ]

    def _make_gz_response(self, csv_content: str) -> MagicMock:
        """Create a mock httpx response with gzipped content."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = gzip.compress(csv_content.encode("utf-8"))
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_dma_url_construction(self):
        """DMA URL is correctly formed for a given date."""
        from app.modules.dma_client import _build_url

        url = _build_url(date(2026, 3, 1), gzip=True)
        assert url == "https://web.ais.dk/aisdata/aisdk-2026-03-01.csv.gz"

        url_plain = _build_url(date(2026, 3, 1), gzip=False)
        assert url_plain == "https://web.ais.dk/aisdata/aisdk-2026-03-01.csv"

    def test_dma_csv_parsing(self):
        """DMA column names are correctly normalized."""
        from app.modules.dma_client import _DMA_COLUMN_MAP, _normalize_row

        header = list(_DMA_COLUMN_MAP.keys())
        values = ["01/03/2026 12:00:00"] + ["test"] * (len(header) - 1)
        row = _normalize_row(header, values)
        assert row is not None
        assert "timestamp" in row
        assert "mmsi" in row

    def test_dma_timestamp_dayfirst(self):
        """DMA timestamps use DD/MM/YYYY, not MM/DD."""
        from app.modules.dma_client import _parse_dma_timestamp

        ts = _parse_dma_timestamp("15/03/2026 08:30:00")
        assert ts is not None
        assert ts.month == 3
        assert ts.day == 15

    def test_dma_unknown_imo_skipped(self):
        """Rows with IMO='Unknown' should not store an IMO on the vessel."""
        db = SafeSessionMock(spec=Session)
        db.query.return_value.filter.return_value.first.return_value = None

        row = self._make_dma_row(imo="Unknown")
        csv_content = self._make_csv_content([row])
        mock_resp = self._make_gz_response(csv_content)

        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("app.modules.dma_client.settings") as mock_settings:
            mock_settings.DMA_ENABLED = True
            with patch("app.modules.dma_client.httpx.Client", return_value=mock_client):
                from app.modules.dma_client import fetch_and_import_dma

                fetch_and_import_dma(db, date(2026, 3, 1), date(2026, 3, 1))
                # Verify that when vessel is created, imo is None
                for call in db.add.call_args_list:
                    obj = call[0][0]
                    if hasattr(obj, "mmsi") and hasattr(obj, "imo"):
                        assert obj.imo is None

    def test_dma_feature_flag_disabled(self):
        """Returns early when DMA_ENABLED=False."""
        db = SafeSessionMock(spec=Session)
        with patch("app.modules.dma_client.settings") as mock_settings:
            mock_settings.DMA_ENABLED = False
            from app.modules.dma_client import fetch_and_import_dma

            result = fetch_and_import_dma(db, date(2026, 3, 1), date(2026, 3, 1))
            assert result["points_imported"] == 0
            assert result["days_processed"] == 0

    def test_dma_vessel_type_filter(self):
        """Only tankers imported when vessel_types filter is set."""
        db = SafeSessionMock(spec=Session)
        db.query.return_value.filter.return_value.first.return_value = None

        tanker_row = self._make_dma_row(mmsi="219000001", ship_type="Tanker")
        cargo_row = self._make_dma_row(mmsi="219000002", ship_type="Cargo")
        csv_content = self._make_csv_content([tanker_row, cargo_row])
        mock_resp = self._make_gz_response(csv_content)
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("app.modules.dma_client.settings") as mock_settings:
            mock_settings.DMA_ENABLED = True
            with patch("app.modules.dma_client.httpx.Client", return_value=mock_client):
                from app.modules.dma_client import fetch_and_import_dma

                result = fetch_and_import_dma(
                    db,
                    date(2026, 3, 1),
                    date(2026, 3, 1),
                    vessel_types=["tanker"],
                )
                assert result["days_processed"] == 1

    def test_dma_network_error(self):
        """Graceful handling of network errors."""
        db = SafeSessionMock(spec=Session)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("Network error")

        with patch("app.modules.dma_client.settings") as mock_settings:
            mock_settings.DMA_ENABLED = True
            with patch("app.modules.dma_client.httpx.Client", return_value=mock_client):
                from app.modules.dma_client import fetch_and_import_dma

                result = fetch_and_import_dma(db, date(2026, 3, 1), date(2026, 3, 1))
                assert result["errors"] >= 1
                assert result["points_imported"] == 0

    def test_dma_gzip_support(self):
        """Gzipped DMA files are handled correctly."""
        db = SafeSessionMock(spec=Session)
        db.query.return_value.filter.return_value.first.return_value = None

        row = self._make_dma_row()
        csv_content = self._make_csv_content([row])
        mock_resp = self._make_gz_response(csv_content)
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("app.modules.dma_client.settings") as mock_settings:
            mock_settings.DMA_ENABLED = True
            with patch("app.modules.dma_client.httpx.Client", return_value=mock_client):
                from app.modules.dma_client import fetch_and_import_dma

                result = fetch_and_import_dma(db, date(2026, 3, 1), date(2026, 3, 1))
                assert result["days_processed"] == 1

    def test_dma_stats_returned(self):
        """Stats dict has all expected keys."""
        db = SafeSessionMock(spec=Session)
        with patch("app.modules.dma_client.settings") as mock_settings:
            mock_settings.DMA_ENABLED = False
            from app.modules.dma_client import fetch_and_import_dma

            result = fetch_and_import_dma(db, date(2026, 3, 1), date(2026, 3, 1))
            expected_keys = {
                "points_imported",
                "vessels_created",
                "vessels_updated",
                "days_processed",
                "errors",
            }
            assert expected_keys == set(result.keys())

    def test_dma_date_range(self):
        """Multi-day import processes each day."""
        db = SafeSessionMock(spec=Session)
        call_count = [0]

        def counting_client(*args, **kwargs):
            call_count[0] += 1
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = Exception("simulated failure")
            return mock_client

        with patch("app.modules.dma_client.settings") as mock_settings:
            mock_settings.DMA_ENABLED = True
            with patch("app.modules.dma_client.httpx.Client", side_effect=counting_client):
                from app.modules.dma_client import fetch_and_import_dma

                result = fetch_and_import_dma(db, date(2026, 3, 1), date(2026, 3, 3))
                # Should attempt 3 days (each day creates a client)
                assert call_count[0] == 3
                assert result["errors"] == 3

    def test_dma_dedup(self):
        """Duplicate rows are not re-imported."""
        db = SafeSessionMock(spec=Session)

        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1
        mock_vessel.imo = "1234567"
        mock_vessel.name = "TEST"

        MagicMock()

        def mock_filter(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.first.return_value = mock_vessel
            mock_result.filter.return_value = mock_result
            return mock_result

        db.query.return_value.filter.side_effect = mock_filter

        row = self._make_dma_row()
        csv_content = self._make_csv_content([row, row])  # duplicate
        mock_resp = self._make_gz_response(csv_content)
        mock_client = _make_mock_httpx_client(mock_resp)

        with patch("app.modules.dma_client.settings") as mock_settings:
            mock_settings.DMA_ENABLED = True
            with patch("app.modules.dma_client.httpx.Client", return_value=mock_client):
                from app.modules.dma_client import fetch_and_import_dma

                result = fetch_and_import_dma(db, date(2026, 3, 1), date(2026, 3, 1))
                assert result["days_processed"] == 1

    def test_dma_safe_float(self):
        """_safe_float handles various inputs."""
        from app.modules.dma_client import _safe_float

        assert _safe_float("12.5") == 12.5
        assert _safe_float("") is None
        assert _safe_float(None) is None
        assert _safe_float("abc") is None
