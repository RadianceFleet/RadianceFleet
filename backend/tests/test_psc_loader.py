"""Tests for PSC detention data loaders (FTM JSON + EMSA ban API)."""
import json
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from app.modules.psc_loader import load_psc_ftm, load_emsa_bans


def _make_vessel(vessel_id=1, imo="1234567", name="TEST VESSEL", psc_detained=False):
    """Create a vessel-like mock."""
    v = MagicMock()
    v.vessel_id = vessel_id
    v.imo = imo
    v.name = name
    v.psc_detained_last_12m = psc_detained
    return v


def _make_db_for_imo_lookup(vessel):
    """Build a mock DB that finds a vessel by IMO query."""
    db = MagicMock()
    # load_psc_ftm: db.query(Vessel).filter(Vessel.imo == imo).first()
    db.query.return_value.filter.return_value.first.return_value = vessel
    return db


class TestLoadPscFtm:
    def test_load_ftm_matches_vessel_by_imo(self, tmp_path):
        """FTM entity with imoNumber property matches vessel."""
        vessel = _make_vessel(imo="9553359")
        db = _make_db_for_imo_lookup(vessel)

        ftm_data = [{
            "id": "test-001",
            "schema": "Vessel",
            "properties": {
                "imoNumber": ["9553359"],
                "name": ["CRUDE CARRIER"],
                "date": [date.today().isoformat()],
            },
        }]

        ftm_path = tmp_path / "test_ftm.json"
        ftm_path.write_text(json.dumps(ftm_data))

        result = load_psc_ftm(db, ftm_path, source="test_mou")

        assert result["total"] == 1
        assert result["matched"] == 1

    def test_load_ftm_sets_detained_true(self, tmp_path):
        """Matching vessel gets psc_detained_last_12m set to True."""
        vessel = _make_vessel(imo="9553359", psc_detained=False)
        db = _make_db_for_imo_lookup(vessel)

        ftm_data = [{
            "schema": "Vessel",
            "properties": {
                "imoNumber": ["9553359"],
                "date": [date.today().isoformat()],
            },
        }]

        ftm_path = tmp_path / "test_ftm.json"
        ftm_path.write_text(json.dumps(ftm_data))

        load_psc_ftm(db, ftm_path, source="test")

        assert vessel.psc_detained_last_12m is True

    def test_old_detention_not_flagged(self, tmp_path):
        """Detention older than recency_days is skipped."""
        vessel = _make_vessel(imo="9553359")
        db = _make_db_for_imo_lookup(vessel)

        old_date = (date.today() - timedelta(days=400)).isoformat()
        ftm_data = [{
            "schema": "Vessel",
            "properties": {
                "imoNumber": ["9553359"],
                "date": [old_date],
            },
        }]

        ftm_path = tmp_path / "test_ftm.json"
        ftm_path.write_text(json.dumps(ftm_data))

        result = load_psc_ftm(db, ftm_path, source="test", recency_days=365)

        assert result["skipped"] == 1
        assert result["matched"] == 0

    def test_ndjson_format(self, tmp_path):
        """Newline-delimited JSON (one entity per line) is parsed correctly."""
        vessel = _make_vessel(imo="9553359")
        db = _make_db_for_imo_lookup(vessel)

        lines = [
            json.dumps({"schema": "Vessel", "properties": {"imoNumber": ["9553359"], "date": [date.today().isoformat()]}}),
            json.dumps({"schema": "Vessel", "properties": {"imoNumber": ["9999999"], "date": [date.today().isoformat()]}}),
        ]

        ftm_path = tmp_path / "test_ftm.json"
        ftm_path.write_text("\n".join(lines))

        result = load_psc_ftm(db, ftm_path, source="test")

        # Both entities processed (both match the same mock vessel)
        assert result["total"] == 2


class TestLoadEmsaBans:
    def test_load_emsa_ban_matches_vessel(self, tmp_path):
        """EMSA ban entry with imoNumber matches vessel."""
        vessel = _make_vessel(imo="9553359", psc_detained=False)
        db = _make_db_for_imo_lookup(vessel)

        ban_data = [{
            "imoNumber": "9553359",
            "shipName": "CRUDE CARRIER",
            "banDate": date.today().isoformat(),
            "banningAuthority": "Paris MOU",
            "flag": "KM",
        }]

        ban_path = tmp_path / "test_bans.json"
        ban_path.write_text(json.dumps(ban_data))

        result = load_emsa_bans(db, ban_path)

        assert result["total"] == 1
        assert result["matched"] == 1
        assert vessel.psc_detained_last_12m is True
