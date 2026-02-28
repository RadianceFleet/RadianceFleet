"""Tests for E3 (transaction atomicity) and E4 (missing database index).

E3: Verifies that:
  - purge_old() does not auto-commit (caller manages transaction)
  - paid_verification uses flush (not commit) so caller controls commit
  - corridor import uses flush so partial failures roll back

E4: Verifies that:
  - AISObservation has an index on received_utc
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.ais_observation import AISObservation
from app.models.vessel import Vessel
from app.models.verification_log import VerificationLog


# ── Shared fixture: in-memory SQLite session ─────────────────────────────────

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


@pytest.fixture
def db_with_vessel(db):
    """In-memory SQLite session with a pre-created vessel for FK references."""
    vessel = Vessel(mmsi="999888777", name="TEST TANKER")
    db.add(vessel)
    db.commit()
    return db, vessel


# ══════════════════════════════════════════════════════════════════════════════
# E3: purge_old() does NOT auto-commit
# ══════════════════════════════════════════════════════════════════════════════


class TestPurgeOldNoAutoCommit:
    """Verify that AISObservation.purge_old() does not call db.commit()."""

    def test_purge_old_does_not_commit(self):
        """purge_old() should not call commit on the session."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.delete.return_value = 5

        result = AISObservation.purge_old(mock_db, hours=72)

        assert result == 5
        mock_db.commit.assert_not_called()

    def test_purge_old_delete_is_uncommitted(self, db):
        """After purge_old, the deletion is visible in-session but not committed."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=100)
        recent_time = now - timedelta(hours=1)

        old_obs = AISObservation(
            mmsi="111111111", source="aishub",
            timestamp_utc=old_time, received_utc=old_time,
            lat=55.0, lon=25.0,
        )
        recent_obs = AISObservation(
            mmsi="222222222", source="aisstream",
            timestamp_utc=recent_time, received_utc=recent_time,
            lat=56.0, lon=26.0,
        )
        db.add_all([old_obs, recent_obs])
        db.commit()

        assert db.query(AISObservation).count() == 2

        deleted = AISObservation.purge_old(db, hours=72)
        assert deleted == 1

        # The deletion is visible within the session (flushed but not committed)
        remaining = db.query(AISObservation).all()
        assert len(remaining) == 1
        assert remaining[0].mmsi == "222222222"

        # But if we rollback, the old record reappears (proves it was not committed)
        db.rollback()
        after_rollback = db.query(AISObservation).all()
        assert len(after_rollback) == 2

    def test_purge_old_caller_commits(self, db):
        """Caller can commit after purge_old to persist the deletion."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=100)

        old_obs = AISObservation(
            mmsi="333333333", source="kystverket",
            timestamp_utc=old_time, received_utc=old_time,
            lat=60.0, lon=10.0,
        )
        db.add(old_obs)
        db.commit()

        deleted = AISObservation.purge_old(db, hours=72)
        assert deleted == 1

        # Caller explicitly commits
        db.commit()

        # Now rollback has no effect -- the deletion is persisted
        db.rollback()
        remaining = db.query(AISObservation).all()
        assert len(remaining) == 0


# ══════════════════════════════════════════════════════════════════════════════
# E3: paid_verification transaction atomicity
# ══════════════════════════════════════════════════════════════════════════════


class TestPaidVerificationAtomicity:
    """Verify that verify_vessel() uses flush (not commit) for both budget
    exceeded and normal verification paths."""

    def test_budget_exceeded_does_not_commit(self):
        """Budget-exceeded log entry should be flushed, not committed."""
        mock_db = MagicMock()
        mock_vessel = MagicMock()
        mock_vessel.mmsi = "123456789"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel

        # Mock budget to be exceeded: spend=500 + estimated_cost=0.50 > budget=500
        with patch("app.modules.paid_verification.get_monthly_spend", return_value=500.0), \
             patch("app.modules.paid_verification.settings") as mock_settings:
            mock_settings.VERIFICATION_MONTHLY_BUDGET_USD = 500.0

            from app.modules.paid_verification import verify_vessel
            result = verify_vessel(mock_db, vessel_id=1, provider_name="spire")

        assert result.success is False
        assert "budget exceeded" in result.error.lower()

        # flush is called, but NOT commit
        mock_db.flush.assert_called()
        mock_db.commit.assert_not_called()

    def test_verification_result_does_not_commit(self):
        """Verification result log should be flushed, not committed."""
        mock_db = MagicMock()
        mock_vessel = MagicMock()
        mock_vessel.mmsi = "123456789"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel

        with patch("app.modules.paid_verification.get_monthly_spend", return_value=0.0), \
             patch("app.modules.paid_verification.settings") as mock_settings:
            mock_settings.VERIFICATION_MONTHLY_BUDGET_USD = 500.0
            mock_settings.SKYLIGHT_API_KEY = None  # Stub will return error

            from app.modules.paid_verification import verify_vessel
            result = verify_vessel(mock_db, vessel_id=1, provider_name="skylight")

        # The log entry is added and flushed
        mock_db.add.assert_called()
        mock_db.flush.assert_called()
        # But NOT committed -- caller manages the transaction
        mock_db.commit.assert_not_called()

    def test_verification_budget_and_log_in_single_transaction(self, db_with_vessel):
        """Both budget-exceeded log and normal log stay in the same transaction."""
        db, vessel = db_with_vessel

        with patch("app.modules.paid_verification.settings") as mock_settings:
            mock_settings.VERIFICATION_MONTHLY_BUDGET_USD = 500.0
            mock_settings.SKYLIGHT_API_KEY = None

            from app.modules.paid_verification import verify_vessel

            # First call: normal verification (within budget)
            result1 = verify_vessel(db, vessel.vessel_id, provider_name="skylight")

            # Check log was added but not committed
            logs = db.query(VerificationLog).all()
            assert len(logs) == 1

            # Rollback -- the log entry should disappear
            db.rollback()
            logs_after = db.query(VerificationLog).all()
            assert len(logs_after) == 0


# ══════════════════════════════════════════════════════════════════════════════
# E3: corridor import rollback on failure
# ══════════════════════════════════════════════════════════════════════════════


class TestCorridorImportRollback:
    """Verify that the corridor import in setup() uses flush (not commit),
    so failures can roll back without partial data persisting."""

    def test_cli_corridor_import_uses_flush_not_commit(self):
        """The corridor import code path should call flush, not commit."""
        # We test this by inspecting the source code directly
        import inspect
        from app import cli

        source = inspect.getsource(cli.setup)
        # The old code had db.commit() for corridor import, now it should use db.flush()
        # Find the corridor import section: starts at "Step 2" (Importing corridors)
        # and ends at "# 4. Fetch watchlists" (the comment for the next section)
        corridor_section_start = source.find("Step 2/{total_steps}: Importing corridors")
        corridor_section_end = source.find("# 4. Fetch watchlists")
        assert corridor_section_start > 0, "Could not find corridor import section"
        assert corridor_section_end > corridor_section_start, (
            f"Corridor section end ({corridor_section_end}) should be after start ({corridor_section_start})"
        )

        corridor_section = source[corridor_section_start:corridor_section_end]
        assert "db.flush()" in corridor_section, "corridor import should use db.flush()"
        assert "db.commit()" not in corridor_section, "corridor import should NOT use db.commit()"

    def test_cli_corridor_import_rollback_on_error(self):
        """If corridor import fails after adding some records, db.rollback() is called."""
        import inspect
        from app import cli

        source = inspect.getsource(cli.setup)
        # The except block for corridor import should call db.rollback()
        corridor_section_start = source.find("Step 2/{total_steps}: Importing corridors")
        corridor_section_end = source.find("# 4. Fetch watchlists")
        assert corridor_section_start > 0
        corridor_section = source[corridor_section_start:corridor_section_end]
        assert "db.rollback()" in corridor_section, "corridor import exception handler should rollback"


# ══════════════════════════════════════════════════════════════════════════════
# E4: received_utc index exists
# ══════════════════════════════════════════════════════════════════════════════


class TestReceivedUtcIndex:
    """Verify that AISObservation has an index on received_utc column."""

    def test_received_utc_index_in_table_args(self):
        """The __table_args__ should contain an index on received_utc."""
        table_args = AISObservation.__table_args__
        index_names = [idx.name for idx in table_args if hasattr(idx, "name")]
        assert "ix_ais_obs_received_utc" in index_names, (
            f"Expected ix_ais_obs_received_utc in table_args indexes, got: {index_names}"
        )

    def test_received_utc_index_columns(self):
        """The received_utc index should cover the received_utc column."""
        table_args = AISObservation.__table_args__
        for idx in table_args:
            if hasattr(idx, "name") and idx.name == "ix_ais_obs_received_utc":
                col_names = [col.name for col in idx.columns]
                assert "received_utc" in col_names
                break
        else:
            pytest.fail("ix_ais_obs_received_utc index not found in __table_args__")

    def test_received_utc_index_created_in_db(self, db):
        """The index should actually exist in a created database."""
        engine = db.get_bind()
        inspector = inspect(engine)
        indexes = inspector.get_indexes("ais_observations")
        index_names = [idx["name"] for idx in indexes]
        assert "ix_ais_obs_received_utc" in index_names, (
            f"Expected ix_ais_obs_received_utc in DB indexes, got: {index_names}"
        )

    def test_original_mmsi_ts_index_still_exists(self):
        """The original (mmsi, timestamp_utc) composite index should still exist."""
        table_args = AISObservation.__table_args__
        index_names = [idx.name for idx in table_args if hasattr(idx, "name")]
        assert "ix_ais_obs_mmsi_ts" in index_names, (
            "Original mmsi+timestamp_utc index should not be removed"
        )
