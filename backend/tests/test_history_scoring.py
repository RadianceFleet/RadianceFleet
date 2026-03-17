"""Tests for VesselHistory scoring, GFW encounter blocker, and identity velocity signal.

Covers:
  - Historical IMO cross-reference (+20 points)
  - Historical callsign cross-reference (+8 points)
  - Identity change velocity signal (+10 points)
  - GFW encounter anti-merge blocker
  - Cache builder correctness
  - Feature flag gating (HISTORY_CROSS_REFERENCE_ENABLED)
  - Backward compatibility (no caches passed -> 0 new points)
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.base import STSDetectionTypeEnum
from app.models.sts_transfer import StsTransferEvent
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.modules.identity_resolver import (
    _build_encounter_cache,
    _build_history_cache,
    _get_historical_values,
    _get_recent_change_count,
    _score_candidate,
)

# -- Shared fixture: in-memory SQLite session --

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
    engine.dispose()


# -- Helper factories --

def _make_vessel(db_session, mmsi="211456789", name="TEST VESSEL", **kwargs):
    v = Vessel(mmsi=mmsi, name=name, **kwargs)
    db_session.add(v)
    db_session.flush()
    return v


def _make_history(db_session, vessel_id, field, old_val, new_val, days_ago=0):
    ts = datetime.utcnow() - timedelta(days=days_ago)
    h = VesselHistory(
        vessel_id=vessel_id,
        field_changed=field,
        old_value=old_val,
        new_value=new_val,
        observed_at=ts,
        source="test",
    )
    db_session.add(h)
    db_session.flush()
    return h


def _make_encounter(db_session, v1_id, v2_id, hours_ago=0):
    ts = datetime.utcnow() - timedelta(hours=hours_ago)
    sts = StsTransferEvent(
        vessel_1_id=v1_id,
        vessel_2_id=v2_id,
        detection_type=STSDetectionTypeEnum.GFW_ENCOUNTER,
        start_time_utc=ts,
        end_time_utc=ts + timedelta(hours=1),
    )
    db_session.add(sts)
    db_session.flush()
    return sts, ts


def _default_times():
    now = datetime.utcnow()
    return {
        "dark_last": {"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=24)},
        "new_first": {"lat": 55.0, "lon": 20.0, "ts": now},
    }


# -- Cache builder tests --

class TestBuildHistoryCache:
    def test_empty_vessel_ids(self, db):
        cache = _build_history_cache(db, set())
        assert cache == {}

    def test_correct_structure(self, db):
        v = _make_vessel(db, mmsi="211000001")
        _make_history(db, v.vessel_id, "imo", "1234567", "9074729", days_ago=10)
        _make_history(db, v.vessel_id, "name", "OLD NAME", "NEW NAME", days_ago=5)

        cache = _build_history_cache(db, {v.vessel_id})
        assert v.vessel_id in cache
        assert "imo" in cache[v.vessel_id]
        assert "name" in cache[v.vessel_id]
        assert len(cache[v.vessel_id]["imo"]) == 1
        # Check tuple structure: (old_val, new_val, observed_at)
        entry = cache[v.vessel_id]["imo"][0]
        assert entry[0] == "1234567"
        assert entry[1] == "9074729"
        assert isinstance(entry[2], datetime)

    def test_no_history_returns_empty(self, db):
        v = _make_vessel(db, mmsi="211000001")
        cache = _build_history_cache(db, {v.vessel_id})
        assert cache == {}


class TestBuildEncounterCache:
    def test_empty_vessel_ids(self, db):
        cache = _build_encounter_cache(db, set())
        assert cache == {}

    def test_correct_key_ordering(self, db):
        v1 = _make_vessel(db, mmsi="211000001")
        v2 = _make_vessel(db, mmsi="211000002")
        _, ts = _make_encounter(db, v2.vessel_id, v1.vessel_id, hours_ago=12)

        cache = _build_encounter_cache(db, {v1.vessel_id, v2.vessel_id})
        expected_key = (min(v1.vessel_id, v2.vessel_id), max(v1.vessel_id, v2.vessel_id))
        assert expected_key in cache
        assert cache[expected_key] == ts

    def test_no_encounters_returns_empty(self, db):
        v = _make_vessel(db, mmsi="211000001")
        cache = _build_encounter_cache(db, {v.vessel_id})
        assert cache == {}


# -- Helper function tests --

class TestGetHistoricalValues:
    def test_returns_all_values(self):
        cache = {
            1: {
                "imo": [("1234567", "9074729", datetime.utcnow())],
            }
        }
        values = _get_historical_values(cache, 1, "imo")
        assert "1234567" in values
        assert "9074729" in values

    def test_excludes_empty_values(self):
        cache = {
            1: {
                "imo": [("", "9074729", datetime.utcnow())],
            }
        }
        values = _get_historical_values(cache, 1, "imo")
        assert "" not in values
        assert "9074729" in values

    def test_missing_vessel_returns_empty(self):
        cache = {}
        values = _get_historical_values(cache, 999, "imo")
        assert values == set()

    def test_missing_field_returns_empty(self):
        cache = {1: {"name": [("A", "B", datetime.utcnow())]}}
        values = _get_historical_values(cache, 1, "imo")
        assert values == set()


class TestGetRecentChangeCount:
    def test_counts_real_transitions(self):
        now = datetime.utcnow()
        cutoff = now - timedelta(days=90)
        cache = {
            1: {
                "name": [("ALPHA", "BRAVO", now - timedelta(days=10))],
                "flag": [("DE", "PA", now - timedelta(days=20))],
                "callsign": [("ABC1", "DEF2", now - timedelta(days=30))],
            }
        }
        assert _get_recent_change_count(cache, 1, cutoff) == 3

    def test_snapshot_observations_excluded(self):
        """Snapshot-only records (old_value="") do NOT count."""
        now = datetime.utcnow()
        cutoff = now - timedelta(days=90)
        cache = {
            1: {
                "name": [("", "BRAVO", now - timedelta(days=10))],
                "flag": [("", "PA", now - timedelta(days=20))],
            }
        }
        assert _get_recent_change_count(cache, 1, cutoff) == 0

    def test_old_changes_excluded(self):
        """Changes before cutoff are not counted."""
        now = datetime.utcnow()
        cutoff = now - timedelta(days=90)
        cache = {
            1: {
                "name": [("ALPHA", "BRAVO", now - timedelta(days=200))],
            }
        }
        assert _get_recent_change_count(cache, 1, cutoff) == 0

    def test_same_old_new_excluded(self):
        """old_val == new_val is not a real transition."""
        now = datetime.utcnow()
        cutoff = now - timedelta(days=90)
        cache = {
            1: {
                "name": [("ALPHA", "ALPHA", now - timedelta(days=10))],
            }
        }
        assert _get_recent_change_count(cache, 1, cutoff) == 0


# -- _score_candidate scoring tests --

class TestHistoricalIMOScoring:
    def test_shared_historical_imo_gives_20_points(self, db):
        """Two vessels with shared historical IMO 9074729 -> +20 points."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        # Both had IMO 9074729 in history
        _make_history(db, dark_v.vessel_id, "imo", "1111111", "9074729", days_ago=60)
        _make_history(db, new_v.vessel_id, "imo", "0000000", "9074729", days_ago=30)
        db.commit()

        history_cache = _build_history_cache(db, {dark_v.vessel_id, new_v.vessel_id})
        times = _default_times()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache=history_cache,
            encounter_cache={},
        )

        assert "historical_shared_imo" in reasons
        assert reasons["historical_shared_imo"]["points"] == 20

    def test_different_current_imos_block_merge(self, db):
        """Different valid current IMOs -> same_imo not set, but
        imo_mismatch isn't a thing in current code. The historical check
        should still look because there's no same_imo or imo_mismatch."""
        dark_v = _make_vessel(db, mmsi="211000001", imo="9074729")
        new_v = _make_vessel(db, mmsi="211000002", imo="9166778")
        db.commit()

        times = _default_times()
        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache={},
            encounter_cache={},
        )

        # No same_imo because they differ, no historical data either
        assert "same_imo" not in reasons
        assert "historical_shared_imo" not in reasons

    def test_same_current_imo_skips_historical(self, db):
        """When same_imo is already set, historical check is skipped."""
        dark_v = _make_vessel(db, mmsi="211000001", imo="9074729")
        new_v = _make_vessel(db, mmsi="211000002", imo="9074729")
        db.commit()

        times = _default_times()
        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache={},
            encounter_cache={},
        )

        assert "same_imo" in reasons
        assert "historical_shared_imo" not in reasons

    def test_invalid_imo_in_history_no_points(self, db):
        """Invalid IMO checksum (1111111) in history -> no points."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        _make_history(db, dark_v.vessel_id, "imo", "", "1111111", days_ago=60)
        _make_history(db, new_v.vessel_id, "imo", "", "1111111", days_ago=30)
        db.commit()

        history_cache = _build_history_cache(db, {dark_v.vessel_id, new_v.vessel_id})
        times = _default_times()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache=history_cache,
            encounter_cache={},
        )

        assert "historical_shared_imo" not in reasons


class TestHistoricalCallsignScoring:
    def test_shared_historical_callsign_gives_8_points(self, db):
        """Two vessels with shared historical callsign -> +8 points."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        _make_history(db, dark_v.vessel_id, "callsign", "", "UBCX7", days_ago=60)
        _make_history(db, new_v.vessel_id, "callsign", "", "UBCX7", days_ago=30)
        db.commit()

        history_cache = _build_history_cache(db, {dark_v.vessel_id, new_v.vessel_id})
        times = _default_times()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache=history_cache,
            encounter_cache={},
        )

        assert "historical_shared_callsign" in reasons
        assert reasons["historical_shared_callsign"]["points"] == 8

    def test_same_current_callsign_skips_historical(self, db):
        """When same_callsign is already set, historical check is skipped."""
        dark_v = _make_vessel(db, mmsi="211000001", callsign="UBCX7")
        new_v = _make_vessel(db, mmsi="211000002", callsign="UBCX7")
        db.commit()

        times = _default_times()
        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache={},
            encounter_cache={},
        )

        assert "same_callsign" in reasons
        assert "historical_shared_callsign" not in reasons


class TestIdentityChangeVelocity:
    def test_velocity_2_fields_gives_10_points(self, db):
        """Vessel with 2+ REAL field transitions in 90d -> +10."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        _make_history(db, new_v.vessel_id, "name", "ALPHA", "BRAVO", days_ago=30)
        _make_history(db, new_v.vessel_id, "flag", "DE", "PA", days_ago=20)
        db.commit()

        history_cache = _build_history_cache(db, {dark_v.vessel_id, new_v.vessel_id})
        times = _default_times()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache=history_cache,
            encounter_cache={},
        )

        assert "identity_change_velocity" in reasons
        assert reasons["identity_change_velocity"]["points"] == 10
        assert reasons["identity_change_velocity"]["fields_changed"] >= 2

    def test_velocity_1_field_no_points(self, db):
        """Only 1 field changed -> no velocity bonus."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        _make_history(db, new_v.vessel_id, "name", "ALPHA", "BRAVO", days_ago=30)
        db.commit()

        history_cache = _build_history_cache(db, {dark_v.vessel_id, new_v.vessel_id})
        times = _default_times()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache=history_cache,
            encounter_cache={},
        )

        assert "identity_change_velocity" not in reasons

    def test_velocity_snapshots_not_counted(self, db):
        """Snapshot-only records (old_value='') don't count for velocity."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        # Snapshots: old_value is empty
        _make_history(db, new_v.vessel_id, "name", "", "BRAVO", days_ago=30)
        _make_history(db, new_v.vessel_id, "flag", "", "PA", days_ago=20)
        _make_history(db, new_v.vessel_id, "callsign", "", "XYZ1", days_ago=10)
        db.commit()

        history_cache = _build_history_cache(db, {dark_v.vessel_id, new_v.vessel_id})
        times = _default_times()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache=history_cache,
            encounter_cache={},
        )

        assert "identity_change_velocity" not in reasons


class TestEncounterBlocker:
    def test_encounter_after_gap_blocks_merge(self, db):
        """GFW encounter after dark_last['ts'] -> blocked (return 0)."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        db.commit()

        now = datetime.utcnow()
        dark_last_ts = now - timedelta(hours=48)
        encounter_ts = now - timedelta(hours=12)  # After dark_last

        encounter_cache = {
            (min(dark_v.vessel_id, new_v.vessel_id), max(dark_v.vessel_id, new_v.vessel_id)): encounter_ts,
        }

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": dark_last_ts},
            new_first={"lat": 55.0, "lon": 20.0, "ts": now},
            distance=0.0, time_delta_h=48.0, max_travel=768.0,
            corridor_vessels_cache={},
            history_cache={},
            encounter_cache=encounter_cache,
        )

        assert score == 0
        assert "encounter_after_gap" in reasons
        assert reasons["encounter_after_gap"]["blocked"] is True

    def test_encounter_before_gap_no_block(self, db):
        """GFW encounter before dark_last['ts'] -> no block."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        db.commit()

        now = datetime.utcnow()
        dark_last_ts = now - timedelta(hours=24)
        encounter_ts = now - timedelta(hours=72)  # Before dark_last

        encounter_cache = {
            (min(dark_v.vessel_id, new_v.vessel_id), max(dark_v.vessel_id, new_v.vessel_id)): encounter_ts,
        }

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": dark_last_ts},
            new_first={"lat": 55.0, "lon": 20.0, "ts": now},
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache={},
            encounter_cache=encounter_cache,
        )

        assert score > 0
        assert "encounter_after_gap" not in reasons

    def test_no_encounter_no_block(self, db):
        """No encounter in cache -> no block."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        db.commit()

        times = _default_times()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
            history_cache={},
            encounter_cache={},
        )

        assert "encounter_after_gap" not in reasons


class TestFeatureFlagGating:
    def test_disabled_flag_no_historical_scoring(self, db):
        """HISTORY_CROSS_REFERENCE_ENABLED=False -> no historical scoring, no velocity."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        _make_history(db, dark_v.vessel_id, "imo", "1111111", "9074729", days_ago=60)
        _make_history(db, new_v.vessel_id, "imo", "0000000", "9074729", days_ago=30)
        _make_history(db, dark_v.vessel_id, "callsign", "", "UBCX7", days_ago=60)
        _make_history(db, new_v.vessel_id, "callsign", "", "UBCX7", days_ago=30)
        _make_history(db, new_v.vessel_id, "name", "ALPHA", "BRAVO", days_ago=30)
        _make_history(db, new_v.vessel_id, "flag", "DE", "PA", days_ago=20)
        db.commit()

        history_cache = _build_history_cache(db, {dark_v.vessel_id, new_v.vessel_id})
        times = _default_times()

        from unittest.mock import patch
        with patch("app.modules.merge_candidates.settings") as mock_settings:
            mock_settings.HISTORY_CROSS_REFERENCE_ENABLED = False
            mock_settings.ISM_CONTINUITY_SCORING_ENABLED = False
            mock_settings.FINGERPRINT_ENABLED = False

            score, reasons = _score_candidate(
                db, dark_v, new_v,
                dark_last=times["dark_last"],
                new_first=times["new_first"],
                distance=0.0, time_delta_h=24.0, max_travel=384.0,
                corridor_vessels_cache={},
                history_cache=history_cache,
                encounter_cache={},
            )

        assert "historical_shared_imo" not in reasons
        assert "historical_shared_callsign" not in reasons
        assert "identity_change_velocity" not in reasons


class TestBackwardCompatibility:
    def test_no_caches_passed_produces_zero_new_points(self, db):
        """Calling _score_candidate() without history/encounter caches
        (defaulting to None) -> all new scoring blocks produce 0 points."""
        dark_v = _make_vessel(db, mmsi="211000001")
        new_v = _make_vessel(db, mmsi="211000002")
        db.commit()

        times = _default_times()

        # Call WITHOUT history_cache and encounter_cache (defaults to None)
        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last=times["dark_last"],
            new_first=times["new_first"],
            distance=0.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
        )

        assert "historical_shared_imo" not in reasons
        assert "historical_shared_callsign" not in reasons
        assert "identity_change_velocity" not in reasons
        assert "encounter_after_gap" not in reasons
