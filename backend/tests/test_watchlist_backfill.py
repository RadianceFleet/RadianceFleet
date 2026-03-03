"""Tests for watchlist identity backfill with provenance + DMA history tracking.

Verifies that:
- OFAC/KSE/OpenSanctions/FleetLeaks/GUR loaders backfill IMO (and callsign for OFAC)
  onto vessel records when matched by exact MMSI or IMO, with VesselHistory provenance.
- Fuzzy name matches do NOT trigger backfill.
- Invalid/zero IMOs are rejected by validate_imo_checksum.
- Existing IMO is never overwritten (fill-if-empty only).
- Duplicate provenance rows are not created on re-run.
- _track_field_change (used by DMA) only creates VesselHistory when both old and new
  values are non-None (initial population is skipped).

Uses in-memory SQLite with real ORM models.
"""
import csv
import io
import json
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.models.vessel_watchlist import VesselWatchlist
from app.modules.watchlist_loader import (
    load_ofac_sdn,
    load_kse_list,
    load_opensanctions,
    load_fleetleaks,
    load_gur_list,
)
from app.modules.ingest import _track_field_change
from app.utils.vessel_identity import validate_imo_checksum


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
# Helper: create vessel
# ---------------------------------------------------------------------------

def _make_vessel(db, mmsi="211456789", name="TEST VESSEL", **kwargs):
    v = Vessel(mmsi=mmsi, name=name, **kwargs)
    db.add(v)
    db.flush()
    return v


# ---------------------------------------------------------------------------
# Helper: write temp CSV for OFAC
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
# A valid IMO for testing: 9074729 (checksum: 7*9 + 6*0 + 5*7 + 4*4 + 3*7 + 2*2 = 63+0+35+16+21+4 = 139 -> 139%10 = 9)
# Let's compute a valid one: IMO 1234567
# 1*7 + 2*6 + 3*5 + 4*4 + 5*3 + 6*2 = 7+12+15+16+15+12 = 77 -> 77%10 = 7 -> check digit = 7. Valid!
# ---------------------------------------------------------------------------

VALID_IMO = "1234567"
INVALID_IMO_CHECKSUM = "1234568"  # check digit should be 7, not 8
ZERO_IMO = "0000000"


# ===========================================================================
# OFAC backfill tests
# ===========================================================================

class TestOfacBackfill:
    """OFAC loader identity backfill tests."""

    def test_mmsi_match_backfills_imo(self, db):
        """Vessel matched by MMSI gets IMO backfilled + VesselHistory provenance."""
        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW TANKER")
        assert vessel.imo is None

        path = _write_ofac_csv([{
            "ent_num": VALID_IMO,
            "SDN_NAME": "SHADOW TANKER",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "211456789",
            "Call_Sign": "",
            "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "Sanctioned",
        }])
        try:
            result = load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        assert result["matched"] == 1
        # Vessel IMO should be backfilled
        db.refresh(vessel)
        assert vessel.imo == VALID_IMO

        # VesselHistory provenance should exist
        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "imo",
            VesselHistory.source == "watchlist_backfill:ofac",
        ).all()
        assert len(history) == 1
        assert history[0].old_value == ""
        assert history[0].new_value == VALID_IMO

    def test_no_duplicate_provenance_on_rerun(self, db):
        """Re-running on same vessel does NOT create duplicate provenance."""
        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW TANKER")

        path = _write_ofac_csv([{
            "ent_num": VALID_IMO,
            "SDN_NAME": "SHADOW TANKER",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "211456789",
            "Call_Sign": "UBCD",
            "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "Sanctioned",
        }])
        try:
            load_ofac_sdn(db, path)
            # Run again -- vessel now has IMO, so backfill won't fire (fill-if-empty)
            # But also callsign is now set, so also won't fire
            load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        # Only 1 IMO history + 1 callsign history entry
        imo_history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "imo",
            VesselHistory.source == "watchlist_backfill:ofac",
        ).all()
        assert len(imo_history) == 1

        callsign_history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "callsign",
            VesselHistory.source == "watchlist_backfill:ofac",
        ).all()
        assert len(callsign_history) == 1

    def test_fuzzy_name_match_does_not_backfill(self, db):
        """Fuzzy name match (not exact MMSI/IMO) does NOT backfill IMO."""
        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW TANKER")

        # No VESSEL_ID (MMSI) and ent_num won't match any vessel IMO
        # The vessel will be matched by fuzzy name only
        path = _write_ofac_csv([{
            "ent_num": VALID_IMO,
            "SDN_NAME": "SHADOW TANKER",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "",  # No MMSI -> forces fuzzy name match
            "Call_Sign": "UBCD",
            "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "Sanctioned",
        }])
        try:
            result = load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        assert result["matched"] == 1
        db.refresh(vessel)
        # IMO should NOT be backfilled for fuzzy matches
        assert vessel.imo is None
        # No VesselHistory
        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.source == "watchlist_backfill:ofac",
        ).all()
        assert len(history) == 0

    def test_zero_imo_rejected(self, db):
        """IMO '0000000' is rejected by validate_imo_checksum."""
        assert validate_imo_checksum(ZERO_IMO) is False

        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW TANKER")
        path = _write_ofac_csv([{
            "ent_num": ZERO_IMO,
            "SDN_NAME": "SHADOW TANKER",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "211456789",
            "Call_Sign": "",
            "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "Sanctioned",
        }])
        try:
            load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        db.refresh(vessel)
        assert vessel.imo is None

    def test_invalid_checksum_rejected(self, db):
        """IMO with invalid checksum is rejected."""
        assert validate_imo_checksum(INVALID_IMO_CHECKSUM) is False

        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW TANKER")
        path = _write_ofac_csv([{
            "ent_num": INVALID_IMO_CHECKSUM,
            "SDN_NAME": "SHADOW TANKER",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "211456789",
            "Call_Sign": "",
            "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "Sanctioned",
        }])
        try:
            load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        db.refresh(vessel)
        assert vessel.imo is None

    def test_existing_imo_never_overwritten(self, db):
        """Vessel with existing IMO keeps it -- fill-if-empty only."""
        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW TANKER", imo="9999999")
        # 9999999 checksum: 9*7+9*6+9*5+9*4+9*3+9*2 = 63+54+45+36+27+18 = 243 -> 243%10=3, not 9
        # Doesn't matter -- we just need it to be non-empty

        path = _write_ofac_csv([{
            "ent_num": VALID_IMO,
            "SDN_NAME": "SHADOW TANKER",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "211456789",
            "Call_Sign": "",
            "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "Sanctioned",
        }])
        try:
            load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        db.refresh(vessel)
        assert vessel.imo == "9999999"  # Unchanged

    def test_callsign_backfilled_from_ofac(self, db):
        """Callsign is backfilled from OFAC's Call_Sign field."""
        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW TANKER")
        assert vessel.callsign is None

        path = _write_ofac_csv([{
            "ent_num": VALID_IMO,
            "SDN_NAME": "SHADOW TANKER",
            "SDN_TYPE": "Vessel",
            "VESSEL_ID": "211456789",
            "Call_Sign": "UBCD5",
            "Program": "", "Title": "", "Vess_type": "",
            "Tonnage": "", "GRT": "", "Vess_flag": "",
            "Vess_owner": "", "REMARKS": "Sanctioned",
        }])
        try:
            load_ofac_sdn(db, path)
        finally:
            os.unlink(path)

        db.refresh(vessel)
        assert vessel.callsign == "UBCD5"

        # VesselHistory for callsign
        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "callsign",
            VesselHistory.source == "watchlist_backfill:ofac",
        ).all()
        assert len(history) == 1
        assert history[0].new_value == "UBCD5"


# ===========================================================================
# KSE backfill tests
# ===========================================================================

class TestKseBackfill:
    """KSE loader identity backfill tests."""

    def test_kse_mmsi_match_backfills_imo(self, db):
        """KSE: vessel matched by MMSI gets IMO backfilled."""
        vessel = _make_vessel(db, mmsi="211456789", name="DARK TRADER")
        assert vessel.imo is None

        path = _write_kse_csv([{
            "vessel_name": "DARK TRADER",
            "flag": "RU",
            "imo": VALID_IMO,
            "mmsi": "211456789",
        }])
        try:
            result = load_kse_list(db, path)
        finally:
            os.unlink(path)

        assert result["matched"] == 1
        db.refresh(vessel)
        assert vessel.imo == VALID_IMO

        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "imo",
            VesselHistory.source == "watchlist_backfill:kse",
        ).all()
        assert len(history) == 1

    def test_kse_invalid_imo_not_backfilled(self, db):
        """KSE: invalid checksum IMO is not backfilled."""
        vessel = _make_vessel(db, mmsi="211456789", name="DARK TRADER")

        path = _write_kse_csv([{
            "vessel_name": "DARK TRADER",
            "flag": "RU",
            "imo": INVALID_IMO_CHECKSUM,
            "mmsi": "211456789",
        }])
        try:
            load_kse_list(db, path)
        finally:
            os.unlink(path)

        db.refresh(vessel)
        assert vessel.imo is None


# ===========================================================================
# OpenSanctions backfill tests
# ===========================================================================

class TestOpenSanctionsBackfill:
    """OpenSanctions loader identity backfill tests."""

    def test_opensanctions_mmsi_match_backfills_imo(self, db):
        """OpenSanctions: vessel matched by MMSI gets IMO backfilled."""
        vessel = _make_vessel(db, mmsi="211456789", name="SANCTIONED CARRIER")
        assert vessel.imo is None

        entities = [{
            "schema": "Vessel",
            "caption": "SANCTIONED CARRIER",
            "properties": {
                "name": "SANCTIONED CARRIER",
                "mmsi": "211456789",
                "imoNumber": VALID_IMO,
                "flag": "RU",
            },
            "datasets": ["us_ofac_sdn"],
        }]
        path = _write_opensanctions_json(entities)
        try:
            result = load_opensanctions(db, path)
        finally:
            os.unlink(path)

        assert result["matched"] == 1
        db.refresh(vessel)
        assert vessel.imo == VALID_IMO

        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "imo",
            VesselHistory.source == "watchlist_backfill:opensanctions",
        ).all()
        assert len(history) == 1

    def test_opensanctions_fuzzy_match_no_backfill(self, db):
        """OpenSanctions: fuzzy name match does not backfill IMO."""
        vessel = _make_vessel(db, mmsi="211456789", name="SANCTIONED CARRIER")

        entities = [{
            "schema": "Vessel",
            "caption": "SANCTIONED CARRIER",
            "properties": {
                "name": "SANCTIONED CARRIER",
                # No MMSI, no IMO in DB -> fuzzy match
                "imoNumber": VALID_IMO,
                "flag": "RU",
            },
            "datasets": ["opensanctions"],
        }]
        path = _write_opensanctions_json(entities)
        try:
            load_opensanctions(db, path)
        finally:
            os.unlink(path)

        db.refresh(vessel)
        assert vessel.imo is None


# ===========================================================================
# FleetLeaks backfill tests
# ===========================================================================

class TestFleetLeaksBackfill:
    """FleetLeaks loader identity backfill tests."""

    def test_fleetleaks_mmsi_match_backfills_imo(self, db):
        """FleetLeaks: vessel matched by MMSI gets IMO backfilled."""
        vessel = _make_vessel(db, mmsi="211456789", name="LEAKED VESSEL")
        assert vessel.imo is None

        data = [{
            "name": "LEAKED VESSEL",
            "mmsi": "211456789",
            "imo": VALID_IMO,
            "flag": "RU",
        }]
        path = _write_fleetleaks_json(data)
        try:
            result = load_fleetleaks(db, path)
        finally:
            os.unlink(path)

        assert result["matched"] == 1
        db.refresh(vessel)
        assert vessel.imo == VALID_IMO

        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "imo",
            VesselHistory.source == "watchlist_backfill:fleetleaks",
        ).all()
        assert len(history) == 1

    def test_fleetleaks_existing_imo_not_overwritten(self, db):
        """FleetLeaks: existing IMO never overwritten."""
        vessel = _make_vessel(db, mmsi="211456789", name="LEAKED VESSEL", imo="7654321")

        data = [{
            "name": "LEAKED VESSEL",
            "mmsi": "211456789",
            "imo": VALID_IMO,
            "flag": "RU",
        }]
        path = _write_fleetleaks_json(data)
        try:
            load_fleetleaks(db, path)
        finally:
            os.unlink(path)

        db.refresh(vessel)
        assert vessel.imo == "7654321"


# ===========================================================================
# GUR backfill tests
# ===========================================================================

class TestGurBackfill:
    """GUR loader identity backfill tests."""

    def test_gur_mmsi_match_backfills_imo(self, db):
        """GUR: vessel matched by MMSI gets IMO backfilled."""
        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW FLEET TANKER")
        assert vessel.imo is None

        path = _write_gur_csv([{
            "name": "SHADOW FLEET TANKER",
            "flag": "RU",
            "imo": VALID_IMO,
            "mmsi": "211456789",
        }])
        try:
            result = load_gur_list(db, path)
        finally:
            os.unlink(path)

        assert result["matched"] == 1
        db.refresh(vessel)
        assert vessel.imo == VALID_IMO

        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "imo",
            VesselHistory.source == "watchlist_backfill:gur",
        ).all()
        assert len(history) == 1

    def test_gur_fuzzy_match_no_backfill(self, db):
        """GUR: fuzzy name match does not backfill."""
        vessel = _make_vessel(db, mmsi="211456789", name="SHADOW FLEET TANKER")

        path = _write_gur_csv([{
            "name": "SHADOW FLEET TANKER",
            "flag": "",
            "imo": VALID_IMO,
            "mmsi": "",  # No MMSI -> fuzzy name match
        }])
        try:
            load_gur_list(db, path)
        finally:
            os.unlink(path)

        db.refresh(vessel)
        assert vessel.imo is None


# ===========================================================================
# DMA history tracking tests (via _track_field_change)
# ===========================================================================

class TestDmaHistoryTracking:
    """DMA identity change creates VesselHistory; initial population does not."""

    def test_identity_change_creates_history(self, db):
        """When both old and new values are non-None, VesselHistory is created."""
        from app.models.ais_point import AISPoint

        vessel = _make_vessel(db, mmsi="211456789", name="TANKER A", imo="1234567")
        # Need an AIS point for _track_field_change internal logic
        pt = AISPoint(
            vessel_id=vessel.vessel_id,
            lat=55.0, lon=10.0,
            timestamp_utc=datetime.utcnow() - timedelta(hours=2),
            sog=5.0, cog=90.0,
        )
        db.add(pt)
        db.flush()

        ts = datetime.utcnow()
        _track_field_change(db, vessel, "imo", "1234567", "7654321", ts, "dma")
        db.flush()

        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "imo",
            VesselHistory.source == "dma",
        ).all()
        assert len(history) == 1
        assert history[0].old_value == "1234567"
        assert history[0].new_value == "7654321"

    def test_initial_population_no_history(self, db):
        """When old_val is None, _track_field_change returns early (no history)."""
        vessel = _make_vessel(db, mmsi="211456789", name="TANKER B")
        assert vessel.imo is None

        ts = datetime.utcnow()
        _track_field_change(db, vessel, "imo", None, "1234567", ts, "dma")
        db.flush()

        history = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "imo",
            VesselHistory.source == "dma",
        ).all()
        assert len(history) == 0


# ===========================================================================
# validate_imo_checksum unit tests
# ===========================================================================

class TestValidateImoChecksum:
    """Unit tests for the IMO checksum validator."""

    def test_valid_imo(self):
        assert validate_imo_checksum("1234567") is True

    def test_invalid_checksum(self):
        assert validate_imo_checksum("1234568") is False

    def test_zero_imo(self):
        assert validate_imo_checksum("0000000") is False

    def test_too_short(self):
        assert validate_imo_checksum("123456") is False

    def test_too_long(self):
        assert validate_imo_checksum("12345678") is False

    def test_with_imo_prefix(self):
        assert validate_imo_checksum("IMO1234567") is True

    def test_non_numeric(self):
        assert validate_imo_checksum("ABCDEFG") is False
