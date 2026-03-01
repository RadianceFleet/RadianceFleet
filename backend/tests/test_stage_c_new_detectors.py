"""Tests for Stage C: Missing evasion technique detectors.

Covers:
  C1: Route laundering detector
  C2: P&I club change velocity detector
  C3: Sparse AIS transmission detector
  C4: Vessel type consistency detector
  + enum values, feature flags, YAML sections

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
from app.models.port import Port
from app.models.port_call import PortCall
from app.models.spoofing_anomaly import SpoofingAnomaly


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


def _make_port(db, name="Test Port", country="US", **kwargs):
    p = Port(name=name, country=country, **kwargs)
    db.add(p)
    db.flush()
    return p


def _make_port_call(db, vessel_id, port_id=None, arrival_utc=None, **kwargs):
    if arrival_utc is None:
        arrival_utc = datetime.utcnow()
    pc = PortCall(
        vessel_id=vessel_id,
        port_id=port_id,
        arrival_utc=arrival_utc,
        **kwargs,
    )
    db.add(pc)
    db.flush()
    return pc


def _make_history(db, vessel_id, field, old_val, new_val, days_ago=0):
    ts = datetime.utcnow() - timedelta(days=days_ago)
    h = VesselHistory(
        vessel_id=vessel_id,
        field_changed=field,
        old_value=old_val,
        new_value=new_val,
        observed_at=ts,
        source="test",
    )
    db.add(h)
    db.flush()
    return h


def _make_ais_point(db, vessel_id, lat=55.0, lon=20.0, ts=None, sog=10.0):
    if ts is None:
        ts = datetime.utcnow()
    pt = AISPoint(
        vessel_id=vessel_id,
        timestamp_utc=ts,
        lat=lat,
        lon=lon,
        sog=sog,
        cog=180.0,
    )
    db.add(pt)
    db.flush()
    return pt


# ═══════════════════════════════════════════════════════════════════════════
# Enum values exist
# ═══════════════════════════════════════════════════════════════════════════

class TestEnumValues:
    def test_route_laundering_enum(self):
        assert SpoofingTypeEnum.ROUTE_LAUNDERING.value == "route_laundering"

    def test_pi_cycling_enum(self):
        assert SpoofingTypeEnum.PI_CYCLING.value == "pi_cycling"

    def test_sparse_transmission_enum(self):
        assert SpoofingTypeEnum.SPARSE_TRANSMISSION.value == "sparse_transmission"

    def test_type_dwt_mismatch_enum(self):
        assert SpoofingTypeEnum.TYPE_DWT_MISMATCH.value == "type_dwt_mismatch"


# ═══════════════════════════════════════════════════════════════════════════
# Feature flags exist
# ═══════════════════════════════════════════════════════════════════════════

class TestFeatureFlags:
    def test_route_laundering_flags(self):
        from app.config import Settings
        s = Settings()
        assert s.ROUTE_LAUNDERING_DETECTION_ENABLED is False
        assert s.ROUTE_LAUNDERING_SCORING_ENABLED is False
        assert s.ROUTE_LAUNDERING_LOOKBACK_DAYS == 180

    def test_pi_cycling_flags(self):
        from app.config import Settings
        s = Settings()
        assert s.PI_CYCLING_DETECTION_ENABLED is False
        assert s.PI_CYCLING_SCORING_ENABLED is False

    def test_sparse_transmission_flags(self):
        from app.config import Settings
        s = Settings()
        assert s.SPARSE_TRANSMISSION_DETECTION_ENABLED is False
        assert s.SPARSE_TRANSMISSION_SCORING_ENABLED is False

    def test_type_consistency_flags(self):
        from app.config import Settings
        s = Settings()
        assert s.TYPE_CONSISTENCY_DETECTION_ENABLED is False
        assert s.TYPE_CONSISTENCY_SCORING_ENABLED is False


# ═══════════════════════════════════════════════════════════════════════════
# YAML sections exist
# ═══════════════════════════════════════════════════════════════════════════

def _find_risk_scoring_yaml():
    """Find risk_scoring.yaml from test file location."""
    import yaml
    from pathlib import Path
    # __file__ is backend/tests/test_*.py  -> parent.parent.parent = repo root
    repo_root = Path(__file__).parent.parent.parent
    config_path = repo_root / "config" / "risk_scoring.yaml"
    if not config_path.exists():
        # Fallback: try relative from backend/
        config_path = Path(__file__).parent.parent / "config" / "risk_scoring.yaml"
    if not config_path.exists():
        return None
    return yaml.safe_load(config_path.read_text())


class TestYAMLSections:
    def test_expected_sections_include_stage_c(self):
        from app.modules.risk_scoring import _EXPECTED_SECTIONS
        assert "route_laundering" in _EXPECTED_SECTIONS
        assert "pi_cycling" in _EXPECTED_SECTIONS
        assert "sparse_transmission" in _EXPECTED_SECTIONS
        assert "vessel_type_consistency" in _EXPECTED_SECTIONS

    def test_yaml_has_route_laundering_section(self):
        config = _find_risk_scoring_yaml()
        if config is None:
            pytest.skip("risk_scoring.yaml not found")
        assert "route_laundering" in config
        assert config["route_laundering"]["confirmed_3_hop"] == 35
        assert config["route_laundering"]["partial_2_hop"] == 20
        assert config["route_laundering"]["pattern_only"] == 15

    def test_yaml_has_pi_cycling_section(self):
        config = _find_risk_scoring_yaml()
        if config is None:
            pytest.skip("risk_scoring.yaml not found")
        assert "pi_cycling" in config
        assert config["pi_cycling"]["rapid_change_90d"] == 20
        assert config["pi_cycling"]["non_ig_club"] == 30

    def test_yaml_has_sparse_transmission_section(self):
        config = _find_risk_scoring_yaml()
        if config is None:
            pytest.skip("risk_scoring.yaml not found")
        assert "sparse_transmission" in config
        assert config["sparse_transmission"]["moderate_sparsity"] == 15
        assert config["sparse_transmission"]["severe_sparsity"] == 25

    def test_yaml_has_vessel_type_consistency_section(self):
        config = _find_risk_scoring_yaml()
        if config is None:
            pytest.skip("risk_scoring.yaml not found")
        assert "vessel_type_consistency" in config
        assert config["vessel_type_consistency"]["type_dwt_mismatch"] == 25
        assert config["vessel_type_consistency"]["recent_type_change"] == 15


# ═══════════════════════════════════════════════════════════════════════════
# C1: Route laundering detector
# ═══════════════════════════════════════════════════════════════════════════

_TEST_INTERMEDIARY_CONFIG = {
    "intermediary_ports": [
        {"name": "Fujairah", "country": "AE"},
        {"name": "Sohar", "country": "OM"},
        {"name": "Ceyhan", "country": "TR"},
        {"name": "Jamnagar", "country": "IN"},
        {"name": "Kalamata", "country": "GR"},
        {"name": "Ceuta", "country": "ES"},
    ]
}


class TestRouteLaunderingDetector:
    @pytest.fixture(autouse=True)
    def _inject_config(self):
        """Inject intermediary config directly to avoid filesystem dependency."""
        import app.modules.route_laundering_detector as rl_mod
        rl_mod._INTERMEDIARY_CONFIG = _TEST_INTERMEDIARY_CONFIG
        yield
        rl_mod._INTERMEDIARY_CONFIG = None

    def test_disabled_returns_early(self, db):
        from app.modules.route_laundering_detector import run_route_laundering_detection
        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = False
            result = run_route_laundering_detection(db)
        assert result == {"status": "disabled"}

    def test_3_hop_detection(self, db):
        from app.modules.route_laundering_detector import run_route_laundering_detection
        vessel = _make_vessel(db)

        # Create ports
        ru_port = _make_port(db, name="Novorossiysk", country="RU")
        int_port = _make_port(db, name="Fujairah", country="AE")
        sanc_port = _make_port(db, name="Bandar Abbas", country="IR")

        # Create port call sequence: Russian -> Intermediary -> Sanctioned
        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, ru_port.port_id, arrival_utc=now - timedelta(days=30))
        _make_port_call(db, vessel.vessel_id, int_port.port_id, arrival_utc=now - timedelta(days=20))
        _make_port_call(db, vessel.vessel_id, sanc_port.port_id, arrival_utc=now - timedelta(days=10))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            result = run_route_laundering_detection(db)

        assert result["status"] == "ok"
        assert result["anomalies_created"] == 1

        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING,
        ).first()
        assert anomaly is not None
        assert anomaly.risk_score_component == 35
        assert anomaly.evidence_json["hop_count"] == 3

    def test_2_hop_detection(self, db):
        from app.modules.route_laundering_detector import run_route_laundering_detection
        vessel = _make_vessel(db)

        ru_port = _make_port(db, name="Ust-Luga", country="RU")
        int_port = _make_port(db, name="Ceuta", country="ES")

        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, ru_port.port_id, arrival_utc=now - timedelta(days=30))
        _make_port_call(db, vessel.vessel_id, int_port.port_id, arrival_utc=now - timedelta(days=20))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            result = run_route_laundering_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING,
        ).first()
        assert anomaly.risk_score_component == 20
        assert anomaly.evidence_json["hop_count"] == 2

    def test_no_pattern_no_anomaly(self, db):
        from app.modules.route_laundering_detector import run_route_laundering_detection
        vessel = _make_vessel(db)

        # Clean ports: US -> DE -> UK
        us_port = _make_port(db, name="Houston", country="US")
        de_port = _make_port(db, name="Hamburg", country="DE")

        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, us_port.port_id, arrival_utc=now - timedelta(days=30))
        _make_port_call(db, vessel.vessel_id, de_port.port_id, arrival_utc=now - timedelta(days=20))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            result = run_route_laundering_detection(db)

        assert result["anomalies_created"] == 0

    def test_dedup_existing_anomaly(self, db):
        from app.modules.route_laundering_detector import run_route_laundering_detection
        vessel = _make_vessel(db)

        ru_port = _make_port(db, name="Primorsk", country="RU")
        int_port = _make_port(db, name="Sohar", country="OM")

        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, ru_port.port_id, arrival_utc=now - timedelta(days=30))
        _make_port_call(db, vessel.vessel_id, int_port.port_id, arrival_utc=now - timedelta(days=20))

        # Pre-existing anomaly
        existing = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.ROUTE_LAUNDERING,
            start_time_utc=now - timedelta(days=30),
            risk_score_component=20,
        )
        db.add(existing)
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            result = run_route_laundering_detection(db)

        assert result["anomalies_created"] == 0

    def test_russian_oil_terminal_flag(self, db):
        """Port with is_russian_oil_terminal=True should be classified as russian."""
        from app.modules.route_laundering_detector import run_route_laundering_detection
        vessel = _make_vessel(db)

        # Port with different country but russian oil terminal flag
        oil_terminal = _make_port(db, name="Oil Terminal", country="XX",
                                  is_russian_oil_terminal=True)
        int_port = _make_port(db, name="Jamnagar", country="IN")

        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, oil_terminal.port_id, arrival_utc=now - timedelta(days=30))
        _make_port_call(db, vessel.vessel_id, int_port.port_id, arrival_utc=now - timedelta(days=20))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            result = run_route_laundering_detection(db)

        assert result["anomalies_created"] == 1

    def test_lookback_window_respected(self, db):
        """Port calls outside lookback window should not trigger detection."""
        from app.modules.route_laundering_detector import run_route_laundering_detection
        vessel = _make_vessel(db)

        ru_port = _make_port(db, name="Novorossiysk", country="RU")
        int_port = _make_port(db, name="Fujairah", country="AE")

        now = datetime.utcnow()
        # Port calls beyond 30-day lookback
        _make_port_call(db, vessel.vessel_id, ru_port.port_id, arrival_utc=now - timedelta(days=60))
        _make_port_call(db, vessel.vessel_id, int_port.port_id, arrival_utc=now - timedelta(days=50))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 30  # Short lookback
            result = run_route_laundering_detection(db)

        assert result["anomalies_created"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# C2: P&I club change velocity detector
# ═══════════════════════════════════════════════════════════════════════════

class TestPICyclingDetector:
    def test_disabled_returns_early(self, db):
        from app.modules.pi_cycling_detector import run_pi_cycling_detection
        with patch("app.modules.pi_cycling_detector.settings") as mock_settings:
            mock_settings.PI_CYCLING_DETECTION_ENABLED = False
            result = run_pi_cycling_detection(db)
        assert result == {"status": "disabled"}

    def test_rapid_changes_detected(self, db):
        from app.modules.pi_cycling_detector import run_pi_cycling_detection
        vessel = _make_vessel(db)

        # 2 P&I club changes in 90 days with IG clubs
        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Gard P&I", "Skuld", days_ago=30)
        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Skuld", "Britannia", days_ago=10)
        db.commit()

        with patch("app.modules.pi_cycling_detector.settings") as mock_settings:
            mock_settings.PI_CYCLING_DETECTION_ENABLED = True
            result = run_pi_cycling_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.PI_CYCLING,
        ).first()
        assert anomaly is not None
        assert anomaly.risk_score_component == 20  # IG club -> lower score

    def test_non_ig_club_higher_score(self, db):
        from app.modules.pi_cycling_detector import run_pi_cycling_detection
        vessel = _make_vessel(db)

        # 2 changes, latest to non-IG club
        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Gard P&I", "Skuld", days_ago=30)
        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Skuld", "Unknown P&I Ltd", days_ago=10)
        db.commit()

        with patch("app.modules.pi_cycling_detector.settings") as mock_settings:
            mock_settings.PI_CYCLING_DETECTION_ENABLED = True
            result = run_pi_cycling_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.PI_CYCLING,
        ).first()
        assert anomaly.risk_score_component == 30
        assert anomaly.evidence_json["non_ig_club"] is True

    def test_single_change_no_anomaly(self, db):
        from app.modules.pi_cycling_detector import run_pi_cycling_detection
        vessel = _make_vessel(db)

        # Only 1 change -- not enough
        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Gard P&I", "Skuld", days_ago=30)
        db.commit()

        with patch("app.modules.pi_cycling_detector.settings") as mock_settings:
            mock_settings.PI_CYCLING_DETECTION_ENABLED = True
            result = run_pi_cycling_detection(db)

        assert result["anomalies_created"] == 0

    def test_dedup_existing_anomaly(self, db):
        from app.modules.pi_cycling_detector import run_pi_cycling_detection
        vessel = _make_vessel(db)

        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Gard P&I", "Skuld", days_ago=30)
        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Skuld", "Britannia", days_ago=10)

        existing = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.PI_CYCLING,
            start_time_utc=datetime.utcnow() - timedelta(days=30),
            risk_score_component=20,
        )
        db.add(existing)
        db.commit()

        with patch("app.modules.pi_cycling_detector.settings") as mock_settings:
            mock_settings.PI_CYCLING_DETECTION_ENABLED = True
            result = run_pi_cycling_detection(db)

        assert result["anomalies_created"] == 0

    def test_old_changes_not_counted(self, db):
        """Changes older than 90 days should not trigger detection."""
        from app.modules.pi_cycling_detector import run_pi_cycling_detection
        vessel = _make_vessel(db)

        # Both changes > 90 days ago
        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Gard P&I", "Skuld", days_ago=150)
        _make_history(db, vessel.vessel_id, "pi_club_name",
                      "Skuld", "Britannia", days_ago=120)
        db.commit()

        with patch("app.modules.pi_cycling_detector.settings") as mock_settings:
            mock_settings.PI_CYCLING_DETECTION_ENABLED = True
            result = run_pi_cycling_detection(db)

        assert result["anomalies_created"] == 0

    def test_ig_club_matching(self):
        from app.modules.pi_cycling_detector import _is_ig_club
        assert _is_ig_club("Gard P&I") is True
        assert _is_ig_club("gard") is True
        assert _is_ig_club("Skuld") is True
        assert _is_ig_club("UK P&I Club") is True
        assert _is_ig_club("Unknown P&I Company") is False
        assert _is_ig_club(None) is False
        assert _is_ig_club("") is False


# ═══════════════════════════════════════════════════════════════════════════
# C3: Sparse AIS transmission detector
# ═══════════════════════════════════════════════════════════════════════════

class TestSparseTransmissionDetector:
    def test_disabled_returns_early(self, db):
        from app.modules.sparse_transmission_detector import run_sparse_transmission_detection
        with patch("app.modules.sparse_transmission_detector.settings") as mock_settings:
            mock_settings.SPARSE_TRANSMISSION_DETECTION_ENABLED = False
            result = run_sparse_transmission_detection(db)
        assert result == {"status": "disabled"}

    def test_severe_sparsity_detected(self, db):
        from app.modules.sparse_transmission_detector import run_sparse_transmission_detection
        vessel = _make_vessel(db)

        # Create sparse AIS points: 6 points over 12 hours = 0.5 pts/hour
        now = datetime.utcnow()
        for i in range(6):
            _make_ais_point(db, vessel.vessel_id, sog=10.0,
                            ts=now - timedelta(hours=12) + timedelta(hours=i * 2.4))
        db.commit()

        with patch("app.modules.sparse_transmission_detector.settings") as mock_settings:
            mock_settings.SPARSE_TRANSMISSION_DETECTION_ENABLED = True
            result = run_sparse_transmission_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.SPARSE_TRANSMISSION,
        ).first()
        assert anomaly is not None
        assert anomaly.risk_score_component == 25  # severe
        assert anomaly.evidence_json["severity"] == "severe"

    def test_moderate_sparsity_detected(self, db):
        from app.modules.sparse_transmission_detector import run_sparse_transmission_detection
        vessel = _make_vessel(db)

        # Create moderately sparse points: 8 points over 5 hours = 1.6 pts/hour
        now = datetime.utcnow()
        for i in range(8):
            _make_ais_point(db, vessel.vessel_id, sog=10.0,
                            ts=now - timedelta(hours=5) + timedelta(minutes=i * 37.5))
        db.commit()

        with patch("app.modules.sparse_transmission_detector.settings") as mock_settings:
            mock_settings.SPARSE_TRANSMISSION_DETECTION_ENABLED = True
            result = run_sparse_transmission_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.SPARSE_TRANSMISSION,
        ).first()
        assert anomaly.risk_score_component == 15  # moderate
        assert anomaly.evidence_json["severity"] == "moderate"

    def test_normal_transmission_no_anomaly(self, db):
        from app.modules.sparse_transmission_detector import run_sparse_transmission_detection
        vessel = _make_vessel(db)

        # Normal density: 100 points over 5 hours = 20 pts/hour
        now = datetime.utcnow()
        for i in range(100):
            _make_ais_point(db, vessel.vessel_id, sog=10.0,
                            ts=now - timedelta(hours=5) + timedelta(minutes=i * 3))
        db.commit()

        with patch("app.modules.sparse_transmission_detector.settings") as mock_settings:
            mock_settings.SPARSE_TRANSMISSION_DETECTION_ENABLED = True
            result = run_sparse_transmission_detection(db)

        assert result["anomalies_created"] == 0

    def test_dedup_existing_anomaly(self, db):
        from app.modules.sparse_transmission_detector import run_sparse_transmission_detection
        vessel = _make_vessel(db)

        now = datetime.utcnow()
        for i in range(6):
            _make_ais_point(db, vessel.vessel_id, sog=10.0,
                            ts=now - timedelta(hours=12) + timedelta(hours=i * 2.4))

        existing = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.SPARSE_TRANSMISSION,
            start_time_utc=now - timedelta(hours=12),
            risk_score_component=25,
        )
        db.add(existing)
        db.commit()

        with patch("app.modules.sparse_transmission_detector.settings") as mock_settings:
            mock_settings.SPARSE_TRANSMISSION_DETECTION_ENABLED = True
            result = run_sparse_transmission_detection(db)

        assert result["anomalies_created"] == 0

    def test_stationary_vessel_not_flagged(self, db):
        """Vessel with SOG=0 (at anchor) should not be flagged even if sparse."""
        from app.modules.sparse_transmission_detector import run_sparse_transmission_detection
        vessel = _make_vessel(db)

        # Sparse points but vessel is stationary (SOG=0)
        now = datetime.utcnow()
        for i in range(6):
            _make_ais_point(db, vessel.vessel_id, sog=0.5,
                            ts=now - timedelta(hours=12) + timedelta(hours=i * 2.4))
        db.commit()

        with patch("app.modules.sparse_transmission_detector.settings") as mock_settings:
            mock_settings.SPARSE_TRANSMISSION_DETECTION_ENABLED = True
            result = run_sparse_transmission_detection(db)

        assert result["anomalies_created"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# C4: Vessel type consistency detector
# ═══════════════════════════════════════════════════════════════════════════

class TestVesselTypeConsistencyDetector:
    def test_disabled_returns_early(self, db):
        from app.modules.vessel_type_consistency_detector import run_vessel_type_consistency_detection
        with patch("app.modules.vessel_type_consistency_detector.settings") as mock_settings:
            mock_settings.TYPE_CONSISTENCY_DETECTION_ENABLED = False
            result = run_vessel_type_consistency_detection(db)
        assert result == {"status": "disabled"}

    def test_large_vessel_fishing_type_flagged(self, db):
        from app.modules.vessel_type_consistency_detector import run_vessel_type_consistency_detection
        vessel = _make_vessel(db, deadweight=100000.0, vessel_type="fishing vessel")
        db.commit()

        with patch("app.modules.vessel_type_consistency_detector.settings") as mock_settings:
            mock_settings.TYPE_CONSISTENCY_DETECTION_ENABLED = True
            result = run_vessel_type_consistency_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.TYPE_DWT_MISMATCH,
        ).first()
        assert anomaly is not None
        assert anomaly.risk_score_component == 25
        assert anomaly.evidence_json["reason"] == "type_dwt_mismatch"

    def test_large_vessel_tanker_type_ok(self, db):
        from app.modules.vessel_type_consistency_detector import run_vessel_type_consistency_detection
        vessel = _make_vessel(db, deadweight=100000.0, vessel_type="crude oil tanker")
        db.commit()

        with patch("app.modules.vessel_type_consistency_detector.settings") as mock_settings:
            mock_settings.TYPE_CONSISTENCY_DETECTION_ENABLED = True
            result = run_vessel_type_consistency_detection(db)

        assert result["anomalies_created"] == 0

    def test_recent_type_change_flagged(self, db):
        from app.modules.vessel_type_consistency_detector import run_vessel_type_consistency_detection
        vessel = _make_vessel(db, deadweight=3000.0, vessel_type="cargo")  # small vessel
        _make_history(db, vessel.vessel_id, "vessel_type",
                      "crude oil tanker", "cargo", days_ago=30)
        db.commit()

        with patch("app.modules.vessel_type_consistency_detector.settings") as mock_settings:
            mock_settings.TYPE_CONSISTENCY_DETECTION_ENABLED = True
            result = run_vessel_type_consistency_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.TYPE_DWT_MISMATCH,
        ).first()
        assert anomaly.risk_score_component == 15
        assert anomaly.evidence_json["recent_type_change"] is True

    def test_dedup_existing_anomaly(self, db):
        from app.modules.vessel_type_consistency_detector import run_vessel_type_consistency_detection
        vessel = _make_vessel(db, deadweight=100000.0, vessel_type="fishing vessel")

        existing = SpoofingAnomaly(
            vessel_id=vessel.vessel_id,
            anomaly_type=SpoofingTypeEnum.TYPE_DWT_MISMATCH,
            start_time_utc=datetime.utcnow(),
            risk_score_component=25,
        )
        db.add(existing)
        db.commit()

        with patch("app.modules.vessel_type_consistency_detector.settings") as mock_settings:
            mock_settings.TYPE_CONSISTENCY_DETECTION_ENABLED = True
            result = run_vessel_type_consistency_detection(db)

        assert result["anomalies_created"] == 0

    def test_small_vessel_not_flagged(self, db):
        """Vessels under 5000 DWT with non-commercial type should not be flagged."""
        from app.modules.vessel_type_consistency_detector import run_vessel_type_consistency_detection
        vessel = _make_vessel(db, deadweight=500.0, vessel_type="fishing vessel")
        db.commit()

        with patch("app.modules.vessel_type_consistency_detector.settings") as mock_settings:
            mock_settings.TYPE_CONSISTENCY_DETECTION_ENABLED = True
            result = run_vessel_type_consistency_detection(db)

        assert result["anomalies_created"] == 0

    def test_both_signals_combined(self, db):
        """Large vessel with non-commercial type AND recent type change."""
        from app.modules.vessel_type_consistency_detector import run_vessel_type_consistency_detection
        vessel = _make_vessel(db, deadweight=80000.0, vessel_type="pleasure craft")
        _make_history(db, vessel.vessel_id, "vessel_type",
                      "crude oil tanker", "pleasure craft", days_ago=15)
        db.commit()

        with patch("app.modules.vessel_type_consistency_detector.settings") as mock_settings:
            mock_settings.TYPE_CONSISTENCY_DETECTION_ENABLED = True
            result = run_vessel_type_consistency_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = db.query(SpoofingAnomaly).filter(
            SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.TYPE_DWT_MISMATCH,
        ).first()
        # DWT mismatch takes priority: +25
        assert anomaly.risk_score_component == 25
        assert anomaly.evidence_json["recent_type_change"] is True

    def test_non_commercial_type_matching(self):
        from app.modules.vessel_type_consistency_detector import _is_non_commercial_type
        assert _is_non_commercial_type("fishing") is True
        assert _is_non_commercial_type("Fishing Vessel") is True
        assert _is_non_commercial_type("pleasure craft") is True
        assert _is_non_commercial_type("crude oil tanker") is False
        assert _is_non_commercial_type("bulk carrier") is False
        assert _is_non_commercial_type(None) is False
        assert _is_non_commercial_type("") is False


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline integration
# ═══════════════════════════════════════════════════════════════════════════

class TestPipelineIntegration:
    def test_discovery_pipeline_has_stage_c_steps(self):
        """Verify the pipeline discovery function references stage C detectors."""
        import inspect
        from app.modules.dark_vessel_discovery import discover_dark_vessels
        source = inspect.getsource(discover_dark_vessels)
        assert "vessel_type_consistency" in source
        assert "route_laundering" in source
        assert "pi_cycling" in source
        assert "sparse_transmission" in source
