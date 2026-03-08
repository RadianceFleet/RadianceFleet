"""Tests for PscDetention model, upsert, and sync logic."""
import pytest
from datetime import date, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.vessel import Vessel
from app.models.psc_detention import PscDetention
from app.modules.psc_loader import _upsert_detention, sync_vessel_psc_summary


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


@pytest.fixture
def vessel(db):
    """Create a test vessel and return it."""
    v = Vessel(mmsi="123456789", imo="1234567", name="TEST VESSEL", flag="PA")
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


# ---------------------------------------------------------------------------
# 1. PscDetention CRUD — create and read
# ---------------------------------------------------------------------------

class TestPscDetentionCRUD:
    def test_create_and_read(self, db, vessel):
        """PscDetention can be created and read back."""
        d = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date(2026, 1, 15),
            mou_source="tokyo_mou",
            data_source="opensanctions_ftm",
            deficiency_count=3,
            major_deficiency_count=1,
            port_name="Busan",
            port_country="KR",
        )
        db.add(d)
        db.commit()
        db.refresh(d)

        assert d.psc_detention_id is not None
        assert d.detention_date == date(2026, 1, 15)
        assert d.mou_source == "tokyo_mou"
        assert d.deficiency_count == 3
        assert d.major_deficiency_count == 1
        assert d.port_name == "Busan"
        assert d.created_at is not None

    def test_optional_fields_default_none(self, db, vessel):
        """Optional fields default to None when not set."""
        d = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date(2026, 2, 1),
            mou_source="paris_mou",
            data_source="emsa_ban_api",
        )
        db.add(d)
        db.commit()
        db.refresh(d)

        assert d.release_date is None
        assert d.port_name is None
        assert d.detention_reason is None
        assert d.ban_type is None
        assert d.authority_name is None
        assert d.flag_at_detention is None
        assert d.raw_entity_id is None


# ---------------------------------------------------------------------------
# 2. Unique constraint enforcement
# ---------------------------------------------------------------------------

class TestUniqueConstraint:
    def test_duplicate_raises_integrity_error(self, db, vessel):
        """Same vessel+date+mou+entity_id combination violates unique constraint."""
        from sqlalchemy.exc import IntegrityError

        d1 = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date(2026, 1, 15),
            mou_source="tokyo_mou",
            data_source="opensanctions_ftm",
            raw_entity_id="entity-123",
        )
        db.add(d1)
        db.commit()

        d2 = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date(2026, 1, 15),
            mou_source="tokyo_mou",
            data_source="opensanctions_ftm",
            raw_entity_id="entity-123",
        )
        db.add(d2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ---------------------------------------------------------------------------
# 3. Relationship: vessel.psc_detentions
# ---------------------------------------------------------------------------

class TestRelationship:
    def test_vessel_psc_detentions_returns_list(self, db, vessel):
        """vessel.psc_detentions returns related detention records."""
        d1 = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date(2026, 1, 10),
            mou_source="tokyo_mou",
            data_source="opensanctions_ftm",
        )
        d2 = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date(2026, 2, 20),
            mou_source="paris_mou",
            data_source="emsa_ban_api",
        )
        db.add_all([d1, d2])
        db.commit()
        db.refresh(vessel)

        assert len(vessel.psc_detentions) == 2
        dates = {d.detention_date for d in vessel.psc_detentions}
        assert date(2026, 1, 10) in dates
        assert date(2026, 2, 20) in dates


# ---------------------------------------------------------------------------
# 4-5. _upsert_detention creates and skips duplicates
# ---------------------------------------------------------------------------

class TestUpsertDetention:
    def test_creates_new_record(self, db, vessel):
        """_upsert_detention creates a new PscDetention record."""
        data = {
            "detention_date": date(2026, 3, 1),
            "mou_source": "tokyo_mou",
            "data_source": "opensanctions_ftm",
            "raw_entity_id": "ftm-abc-123",
            "imo_at_detention": "1234567",
            "vessel_name_at_detention": "TEST VESSEL",
        }
        result = _upsert_detention(db, vessel, data)
        db.commit()

        assert result is True
        records = db.query(PscDetention).filter(PscDetention.vessel_id == vessel.vessel_id).all()
        assert len(records) == 1
        assert records[0].mou_source == "tokyo_mou"
        assert records[0].raw_entity_id == "ftm-abc-123"

    def test_skips_duplicate(self, db, vessel):
        """_upsert_detention skips when same vessel+date+mou+entity_id exists."""
        data = {
            "detention_date": date(2026, 3, 1),
            "mou_source": "tokyo_mou",
            "data_source": "opensanctions_ftm",
            "raw_entity_id": "ftm-abc-123",
        }
        result1 = _upsert_detention(db, vessel, data)
        db.commit()
        result2 = _upsert_detention(db, vessel, data)
        db.commit()

        assert result1 is True
        assert result2 is False
        records = db.query(PscDetention).filter(PscDetention.vessel_id == vessel.vessel_id).all()
        assert len(records) == 1


# ---------------------------------------------------------------------------
# 6-8. sync_vessel_psc_summary
# ---------------------------------------------------------------------------

class TestSyncVesselPscSummary:
    def test_no_detentions_resets_flags(self, db, vessel):
        """With no detentions, flags reset to False/0."""
        vessel.psc_detained_last_12m = True
        vessel.psc_major_deficiencies_last_12m = 5
        db.commit()

        sync_vessel_psc_summary(db, vessel)
        db.commit()

        assert vessel.psc_detained_last_12m is False
        assert vessel.psc_major_deficiencies_last_12m == 0

    def test_recent_detentions_set_flags(self, db, vessel):
        """Recent detentions set psc_detained_last_12m and count major deficiencies."""
        d1 = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date.today() - timedelta(days=30),
            mou_source="tokyo_mou",
            data_source="opensanctions_ftm",
            deficiency_count=5,
            major_deficiency_count=2,
        )
        d2 = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date.today() - timedelta(days=60),
            mou_source="paris_mou",
            data_source="emsa_ban_api",
            deficiency_count=3,
            major_deficiency_count=1,
        )
        db.add_all([d1, d2])
        db.commit()

        sync_vessel_psc_summary(db, vessel)
        db.commit()

        assert vessel.psc_detained_last_12m is True
        assert vessel.psc_major_deficiencies_last_12m == 3

    def test_old_detentions_not_counted(self, db, vessel):
        """Detentions older than 12 months don't set flags."""
        d = PscDetention(
            vessel_id=vessel.vessel_id,
            detention_date=date.today() - timedelta(days=400),
            mou_source="tokyo_mou",
            data_source="opensanctions_ftm",
            major_deficiency_count=5,
        )
        db.add(d)
        db.commit()

        sync_vessel_psc_summary(db, vessel)
        db.commit()

        assert vessel.psc_detained_last_12m is False
        assert vessel.psc_major_deficiencies_last_12m == 0
