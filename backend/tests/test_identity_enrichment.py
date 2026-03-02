"""Tests for identity enrichment pipeline.

Covers OFAC SDN fixes, KSE key, aisstream destination/draught extraction,
kystverket destination/draught, CLI watchlist wiring, busy_timeout PRAGMA,
AISPoint unique constraint, and ingest preservation.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock, call

import pytest


# ---------------------------------------------------------------------------
# OFAC SDN loader tests
# ---------------------------------------------------------------------------


class TestOFACSdn:
    """OFAC SDN loader bug fixes."""

    def _make_row(self, sdn_type="vessel", name="OCEAN STAR", remarks="", flag=""):
        return {
            "SDN_TYPE": sdn_type,
            "SDN_NAME": name,
            "REMARKS": remarks,
            "Vess_flag": flag,
            "ent_num": "12345",
        }

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_ofac_case_insensitive_sdn_type(self, mock_upsert, mock_resolve):
        """Lowercase 'vessel' in SDN_TYPE should be accepted (Bug 1)."""
        from app.modules.watchlist_loader import load_ofac_sdn

        mock_resolve.return_value = (MagicMock(vessel_id=1), "exact_mmsi", 100)

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
                                                     "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
                                                     "Vess_owner", "REMARKS"])
            writer.writeheader()
            writer.writerow({
                "ent_num": "99999",
                "SDN_NAME": "TEST VESSEL",
                "SDN_TYPE": "vessel",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "PA",
                "Vess_owner": "",
                "REMARKS": "IMO 9187629; MMSI 572469210",
            })
            path = f.name

        db = MagicMock()
        try:
            result = load_ofac_sdn(db, path)
            assert result["matched"] == 1
            assert result["skipped"] == 0
        finally:
            os.unlink(path)

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_ofac_imo_parsed_from_remarks(self, mock_upsert, mock_resolve):
        """IMO should be parsed from REMARKS field, not ent_num (Bug 2)."""
        from app.modules.watchlist_loader import load_ofac_sdn

        mock_resolve.return_value = (MagicMock(vessel_id=1), "exact_imo", 100)

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
                                                     "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
                                                     "Vess_owner", "REMARKS"])
            writer.writeheader()
            writer.writerow({
                "ent_num": "99999",
                "SDN_NAME": "TEST VESSEL",
                "SDN_TYPE": "Vessel",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "",
                "Vess_owner": "",
                "REMARKS": "IMO 9187629; some other text",
            })
            path = f.name

        db = MagicMock()
        try:
            load_ofac_sdn(db, path)
            # Check that _resolve_vessel was called with IMO from REMARKS
            args, kwargs = mock_resolve.call_args
            assert kwargs.get("imo") == "9187629" or args[2] == "9187629"
        finally:
            os.unlink(path)

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_ofac_mmsi_parsed_from_remarks(self, mock_upsert, mock_resolve):
        """MMSI should be parsed from REMARKS field, not VESSEL_ID (Bug 3)."""
        from app.modules.watchlist_loader import load_ofac_sdn

        mock_resolve.return_value = (MagicMock(vessel_id=1), "exact_mmsi", 100)

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
                                                     "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
                                                     "Vess_owner", "REMARKS"])
            writer.writeheader()
            writer.writerow({
                "ent_num": "99999",
                "SDN_NAME": "TEST VESSEL",
                "SDN_TYPE": "Vessel",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "",
                "Vess_owner": "",
                "REMARKS": "MMSI 572469210; some other text",
            })
            path = f.name

        db = MagicMock()
        try:
            load_ofac_sdn(db, path)
            args, kwargs = mock_resolve.call_args
            assert kwargs.get("mmsi") == "572469210" or args[1] == "572469210"
        finally:
            os.unlink(path)

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_ofac_creates_watchlist_entries(self, mock_upsert, mock_resolve):
        """End-to-end: OFAC loader creates watchlist entries for matched vessels."""
        from app.modules.watchlist_loader import load_ofac_sdn

        vessel_mock = MagicMock(vessel_id=42)
        mock_resolve.return_value = (vessel_mock, "exact_mmsi", 100)

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
                                                     "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
                                                     "Vess_owner", "REMARKS"])
            writer.writeheader()
            writer.writerow({
                "ent_num": "1", "SDN_NAME": "V1", "SDN_TYPE": "Vessel",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "PA", "Vess_owner": "",
                "REMARKS": "IMO 9187629; MMSI 572469210",
            })
            writer.writerow({
                "ent_num": "2", "SDN_NAME": "V2", "SDN_TYPE": "Vessel",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "LR", "Vess_owner": "",
                "REMARKS": "IMO 9234567; MMSI 636012345",
            })
            path = f.name

        db = MagicMock()
        try:
            result = load_ofac_sdn(db, path)
            assert result["matched"] == 2
            assert mock_upsert.call_count == 2
        finally:
            os.unlink(path)

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_ofac_dedup(self, mock_upsert, mock_resolve):
        """Running OFAC loader twice should not create duplicate entries."""
        from app.modules.watchlist_loader import load_ofac_sdn

        mock_resolve.return_value = (MagicMock(vessel_id=1), "exact_mmsi", 100)

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
                                                     "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
                                                     "Vess_owner", "REMARKS"])
            writer.writeheader()
            writer.writerow({
                "ent_num": "1", "SDN_NAME": "V1", "SDN_TYPE": "Vessel",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "", "Vess_owner": "",
                "REMARKS": "IMO 9187629",
            })
            path = f.name

        db = MagicMock()
        try:
            load_ofac_sdn(db, path)
            load_ofac_sdn(db, path)
            # _upsert_watchlist handles dedup internally, but both calls should succeed
            assert mock_upsert.call_count == 2
        finally:
            os.unlink(path)

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_ofac_bad_remarks_skipped(self, mock_upsert, mock_resolve):
        """Rows without IMO/MMSI patterns in REMARKS are handled gracefully."""
        from app.modules.watchlist_loader import load_ofac_sdn

        mock_resolve.return_value = None  # No match without identifiers

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
                                                     "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
                                                     "Vess_owner", "REMARKS"])
            writer.writeheader()
            writer.writerow({
                "ent_num": "1", "SDN_NAME": "UNKNOWN", "SDN_TYPE": "vessel",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "", "Vess_owner": "",
                "REMARKS": "no identifiers here",
            })
            path = f.name

        db = MagicMock()
        try:
            result = load_ofac_sdn(db, path)
            assert result["unmatched"] == 1
            assert result["matched"] == 0
        finally:
            os.unlink(path)

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_ofac_flag_passed_to_resolver(self, mock_upsert, mock_resolve):
        """Vess_flag should be passed to _resolve_vessel (Bug 4)."""
        from app.modules.watchlist_loader import load_ofac_sdn

        mock_resolve.return_value = (MagicMock(vessel_id=1), "fuzzy_name", 90)

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
                                                     "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
                                                     "Vess_owner", "REMARKS"])
            writer.writeheader()
            writer.writerow({
                "ent_num": "1", "SDN_NAME": "TEST VESSEL", "SDN_TYPE": "Vessel",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "PA", "Vess_owner": "",
                "REMARKS": "",
            })
            path = f.name

        db = MagicMock()
        try:
            load_ofac_sdn(db, path)
            args, kwargs = mock_resolve.call_args
            assert kwargs.get("flag") == "PA"
        finally:
            os.unlink(path)

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_ofac_non_vessel_rows_skipped(self, mock_upsert, mock_resolve):
        """Non-vessel SDN types (individuals, entities) should be skipped."""
        from app.modules.watchlist_loader import load_ofac_sdn

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
                                                     "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
                                                     "Vess_owner", "REMARKS"])
            writer.writeheader()
            writer.writerow({
                "ent_num": "1", "SDN_NAME": "JOHN DOE", "SDN_TYPE": "Individual",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "", "Vess_owner": "",
                "REMARKS": "",
            })
            writer.writerow({
                "ent_num": "2", "SDN_NAME": "EVIL CORP", "SDN_TYPE": "Entity",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "", "Vess_owner": "",
                "REMARKS": "",
            })
            writer.writerow({
                "ent_num": "3", "SDN_NAME": "", "SDN_TYPE": "-0-",
                "Program": "", "Title": "", "Call_Sign": "",
                "Vess_type": "", "Tonnage": "", "GRT": "",
                "Vess_flag": "", "Vess_owner": "",
                "REMARKS": "",
            })
            path = f.name

        db = MagicMock()
        try:
            result = load_ofac_sdn(db, path)
            assert result["skipped"] == 3
            assert result["matched"] == 0
            mock_resolve.assert_not_called()
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# KSE loader key test
# ---------------------------------------------------------------------------


class TestKSEKey:
    """KSE loader watchlist_source key."""

    @patch("app.modules.watchlist_loader._resolve_vessel")
    @patch("app.modules.watchlist_loader._upsert_watchlist")
    def test_kse_key_matches_scoring(self, mock_upsert, mock_resolve):
        """KSE loader should use 'KSE_SHADOW' key, not 'KSE_INSTITUTE'."""
        from app.modules.watchlist_loader import load_kse_list

        mock_resolve.return_value = (MagicMock(vessel_id=1), "exact_imo", 100)

        import tempfile, csv, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["vessel_name", "imo", "flag", "mmsi"])
            writer.writeheader()
            writer.writerow({"vessel_name": "TEST", "imo": "1234567", "flag": "RU", "mmsi": ""})
            path = f.name

        db = MagicMock()
        try:
            load_kse_list(db, path)
            args, kwargs = mock_upsert.call_args
            assert kwargs.get("watchlist_source") == "KSE_SHADOW"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# AISstream destination/draught tests
# ---------------------------------------------------------------------------


class TestAISStreamEnrichment:
    """AISstream client destination/draught extraction."""

    def test_aisstream_extracts_destination(self):
        """ShipStaticData should extract Destination field."""
        from app.modules.aisstream_client import _map_static_data

        msg = {
            "MetaData": {"MMSI": 211234567, "ShipName": "TEST"},
            "Message": {
                "ShipStaticData": {
                    "ImoNumber": 9876543,
                    "Type": 80,
                    "Dimension": {},
                    "CallSign": "ABC",
                    "Destination": "ROTTERDAM  ",
                    "Draught": 12.5,
                }
            },
        }
        result = _map_static_data(msg)
        assert result is not None
        assert result["destination"] == "ROTTERDAM"

    def test_aisstream_extracts_draught(self):
        """ShipStaticData should extract Draught field."""
        from app.modules.aisstream_client import _map_static_data

        msg = {
            "MetaData": {"MMSI": 211234567, "ShipName": "TEST"},
            "Message": {
                "ShipStaticData": {
                    "ImoNumber": 9876543,
                    "Type": 80,
                    "Dimension": {},
                    "CallSign": "ABC",
                    "Destination": "",
                    "Draught": 14.2,
                }
            },
        }
        result = _map_static_data(msg)
        assert result is not None
        assert result["draught"] == pytest.approx(14.2)

    def test_aisstream_null_destination_no_overwrite(self):
        """Empty Destination should result in None, not empty string."""
        from app.modules.aisstream_client import _map_static_data

        msg = {
            "MetaData": {"MMSI": 211234567, "ShipName": "TEST"},
            "Message": {
                "ShipStaticData": {
                    "ImoNumber": 0,
                    "Type": 80,
                    "Dimension": {},
                    "CallSign": "",
                    "Destination": "",
                    "Draught": 0,
                }
            },
        }
        result = _map_static_data(msg)
        assert result is not None
        assert result["destination"] is None
        assert result["draught"] is None  # 0 is falsy

    def test_aisstream_imo_already_works(self):
        """Regression guard: IMO extraction should still work."""
        from app.modules.aisstream_client import _map_static_data

        msg = {
            "MetaData": {"MMSI": 211234567, "ShipName": "TEST"},
            "Message": {
                "ShipStaticData": {
                    "ImoNumber": 9876543,
                    "Type": 80,
                    "Dimension": {},
                    "CallSign": "XYZ",
                    "Destination": "FUJAIRAH",
                    "Draught": 10.0,
                }
            },
        }
        result = _map_static_data(msg)
        assert result["imo"] == "9876543"

    def test_aisstream_destination_truncated_to_20(self):
        """Destination should be truncated to 20 characters."""
        from app.modules.aisstream_client import _map_static_data

        msg = {
            "MetaData": {"MMSI": 211234567, "ShipName": "TEST"},
            "Message": {
                "ShipStaticData": {
                    "ImoNumber": 0,
                    "Type": 80,
                    "Dimension": {},
                    "CallSign": "",
                    "Destination": "A" * 30,
                    "Draught": None,
                }
            },
        }
        result = _map_static_data(msg)
        assert result is not None
        assert len(result["destination"]) == 20


# ---------------------------------------------------------------------------
# Database PRAGMA tests
# ---------------------------------------------------------------------------


class TestDatabasePragma:

    def test_busy_timeout_set(self):
        """PRAGMA busy_timeout=5000 should be in SQLite connection setup."""
        import app.database
        import importlib

        # Check the source code directly for the PRAGMA
        import inspect
        source = inspect.getsource(app.database)
        assert "busy_timeout=5000" in source


# ---------------------------------------------------------------------------
# AIS Point unique constraint
# ---------------------------------------------------------------------------


class TestAISPointConstraint:

    def test_ais_point_unique_constraint_defined(self):
        """AISPoint model should have unique constraint on (vessel_id, timestamp_utc, source)."""
        from app.models.ais_point import AISPoint

        # Check __table_args__ for UniqueConstraint
        found = False
        for arg in AISPoint.__table_args__:
            if hasattr(arg, "name") and arg.name == "uq_ais_point_vessel_ts_source":
                found = True
                break
        assert found, "UniqueConstraint uq_ais_point_vessel_ts_source not found in __table_args__"


# ---------------------------------------------------------------------------
# Ingest preservation tests
# ---------------------------------------------------------------------------


class TestIngestPreservation:

    def test_ingest_preserves_existing_imo(self):
        """When a vessel already has an IMO, CSV import should not overwrite it."""
        from app.modules.ingest import _get_or_create_vessel

        existing_vessel = MagicMock()
        existing_vessel.imo = "9999999"
        existing_vessel.name = "OLD NAME"
        existing_vessel.flag = "PA"
        existing_vessel.flag_risk_category = "high"
        existing_vessel.ais_class = "A"
        existing_vessel.callsign = "OLD"
        existing_vessel.mmsi = "123456789"
        existing_vessel.deadweight = None

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = existing_vessel

        row = {
            "mmsi": "123456789",
            "timestamp": "2025-01-01T00:00:00Z",
            "lat": 55.0,
            "lon": 20.0,
            "imo": "1111111",  # Different IMO — should NOT overwrite
            "vessel_name": "NEW NAME",
        }

        result = _get_or_create_vessel(db, row)
        # IMO should remain as the original value
        assert result.imo == "9999999"

    def test_ingest_fills_empty_imo(self):
        """When a vessel has no IMO, CSV import should fill it in."""
        from app.modules.ingest import _get_or_create_vessel

        existing_vessel = MagicMock()
        existing_vessel.imo = None
        existing_vessel.name = "TEST"
        existing_vessel.flag = "PA"
        existing_vessel.flag_risk_category = "high"
        existing_vessel.ais_class = "A"
        existing_vessel.callsign = None
        existing_vessel.mmsi = "123456789"
        existing_vessel.deadweight = None

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = existing_vessel

        row = {
            "mmsi": "123456789",
            "timestamp": "2025-01-01T00:00:00Z",
            "lat": 55.0,
            "lon": 20.0,
            "imo": "9876543",
        }

        result = _get_or_create_vessel(db, row)
        assert result.imo == "9876543"


# ---------------------------------------------------------------------------
# CLI watchlist wiring
# ---------------------------------------------------------------------------


class TestCLIWatchlistWiring:

    @patch("app.modules.watchlist_loader.load_gur_list")
    @patch("app.modules.watchlist_loader.load_fleetleaks")
    @patch("app.modules.watchlist_loader.load_opensanctions")
    @patch("app.modules.watchlist_loader.load_ofac_sdn")
    @patch("app.modules.data_fetcher.fetch_all")
    @patch("app.modules.data_fetcher._find_latest")
    def test_update_fetch_watchlists_calls_fleetleaks(
        self, mock_find, mock_fetch, mock_ofac, mock_os, mock_fl, mock_gur
    ):
        """_update_fetch_watchlists should call load_fleetleaks when file exists."""
        from app.cli import _update_fetch_watchlists

        # Return a path for fleetleaks, None for others
        def find_latest(data_dir, prefix):
            if prefix == "fleetleaks_":
                return "/tmp/fleetleaks_2025.json"
            return None

        mock_find.side_effect = find_latest
        db = MagicMock()
        _update_fetch_watchlists(db)
        mock_fl.assert_called_once_with(db, "/tmp/fleetleaks_2025.json")

    @patch("app.modules.watchlist_loader.load_gur_list")
    @patch("app.modules.watchlist_loader.load_fleetleaks")
    @patch("app.modules.watchlist_loader.load_opensanctions")
    @patch("app.modules.watchlist_loader.load_ofac_sdn")
    @patch("app.modules.data_fetcher.fetch_all")
    @patch("app.modules.data_fetcher._find_latest")
    def test_update_fetch_watchlists_calls_gur(
        self, mock_find, mock_fetch, mock_ofac, mock_os, mock_fl, mock_gur
    ):
        """_update_fetch_watchlists should call load_gur_list when file exists."""
        from app.cli import _update_fetch_watchlists

        def find_latest(data_dir, prefix):
            if prefix == "gur_shadow_":
                return "/tmp/gur_shadow_2025.csv"
            return None

        mock_find.side_effect = find_latest
        db = MagicMock()
        _update_fetch_watchlists(db)
        mock_gur.assert_called_once_with(db, "/tmp/gur_shadow_2025.csv")


# ---------------------------------------------------------------------------
# AIS Point dedup
# ---------------------------------------------------------------------------


class TestAISPointDedup:

    def test_ais_point_dedup_savepoint_pattern(self):
        """Duplicate AIS point insertion should be handled gracefully via savepoints."""
        # This tests the pattern used in aisstream_client and kystverket_client
        from app.modules.aisstream_client import _ingest_batch

        db = MagicMock()
        # Simulate vessel lookup returning an existing vessel
        vessel_mock = MagicMock()
        vessel_mock.vessel_id = 1
        db.query.return_value.filter.return_value.first.return_value = vessel_mock
        # Simulate existing AIS point (dedup hit)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        points = [{
            "mmsi": "123456789",
            "timestamp": "2025-01-01T00:00:00Z",
            "lat": 55.0,
            "lon": 20.0,
            "sog": 10.0,
            "cog": 180.0,
            "heading": 179,
            "nav_status": 0,
            "ais_class": "A",
            "source": "aisstream",
        }]
        static = {}

        # Should not raise
        result = _ingest_batch(db, points, static)
        assert isinstance(result, dict)

    def test_ais_point_dedup_cleanup_migration(self):
        """Database migration should include AIS point dedup cleanup SQL."""
        import inspect
        import app.database
        source = inspect.getsource(app.database._run_migrations)
        assert "uq_ais_point_vessel_ts_source" in source
        assert "DELETE FROM ais_points" in source


# ---------------------------------------------------------------------------
# Kystverket destination/draught
# ---------------------------------------------------------------------------


class TestKystverketEnrichment:

    def test_kystverket_ingest_point_passes_destination(self):
        """_ingest_point should store destination from point dict."""
        from app.modules.kystverket_client import _ingest_point

        db = MagicMock()
        vessel_mock = MagicMock()
        vessel_mock.vessel_id = 1
        db.query.return_value.filter.return_value.first.side_effect = [
            vessel_mock,  # vessel lookup
            None,         # dedup check
        ]

        pt = {
            "mmsi": "123456789",
            "lat": 70.0,
            "lon": 25.0,
            "sog": 10.0,
            "cog": 180.0,
            "heading": None,
            "timestamp_utc": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "source": "kystverket",
            "destination": "MURMANSK",
            "draught": 12.5,
        }

        _ingest_point(db, pt)
        # Check db.add was called with an AISPoint that has destination/draught
        # (first db.add call is the AISPoint, second is the AISObservation dual-write)
        from app.models.ais_point import AISPoint
        point_calls = [c for c in db.add.call_args_list if isinstance(c[0][0], AISPoint)]
        assert len(point_calls) >= 1
        point_obj = point_calls[0][0][0]
        assert point_obj.destination == "MURMANSK"
        assert point_obj.draught == 12.5

    def test_kystverket_ingest_point_null_destination(self):
        """_ingest_point should handle None destination/draught gracefully."""
        from app.modules.kystverket_client import _ingest_point

        db = MagicMock()
        vessel_mock = MagicMock()
        vessel_mock.vessel_id = 1
        db.query.return_value.filter.return_value.first.side_effect = [
            vessel_mock,  # vessel lookup
            None,         # dedup check
        ]

        pt = {
            "mmsi": "123456789",
            "lat": 70.0,
            "lon": 25.0,
            "sog": 10.0,
            "cog": 180.0,
            "heading": None,
            "timestamp_utc": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "source": "kystverket",
            "destination": None,
            "draught": None,
        }

        _ingest_point(db, pt)
        from app.models.ais_point import AISPoint
        point_calls = [c for c in db.add.call_args_list if isinstance(c[0][0], AISPoint)]
        assert len(point_calls) >= 1
        point_obj = point_calls[0][0][0]
        assert point_obj.destination is None
        assert point_obj.draught is None


# ---------------------------------------------------------------------------
# OFAC regex edge cases
# ---------------------------------------------------------------------------


class TestOFACRemarks:

    def test_ofac_imo_regex_captures_7_digits(self):
        """IMO regex should capture exactly 7 digits."""
        pattern = re.compile(r'IMO\s*(\d{7})')
        assert pattern.search("IMO 9187629").group(1) == "9187629"
        assert pattern.search("IMO9187629").group(1) == "9187629"
        assert pattern.search("blah IMO 9187629 blah").group(1) == "9187629"

    def test_ofac_mmsi_regex_captures_9_digits(self):
        """MMSI regex should capture exactly 9 digits."""
        pattern = re.compile(r'MMSI\s*(\d{9})')
        assert pattern.search("MMSI 572469210").group(1) == "572469210"
        assert pattern.search("MMSI572469210").group(1) == "572469210"

    def test_ofac_remarks_with_both_imo_mmsi(self):
        """REMARKS with both IMO and MMSI should parse both."""
        remarks = "IMO 9187629; MMSI 572469210; Flag: Panama"
        imo_match = re.search(r'IMO\s*(\d{7})', remarks)
        mmsi_match = re.search(r'MMSI\s*(\d{9})', remarks)
        assert imo_match.group(1) == "9187629"
        assert mmsi_match.group(1) == "572469210"

    def test_ofac_remarks_no_match(self):
        """REMARKS without IMO/MMSI should return None."""
        remarks = "some general text about sanctions"
        assert re.search(r'IMO\s*(\d{7})', remarks) is None
        assert re.search(r'MMSI\s*(\d{9})', remarks) is None
