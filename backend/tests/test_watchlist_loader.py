"""Watchlist loader tests — MMSI validation, fuzzy matching, and stub creation."""
import csv
import json
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.vessel import Vessel
from app.models.vessel_watchlist import VesselWatchlist
from app.modules.watchlist_loader import (
    _is_valid_mmsi,
    _fuzzy_match_vessel,
    load_ofac_sdn,
    load_kse_list,
    load_opensanctions,
    load_fleetleaks,
    load_gur_list,
)


# ---------------------------------------------------------------------------
# Shared fixture: in-memory SQLite session
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """Create an in-memory SQLite database with all tables for each test."""
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


# ---------------------------------------------------------------------------
# Helpers for creating temp fixture files
# ---------------------------------------------------------------------------

def _write_ofac_csv(rows):
    """Write OFAC SDN CSV with header row. Returns path to temp file."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "ent_num", "SDN_NAME", "SDN_TYPE", "Program", "Title",
            "Call_Sign", "Vess_type", "Tonnage", "GRT", "Vess_flag",
            "Vess_owner", "REMARKS", "VESSEL_ID",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _write_kse_csv(rows):
    """Write KSE CSV. Returns path to temp file."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["vessel_name", "flag", "imo", "mmsi"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _write_gur_csv(rows):
    """Write GUR CSV. Returns path to temp file."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "flag", "imo", "mmsi"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _write_opensanctions_json(entities):
    """Write OpenSanctions NDJSON. Returns path to temp file."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for entity in entities:
            fh.write(json.dumps(entity) + "\n")
    return path


def _write_fleetleaks_json(vessels):
    """Write FleetLeaks JSON array. Returns path to temp file."""
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(vessels, fh)
    return path


# ---------------------------------------------------------------------------
# Unit tests for _is_valid_mmsi
# ---------------------------------------------------------------------------

class TestIsValidMMSI:
    def test_valid_9_digit(self):
        assert _is_valid_mmsi("123456789") is True

    def test_valid_with_whitespace(self):
        assert _is_valid_mmsi("  123456789  ") is True

    def test_invalid_8_digits(self):
        assert _is_valid_mmsi("12345678") is False

    def test_invalid_10_digits(self):
        assert _is_valid_mmsi("1234567890") is False

    def test_invalid_letters(self):
        assert _is_valid_mmsi("12345678a") is False

    def test_empty_string(self):
        assert _is_valid_mmsi("") is False

    def test_none_value(self):
        assert _is_valid_mmsi(None) is False


# ---------------------------------------------------------------------------
# Unit tests for _fuzzy_match_vessel (mock DB)
# ---------------------------------------------------------------------------

class TestFuzzyMatchVessel:
    def _make_vessel(self, name, flag=None):
        v = MagicMock()
        v.name = name
        v.flag = flag
        return v

    def test_exact_match_returns_vessel(self):
        db = MagicMock()
        vessel = self._make_vessel("TANKER ONE")
        db.query.return_value.filter.return_value.all.return_value = [vessel]
        result = _fuzzy_match_vessel(db, "TANKER ONE")
        # Returns (vessel, match_type, confidence) tuple
        assert result[0] is vessel
        assert result[1] == "fuzzy_name"
        assert result[2] == 100

    def test_below_threshold_returns_none(self):
        db = MagicMock()
        vessel = self._make_vessel("COMPLETELY DIFFERENT NAME")
        db.query.return_value.filter.return_value.all.return_value = [vessel]
        result = _fuzzy_match_vessel(db, "TANKER ONE", threshold=85)
        assert result is None

    def test_above_threshold_matches(self):
        db = MagicMock()
        vessel = self._make_vessel("TANKER ONEE")  # minor typo
        db.query.return_value.filter.return_value.all.return_value = [vessel]
        result = _fuzzy_match_vessel(db, "TANKER ONE", threshold=85)
        assert result[0] is vessel
        assert result[1] == "fuzzy_name"
        assert result[2] >= 85

    def test_empty_name_returns_none(self):
        db = MagicMock()
        result = _fuzzy_match_vessel(db, "")
        assert result is None

    def test_none_name_returns_none(self):
        db = MagicMock()
        result = _fuzzy_match_vessel(db, None)
        assert result is None

    def test_no_candidates_returns_none(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = _fuzzy_match_vessel(db, "TANKER ONE")
        assert result is None

    def test_candidate_with_none_name_skipped(self):
        db = MagicMock()
        v_none = self._make_vessel(None)
        v_real = self._make_vessel("TANKER ONE")
        db.query.return_value.filter.return_value.all.return_value = [v_none, v_real]
        result = _fuzzy_match_vessel(db, "TANKER ONE")
        assert result[0] is v_real


# ---------------------------------------------------------------------------
# Integration tests for stub vessel creation
# ---------------------------------------------------------------------------

class TestOfacStubCreation:
    """OFAC loader creates a vessel stub when MMSI is present but no DB match."""

    def test_stub_created_for_unmatched_mmsi(self, db):
        """Empty DB: OFAC entry with valid MMSI creates a stub vessel and watchlist entry."""
        path = _write_ofac_csv([{
            "ent_num": "",
            "SDN_NAME": "SHADOW TANKER",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "",
            "Call_Sign": "", "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "RU",
            "Vess_owner": "", "REMARKS": "MMSI 273123456",  # OFAC loader reads MMSI from REMARKS regex
        }])
        try:
            result = load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        assert result["stubs_created"] == 1
        assert result["matched"] == 1  # stubs also counted as matched
        assert result["unmatched"] == 0

        # A vessel stub was created with the correct MMSI
        vessel = db.query(Vessel).filter(Vessel.mmsi == "273123456").first()
        assert vessel is not None
        assert vessel.name == "SHADOW TANKER"

        # A VesselWatchlist entry was created
        wl = db.query(VesselWatchlist).filter(
            VesselWatchlist.vessel_id == vessel.vessel_id,
            VesselWatchlist.watchlist_source == "OFAC_SDN",
        ).first()
        assert wl is not None
        assert wl.is_active is True
        assert wl.match_type == "stub_created"

    def test_stub_not_created_for_imo_only_entry(self, db):
        """OFAC entry with IMO but no MMSI: no stub, counted as unmatched."""
        path = _write_ofac_csv([{
            "ent_num": "1234567",
            "SDN_NAME": "IMO ONLY VESSEL",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "",  # no MMSI
            "Call_Sign": "", "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "",
        }])
        try:
            result = load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        assert result["unmatched"] == 1
        assert result["stubs_created"] == 0
        # No vessel was created
        assert db.query(Vessel).count() == 0

    def test_no_duplicate_stub_if_vessel_exists(self, db):
        """If vessel already in DB by MMSI, no stub created — normal match path."""
        vessel = Vessel(mmsi="273123456", name="EXISTING VESSEL")
        db.add(vessel)
        db.flush()

        path = _write_ofac_csv([{
            "ent_num": "",
            "SDN_NAME": "EXISTING VESSEL",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "273123456",
            "Call_Sign": "", "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "",
        }])
        try:
            result = load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        # Should match, not stub
        assert result["matched"] == 1
        assert result["stubs_created"] == 0

        # Still only 1 vessel in the DB (no duplicate)
        assert db.query(Vessel).count() == 1

        # Watchlist entry was created for the existing vessel
        wl = db.query(VesselWatchlist).filter(
            VesselWatchlist.vessel_id == vessel.vessel_id,
            VesselWatchlist.watchlist_source == "OFAC_SDN",
        ).first()
        assert wl is not None

    def test_stub_vessel_has_correct_flag_from_mmsi(self, db):
        """Stub vessel derived from Russian MMSI (MID 273) gets flag 'RU'."""
        path = _write_ofac_csv([{
            "ent_num": "",
            "SDN_NAME": "RUSSIAN SHADOW",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "",
            "Call_Sign": "", "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "MMSI 273999001",  # MID 273 = RU; OFAC reads MMSI from REMARKS regex
        }])
        try:
            result = load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        assert result["stubs_created"] == 1
        vessel = db.query(Vessel).filter(Vessel.mmsi == "273999001").first()
        assert vessel is not None
        assert vessel.flag == "RU"


class TestKseStubCreation:
    """KSE loader creates a vessel stub when MMSI is present but no DB match."""

    def test_stub_created_for_unmatched_mmsi(self, db):
        """Empty DB: KSE entry with valid MMSI creates a stub vessel and watchlist entry."""
        path = _write_kse_csv([{
            "vessel_name": "KSE SHADOW", "flag": "PW",
            "imo": "", "mmsi": "511234567",
        }])
        try:
            result = load_kse_list(db, path)
        finally:
            os.unlink(path)

        assert result["stubs_created"] == 1
        assert result["matched"] == 1
        assert result["unmatched"] == 0

        vessel = db.query(Vessel).filter(Vessel.mmsi == "511234567").first()
        assert vessel is not None
        assert vessel.name == "KSE SHADOW"

        wl = db.query(VesselWatchlist).filter(
            VesselWatchlist.vessel_id == vessel.vessel_id,
            VesselWatchlist.watchlist_source == "KSE_SHADOW",
        ).first()
        assert wl is not None
        assert wl.match_type == "stub_created"

    def test_no_stub_for_imo_only(self, db):
        """KSE entry with IMO but no MMSI: no stub created, counted as unmatched."""
        path = _write_kse_csv([{
            "vessel_name": "IMO ONLY", "flag": "RU",
            "imo": "1234567", "mmsi": "",
        }])
        try:
            result = load_kse_list(db, path)
        finally:
            os.unlink(path)

        assert result["unmatched"] == 1
        assert result["stubs_created"] == 0
        assert db.query(Vessel).count() == 0


class TestOpenSanctionsStubCreation:
    """OpenSanctions loader creates a vessel stub when MMSI present but no DB match."""

    def test_stub_created_for_unmatched_mmsi(self, db):
        """Empty DB: OpenSanctions NDJSON entity with MMSI creates stub and watchlist entry."""
        entity = {
            "schema": "Vessel",
            "datasets": ["opensanctions"],
            "caption": "OPEN SHADOW",
            "properties": {
                "name": ["OPEN SHADOW"],
                "mmsi": ["620456789"],
                "flag": ["KM"],
            },
        }
        path = _write_opensanctions_json([entity])
        try:
            result = load_opensanctions(db, path)
        finally:
            os.unlink(path)

        assert result["stubs_created"] == 1
        assert result["matched"] == 1
        assert result["unmatched"] == 0

        vessel = db.query(Vessel).filter(Vessel.mmsi == "620456789").first()
        assert vessel is not None
        assert vessel.name == "OPEN SHADOW"
        assert vessel.flag == "KM"

        wl = db.query(VesselWatchlist).filter(
            VesselWatchlist.vessel_id == vessel.vessel_id,
            VesselWatchlist.watchlist_source == "OPENSANCTIONS",
        ).first()
        assert wl is not None
        assert wl.match_type == "stub_created"

    def test_no_stub_for_imo_only(self, db):
        """OpenSanctions entity with IMO but no MMSI: no stub, counted as unmatched."""
        entity = {
            "schema": "Vessel",
            "datasets": ["opensanctions"],
            "caption": "IMO ONLY VESSEL",
            "properties": {
                "name": ["IMO ONLY VESSEL"],
                "imoNumber": ["1234567"],
            },
        }
        path = _write_opensanctions_json([entity])
        try:
            result = load_opensanctions(db, path)
        finally:
            os.unlink(path)

        assert result["unmatched"] == 1
        assert result["stubs_created"] == 0
        assert db.query(Vessel).count() == 0


class TestFleetLeaksStubCreation:
    """FleetLeaks loader creates a vessel stub when MMSI present but no DB match."""

    def test_stub_created_for_unmatched_mmsi(self, db):
        """Empty DB: FleetLeaks entry with valid MMSI creates stub and watchlist entry."""
        vessels = [{"name": "FLEET SHADOW", "mmsi": "667345678", "imo": "", "flag": "SL"}]
        path = _write_fleetleaks_json(vessels)
        try:
            result = load_fleetleaks(db, path)
        finally:
            os.unlink(path)

        assert result["stubs_created"] == 1
        assert result["matched"] == 1
        assert result["unmatched"] == 0

        vessel = db.query(Vessel).filter(Vessel.mmsi == "667345678").first()
        assert vessel is not None
        assert vessel.name == "FLEET SHADOW"
        assert vessel.flag == "SL"

        wl = db.query(VesselWatchlist).filter(
            VesselWatchlist.vessel_id == vessel.vessel_id,
            VesselWatchlist.watchlist_source == "FLEETLEAKS",
        ).first()
        assert wl is not None
        assert wl.match_type == "stub_created"

    def test_no_stub_for_imo_only(self, db):
        """FleetLeaks entry with IMO but no MMSI: no stub, counted as unmatched."""
        vessels = [{"name": "IMO ONLY", "mmsi": "", "imo": "9999999", "flag": "PA"}]
        path = _write_fleetleaks_json(vessels)
        try:
            result = load_fleetleaks(db, path)
        finally:
            os.unlink(path)

        assert result["unmatched"] == 1
        assert result["stubs_created"] == 0
        assert db.query(Vessel).count() == 0


class TestGurStubCreation:
    """GUR loader creates a vessel stub when MMSI present but no DB match."""

    def test_stub_created_for_unmatched_mmsi(self, db):
        """Empty DB: GUR entry with valid MMSI creates stub and watchlist entry."""
        path = _write_gur_csv([{
            "name": "GUR SHADOW", "flag": "PW",
            "imo": "", "mmsi": "511987654",
        }])
        try:
            result = load_gur_list(db, path)
        finally:
            os.unlink(path)

        assert result["stubs_created"] == 1
        assert result["matched"] == 1
        assert result["unmatched"] == 0

        vessel = db.query(Vessel).filter(Vessel.mmsi == "511987654").first()
        assert vessel is not None
        assert vessel.name == "GUR SHADOW"

        wl = db.query(VesselWatchlist).filter(
            VesselWatchlist.vessel_id == vessel.vessel_id,
            VesselWatchlist.watchlist_source == "UKRAINE_GUR",
        ).first()
        assert wl is not None
        assert wl.match_type == "stub_created"

    def test_no_stub_for_imo_only(self, db):
        """GUR entry with IMO but no MMSI: no stub, counted as unmatched."""
        path = _write_gur_csv([{
            "name": "IMO ONLY GUR", "flag": "RU",
            "imo": "1234567", "mmsi": "",
        }])
        try:
            result = load_gur_list(db, path)
        finally:
            os.unlink(path)

        assert result["unmatched"] == 1
        assert result["stubs_created"] == 0
        assert db.query(Vessel).count() == 0

    def test_no_duplicate_stub_if_vessel_exists(self, db):
        """If vessel already in DB by MMSI, no stub, normal match path used."""
        vessel = Vessel(mmsi="511987654", name="EXISTING GUR VESSEL")
        db.add(vessel)
        db.flush()

        path = _write_gur_csv([{
            "name": "EXISTING GUR VESSEL", "flag": "PW",
            "imo": "", "mmsi": "511987654",
        }])
        try:
            result = load_gur_list(db, path)
        finally:
            os.unlink(path)

        assert result["matched"] == 1
        assert result["stubs_created"] == 0
        # No duplicate vessel
        assert db.query(Vessel).count() == 1
