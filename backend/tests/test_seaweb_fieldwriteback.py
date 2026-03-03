"""Tests for Track B3: SeaWeb verification result write-back.

Covers:
  - _apply_verification_result writes DWT, vessel_type, year_built to Vessel
  - VesselHistory records created for Vessel field changes
  - ISM/P&I club written to most recent VesselOwner (not a new row)
  - No VesselHistory for owner fields
  - No update when value unchanged
  - No-op on result.success=False or empty data
  - Invalid cast gracefully skipped
  - verify_vessel() calls _apply_verification_result on success
  - VerificationLog.result_json stored correctly (full payload, not truncated)
  - VerificationLog.result_json is None for failed/budget-exceeded verifications

Uses in-memory SQLite for tests requiring real SQL queries.
"""
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.models.vessel_owner import VesselOwner
from app.models.verification_log import VerificationLog
from app.modules.paid_verification import (
    _apply_verification_result,
    verify_vessel,
    VerificationResult,
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


# -- Helper factories --

def _make_vessel(db, mmsi="211456789", name="TEST VESSEL", **kwargs):
    v = Vessel(mmsi=mmsi, name=name, **kwargs)
    db.add(v)
    db.flush()
    return v


def _make_owner(db, vessel_id, owner_name="Test Owner", **kwargs):
    o = VesselOwner(vessel_id=vessel_id, owner_name=owner_name, **kwargs)
    db.add(o)
    db.flush()
    return o


def _make_result(provider="seaweb", success=True, data=None, error=None, cost_usd=2.0):
    return VerificationResult(
        provider=provider,
        success=success,
        data=data if data is not None else {},
        cost_usd=cost_usd,
        error=error,
    )


# -- Test 1: _apply_verification_result writes DWT, vessel_type, year_built to Vessel --

class TestApplyVerificationResultVesselFields:

    def test_writes_dwt_vessel_type_year_built(self, db):
        """_apply_verification_result writes all three vessel fields from result.data."""
        vessel = _make_vessel(db, deadweight=None, vessel_type=None, year_built=None)
        result = _make_result(data={"dwt": 50000, "vessel_type": "Product Tanker", "year_built": 2005})

        _apply_verification_result(vessel, result, db)

        assert vessel.deadweight == 50000.0
        assert vessel.vessel_type == "Product Tanker"
        assert vessel.year_built == 2005

    def test_writes_dwt_only(self, db):
        """Only present keys are updated — missing keys leave vessel fields unchanged."""
        vessel = _make_vessel(db, deadweight=None, vessel_type="Crude Oil Tanker", year_built=2000)
        result = _make_result(data={"dwt": 75000})

        _apply_verification_result(vessel, result, db)

        assert vessel.deadweight == 75000.0
        assert vessel.vessel_type == "Crude Oil Tanker"  # unchanged
        assert vessel.year_built == 2000              # unchanged

    def test_dwt_cast_to_float(self, db):
        """DWT is cast to float regardless of input type."""
        vessel = _make_vessel(db, deadweight=None)
        result = _make_result(data={"dwt": "50000"})

        _apply_verification_result(vessel, result, db)

        assert vessel.deadweight == 50000.0
        assert isinstance(vessel.deadweight, float)

    def test_year_built_cast_to_int(self, db):
        """year_built is cast to int regardless of input type."""
        vessel = _make_vessel(db, year_built=None)
        result = _make_result(data={"year_built": "2005"})

        _apply_verification_result(vessel, result, db)

        assert vessel.year_built == 2005
        assert isinstance(vessel.year_built, int)


# -- Test 2: _apply_verification_result creates VesselHistory records --

class TestApplyVerificationResultVesselHistory:

    def test_creates_three_history_records(self, db):
        """One VesselHistory row is created for each changed vessel field."""
        vessel = _make_vessel(db, deadweight=None, vessel_type=None, year_built=None)
        result = _make_result(data={"dwt": 50000, "vessel_type": "Product Tanker", "year_built": 2005})

        _apply_verification_result(vessel, result, db)
        db.flush()

        histories = (
            db.query(VesselHistory)
            .filter(VesselHistory.vessel_id == vessel.vessel_id)
            .all()
        )
        assert len(histories) == 3
        fields_changed = {h.field_changed for h in histories}
        assert fields_changed == {"deadweight", "vessel_type", "year_built"}

    def test_history_source_is_paid_verification_provider(self, db):
        """VesselHistory source field reflects the provider name."""
        vessel = _make_vessel(db, deadweight=None)
        result = _make_result(provider="seaweb", data={"dwt": 50000})

        _apply_verification_result(vessel, result, db)
        db.flush()

        history = (
            db.query(VesselHistory)
            .filter(VesselHistory.vessel_id == vessel.vessel_id)
            .first()
        )
        assert history.source == "paid_verification:seaweb"

    def test_history_old_and_new_values_stored(self, db):
        """VesselHistory records the old and new values."""
        vessel = _make_vessel(db, deadweight=30000.0)
        result = _make_result(data={"dwt": 50000})

        _apply_verification_result(vessel, result, db)
        db.flush()

        history = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.field_changed == "deadweight",
            )
            .first()
        )
        assert history.old_value == "30000.0"
        assert history.new_value == "50000.0"

    def test_no_history_when_value_unchanged(self, db):
        """No VesselHistory record is created when the new value equals the existing value."""
        vessel = _make_vessel(db, deadweight=50000.0)
        result = _make_result(data={"dwt": 50000})

        _apply_verification_result(vessel, result, db)
        db.flush()

        histories = (
            db.query(VesselHistory)
            .filter(VesselHistory.vessel_id == vessel.vessel_id)
            .all()
        )
        assert len(histories) == 0


# -- Test 3: db.flush() is used, not db.commit() --

class TestFlushNotCommit:

    def test_changes_visible_in_session_after_flush(self, db):
        """After _apply_verification_result, changes are visible within the same session."""
        vessel = _make_vessel(db, deadweight=None)
        result = _make_result(data={"dwt": 60000})

        _apply_verification_result(vessel, result, db)

        # Changes visible in-session without commit
        assert vessel.deadweight == 60000.0

    def test_rollback_reverts_changes(self, db):
        """Rolling back the session after _apply_verification_result reverts changes."""
        vessel = _make_vessel(db, deadweight=None)
        db.commit()  # commit initial state

        result = _make_result(data={"dwt": 60000})
        _apply_verification_result(vessel, result, db)
        db.rollback()  # revert without commit

        db.refresh(vessel)
        assert vessel.deadweight is None


# -- Test 4: ism_manager NOT written if no existing VesselOwner --

class TestISMManagerNoOwner:

    def test_no_new_owner_created_when_no_existing_owner(self, db):
        """When vessel has no VesselOwner, ism_manager data does not create a new row."""
        vessel = _make_vessel(db)
        result = _make_result(data={"ism_manager": "Acme Ship Mgmt"})

        # Should not raise, should not create a new VesselOwner
        _apply_verification_result(vessel, result, db)
        db.flush()

        owners = db.query(VesselOwner).filter(VesselOwner.vessel_id == vessel.vessel_id).all()
        assert len(owners) == 0

    def test_no_error_raised_when_no_existing_owner(self, db):
        """_apply_verification_result does not raise when vessel has no owners."""
        vessel = _make_vessel(db)
        result = _make_result(data={"ism_manager": "Acme Ship Mgmt", "pi_club": "UK P&I"})

        # Must complete without exception
        _apply_verification_result(vessel, result, db)


# -- Test 5: ism_manager written to most recent VesselOwner --

class TestISMManagerMostRecentOwner:

    def test_ism_manager_written_to_most_recent_owner(self, db):
        """ism_manager is written to the VesselOwner with the highest owner_id."""
        vessel = _make_vessel(db)
        older_owner = _make_owner(db, vessel.vessel_id, owner_name="Old Owner Corp")
        newer_owner = _make_owner(db, vessel.vessel_id, owner_name="New Owner Corp")

        result = _make_result(data={"ism_manager": "Best Ship Mgmt"})
        _apply_verification_result(vessel, result, db)
        db.flush()

        db.refresh(older_owner)
        db.refresh(newer_owner)

        assert newer_owner.ism_manager == "Best Ship Mgmt"
        assert older_owner.ism_manager is None  # not updated


# -- Test 6: pi_club written to most recent VesselOwner --

class TestPIClubMostRecentOwner:

    def test_pi_club_written_to_most_recent_owner(self, db):
        """pi_club_name is written to the VesselOwner with the highest owner_id."""
        vessel = _make_vessel(db)
        older_owner = _make_owner(db, vessel.vessel_id, owner_name="Old Owner Corp")
        newer_owner = _make_owner(db, vessel.vessel_id, owner_name="New Owner Corp")

        result = _make_result(data={"pi_club": "UK P&I Club"})
        _apply_verification_result(vessel, result, db)
        db.flush()

        db.refresh(older_owner)
        db.refresh(newer_owner)

        assert newer_owner.pi_club_name == "UK P&I Club"
        assert older_owner.pi_club_name is None  # not updated


# -- Test 7: No VesselHistory for ism_manager/pi_club --

class TestNoHistoryForOwnerFields:

    def test_no_vessel_history_for_ism_manager(self, db):
        """VesselHistory is not recorded for ism_manager changes."""
        vessel = _make_vessel(db)
        _make_owner(db, vessel.vessel_id)

        result = _make_result(data={"ism_manager": "Acme Mgmt", "pi_club": "West of England"})
        _apply_verification_result(vessel, result, db)
        db.flush()

        histories = (
            db.query(VesselHistory)
            .filter(
                VesselHistory.vessel_id == vessel.vessel_id,
                VesselHistory.field_changed.in_(["ism_manager", "pi_club_name"]),
            )
            .all()
        )
        assert len(histories) == 0


# -- Test 8: No update when value unchanged --

class TestNoUpdateWhenUnchanged:

    def test_no_history_when_deadweight_unchanged(self, db):
        """No VesselHistory is created when DWT in result equals existing vessel.deadweight."""
        vessel = _make_vessel(db, deadweight=50000.0)
        result = _make_result(data={"dwt": 50000})

        _apply_verification_result(vessel, result, db)
        db.flush()

        histories = (
            db.query(VesselHistory)
            .filter(VesselHistory.vessel_id == vessel.vessel_id)
            .all()
        )
        assert len(histories) == 0
        assert vessel.deadweight == 50000.0  # unchanged


# -- Test 9: verify_vessel() calls _apply_verification_result on success --

class TestVerifyVesselCallsApply:

    def test_verify_vessel_writes_fields_on_success(self, db):
        """verify_vessel() applies field write-back when provider returns success=True."""
        vessel = _make_vessel(db, deadweight=None, vessel_type=None, year_built=None)

        mock_result = VerificationResult(
            provider="seaweb",
            success=True,
            data={"dwt": 50000, "vessel_type": "Product Tanker", "year_built": 2005},
            cost_usd=2.0,
        )

        with patch("app.modules.paid_verification._PROVIDERS") as mock_providers:
            mock_provider = MagicMock()
            mock_provider.name.return_value = "seaweb"
            mock_provider.estimated_cost.return_value = 2.0
            mock_provider.verify_vessel.return_value = mock_result
            mock_providers.get.return_value = mock_provider

            with patch("app.modules.paid_verification.get_monthly_spend", return_value=0.0):
                verify_vessel(db, vessel.vessel_id, "seaweb")

        db.refresh(vessel)
        assert vessel.deadweight == 50000.0
        assert vessel.vessel_type == "Product Tanker"
        assert vessel.year_built == 2005

    def test_verify_vessel_does_not_write_fields_on_failure(self, db):
        """verify_vessel() does not call _apply_verification_result when provider fails."""
        vessel = _make_vessel(db, deadweight=None)

        mock_result = VerificationResult(
            provider="seaweb",
            success=False,
            error="API error",
            cost_usd=0.0,
        )

        with patch("app.modules.paid_verification._PROVIDERS") as mock_providers:
            mock_provider = MagicMock()
            mock_provider.name.return_value = "seaweb"
            mock_provider.estimated_cost.return_value = 2.0
            mock_provider.verify_vessel.return_value = mock_result
            mock_providers.get.return_value = mock_provider

            with patch("app.modules.paid_verification.get_monthly_spend", return_value=0.0):
                verify_vessel(db, vessel.vessel_id, "seaweb")

        db.refresh(vessel)
        assert vessel.deadweight is None  # not updated


# -- Test 10: VerificationLog.result_json stored correctly --

class TestVerificationLogResultJson:

    def test_result_json_contains_full_data(self, db):
        """VerificationLog.result_json equals json.dumps(result.data) from provider."""
        vessel = _make_vessel(db, deadweight=None)
        data = {"dwt": 50000, "ism_manager": "Acme Ship Mgmt"}

        mock_result = VerificationResult(
            provider="seaweb",
            success=True,
            data=data,
            cost_usd=2.0,
        )

        with patch("app.modules.paid_verification._PROVIDERS") as mock_providers:
            mock_provider = MagicMock()
            mock_provider.name.return_value = "seaweb"
            mock_provider.estimated_cost.return_value = 2.0
            mock_provider.verify_vessel.return_value = mock_result
            mock_providers.get.return_value = mock_provider

            with patch("app.modules.paid_verification.get_monthly_spend", return_value=0.0):
                verify_vessel(db, vessel.vessel_id, "seaweb")

        log = db.query(VerificationLog).filter(VerificationLog.vessel_id == vessel.vessel_id).first()
        assert log is not None
        assert log.result_json == json.dumps(data)

    def test_result_summary_still_truncated(self, db):
        """result_summary is still truncated to 500 chars (old field retained alongside result_json)."""
        vessel = _make_vessel(db)
        long_data = {"dwt": 50000, "description": "X" * 600}

        mock_result = VerificationResult(
            provider="seaweb",
            success=True,
            data=long_data,
            cost_usd=2.0,
        )

        with patch("app.modules.paid_verification._PROVIDERS") as mock_providers:
            mock_provider = MagicMock()
            mock_provider.name.return_value = "seaweb"
            mock_provider.estimated_cost.return_value = 2.0
            mock_provider.verify_vessel.return_value = mock_result
            mock_providers.get.return_value = mock_provider

            with patch("app.modules.paid_verification.get_monthly_spend", return_value=0.0):
                verify_vessel(db, vessel.vessel_id, "seaweb")

        log = db.query(VerificationLog).filter(VerificationLog.vessel_id == vessel.vessel_id).first()
        assert len(log.result_summary) <= 500
        assert len(log.result_json) > 500  # full payload preserved

    def test_both_fields_on_same_log_row(self, db):
        """result_json and result_summary both exist on the same VerificationLog row."""
        vessel = _make_vessel(db)
        data = {"dwt": 50000}

        mock_result = VerificationResult(
            provider="seaweb",
            success=True,
            data=data,
            cost_usd=2.0,
        )

        with patch("app.modules.paid_verification._PROVIDERS") as mock_providers:
            mock_provider = MagicMock()
            mock_provider.name.return_value = "seaweb"
            mock_provider.estimated_cost.return_value = 2.0
            mock_provider.verify_vessel.return_value = mock_result
            mock_providers.get.return_value = mock_provider

            with patch("app.modules.paid_verification.get_monthly_spend", return_value=0.0):
                verify_vessel(db, vessel.vessel_id, "seaweb")

        log = db.query(VerificationLog).filter(VerificationLog.vessel_id == vessel.vessel_id).first()
        assert log.result_json is not None
        assert log.result_summary is not None


# -- Test 11: VerificationLog.result_json is None for failed verification --

class TestResultJsonNoneOnFailure:

    def test_result_json_none_on_provider_failure(self, db):
        """result_json is None when provider returns success=False."""
        vessel = _make_vessel(db)

        mock_result = VerificationResult(
            provider="seaweb",
            success=False,
            error="API unavailable",
            cost_usd=0.0,
        )

        with patch("app.modules.paid_verification._PROVIDERS") as mock_providers:
            mock_provider = MagicMock()
            mock_provider.name.return_value = "seaweb"
            mock_provider.estimated_cost.return_value = 2.0
            mock_provider.verify_vessel.return_value = mock_result
            mock_providers.get.return_value = mock_provider

            with patch("app.modules.paid_verification.get_monthly_spend", return_value=0.0):
                verify_vessel(db, vessel.vessel_id, "seaweb")

        log = db.query(VerificationLog).filter(VerificationLog.vessel_id == vessel.vessel_id).first()
        assert log.result_json is None


# -- Test 12: Invalid cast gracefully skipped --

class TestInvalidCastGracefulSkip:

    def test_invalid_year_built_not_crash(self, db):
        """A non-numeric year_built value is skipped without crashing."""
        vessel = _make_vessel(db, year_built=None)
        result = _make_result(data={"year_built": "not-a-number"})

        # Must not raise
        _apply_verification_result(vessel, result, db)

        assert vessel.year_built is None  # not updated

    def test_invalid_dwt_not_crash(self, db):
        """A non-numeric dwt value is skipped without crashing."""
        vessel = _make_vessel(db, deadweight=None)
        result = _make_result(data={"dwt": "not-a-float"})

        _apply_verification_result(vessel, result, db)

        assert vessel.deadweight is None  # not updated


# -- Test 13: _apply_verification_result is no-op if result.success=False --

class TestNoOpOnFailure:

    def test_noop_when_success_false(self, db):
        """_apply_verification_result does nothing when result.success is False."""
        vessel = _make_vessel(db, deadweight=None)
        result = _make_result(success=False, data={"dwt": 50000}, error="API error")

        _apply_verification_result(vessel, result, db)

        assert vessel.deadweight is None  # not updated

    def test_noop_when_data_empty(self, db):
        """_apply_verification_result does nothing when result.data is empty."""
        vessel = _make_vessel(db, deadweight=None)
        result = _make_result(success=True, data={})

        _apply_verification_result(vessel, result, db)

        histories = (
            db.query(VesselHistory)
            .filter(VesselHistory.vessel_id == vessel.vessel_id)
            .all()
        )
        assert len(histories) == 0


# -- Test 14: Budget exceeded: result_json is None (no data) --

class TestBudgetExceededResultJsonNone:

    def test_result_json_none_when_budget_exceeded(self, db):
        """When budget is exceeded, VerificationLog.result_json is None."""
        vessel = _make_vessel(db)

        with patch("app.modules.paid_verification._PROVIDERS") as mock_providers:
            mock_provider = MagicMock()
            mock_provider.name.return_value = "seaweb"
            mock_provider.estimated_cost.return_value = 2.0
            mock_providers.get.return_value = mock_provider

            # Simulate budget exceeded: spend > budget
            with patch("app.modules.paid_verification.get_monthly_spend", return_value=499.0):
                with patch("app.config.settings") as mock_settings:
                    mock_settings.VERIFICATION_MONTHLY_BUDGET_USD = 500.0
                    verify_vessel(db, vessel.vessel_id, "seaweb")

        log = (
            db.query(VerificationLog)
            .filter(
                VerificationLog.vessel_id == vessel.vessel_id,
                VerificationLog.response_status == "budget_exceeded",
            )
            .first()
        )
        assert log is not None
        assert log.result_json is None


# -- Test 15: result_json stores full data without 500-char truncation --

class TestResultJsonFullPayload:

    def test_result_json_not_truncated(self, db):
        """result_json contains the full payload even when it exceeds 500 characters."""
        vessel = _make_vessel(db)
        long_description = "X" * 600
        data = {"dwt": 50000, "description": long_description}

        mock_result = VerificationResult(
            provider="seaweb",
            success=True,
            data=data,
            cost_usd=2.0,
        )

        with patch("app.modules.paid_verification._PROVIDERS") as mock_providers:
            mock_provider = MagicMock()
            mock_provider.name.return_value = "seaweb"
            mock_provider.estimated_cost.return_value = 2.0
            mock_provider.verify_vessel.return_value = mock_result
            mock_providers.get.return_value = mock_provider

            with patch("app.modules.paid_verification.get_monthly_spend", return_value=0.0):
                verify_vessel(db, vessel.vessel_id, "seaweb")

        log = db.query(VerificationLog).filter(VerificationLog.vessel_id == vessel.vessel_id).first()

        parsed = json.loads(log.result_json)
        assert parsed["description"] == long_description  # full description preserved
        assert len(log.result_summary) <= 500             # summary truncated
        assert len(log.result_json) > 500                 # json full
