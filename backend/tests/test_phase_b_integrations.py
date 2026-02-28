"""Tests for Phase B8-10: Nordic AIS clients, watchlists, and CREA integration."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Kystverket Tests ──────────────────────────────────────────────────────────


class TestKystverketDisabled:
    """Test that Kystverket returns 0 when disabled."""

    @patch("app.modules.kystverket_client.settings")
    def test_stream_kystverket_disabled(self, mock_settings):
        mock_settings.KYSTVERKET_ENABLED = False
        from app.modules.kystverket_client import stream_kystverket

        db = MagicMock()
        result = stream_kystverket(db, duration_seconds=10)
        assert result["points_ingested"] == 0
        assert result["vessels_seen"] == 0
        assert result["errors"] == 0

    @patch("app.modules.kystverket_client.settings")
    def test_kystverket_pyais_not_installed(self, mock_settings):
        """Verify graceful handling when pyais is not installed."""
        mock_settings.KYSTVERKET_ENABLED = True

        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pyais":
                raise ImportError("No module named 'pyais'")
            return real_import(name, *args, **kwargs)

        from app.modules.kystverket_client import stream_kystverket

        db = MagicMock()
        with patch("builtins.__import__", side_effect=mock_import):
            result = stream_kystverket(db, duration_seconds=10)

        assert result["points_ingested"] == 0
        assert result["errors"] == 1


# ── Digitraffic Tests ─────────────────────────────────────────────────────────


class TestDigitrafficAISDisabled:
    """Test that Digitraffic returns 0 when disabled."""

    @patch("app.modules.digitraffic_client.settings")
    def test_fetch_digitraffic_ais_disabled(self, mock_settings):
        mock_settings.DIGITRAFFIC_ENABLED = False
        from app.modules.digitraffic_client import fetch_digitraffic_ais

        db = MagicMock()
        result = fetch_digitraffic_ais(db)
        assert result["points_ingested"] == 0
        assert result["vessels_seen"] == 0
        assert result["errors"] == 0


class TestDigitrafficAISSuccess:
    """Test Digitraffic AIS ingestion with mocked HTTP response."""

    @patch("app.modules.digitraffic_client.settings")
    @patch("app.modules.digitraffic_client.httpx.Client")
    def test_fetch_digitraffic_ais_success(self, mock_client_cls, mock_settings):
        mock_settings.DIGITRAFFIC_ENABLED = True

        # Mock GeoJSON response
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [24.95, 60.17]},
                    "properties": {
                        "mmsi": "230012345",
                        "sog": 120,  # 12.0 knots (1/10)
                        "cog": 1800,  # 180.0 degrees (1/10)
                        "heading": 180,
                        "timestampExternal": 1700000000000,  # ms epoch
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [25.0, 60.2]},
                    "properties": {
                        "mmsi": "230054321",
                        "sog": 50,
                        "cog": 900,
                        "heading": 511,  # unavailable
                        "timestampExternal": 1700000060000,
                    },
                },
            ],
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = geojson
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # Mock DB
        db = MagicMock()
        # Return None for vessel queries (new vessels)
        db.query.return_value.filter.return_value.first.return_value = None

        from app.modules.digitraffic_client import fetch_digitraffic_ais

        result = fetch_digitraffic_ais(db)
        assert result["points_ingested"] == 2
        assert result["vessels_seen"] == 2
        assert result["errors"] == 0


class TestDigitrafficPortCalls:
    """Test Digitraffic port call ingestion."""

    @patch("app.modules.digitraffic_client.settings")
    def test_fetch_digitraffic_port_calls_disabled(self, mock_settings):
        mock_settings.DIGITRAFFIC_ENABLED = False
        from app.modules.digitraffic_client import fetch_digitraffic_port_calls

        db = MagicMock()
        result = fetch_digitraffic_port_calls(db)
        assert result["port_calls_created"] == 0
        assert result["errors"] == 0

    @patch("app.modules.digitraffic_client.settings")
    @patch("app.modules.digitraffic_client.httpx.Client")
    def test_fetch_digitraffic_port_calls_success(self, mock_client_cls, mock_settings):
        mock_settings.DIGITRAFFIC_ENABLED = True

        port_calls_data = {
            "portCalls": [
                {
                    "mmsi": "230012345",
                    "portCallTimestamp": "2026-01-15T10:00:00Z",
                    "departure": "2026-01-16T08:00:00Z",
                    "portName": "Helsinki",
                },
            ]
        }

        mock_resp = MagicMock()
        mock_resp.json.return_value = port_calls_data
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # Mock vessel found
        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 42

        # Mock port found
        mock_port = MagicMock()
        mock_port.port_id = 7

        db = MagicMock()

        # Set up query chains: Vessel query returns vessel, Port query returns port,
        # PortCall dedup query returns None (no existing)
        call_count = [0]

        def filter_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # Vessel query
                result.first.return_value = mock_vessel
            elif call_count[0] == 2:
                # Port query
                result.first.return_value = mock_port
            else:
                # PortCall dedup
                result.first.return_value = None
            return result

        db.query.return_value.filter.side_effect = filter_side_effect

        from app.modules.digitraffic_client import fetch_digitraffic_port_calls

        result = fetch_digitraffic_port_calls(db)
        assert result["port_calls_created"] == 1
        assert result["errors"] == 0


# ── FleetLeaks Tests ──────────────────────────────────────────────────────────


class TestFleetLeaks:
    """Test FleetLeaks watchlist loader."""

    def test_load_fleetleaks_json(self):
        """Create test JSON and verify watchlist entries created via MMSI match."""
        from app.modules.watchlist_loader import load_fleetleaks

        vessels_data = [
            {"name": "SHADOW TANKER", "mmsi": "123456789", "imo": "9876543", "flag": "PA"},
            {"name": "DARK VESSEL", "mmsi": "987654321", "imo": "1234567", "flag": "MT"},
        ]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(vessels_data, f)
            f.flush()
            tmp_path = f.name

        try:
            db = MagicMock()

            # Mock vessel lookups: first MMSI query returns vessel, second returns vessel
            mock_vessel_1 = MagicMock()
            mock_vessel_1.vessel_id = 1
            mock_vessel_1.name = "SHADOW TANKER"

            mock_vessel_2 = MagicMock()
            mock_vessel_2.vessel_id = 2
            mock_vessel_2.name = "DARK VESSEL"

            vessel_lookup = {"123456789": mock_vessel_1, "987654321": mock_vessel_2}

            def query_filter_side(*args, **kwargs):
                result = MagicMock()
                for arg in args:
                    # Check the filter expression for MMSI values
                    try:
                        if hasattr(arg, "right") and hasattr(arg.right, "value"):
                            mmsi_val = arg.right.value
                            if mmsi_val in vessel_lookup:
                                result.first.return_value = vessel_lookup[mmsi_val]
                                return result
                    except Exception:
                        pass
                # Default: return vessel for MMSI match
                result.first.return_value = mock_vessel_1
                return result

            db.query.return_value.filter.side_effect = query_filter_side

            result = load_fleetleaks(db, tmp_path)
            assert result["matched"] == 2
            assert result["unmatched"] == 0
        finally:
            os.unlink(tmp_path)

    def test_load_fleetleaks_no_match(self):
        """Verify unmatched vessels are counted."""
        from app.modules.watchlist_loader import load_fleetleaks

        vessels_data = [
            {"name": "UNKNOWN VESSEL", "mmsi": "000000000", "imo": "0000000"},
        ]

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(vessels_data, f)
            f.flush()
            tmp_path = f.name

        try:
            db = MagicMock()
            # All queries return None (no match)
            db.query.return_value.filter.return_value.first.return_value = None
            db.query.return_value.filter.return_value.all.return_value = []

            result = load_fleetleaks(db, tmp_path)
            assert result["matched"] == 0
            assert result["unmatched"] == 1
        finally:
            os.unlink(tmp_path)

    def test_load_fleetleaks_invalid_json(self):
        """Verify graceful handling of invalid JSON."""
        from app.modules.watchlist_loader import load_fleetleaks

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("not valid json {{{")
            f.flush()
            tmp_path = f.name

        try:
            db = MagicMock()
            result = load_fleetleaks(db, tmp_path)
            assert result["matched"] == 0
            assert result["unmatched"] == 0
        finally:
            os.unlink(tmp_path)


# ── GUR Tests ─────────────────────────────────────────────────────────────────


class TestGURList:
    """Test Ukraine GUR shadow fleet watchlist loader."""

    def test_load_gur_csv(self):
        """Create test CSV and verify watchlist entries created."""
        from app.modules.watchlist_loader import load_gur_list

        csv_content = "name,mmsi,imo,flag\nSHADOW ONE,111222333,9111222,PA\nSHADOW TWO,444555666,9333444,LR\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write(csv_content)
            f.flush()
            tmp_path = f.name

        try:
            db = MagicMock()

            mock_vessel = MagicMock()
            mock_vessel.vessel_id = 10
            mock_vessel.name = "SHADOW ONE"

            # Return vessel for MMSI match
            db.query.return_value.filter.return_value.first.return_value = mock_vessel

            result = load_gur_list(db, tmp_path)
            assert result["matched"] == 2
            assert result["unmatched"] == 0
        finally:
            os.unlink(tmp_path)

    def test_load_gur_csv_no_match(self):
        """Verify unmatched GUR vessels are counted."""
        from app.modules.watchlist_loader import load_gur_list

        csv_content = "name,mmsi,imo,flag\nUNKNOWN,000000000,,\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            f.write(csv_content)
            f.flush()
            tmp_path = f.name

        try:
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = None
            db.query.return_value.filter.return_value.all.return_value = []

            result = load_gur_list(db, tmp_path)
            assert result["matched"] == 0
            assert result["unmatched"] == 1
        finally:
            os.unlink(tmp_path)


# ── CREA Tests ────────────────────────────────────────────────────────────────


class TestCREADisabled:
    """Test CREA returns when disabled."""

    @patch("app.modules.crea_client.settings")
    def test_crea_disabled(self, mock_settings):
        mock_settings.CREA_ENABLED = False
        from app.modules.crea_client import import_crea_data

        db = MagicMock()
        result = import_crea_data(db)
        assert result["queried"] == 0
        assert result["enriched"] == 0
        assert result["errors"] == 0

    @patch("app.modules.crea_client.settings")
    def test_crea_vessel_query_disabled(self, mock_settings):
        mock_settings.CREA_ENABLED = False
        from app.modules.crea_client import fetch_crea_vessel_data

        result = fetch_crea_vessel_data(imo="1234567")
        assert result is None


class TestCREAVesselQuery:
    """Test CREA vessel data query."""

    @patch("app.modules.crea_client.settings")
    @patch("app.modules.crea_client.httpx.Client")
    def test_crea_vessel_query_success(self, mock_client_cls, mock_settings):
        mock_settings.CREA_ENABLED = True
        mock_settings.CREA_API_BASE_URL = "https://api.russiafossiltracker.com"

        response_data = {
            "voyages": [
                {"origin": "Primorsk", "destination": "Rotterdam", "cargo": "crude"},
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.modules.crea_client import fetch_crea_vessel_data

        result = fetch_crea_vessel_data(imo="9876543")
        assert result is not None
        assert len(result["voyages"]) == 1

    @patch("app.modules.crea_client.settings")
    @patch("app.modules.crea_client.httpx.Client")
    def test_crea_vessel_query_not_found(self, mock_client_cls, mock_settings):
        mock_settings.CREA_ENABLED = True
        mock_settings.CREA_API_BASE_URL = "https://api.russiafossiltracker.com"

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        from app.modules.crea_client import fetch_crea_vessel_data

        result = fetch_crea_vessel_data(imo="0000000")
        assert result is None


# ── Data Fetcher Tests ────────────────────────────────────────────────────────


class TestFetchFleetLeaks:
    """Test FleetLeaks data fetcher stub."""

    @patch("app.modules.data_fetcher.settings")
    def test_fetch_fleetleaks_file_exists(self, mock_settings):
        from app.modules.data_fetcher import fetch_fleetleaks

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create the expected file
            fpath = os.path.join(tmpdir, "fleetleaks_vessels.json")
            with open(fpath, "w") as f:
                json.dump([], f)

            result = fetch_fleetleaks(data_dir=tmpdir)
            assert result == fpath

    @patch("app.modules.data_fetcher.settings")
    def test_fetch_fleetleaks_file_missing(self, mock_settings):
        from app.modules.data_fetcher import fetch_fleetleaks

        with tempfile.TemporaryDirectory() as tmpdir:
            result = fetch_fleetleaks(data_dir=tmpdir)
            assert result is None


# ── Config Tests ──────────────────────────────────────────────────────────────


class TestConfigSettings:
    """Test new config settings have correct defaults."""

    def test_kystverket_defaults(self):
        from app.config import Settings

        s = Settings(DATABASE_URL="sqlite:///test.db")
        assert s.KYSTVERKET_ENABLED is False
        assert s.KYSTVERKET_HOST == "153.44.253.27"
        assert s.KYSTVERKET_PORT == 5631

    def test_digitraffic_defaults(self):
        from app.config import Settings

        s = Settings(DATABASE_URL="sqlite:///test.db")
        assert s.DIGITRAFFIC_ENABLED is False

    def test_crea_defaults(self):
        from app.config import Settings

        s = Settings(DATABASE_URL="sqlite:///test.db")
        assert s.CREA_ENABLED is False
        assert s.CREA_API_BASE_URL == "https://api.russiafossiltracker.com"
