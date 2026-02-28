"""Tests for Phase M: Identity fraud detectors.

Covers stateless MMSI detection, flag hopping detection, and IMO fraud detection.
Uses in-memory SQLite for tests requiring real SQL queries.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.base import SpoofingTypeEnum
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.models.ais_point import AISPoint
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.modules.stateless_detector import run_stateless_detection, _extract_ship_mid
from app.modules.flag_hopping_detector import run_flag_hopping_detection
from app.modules.imo_fraud_detector import (
    run_imo_fraud_detection,
    _validate_imo_checksum,
    _haversine_nm,
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


def _make_flag_change(db, vessel_id, old_flag, new_flag, days_ago=0):
    ts = datetime.utcnow() - timedelta(days=days_ago)
    h = VesselHistory(
        vessel_id=vessel_id,
        field_changed="flag",
        old_value=old_flag,
        new_value=new_flag,
        observed_at=ts,
        source="test",
    )
    db.add(h)
    db.flush()
    return h


def _make_owner_change(db, vessel_id, old_name, new_name, days_ago=0):
    ts = datetime.utcnow() - timedelta(days=days_ago)
    h = VesselHistory(
        vessel_id=vessel_id,
        field_changed="owner_name",
        old_value=old_name,
        new_value=new_name,
        observed_at=ts,
        source="test",
    )
    db.add(h)
    db.flush()
    return h


def _make_ais_point(db, vessel, lat, lon, ts=None, sog=10.0):
    if ts is None:
        ts = datetime.now(timezone.utc)
    pt = AISPoint(
        vessel_id=vessel.vessel_id,
        timestamp_utc=ts,
        lat=lat,
        lon=lon,
        sog=sog,
        cog=180.0,
    )
    db.add(pt)
    db.flush()
    return pt


# ============================================================================
# STATELESS MMSI DETECTOR TESTS
# ============================================================================


class TestStatelessDisabled:
    """Test that detector respects feature flag."""

    @patch("app.modules.stateless_detector.settings")
    def test_disabled_returns_status(self, mock_settings, db):
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = False
        result = run_stateless_detection(db)
        assert result == {"status": "disabled"}


class TestStatelessTier1:
    """Test Tier 1: unallocated MID detection."""

    @patch("app.modules.stateless_detector.settings")
    def test_unallocated_mid_tier1_flagged(self, mock_settings, db):
        """MID 646 (unallocated) should create STATELESS_MMSI anomaly with +35pts."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        _make_vessel(db, mmsi="646123456", name="GHOST SHIP")
        db.commit()

        result = run_stateless_detection(db)

        assert result["status"] == "ok"
        assert result["tier1"] == 1
        anomalies = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STATELESS_MMSI
        ).all()
        assert len(anomalies) == 1
        assert anomalies[0].risk_score_component == 35
        assert anomalies[0].evidence_json["tier"] == 1
        assert anomalies[0].evidence_json["mid"] == 646

    @patch("app.modules.stateless_detector.settings")
    def test_mid_607_france_not_stateless(self, mock_settings, db):
        """MID 607 (France territories) should NOT be tier 1 (regression test)."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        _make_vessel(db, mmsi="607123456", name="FRENCH TERRITORY")
        db.commit()

        result = run_stateless_detection(db)

        assert result["tier1"] == 0
        # But it should be tier 3 (micro-territory)
        assert result["tier3"] == 1


class TestStatelessTier2:
    """Test Tier 2: landlocked country MID on tanker."""

    @patch("app.modules.stateless_detector.settings")
    def test_landlocked_mid_tier2_tanker(self, mock_settings, db):
        """MID 609 (Burundi) on tanker should be flagged with +20pts."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        _make_vessel(db, mmsi="609123456", name="LAND TANKER", vessel_type="Oil Tanker")
        db.commit()

        result = run_stateless_detection(db)

        assert result["tier2"] == 1
        anomalies = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STATELESS_MMSI
        ).all()
        assert len(anomalies) == 1
        assert anomalies[0].risk_score_component == 20

    @patch("app.modules.stateless_detector.settings")
    def test_landlocked_mid_non_tanker_not_flagged(self, mock_settings, db):
        """MID 609 (Burundi) on cargo vessel should NOT be flagged."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        _make_vessel(db, mmsi="609123456", name="CARGO SHIP", vessel_type="Bulk Carrier")
        db.commit()

        result = run_stateless_detection(db)

        assert result["tier2"] == 0
        anomalies = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STATELESS_MMSI
        ).all()
        assert len(anomalies) == 0


class TestStatelessTier3:
    """Test Tier 3: micro-territory MID."""

    @patch("app.modules.stateless_detector.settings")
    def test_micro_territory_tier3(self, mock_settings, db):
        """MID 618 (France Crozet/Kerguelen) should be flagged with +10pts."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        _make_vessel(db, mmsi="618123456", name="KERGUELEN VESSEL")
        db.commit()

        result = run_stateless_detection(db)

        assert result["tier3"] == 1
        anomalies = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STATELESS_MMSI
        ).all()
        assert len(anomalies) == 1
        assert anomalies[0].risk_score_component == 10


class TestStatelessExclusions:
    """Test non-ship MMSI pattern exclusions."""

    @patch("app.modules.stateless_detector.settings")
    def test_sar_mmsi_excluded(self, mock_settings, db):
        """SAR aircraft MMSI (111MIDXXX) should be excluded."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        _make_vessel(db, mmsi="111646000", name="SAR AIRCRAFT")
        db.commit()

        result = run_stateless_detection(db)

        assert result["tier1"] == 0
        assert result["vessels_checked"] == 0

    @patch("app.modules.stateless_detector.settings")
    def test_aton_mmsi_excluded(self, mock_settings, db):
        """AtoN MMSI (99MIDXXXX) should be excluded."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        _make_vessel(db, mmsi="996460000", name="NAVIGATION AID")
        db.commit()

        result = run_stateless_detection(db)

        assert result["tier1"] == 0
        assert result["vessels_checked"] == 0


class TestStatelessNormalMid:
    """Test that normal MIDs are not flagged."""

    @patch("app.modules.stateless_detector.settings")
    def test_normal_mid_not_flagged(self, mock_settings, db):
        """MID 211 (Germany) should NOT be flagged."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        _make_vessel(db, mmsi="211456789", name="GERMAN VESSEL")
        db.commit()

        result = run_stateless_detection(db)

        assert result["tier1"] == 0
        assert result["tier2"] == 0
        assert result["tier3"] == 0
        anomalies = db.query(SpoofingAnomaly).all()
        assert len(anomalies) == 0


class TestStatelessNoDuplicate:
    """Test deduplication."""

    @patch("app.modules.stateless_detector.settings")
    def test_no_duplicate_anomaly(self, mock_settings, db):
        """Existing STATELESS_MMSI anomaly should prevent new one."""
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = True
        v = _make_vessel(db, mmsi="646123456", name="GHOST SHIP")

        # Pre-existing anomaly
        existing = SpoofingAnomaly(
            vessel_id=v.vessel_id,
            anomaly_type=SpoofingTypeEnum.STATELESS_MMSI,
            start_time_utc=datetime.now(timezone.utc),
            risk_score_component=35,
        )
        db.add(existing)
        db.commit()

        result = run_stateless_detection(db)

        assert result["tier1"] == 0
        # Should still have exactly 1 anomaly (the pre-existing one)
        anomalies = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.STATELESS_MMSI
        ).all()
        assert len(anomalies) == 1


# ============================================================================
# FLAG HOPPING DETECTOR TESTS
# ============================================================================


class TestFlagHoppingDisabled:
    """Test feature flag."""

    @patch("app.modules.flag_hopping_detector.settings")
    def test_flag_hopping_disabled(self, mock_settings, db):
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = False
        result = run_flag_hopping_detection(db)
        assert result == {"status": "disabled"}


class TestFlagHoppingScoring:
    """Test scoring tiers."""

    @patch("app.modules.flag_hopping_detector.settings")
    def test_2_changes_in_90d(self, mock_settings, db):
        """2 flag changes in 90 days should score +20pts."""
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = True
        v = _make_vessel(db, mmsi="211456789")
        _make_flag_change(db, v.vessel_id, "DE", "PA", days_ago=60)
        _make_flag_change(db, v.vessel_id, "PA", "LR", days_ago=30)
        db.commit()

        result = run_flag_hopping_detection(db)

        assert result["anomalies_created"] == 1
        a = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING
        ).first()
        assert a is not None
        assert a.risk_score_component == 20

    @patch("app.modules.flag_hopping_detector.settings")
    def test_3_changes_in_90d(self, mock_settings, db):
        """3 flag changes in 90 days should score +40pts."""
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = True
        v = _make_vessel(db, mmsi="211456789")
        _make_flag_change(db, v.vessel_id, "DE", "PA", days_ago=80)
        _make_flag_change(db, v.vessel_id, "PA", "LR", days_ago=50)
        _make_flag_change(db, v.vessel_id, "LR", "MT", days_ago=20)
        db.commit()

        result = run_flag_hopping_detection(db)

        assert result["anomalies_created"] == 1
        a = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING
        ).first()
        assert a is not None
        assert a.risk_score_component == 40

    @patch("app.modules.flag_hopping_detector.settings")
    def test_5_changes_in_365d(self, mock_settings, db):
        """5 flag changes in 365 days should score +50pts."""
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = True
        v = _make_vessel(db, mmsi="211456789")
        _make_flag_change(db, v.vessel_id, "DE", "PA", days_ago=350)
        _make_flag_change(db, v.vessel_id, "PA", "LR", days_ago=300)
        _make_flag_change(db, v.vessel_id, "LR", "MT", days_ago=250)
        _make_flag_change(db, v.vessel_id, "MT", "BS", days_ago=200)
        _make_flag_change(db, v.vessel_id, "BS", "SG", days_ago=150)
        db.commit()

        result = run_flag_hopping_detection(db)

        assert result["anomalies_created"] == 1
        a = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING
        ).first()
        assert a is not None
        assert a.risk_score_component == 50

    @patch("app.modules.flag_hopping_detector.settings")
    def test_single_flag_change_not_flagged(self, mock_settings, db):
        """Only 1 flag change should NOT be flagged."""
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = True
        v = _make_vessel(db, mmsi="211456789")
        _make_flag_change(db, v.vessel_id, "DE", "PA", days_ago=30)
        db.commit()

        result = run_flag_hopping_detection(db)

        assert result["anomalies_created"] == 0


class TestFlagHoppingModifiers:
    """Test ownership discount and registry modifiers."""

    @patch("app.modules.flag_hopping_detector.settings")
    def test_ownership_change_discount(self, mock_settings, db):
        """Flag change with concurrent owner change should get 50% discount."""
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = True
        v = _make_vessel(db, mmsi="211456789")
        _make_flag_change(db, v.vessel_id, "DE", "PA", days_ago=60)
        _make_flag_change(db, v.vessel_id, "PA", "LR", days_ago=30)
        # Owner change within 7 days of a flag change
        _make_owner_change(db, v.vessel_id, "Old Co", "New Co", days_ago=58)
        db.commit()

        result = run_flag_hopping_detection(db)

        assert result["anomalies_created"] == 1
        a = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING
        ).first()
        assert a is not None
        # Base 20, discounted 50% = 10
        assert a.risk_score_component == 10

    @patch("app.modules.flag_hopping_detector.settings")
    def test_flag_to_comoros_boosted(self, mock_settings, db):
        """Flag change to Comoros (high-risk registry) should get 2x multiplier."""
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = True
        v = _make_vessel(db, mmsi="211456789")
        _make_flag_change(db, v.vessel_id, "DE", "PA", days_ago=60)
        _make_flag_change(db, v.vessel_id, "PA", "Comoros", days_ago=30)
        db.commit()

        result = run_flag_hopping_detection(db)

        assert result["anomalies_created"] == 1
        a = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING
        ).first()
        assert a is not None
        # Base 20, 2x = 40
        assert a.risk_score_component == 40

    @patch("app.modules.flag_hopping_detector.settings")
    def test_flag_to_norway_reduced(self, mock_settings, db):
        """Flag change to Norway (white-list) should get 0.5x multiplier."""
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = True
        v = _make_vessel(db, mmsi="211456789")
        _make_flag_change(db, v.vessel_id, "PA", "LR", days_ago=60)
        _make_flag_change(db, v.vessel_id, "LR", "Norway", days_ago=30)
        db.commit()

        result = run_flag_hopping_detection(db)

        assert result["anomalies_created"] == 1
        a = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.FLAG_HOPPING
        ).first()
        assert a is not None
        # Base 20, 0.5x = 10
        assert a.risk_score_component == 10


# ============================================================================
# IMO FRAUD DETECTOR TESTS
# ============================================================================


class TestImoFraudDisabled:
    """Test feature flag."""

    @patch("app.modules.imo_fraud_detector.settings")
    def test_imo_fraud_disabled(self, mock_settings, db):
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = False
        result = run_imo_fraud_detection(db)
        assert result == {"status": "disabled"}


class TestImoChecksum:
    """Test IMO checksum validation helper."""

    def test_imo_checksum_valid(self):
        """IMO 9074729 is a known valid checksum."""
        assert _validate_imo_checksum("9074729") is True

    def test_imo_checksum_invalid(self):
        """IMO 9074720 should be invalid (wrong last digit)."""
        assert _validate_imo_checksum("9074720") is False

    def test_imo_checksum_too_short(self):
        assert _validate_imo_checksum("12345") is False

    def test_imo_checksum_non_digit(self):
        assert _validate_imo_checksum("907472a") is False

    def test_imo_checksum_empty(self):
        assert _validate_imo_checksum("") is False
        assert _validate_imo_checksum(None) is False


class TestSimultaneousImo:
    """Test simultaneous IMO use detection."""

    @patch("app.modules.imo_fraud_detector.settings")
    def test_simultaneous_imo_flagged(self, mock_settings, db):
        """Same IMO on 2 vessels, both moving, >500nm apart should be flagged +45pts."""
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = True

        # Use IMO with valid checksum: 9074729
        v1 = _make_vessel(db, mmsi="211000001", name="VESSEL A", imo="9074729")
        v2 = _make_vessel(db, mmsi="211000002", name="VESSEL B", imo="9074729")

        now = datetime.now(timezone.utc)
        # v1 in North Sea, v2 in Mediterranean -- >500nm apart
        _make_ais_point(db, v1, lat=58.0, lon=3.0, ts=now, sog=12.0)
        _make_ais_point(db, v2, lat=35.0, lon=25.0, ts=now, sog=10.0)
        db.commit()

        result = run_imo_fraud_detection(db)

        assert result["status"] == "ok"
        assert result["simultaneous"] == 1
        anomalies = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD
        ).all()
        assert len(anomalies) == 1
        assert anomalies[0].risk_score_component == 45
        assert anomalies[0].evidence_json["detection_type"] == "simultaneous"

    @patch("app.modules.imo_fraud_detector.settings")
    def test_simultaneous_imo_checksum_failure(self, mock_settings, db):
        """Invalid IMO checksum should be filtered out."""
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = True

        # Use invalid IMO checksum
        v1 = _make_vessel(db, mmsi="211000001", name="VESSEL A", imo="1234560")
        v2 = _make_vessel(db, mmsi="211000002", name="VESSEL B", imo="1234560")

        now = datetime.now(timezone.utc)
        _make_ais_point(db, v1, lat=58.0, lon=3.0, ts=now, sog=12.0)
        _make_ais_point(db, v2, lat=35.0, lon=25.0, ts=now, sog=10.0)
        db.commit()

        result = run_imo_fraud_detection(db)

        assert result["simultaneous"] == 0

    @patch("app.modules.imo_fraud_detector.settings")
    def test_simultaneous_imo_close_vessels(self, mock_settings, db):
        """Same IMO but <500nm apart should NOT be flagged."""
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = True

        v1 = _make_vessel(db, mmsi="211000001", name="VESSEL A", imo="9074729")
        v2 = _make_vessel(db, mmsi="211000002", name="VESSEL B", imo="9074729")

        now = datetime.now(timezone.utc)
        # Both in the North Sea, close together
        _make_ais_point(db, v1, lat=58.0, lon=3.0, ts=now, sog=12.0)
        _make_ais_point(db, v2, lat=58.5, lon=3.5, ts=now, sog=10.0)
        db.commit()

        result = run_imo_fraud_detection(db)

        assert result["simultaneous"] == 0


class TestNearMissImo:
    """Test near-miss IMO detection."""

    @patch("app.modules.imo_fraud_detector.settings")
    def test_near_miss_without_qualifiers_not_flagged(self, mock_settings, db):
        """Near-miss IMO without 2 qualifying criteria should NOT be flagged."""
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = True

        v1 = _make_vessel(
            db, mmsi="211000001", name="VESSEL A", imo="9074729",
            vessel_type="Oil Tanker", deadweight=50000.0,
        )
        v2 = _make_vessel(
            db, mmsi="211000002", name="VESSEL B", imo="9074720",
            vessel_type="Bulk Carrier", deadweight=120000.0,
        )

        # Make v1 suspicious with an existing anomaly
        anomaly = SpoofingAnomaly(
            vessel_id=v1.vessel_id,
            anomaly_type=SpoofingTypeEnum.ANCHOR_SPOOF,
            start_time_utc=datetime.now(timezone.utc),
            risk_score_component=10,
        )
        db.add(anomaly)
        db.commit()

        result = run_imo_fraud_detection(db)

        # Different vessel_type, very different DWT, only 1 criteria (other_risk_indicators
        # requires v2 to also be suspicious) -- so 0 or 1 criteria
        assert result["near_miss"] == 0

    @patch("app.modules.imo_fraud_detector.settings")
    def test_near_miss_with_qualifiers_flagged(self, mock_settings, db):
        """Near-miss IMO with same type + similar DWT should be flagged +20pts."""
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = True

        v1 = _make_vessel(
            db, mmsi="211000001", name="VESSEL A", imo="9074729",
            vessel_type="Oil Tanker", deadweight=50000.0,
        )
        v2 = _make_vessel(
            db, mmsi="211000002", name="VESSEL B", imo="9074720",
            vessel_type="Oil Tanker", deadweight=48000.0,
        )

        # Make v1 suspicious with an existing anomaly
        anomaly = SpoofingAnomaly(
            vessel_id=v1.vessel_id,
            anomaly_type=SpoofingTypeEnum.ANCHOR_SPOOF,
            start_time_utc=datetime.now(timezone.utc),
            risk_score_component=10,
        )
        db.add(anomaly)
        db.commit()

        result = run_imo_fraud_detection(db)

        assert result["near_miss"] == 1
        near_miss_anomalies = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IMO_FRAUD
        ).all()
        assert len(near_miss_anomalies) == 1
        assert near_miss_anomalies[0].risk_score_component == 20
        assert near_miss_anomalies[0].evidence_json["detection_type"] == "near_miss"
        assert "same_vessel_type" in near_miss_anomalies[0].evidence_json["qualifying_criteria"]
        assert "similar_dwt" in near_miss_anomalies[0].evidence_json["qualifying_criteria"]
