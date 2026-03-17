"""Tests for GFW full identity extraction, callsign enrichment, and enrich-identity CLI.

Covers:
  1a: GFW parsing — selfReportedInfo, callsign, identity_history
  1b: Enrichment — broadened filter, IMO/callsign provenance, VesselHistory population
  1c: CLI — enrich-identity command with gfw, watchlist, all, and invalid source

Uses in-memory SQLite for enrichment tests requiring real SQL (provenance dedup),
mocks for GFW HTTP parsing, and typer.testing.CliRunner for CLI.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # registers all models
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    """In-memory SQLite session with all tables."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def _make_vessel(db, mmsi="211456789", name="TEST VESSEL", **kwargs):
    v = Vessel(mmsi=mmsi, name=name, **kwargs)
    db.add(v)
    db.flush()
    return v


def _mock_search_vessel_response(entry):
    """Helper: wrap a single entry in a GFW search response."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"entries": [entry]}
    return mock_resp


# ===========================================================================
# 1a: GFW Parsing Tests
# ===========================================================================


class TestGFWParsing:
    """Tests for search_vessel() identity parsing."""

    def _call_search(self, entry):
        """Call search_vessel with a mocked HTTP response containing one entry."""
        from app.modules.gfw_client import search_vessel

        mock_resp = _mock_search_vessel_response(entry)
        with patch("app.utils.http_retry.retry_request", return_value=mock_resp):
            with patch("httpx.Client") as mock_client:
                mock_client.return_value.__enter__ = MagicMock(return_value=MagicMock())
                mock_client.return_value.__exit__ = MagicMock(return_value=False)
                return search_vessel("123456789", token="test-token")

    def test_imo_from_ships_data_with_self_reported_fallback(self):
        """IMO comes from shipsData[0]; selfReportedInfo[0] used as fallback."""
        # Case 1: shipsData has IMO
        entry = {
            "id": "gfw-1",
            "ssvid": "123456789",
            "combinedSourcesInfo": [{"shipsData": [{"imo": "IMO_SHIPS", "shipname": "ALPHA"}]}],
            "selfReportedInfo": [
                {
                    "id": "sr-1",
                    "imo": "IMO_SR",
                    "callsign": "CALL1",
                    "transmissionDateFrom": "2024-01-01T00:00:00Z",
                }
            ],
        }
        results = self._call_search(entry)
        assert results[0]["imo"] == "IMO_SHIPS"

        # Case 2: shipsData has no IMO, falls back to selfReportedInfo
        entry2 = {
            "id": "gfw-2",
            "ssvid": "123456789",
            "combinedSourcesInfo": [{"shipsData": [{"shipname": "ALPHA"}]}],
            "selfReportedInfo": [
                {
                    "id": "sr-2",
                    "imo": "IMO_FALLBACK",
                    "callsign": "C2",
                    "transmissionDateFrom": "2024-01-01T00:00:00Z",
                }
            ],
        }
        results2 = self._call_search(entry2)
        assert results2[0]["imo"] == "IMO_FALLBACK"

    def test_callsign_from_self_reported(self):
        """Callsign is extracted from selfReportedInfo[0]."""
        entry = {
            "id": "gfw-1",
            "ssvid": "123456789",
            "combinedSourcesInfo": [{"shipsData": [{"shipname": "VESSEL"}]}],
            "selfReportedInfo": [
                {"id": "sr-1", "callsign": "D5BX7", "transmissionDateFrom": "2024-01-01T00:00:00Z"}
            ],
        }
        results = self._call_search(entry)
        assert results[0]["callsign"] == "D5BX7"

    def test_multi_entry_self_reported_produces_identity_history(self):
        """Multiple selfReportedInfo entries with transmissionDateFrom create identity_history."""
        entry = {
            "id": "gfw-1",
            "ssvid": "123456789",
            "combinedSourcesInfo": [{"shipsData": [{"shipname": "CURRENT"}]}],
            "selfReportedInfo": [
                {
                    "id": "sr-1",
                    "shipname": "NAME_A",
                    "flag": "PA",
                    "callsign": "C1",
                    "imo": "1111111",
                    "ssvid": "123456789",
                    "transmissionDateFrom": "2023-01-01T00:00:00Z",
                    "transmissionDateTo": "2023-06-01T00:00:00Z",
                },
                {
                    "id": "sr-2",
                    "shipname": "NAME_B",
                    "flag": "LR",
                    "callsign": "C2",
                    "imo": "2222222",
                    "ssvid": "123456789",
                    "transmissionDateFrom": "2023-06-01T00:00:00Z",
                    "transmissionDateTo": "2024-01-01T00:00:00Z",
                },
            ],
        }
        results = self._call_search(entry)
        history = results[0]["identity_history"]
        assert len(history) == 2
        assert history[0]["name"] == "NAME_A"
        assert history[0]["flag"] == "PA"
        assert history[0]["source"] == "gfw_self_reported"
        assert history[1]["name"] == "NAME_B"
        assert history[1]["callsign"] == "C2"

    def test_entries_without_transmission_date_skipped(self):
        """selfReportedInfo entries missing transmissionDateFrom are excluded from identity_history."""
        entry = {
            "id": "gfw-1",
            "ssvid": "123456789",
            "combinedSourcesInfo": [{"shipsData": [{"shipname": "V"}]}],
            "selfReportedInfo": [
                {
                    "id": "sr-1",
                    "shipname": "HAS_DATE",
                    "transmissionDateFrom": "2024-01-01T00:00:00Z",
                },
                {"id": "sr-2", "shipname": "NO_DATE"},  # no transmissionDateFrom
            ],
        }
        results = self._call_search(entry)
        assert len(results[0]["identity_history"]) == 1
        assert results[0]["identity_history"][0]["name"] == "HAS_DATE"

    def test_backward_compat_single_entry(self):
        """Single-entry response produces same top-level fields plus new callsign/identity_history."""
        entry = {
            "id": "gfw-vessel-1",
            "ssvid": "123456789",
            "combinedSourcesInfo": [
                {
                    "shipsData": [
                        {
                            "shipname": "TEST VESSEL",
                            "imo": "IMO1234567",
                            "flag": "PA",
                            "vesselType": "tanker",
                            "lengthM": 250.0,
                            "tonnageGt": 80000,
                            "builtYear": 2005,
                        }
                    ]
                }
            ],
        }
        results = self._call_search(entry)
        r = results[0]
        assert r["gfw_id"] == "gfw-vessel-1"
        assert r["name"] == "TEST VESSEL"
        assert r["mmsi"] == "123456789"
        assert r["imo"] == "IMO1234567"
        assert r["flag"] == "PA"
        assert r["vessel_type"] == "tanker"
        assert r["length_m"] == 250.0
        assert r["tonnage_gt"] == 80000
        assert r["year_built"] == 2005
        # New fields
        assert r["callsign"] is None  # no selfReportedInfo
        assert r["identity_history"] == []


# ===========================================================================
# 1b: Enrichment Tests (in-memory SQLite)
# ===========================================================================


class TestEnrichmentProvenance:
    """Tests for vessel_enrichment.py provenance + broadened filter."""

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_vessel_missing_imo_still_enriched_with_dwt(self, mock_search, mock_sleep, db):
        """Vessel with DWT but no IMO is still picked up by broadened filter."""
        vessel = _make_vessel(db, "273123456", deadweight=50000.0)
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273123456",
                "imo": "9876543",
                "callsign": "D5BX",
                "identity_history": [],
            }
        ]

        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        with patch("app.modules.vessel_enrichment.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = "test-token"
            result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

        assert result["enriched"] == 1
        assert vessel.imo == "9876543"
        assert vessel.callsign == "D5BX"

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_imo_fill_creates_vessel_history(self, mock_search, mock_sleep, db):
        """IMO fill creates VesselHistory with source='gfw_enrichment_fill' and old_value=''."""
        vessel = _make_vessel(db, "273123456")
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273123456",
                "imo": "9876543",
                "identity_history": [],
            }
        ]

        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        with patch("app.modules.vessel_enrichment.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = "test-token"
            enrich_vessels_from_gfw(db, token="test-token", limit=10)

        hist = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.field_changed == "imo",
                VesselHistory.source == "gfw_enrichment_fill",
            )
            .first()
        )
        assert hist is not None
        assert hist.old_value == ""
        assert hist.new_value == "9876543"

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_callsign_fill_creates_vessel_history(self, mock_search, mock_sleep, db):
        """Callsign fill creates VesselHistory with source='gfw_enrichment_fill'."""
        vessel = _make_vessel(db, "273123456")
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273123456",
                "callsign": "D5BX7",
                "identity_history": [],
            }
        ]

        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        with patch("app.modules.vessel_enrichment.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = "test-token"
            enrich_vessels_from_gfw(db, token="test-token", limit=10)

        hist = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.field_changed == "callsign",
                VesselHistory.source == "gfw_enrichment_fill",
            )
            .first()
        )
        assert hist is not None
        assert hist.old_value == ""
        assert hist.new_value == "D5BX7"

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_no_provenance_for_flag_year_dwt(self, mock_search, mock_sleep, db):
        """Flag, year_built, and deadweight fills do NOT create VesselHistory provenance."""
        vessel = _make_vessel(db, "273123456")
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273123456",
                "flag": "PA",
                "year_built": 2005,
                "tonnage_gt": 80000,
                "identity_history": [],
            }
        ]

        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        with patch("app.modules.vessel_enrichment.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = "test-token"
            enrich_vessels_from_gfw(db, token="test-token", limit=10)

        # Only gfw_enrichment_fill entries should be for imo/callsign, not flag/year/dwt
        fill_records = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_fill",
            )
            .all()
        )
        assert len(fill_records) == 0

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_rerun_no_duplicate_provenance(self, mock_search, mock_sleep, db):
        """Re-running enrichment does NOT create duplicate VesselHistory provenance."""
        vessel = _make_vessel(db, "273123456")
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273123456",
                "imo": "9876543",
                "callsign": "D5BX",
                "identity_history": [],
            }
        ]

        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        with patch("app.modules.vessel_enrichment.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = "test-token"
            enrich_vessels_from_gfw(db, token="test-token", limit=10)

        # First run: should have created provenance
        count1 = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_fill",
            )
            .count()
        )
        assert count1 == 2  # imo + callsign

        # Vessel now has imo and callsign set; enrichment should skip fill
        # But let's simulate re-running by clearing the vessel fields
        vessel.imo = None
        vessel.callsign = None
        db.flush()

        enrich_vessels_from_gfw(db, token="test-token", limit=10)

        count2 = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_fill",
            )
            .count()
        )
        # Should still be 2 because the dedup query prevents duplicates
        assert count2 == 2

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_historical_records_use_gfw_timestamp_naive_utc(self, mock_search, mock_sleep, db):
        """Historical records use GFW timestamp as naive UTC, tagged gfw_enrichment_history."""
        vessel = _make_vessel(db, "273123456")
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273123456",
                "identity_history": [
                    {
                        "name": "OLD NAME",
                        "flag": "PA",
                        "imo": "1111111",
                        "callsign": "ABC",
                        "date_from": "2023-06-15T12:00:00Z",
                        "date_to": "2024-01-01T00:00:00Z",
                    },
                ],
            }
        ]

        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        with patch("app.modules.vessel_enrichment.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = "test-token"
            enrich_vessels_from_gfw(db, token="test-token", limit=10)

        history_records = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_history",
            )
            .all()
        )

        # 4 records: imo, name, callsign, flag
        assert len(history_records) == 4
        for rec in history_records:
            assert rec.observed_at.tzinfo is None  # naive UTC
            assert rec.observed_at == datetime(2023, 6, 15, 12, 0, 0)
            assert rec.old_value == ""

        fields = {r.field_changed for r in history_records}
        assert fields == {"imo", "name", "callsign", "flag"}

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_historical_dedup_via_savepoint(self, mock_search, mock_sleep, db):
        """Duplicate historical records are silently skipped via savepoint + IntegrityError."""
        vessel = _make_vessel(db, "273123456")
        db.commit()

        history_entry = {
            "name": "SAME NAME",
            "date_from": "2023-06-15T12:00:00Z",
            "date_to": "2024-01-01T00:00:00Z",
        }
        mock_search.return_value = [
            {
                "mmsi": "273123456",
                "identity_history": [history_entry],
            }
        ]

        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        with patch("app.modules.vessel_enrichment.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = "test-token"
            # First run
            enrich_vessels_from_gfw(db, token="test-token", limit=10)

        count1 = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_history",
            )
            .count()
        )
        assert count1 == 1  # only "name" has a value

        # Second run with same data
        with patch("app.modules.vessel_enrichment.settings") as mock_settings:
            mock_settings.GFW_API_TOKEN = "test-token"
            enrich_vessels_from_gfw(db, token="test-token", limit=10)

        count2 = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_history",
            )
            .count()
        )
        assert count2 == 1  # no duplicate


# ===========================================================================
# 1d: populate_gfw_identity_history Tests
# ===========================================================================


class TestPopulateGFWIdentityHistory:
    """Tests for populate_gfw_identity_history() in vessel_enrichment.py."""

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_writes_history_for_vessel_without_history(self, mock_search, mock_sleep, db):
        """Vessel with MMSI but no history rows gets VesselHistory rows written."""
        vessel = _make_vessel(db, "273111111")
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273111111",
                "identity_history": [
                    {
                        "name": "OLD NAME",
                        "flag": "PA",
                        "imo": "1111111",
                        "callsign": "ABCD",
                        "date_from": "2022-01-01T00:00:00Z",
                        "date_to": "2023-01-01T00:00:00Z",
                    }
                ],
            }
        ]

        from app.modules.vessel_enrichment import populate_gfw_identity_history

        result = populate_gfw_identity_history(db, limit=10, token="test-token")

        history_rows = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_history",
            )
            .all()
        )
        assert len(history_rows) == 4  # imo, name, callsign, flag
        assert result["written"] == 4

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_skips_vessel_with_existing_history(self, mock_search, mock_sleep, db):
        """Vessel already having gfw_enrichment_history rows is in skipped count."""
        vessel = _make_vessel(db, "273222222")
        # Pre-insert a history row with source='gfw_enrichment_history'
        db.add(
            VesselHistory(
                vessel_id=vessel.vessel_id,
                field_changed="name",
                old_value="",
                new_value="PRIOR",
                observed_at=datetime(2022, 1, 1),
                source="gfw_enrichment_history",
            )
        )
        db.commit()

        from app.modules.vessel_enrichment import populate_gfw_identity_history

        result = populate_gfw_identity_history(db, limit=10, token="test-token")

        # search_vessel should NOT be called for this vessel (NOT EXISTS skips it)
        mock_search.assert_not_called()
        assert result["skipped"] == 1
        assert result["processed"] == 0

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_no_metadata_updates(self, mock_search, mock_sleep, db):
        """Only history rows are written — vessel.imo/deadweight/flag stay unchanged."""
        vessel = _make_vessel(db, "273333333", imo=None, flag=None)
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273333333",
                "imo": "9999999",
                "flag": "LR",
                "tonnage_gt": 50000,
                "identity_history": [
                    {
                        "name": "HISTORY VESSEL",
                        "flag": "LR",
                        "date_from": "2021-06-01T00:00:00Z",
                    }
                ],
            }
        ]

        from app.modules.vessel_enrichment import populate_gfw_identity_history

        populate_gfw_identity_history(db, limit=10, token="test-token")

        # Metadata fields must NOT be updated
        db.refresh(vessel)
        assert vessel.imo is None
        assert vessel.flag is None
        assert vessel.deadweight is None

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_returns_processed_written_skipped_counts(self, mock_search, mock_sleep, db):
        """Return dict has keys processed, written, skipped with correct values."""
        _make_vessel(db, "273444444")
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273444444",
                "identity_history": [
                    {
                        "name": "NAME ONE",
                        "date_from": "2020-01-01T00:00:00Z",
                    }
                ],
            }
        ]

        from app.modules.vessel_enrichment import populate_gfw_identity_history

        result = populate_gfw_identity_history(db, limit=10, token="test-token")

        assert "processed" in result
        assert "written" in result
        assert "skipped" in result
        assert result["processed"] == 1
        assert result["written"] == 1  # only "name" has a value
        assert result["skipped"] == 0

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_error_handling_continues_to_next_vessel(self, mock_search, mock_sleep, db):
        """If search_vessel raises on first vessel, processing continues to second."""
        _make_vessel(db, "273555551")
        vessel2 = _make_vessel(db, "273555552")
        db.commit()

        call_count = 0

        def side_effect(mmsi, token=None):
            nonlocal call_count
            call_count += 1
            if mmsi == "273555551":
                raise RuntimeError("API error")
            return [
                {
                    "mmsi": "273555552",
                    "identity_history": [
                        {"name": "SECOND VESSEL", "date_from": "2021-01-01T00:00:00Z"}
                    ],
                }
            ]

        mock_search.side_effect = side_effect

        from app.modules.vessel_enrichment import populate_gfw_identity_history

        result = populate_gfw_identity_history(db, limit=10, token="test-token")

        # Both vessels were attempted
        assert call_count == 2
        # processed = 1 (only vessel2 succeeded), failed = 1 (vessel1 raised)
        assert result["processed"] == 1
        assert result["failed"] == 1

        # vessel2 should have history rows
        rows = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel2.vessel_id,
                VesselHistory.source == "gfw_enrichment_history",
            )
            .all()
        )
        assert len(rows) == 1

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_idempotent_rerun(self, mock_search, mock_sleep, db):
        """Running twice does not double-insert history rows."""
        vessel = _make_vessel(db, "273666666")
        db.commit()

        mock_search.return_value = [
            {
                "mmsi": "273666666",
                "identity_history": [{"name": "SAME NAME", "date_from": "2022-06-01T00:00:00Z"}],
            }
        ]

        from app.modules.vessel_enrichment import populate_gfw_identity_history

        # First run — writes history
        result1 = populate_gfw_identity_history(db, limit=10, token="test-token")
        count1 = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_history",
            )
            .count()
        )
        assert count1 == 1
        assert result1["written"] == 1

        # Second run — vessel now has history so NOT EXISTS skips it
        result2 = populate_gfw_identity_history(db, limit=10, token="test-token")
        count2 = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.source == "gfw_enrichment_history",
            )
            .count()
        )
        assert count2 == 1  # no new rows
        assert result2["skipped"] == 1
        assert result2["processed"] == 0
