"""Tests for CREA write-through persistence (G2).

Validates that import_crea_data persists CreaVoyage records and handles
deduplication via savepoints.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from sqlalchemy.exc import IntegrityError


# ── _parse_date tests ────────────────────────────────────────────────────────


class TestParseDate:
    """Test the _parse_date helper function."""

    def test_valid_iso_date(self):
        from app.modules.crea_client import _parse_date

        result = _parse_date("2024-03-15T10:30:00")
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 3
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30

    def test_valid_iso_date_with_z(self):
        from app.modules.crea_client import _parse_date

        result = _parse_date("2024-03-15T10:30:00Z")
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_valid_iso_date_with_offset(self):
        from app.modules.crea_client import _parse_date

        result = _parse_date("2024-03-15T10:30:00+02:00")
        assert isinstance(result, datetime)

    def test_none_input(self):
        from app.modules.crea_client import _parse_date

        result = _parse_date(None)
        assert result is None

    def test_empty_string(self):
        from app.modules.crea_client import _parse_date

        result = _parse_date("")
        assert result is None

    def test_malformed_string(self):
        from app.modules.crea_client import _parse_date

        result = _parse_date("not-a-date")
        assert result is None

    def test_partial_date(self):
        from app.modules.crea_client import _parse_date

        result = _parse_date("2024-03-15")
        assert isinstance(result, datetime)
        assert result.year == 2024


# ── import_crea_data tests ───────────────────────────────────────────────────


class TestImportCreaDataDisabled:
    """Test import_crea_data when CREA is disabled."""

    @patch("app.modules.crea_client.settings")
    def test_disabled_returns_zeros(self, mock_settings):
        mock_settings.CREA_ENABLED = False
        from app.modules.crea_client import import_crea_data

        db = MagicMock()
        result = import_crea_data(db)
        assert result["queried"] == 0
        assert result["enriched"] == 0
        assert result["voyages_stored"] == 0
        assert result["duplicates_skipped"] == 0


class TestImportCreaDataPersistence:
    """Test that import_crea_data persists CreaVoyage objects."""

    @patch("app.modules.crea_client.fetch_crea_vessel_data")
    @patch("app.modules.crea_client.settings")
    def test_stores_voyage_records(self, mock_settings, mock_fetch):
        """import_crea_data calls db.add for CreaVoyage objects."""
        mock_settings.CREA_ENABLED = True
        from app.modules.crea_client import import_crea_data

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"
        vessel.name = "TEST TANKER"

        mock_fetch.return_value = {
            "voyages": [
                {
                    "departure_port": "Novorossiysk",
                    "arrival_port": "Kalamata",
                    "commodity": "Crude Oil",
                    "cargo_volume_tonnes": 80000.0,
                    "departure_date": "2024-03-01T00:00:00Z",
                    "arrival_date": "2024-03-10T00:00:00Z",
                    "source_url": "https://example.com/voyage/1",
                },
            ]
        }

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]
        # Make begin_nested return a savepoint mock that succeeds
        savepoint = MagicMock()
        db.begin_nested.return_value = savepoint

        result = import_crea_data(db)

        assert result["voyages_stored"] == 1
        assert result["duplicates_skipped"] == 0
        assert result["queried"] == 1
        assert result["enriched"] == 1
        assert db.add.called
        db.commit.assert_called_once()

    @patch("app.modules.crea_client.fetch_crea_vessel_data")
    @patch("app.modules.crea_client.settings")
    def test_result_includes_voyages_stored_and_duplicates_keys(self, mock_settings, mock_fetch):
        """Return dict must include voyages_stored and duplicates_skipped keys."""
        mock_settings.CREA_ENABLED = True
        from app.modules.crea_client import import_crea_data

        mock_fetch.return_value = None

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"
        vessel.name = "TEST"

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]

        result = import_crea_data(db)
        assert "voyages_stored" in result
        assert "duplicates_skipped" in result

    @patch("app.modules.crea_client.fetch_crea_vessel_data")
    @patch("app.modules.crea_client.settings")
    def test_duplicate_import_idempotency(self, mock_settings, mock_fetch):
        """When IntegrityError occurs on add, duplicates_skipped increments."""
        mock_settings.CREA_ENABLED = True
        from app.modules.crea_client import import_crea_data

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"
        vessel.name = "TEST"

        mock_fetch.return_value = {
            "voyages": [
                {"departure_port": "A", "departure_date": "2024-01-01"},
                {"departure_port": "B", "departure_date": "2024-02-01"},
            ]
        }

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]

        # First add succeeds, second raises IntegrityError
        savepoint_ok = MagicMock()
        savepoint_err = MagicMock()

        call_count = [0]

        def side_effect_begin_nested():
            call_count[0] += 1
            if call_count[0] == 1:
                return savepoint_ok
            return savepoint_err

        db.begin_nested.side_effect = side_effect_begin_nested

        # Make second db.add raise IntegrityError
        add_count = [0]

        def add_side_effect(obj):
            add_count[0] += 1
            if add_count[0] == 2:
                raise IntegrityError("duplicate", params=None, orig=Exception())

        db.add.side_effect = add_side_effect

        result = import_crea_data(db)

        assert result["voyages_stored"] == 1
        assert result["duplicates_skipped"] == 1

    @patch("app.modules.crea_client.fetch_crea_vessel_data")
    @patch("app.modules.crea_client.settings")
    def test_import_run_id_is_valid_uuid(self, mock_settings, mock_fetch):
        """import_run_id in the result should be a valid UUID string."""
        mock_settings.CREA_ENABLED = True
        from app.modules.crea_client import import_crea_data

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"
        vessel.name = "TEST"

        mock_fetch.return_value = {
            "voyages": [
                {"departure_port": "A", "departure_date": "2024-01-01"},
            ]
        }

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]
        savepoint = MagicMock()
        db.begin_nested.return_value = savepoint

        result = import_crea_data(db)

        # Validate UUID format
        run_id = result["import_run_id"]
        parsed = uuid.UUID(run_id)
        assert str(parsed) == run_id

    @patch("app.modules.crea_client.fetch_crea_vessel_data")
    @patch("app.modules.crea_client.settings")
    def test_multiple_voyages_stored(self, mock_settings, mock_fetch):
        """Multiple voyages from a single vessel are all persisted."""
        mock_settings.CREA_ENABLED = True
        from app.modules.crea_client import import_crea_data

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"
        vessel.name = "BULK CARRIER"

        mock_fetch.return_value = {
            "voyages": [
                {"departure_port": "Port A", "departure_date": "2024-01-01"},
                {"departure_port": "Port B", "departure_date": "2024-02-01"},
                {"departure_port": "Port C", "departure_date": "2024-03-01"},
            ]
        }

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]
        savepoint = MagicMock()
        db.begin_nested.return_value = savepoint

        result = import_crea_data(db)

        assert result["voyages_stored"] == 3
        assert result["duplicates_skipped"] == 0
        assert db.add.call_count == 3

    @patch("app.modules.crea_client.fetch_crea_vessel_data")
    @patch("app.modules.crea_client.settings")
    def test_no_voyages_in_data(self, mock_settings, mock_fetch):
        """When CREA returns data but no voyages list, no records stored."""
        mock_settings.CREA_ENABLED = True
        from app.modules.crea_client import import_crea_data

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"
        vessel.name = "EMPTY"

        mock_fetch.return_value = {"vessel_name": "EMPTY", "voyages": []}

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]

        result = import_crea_data(db)

        assert result["voyages_stored"] == 0
        assert result["queried"] == 1
        assert result["enriched"] == 0

    @patch("app.modules.crea_client.fetch_crea_vessel_data")
    @patch("app.modules.crea_client.settings")
    def test_fetch_returns_none(self, mock_settings, mock_fetch):
        """When fetch returns None, no voyages stored."""
        mock_settings.CREA_ENABLED = True
        from app.modules.crea_client import import_crea_data

        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.imo = "1234567"
        vessel.name = "NOTFOUND"

        mock_fetch.return_value = None

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]

        result = import_crea_data(db)

        assert result["voyages_stored"] == 0
        assert result["queried"] == 1
        assert result["enriched"] == 0

    @patch("app.modules.crea_client.fetch_crea_vessel_data")
    @patch("app.modules.crea_client.settings")
    def test_multiple_vessels(self, mock_settings, mock_fetch):
        """Voyages from multiple vessels are all persisted."""
        mock_settings.CREA_ENABLED = True
        from app.modules.crea_client import import_crea_data

        vessel_a = MagicMock()
        vessel_a.vessel_id = 1
        vessel_a.imo = "1111111"
        vessel_a.name = "VESSEL_A"

        vessel_b = MagicMock()
        vessel_b.vessel_id = 2
        vessel_b.imo = "2222222"
        vessel_b.name = "VESSEL_B"

        def fetch_side_effect(imo=None, mmsi=None):
            if imo == "1111111":
                return {"voyages": [{"departure_port": "X", "departure_date": "2024-01-01"}]}
            if imo == "2222222":
                return {"voyages": [{"departure_port": "Y", "departure_date": "2024-02-01"}]}
            return None

        mock_fetch.side_effect = fetch_side_effect

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel_a, vessel_b]
        savepoint = MagicMock()
        db.begin_nested.return_value = savepoint

        result = import_crea_data(db)

        assert result["voyages_stored"] == 2
        assert result["enriched"] == 2
        assert result["queried"] == 2


# ── CreaVoyage model tests ──────────────────────────────────────────────────


class TestCreaVoyageModel:
    """Test CreaVoyage model definition."""

    def test_model_has_expected_columns(self):
        from app.models.crea_voyage import CreaVoyage

        assert hasattr(CreaVoyage, "voyage_id")
        assert hasattr(CreaVoyage, "vessel_id")
        assert hasattr(CreaVoyage, "departure_port")
        assert hasattr(CreaVoyage, "arrival_port")
        assert hasattr(CreaVoyage, "commodity")
        assert hasattr(CreaVoyage, "cargo_volume_tonnes")
        assert hasattr(CreaVoyage, "departure_date")
        assert hasattr(CreaVoyage, "arrival_date")
        assert hasattr(CreaVoyage, "source_url")
        assert hasattr(CreaVoyage, "import_run_id")
        assert hasattr(CreaVoyage, "created_at")

    def test_tablename(self):
        from app.models.crea_voyage import CreaVoyage

        assert CreaVoyage.__tablename__ == "crea_voyages"

    def test_model_in_init_all(self):
        from app.models import __all__ as model_all

        assert "CreaVoyage" in model_all

    def test_unique_constraint_exists(self):
        from app.models.crea_voyage import CreaVoyage

        constraints = [
            c.name for c in CreaVoyage.__table_args__
            if hasattr(c, "name")
        ]
        assert "uq_crea_voyage_dedup" in constraints
