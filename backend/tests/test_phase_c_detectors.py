"""Tests for Phase C12-14: AISObservation model, cross-receiver, handshake, and fake position detectors.

Uses in-memory SQLite for tests requiring real SQL queries (observation storage,
cross-receiver comparison, handshake detection, fake position detection).
"""
import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 — registers all models with metadata
from app.models.base import SpoofingTypeEnum
from app.models.vessel import Vessel
from app.models.ais_point import AISPoint
from app.models.ais_observation import AISObservation
from app.models.vessel_history import VesselHistory
from app.models.spoofing_anomaly import SpoofingAnomaly


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


# ── Helper factories ─────────────────────────────────────────────────────────

def _make_vessel(db, mmsi="123456789", name="TEST VESSEL", **kwargs):
    v = Vessel(mmsi=mmsi, name=name, **kwargs)
    db.add(v)
    db.flush()
    return v


def _make_ais_point(db, vessel, lat, lon, ts, sog=10.0, cog=180.0, **kwargs):
    pt = AISPoint(
        vessel_id=vessel.vessel_id,
        timestamp_utc=ts,
        lat=lat,
        lon=lon,
        sog=sog,
        cog=cog,
        **kwargs,
    )
    db.add(pt)
    db.flush()
    return pt


def _make_observation(db, mmsi, source, lat, lon, ts, sog=None, cog=None):
    obs = AISObservation(
        mmsi=mmsi,
        source=source,
        timestamp_utc=ts,
        received_utc=ts,
        lat=lat,
        lon=lon,
        sog=sog,
        cog=cog,
    )
    db.add(obs)
    db.flush()
    return obs


# ══════════════════════════════════════════════════════════════════════════════
# AISObservation model tests
# ══════════════════════════════════════════════════════════════════════════════


class TestAISObservationModel:
    def test_ais_observation_model_creates(self, db):
        """Verify AISObservation table creation and record insert."""
        ts = datetime(2026, 2, 1, 12, 0, 0)
        obs = AISObservation(
            mmsi="123456789",
            source="aisstream",
            timestamp_utc=ts,
            received_utc=ts,
            lat=55.0,
            lon=25.0,
            sog=12.5,
            cog=180.0,
            heading=179.0,
            raw_data='{"test": true}',
        )
        db.add(obs)
        db.commit()

        result = db.query(AISObservation).first()
        assert result is not None
        assert result.mmsi == "123456789"
        assert result.source == "aisstream"
        assert result.lat == 55.0
        assert result.lon == 25.0
        assert result.sog == 12.5
        assert result.cog == 180.0
        assert result.heading == 179.0
        assert result.raw_data == '{"test": true}'
        assert result.observation_id is not None

    def test_ais_observation_purge(self, db):
        """Insert old records, verify purge deletes them."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(hours=100)  # Well beyond 72h
        recent_time = now - timedelta(hours=1)   # Within window

        # Create old observation
        old_obs = AISObservation(
            mmsi="111111111",
            source="aishub",
            timestamp_utc=old_time,
            received_utc=old_time,
            lat=55.0,
            lon=25.0,
        )
        # Create recent observation
        recent_obs = AISObservation(
            mmsi="222222222",
            source="aisstream",
            timestamp_utc=recent_time,
            received_utc=recent_time,
            lat=56.0,
            lon=26.0,
        )
        db.add_all([old_obs, recent_obs])
        db.commit()

        assert db.query(AISObservation).count() == 2

        deleted = AISObservation.purge_old(db, hours=72)
        assert deleted == 1

        remaining = db.query(AISObservation).all()
        assert len(remaining) == 1
        assert remaining[0].mmsi == "222222222"


# ══════════════════════════════════════════════════════════════════════════════
# Cross-receiver detector tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCrossReceiverDetector:
    def test_cross_receiver_detects_disagreement(self, db):
        """Two sources report same MMSI at different positions within time window."""
        from app.modules.cross_receiver_detector import detect_cross_receiver_anomalies

        mmsi = "123456789"
        vessel = _make_vessel(db, mmsi=mmsi)
        ts = datetime(2026, 2, 1, 12, 0, 0)

        # Source A: vessel at position 1
        _make_observation(db, mmsi, "aisstream", lat=55.0, lon=25.0, ts=ts)
        # Source B: vessel at position 2 (>5nm away) within 5 minutes
        _make_observation(db, mmsi, "aishub", lat=56.0, lon=25.0,
                          ts=ts + timedelta(minutes=5))
        db.commit()

        result = detect_cross_receiver_anomalies(db)

        assert result["anomalies_created"] == 1
        assert result["mmsis_checked"] >= 1

        anomaly = db.query(SpoofingAnomaly).first()
        assert anomaly is not None
        assert anomaly.vessel_id == vessel.vessel_id
        assert anomaly.anomaly_type == SpoofingTypeEnum.CROSS_RECEIVER_DISAGREEMENT
        assert anomaly.risk_score_component == 30
        assert anomaly.evidence_json is not None
        assert anomaly.evidence_json["source_a"] == "aisstream"
        assert anomaly.evidence_json["source_b"] == "aishub"

    def test_cross_receiver_no_anomaly_close_positions(self, db):
        """Same MMSI from two sources at nearby positions (within threshold)."""
        from app.modules.cross_receiver_detector import detect_cross_receiver_anomalies

        mmsi = "123456789"
        _make_vessel(db, mmsi=mmsi)
        ts = datetime(2026, 2, 1, 12, 0, 0)

        # Both sources report very close positions (<5nm)
        _make_observation(db, mmsi, "aisstream", lat=55.0, lon=25.0, ts=ts)
        _make_observation(db, mmsi, "aishub", lat=55.001, lon=25.001,
                          ts=ts + timedelta(minutes=1))
        db.commit()

        result = detect_cross_receiver_anomalies(db)
        assert result["anomalies_created"] == 0

    def test_cross_receiver_single_source(self, db):
        """Only one source -- no cross-receiver comparison possible."""
        from app.modules.cross_receiver_detector import detect_cross_receiver_anomalies

        mmsi = "123456789"
        _make_vessel(db, mmsi=mmsi)
        ts = datetime(2026, 2, 1, 12, 0, 0)

        _make_observation(db, mmsi, "aisstream", lat=55.0, lon=25.0, ts=ts)
        _make_observation(db, mmsi, "aisstream", lat=56.0, lon=25.0,
                          ts=ts + timedelta(minutes=5))
        db.commit()

        result = detect_cross_receiver_anomalies(db)
        assert result["anomalies_created"] == 0

    def test_cross_receiver_no_vessel_skipped(self, db):
        """If MMSI has no matching vessel, anomaly is skipped (vessel_id is NOT NULL)."""
        from app.modules.cross_receiver_detector import detect_cross_receiver_anomalies

        mmsi = "999999999"
        ts = datetime(2026, 2, 1, 12, 0, 0)

        # No vessel record for this MMSI
        _make_observation(db, mmsi, "aisstream", lat=55.0, lon=25.0, ts=ts)
        _make_observation(db, mmsi, "aishub", lat=56.0, lon=25.0,
                          ts=ts + timedelta(minutes=5))
        db.commit()

        result = detect_cross_receiver_anomalies(db)
        assert result["anomalies_created"] == 0

    def test_cross_receiver_outside_time_window(self, db):
        """Observations from different sources but too far apart in time."""
        from app.modules.cross_receiver_detector import detect_cross_receiver_anomalies

        mmsi = "123456789"
        _make_vessel(db, mmsi=mmsi)
        ts = datetime(2026, 2, 1, 12, 0, 0)

        _make_observation(db, mmsi, "aisstream", lat=55.0, lon=25.0, ts=ts)
        _make_observation(db, mmsi, "aishub", lat=56.0, lon=25.0,
                          ts=ts + timedelta(hours=2))  # Well beyond 10min window
        db.commit()

        result = detect_cross_receiver_anomalies(db)
        assert result["anomalies_created"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Handshake (identity swap) detector tests
# ══════════════════════════════════════════════════════════════════════════════


class TestHandshakeDetector:
    def test_handshake_detects_swap(self, db):
        """Two vessels near each other with identity attribute swap detected."""
        from app.modules.handshake_detector import detect_handshakes

        vessel_a = _make_vessel(db, mmsi="111111111", name="ALPHA")
        vessel_b = _make_vessel(db, mmsi="222222222", name="BRAVO")

        meet_time = datetime(2026, 2, 1, 12, 0, 0)

        # Both vessels at very similar positions (within 1nm)
        _make_ais_point(db, vessel_a, lat=55.0, lon=25.0, ts=meet_time)
        _make_ais_point(db, vessel_b, lat=55.005, lon=25.005, ts=meet_time)

        # Identity swap: A was ALPHA, becomes BRAVO; B was BRAVO, becomes ALPHA
        swap_time = meet_time + timedelta(minutes=30)
        db.add(VesselHistory(
            vessel_id=vessel_a.vessel_id,
            field_changed="name",
            old_value="ALPHA",
            new_value="BRAVO",
            observed_at=swap_time,
            source="aisstream",
        ))
        db.add(VesselHistory(
            vessel_id=vessel_b.vessel_id,
            field_changed="name",
            old_value="BRAVO",
            new_value="ALPHA",
            observed_at=swap_time,
            source="aisstream",
        ))
        db.commit()

        result = detect_handshakes(db)

        assert result["handshakes_detected"] == 1
        assert result["pairs_checked"] >= 1

        # Should create 2 anomalies (one per vessel)
        anomalies = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.IDENTITY_SWAP
        ).all()
        assert len(anomalies) == 2
        vessel_ids = {a.vessel_id for a in anomalies}
        assert vessel_a.vessel_id in vessel_ids
        assert vessel_b.vessel_id in vessel_ids
        assert all(a.risk_score_component == 50 for a in anomalies)

    def test_handshake_no_swap(self, db):
        """Two vessels near each other but no identity changes."""
        from app.modules.handshake_detector import detect_handshakes

        vessel_a = _make_vessel(db, mmsi="111111111", name="ALPHA")
        vessel_b = _make_vessel(db, mmsi="222222222", name="BRAVO")

        meet_time = datetime(2026, 2, 1, 12, 0, 0)

        # Both vessels at similar positions
        _make_ais_point(db, vessel_a, lat=55.0, lon=25.0, ts=meet_time)
        _make_ais_point(db, vessel_b, lat=55.005, lon=25.005, ts=meet_time)
        db.commit()

        result = detect_handshakes(db)
        assert result["handshakes_detected"] == 0

    def test_handshake_far_apart_no_detection(self, db):
        """Vessels have identity changes but are too far apart for handshake."""
        from app.modules.handshake_detector import detect_handshakes

        vessel_a = _make_vessel(db, mmsi="111111111", name="ALPHA")
        vessel_b = _make_vessel(db, mmsi="222222222", name="BRAVO")

        meet_time = datetime(2026, 2, 1, 12, 0, 0)

        # Vessels far apart (>1nm)
        _make_ais_point(db, vessel_a, lat=55.0, lon=25.0, ts=meet_time)
        _make_ais_point(db, vessel_b, lat=57.0, lon=27.0, ts=meet_time)

        swap_time = meet_time + timedelta(minutes=30)
        db.add(VesselHistory(
            vessel_id=vessel_a.vessel_id,
            field_changed="name",
            old_value="ALPHA",
            new_value="BRAVO",
            observed_at=swap_time,
            source="aisstream",
        ))
        db.add(VesselHistory(
            vessel_id=vessel_b.vessel_id,
            field_changed="name",
            old_value="BRAVO",
            new_value="ALPHA",
            observed_at=swap_time,
            source="aisstream",
        ))
        db.commit()

        result = detect_handshakes(db)
        assert result["handshakes_detected"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Fake position detector tests
# ══════════════════════════════════════════════════════════════════════════════


class TestFakePositionDetector:
    def test_fake_position_impossible_speed(self, db):
        """Positions requiring >25kn transit speed flagged as fake."""
        from app.modules.fake_position_detector import detect_fake_positions

        vessel = _make_vessel(db, mmsi="123456789")
        ts1 = datetime(2026, 2, 1, 12, 0, 0)
        ts2 = ts1 + timedelta(hours=1)

        # Position 1: 55N, 25E
        _make_ais_point(db, vessel, lat=55.0, lon=25.0, ts=ts1)
        # Position 2: 57N, 25E (~120nm apart) in 1h = 120kn (impossible)
        _make_ais_point(db, vessel, lat=57.0, lon=25.0, ts=ts2)
        db.commit()

        result = detect_fake_positions(db)
        assert result["fake_positions_detected"] == 1
        assert result["vessels_checked"] == 1

        anomaly = db.query(SpoofingAnomaly).first()
        assert anomaly is not None
        assert anomaly.vessel_id == vessel.vessel_id
        assert anomaly.anomaly_type == SpoofingTypeEnum.FAKE_PORT_CALL
        assert anomaly.risk_score_component == 40
        assert anomaly.implied_speed_kn is not None
        assert anomaly.implied_speed_kn > 25.0
        assert anomaly.evidence_json is not None
        assert "Kinematically impossible" in anomaly.evidence_json["description"]

    def test_fake_position_normal_speed(self, db):
        """Positions requiring <25kn transit speed not flagged."""
        from app.modules.fake_position_detector import detect_fake_positions

        vessel = _make_vessel(db, mmsi="123456789")
        ts1 = datetime(2026, 2, 1, 12, 0, 0)
        ts2 = ts1 + timedelta(hours=10)

        # ~120nm in 10h = 12kn (normal)
        _make_ais_point(db, vessel, lat=55.0, lon=25.0, ts=ts1)
        _make_ais_point(db, vessel, lat=57.0, lon=25.0, ts=ts2)
        db.commit()

        result = detect_fake_positions(db)
        assert result["fake_positions_detected"] == 0
        assert result["vessels_checked"] == 1

    def test_fake_position_gps_jitter(self, db):
        """Very close positions (GPS jitter) not flagged even with short time gap."""
        from app.modules.fake_position_detector import detect_fake_positions

        vessel = _make_vessel(db, mmsi="123456789")
        ts1 = datetime(2026, 2, 1, 12, 0, 0)
        ts2 = ts1 + timedelta(seconds=60)

        # Very close points (< 1nm) - should be filtered as GPS jitter
        _make_ais_point(db, vessel, lat=55.0, lon=25.0, ts=ts1)
        _make_ais_point(db, vessel, lat=55.001, lon=25.001, ts=ts2)
        db.commit()

        result = detect_fake_positions(db)
        assert result["fake_positions_detected"] == 0

    def test_fake_position_very_short_time_gap(self, db):
        """Positions with <36s time gap filtered out (data race)."""
        from app.modules.fake_position_detector import detect_fake_positions

        vessel = _make_vessel(db, mmsi="123456789")
        ts1 = datetime(2026, 2, 1, 12, 0, 0)
        ts2 = ts1 + timedelta(seconds=10)  # 10 seconds

        # Far apart but within 10s -- data race, not fake
        _make_ais_point(db, vessel, lat=55.0, lon=25.0, ts=ts1)
        _make_ais_point(db, vessel, lat=57.0, lon=25.0, ts=ts2)
        db.commit()

        result = detect_fake_positions(db)
        assert result["fake_positions_detected"] == 0

    def test_fake_position_idempotent(self, db):
        """Running detection twice does not duplicate anomalies."""
        from app.modules.fake_position_detector import detect_fake_positions

        vessel = _make_vessel(db, mmsi="123456789")
        ts1 = datetime(2026, 2, 1, 12, 0, 0)
        ts2 = ts1 + timedelta(hours=1)

        _make_ais_point(db, vessel, lat=55.0, lon=25.0, ts=ts1)
        _make_ais_point(db, vessel, lat=57.0, lon=25.0, ts=ts2)
        db.commit()

        result1 = detect_fake_positions(db)
        result2 = detect_fake_positions(db)

        assert result1["fake_positions_detected"] == 1
        assert result2["fake_positions_detected"] == 0

        total = db.query(SpoofingAnomaly).count()
        assert total == 1


# ══════════════════════════════════════════════════════════════════════════════
# Enum values test
# ══════════════════════════════════════════════════════════════════════════════


class TestSpoofingTypeEnum:
    def test_new_enum_values_exist(self):
        """Verify new spoofing type enum values are registered."""
        assert hasattr(SpoofingTypeEnum, "CROSS_RECEIVER_DISAGREEMENT")
        assert hasattr(SpoofingTypeEnum, "IDENTITY_SWAP")
        assert hasattr(SpoofingTypeEnum, "FAKE_PORT_CALL")

        assert SpoofingTypeEnum.CROSS_RECEIVER_DISAGREEMENT.value == "cross_receiver_disagreement"
        assert SpoofingTypeEnum.IDENTITY_SWAP.value == "identity_swap"
        assert SpoofingTypeEnum.FAKE_PORT_CALL.value == "fake_port_call"
