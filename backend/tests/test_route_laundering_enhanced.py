"""Tests for enhanced route laundering detection with configurable multi-hop
pattern templates.

Covers:
  - YAML loading and parsing
  - Port category classification (single match, multi-match)
  - Each pattern template matching (all 7 patterns)
  - Temporal bonus computation (<48h inter-hop)
  - Multi-hop matching (4-hop and 5-hop patterns)
  - Fallback to hardcoded patterns when YAML missing
  - Merge of laundering_intermediaries.yaml into intermediary category
  - Greedy forward scan matching logic
  - Edge cases (empty port calls, unknown ports, partial matches)
  - Integration: full detect_route_laundering with new patterns
  - Config toggle behavior
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.base import SpoofingTypeEnum
from app.models.port import Port
from app.models.port_call import PortCall
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.vessel import Vessel


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Create an in-memory SQLite database with all tables."""
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


@pytest.fixture(autouse=True)
def _reset_module_caches():
    """Reset module-level caches before each test."""
    import app.modules.route_laundering_detector as rl_mod

    rl_mod._reset_caches()
    yield
    rl_mod._reset_caches()


# ── Helper factories ─────────────────────────────────────────────────────


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


# ── Sample YAML content ─────────────────────────────────────────────────

SAMPLE_YAML = {
    "port_categories": {
        "russian": ["RU"],
        "sanctioned": ["SY", "KP", "IR", "CU"],
        "intermediary": ["AE", "IN", "TR", "EG", "MY", "OM"],
        "eu": ["DE", "NL", "BE", "FR", "IT", "ES", "PL", "GR"],
        "asian": ["CN", "IN", "MY", "SG", "TH", "VN", "KR"],
        "caribbean": ["BS", "KY", "BB", "TT", "JM", "AG"],
        "iranian": ["IR"],
        "venezuelan": ["VE"],
    },
    "patterns": {
        "russian_intermediary_sanctioned": {
            "hops": ["russian", "intermediary", "sanctioned"],
            "base_score": 35,
            "description": "Russia -> intermediary -> sanctioned destination",
        },
        "russian_intermediary": {
            "hops": ["russian", "intermediary"],
            "base_score": 20,
            "description": "Russia -> intermediary port",
        },
        "sanctioned_intermediary_eu": {
            "hops": ["sanctioned", "intermediary", "eu"],
            "base_score": 30,
            "description": "Sanctioned origin -> intermediary -> EU destination",
        },
        "iranian_intermediary_asian": {
            "hops": ["iranian", "intermediary", "asian"],
            "base_score": 30,
            "description": "Iran -> intermediary -> Asian destination",
        },
        "venezuelan_caribbean_us": {
            "hops": ["venezuelan", "caribbean"],
            "base_score": 35,
            "description": "Venezuela -> Caribbean intermediary",
        },
        "russian_multi_hop": {
            "hops": ["russian", "intermediary", "intermediary", "sanctioned"],
            "base_score": 45,
            "description": "Russia -> 2 intermediaries -> sanctioned (4-hop)",
        },
        "russian_5_hop": {
            "hops": ["russian", "intermediary", "intermediary", "intermediary", "sanctioned"],
            "base_score": 50,
            "description": "Russia -> 3 intermediaries -> sanctioned (5-hop)",
        },
    },
    "temporal_bonus": {
        "enabled": True,
        "threshold_hours": 48,
        "bonus_points": 10,
    },
}


@pytest.fixture
def yaml_config_path():
    """Write sample YAML to a temp file and return its path."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        yaml.dump(SAMPLE_YAML, f)
        path = f.name
    yield path
    os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════
# 1. YAML loading and parsing
# ═══════════════════════════════════════════════════════════════════════════


class TestYAMLLoading:
    def test_load_pattern_templates_from_yaml(self, yaml_config_path):
        from app.modules.route_laundering_detector import _load_pattern_templates

        templates = _load_pattern_templates(yaml_config_path)
        assert "russian_intermediary_sanctioned" in templates
        assert "russian_intermediary" in templates
        assert "russian_multi_hop" in templates
        assert "russian_5_hop" in templates
        assert len(templates) == 7

    def test_load_port_categories_from_yaml(self, yaml_config_path):
        from app.modules.route_laundering_detector import _load_port_categories

        categories = _load_port_categories(yaml_config_path)
        assert "russian" in categories
        assert "RU" in categories["russian"]
        assert "intermediary" in categories
        assert "iranian" in categories
        assert "IR" in categories["iranian"]

    def test_fallback_when_yaml_missing(self):
        from app.modules.route_laundering_detector import _load_pattern_templates

        templates = _load_pattern_templates("/nonexistent/path.yaml")
        assert "russian_intermediary_sanctioned" in templates
        assert "russian_intermediary" in templates
        assert len(templates) == 2  # Only hardcoded fallbacks


# ═══════════════════════════════════════════════════════════════════════════
# 2. Port category classification
# ═══════════════════════════════════════════════════════════════════════════


class TestPortCategoryClassification:
    def test_single_category_match(self):
        from app.modules.route_laundering_detector import _classify_port_by_categories

        categories = SAMPLE_YAML["port_categories"]
        result = _classify_port_by_categories("RU", categories)
        assert "russian" in result
        assert len(result) == 1

    def test_multi_category_match(self):
        """IR matches both 'iranian' and 'sanctioned'."""
        from app.modules.route_laundering_detector import _classify_port_by_categories

        categories = SAMPLE_YAML["port_categories"]
        result = _classify_port_by_categories("IR", categories)
        assert "iranian" in result
        assert "sanctioned" in result
        assert len(result) == 2

    def test_intermediary_and_asian_overlap(self):
        """IN matches both 'intermediary' and 'asian'."""
        from app.modules.route_laundering_detector import _classify_port_by_categories

        categories = SAMPLE_YAML["port_categories"]
        result = _classify_port_by_categories("IN", categories)
        assert "intermediary" in result
        assert "asian" in result

    def test_unknown_country(self):
        from app.modules.route_laundering_detector import _classify_port_by_categories

        categories = SAMPLE_YAML["port_categories"]
        result = _classify_port_by_categories("ZZ", categories)
        assert result == []

    def test_empty_country(self):
        from app.modules.route_laundering_detector import _classify_port_by_categories

        categories = SAMPLE_YAML["port_categories"]
        result = _classify_port_by_categories("", categories)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════
# 3. Pattern template matching
# ═══════════════════════════════════════════════════════════════════════════


class TestPatternTemplateMatching:
    def _make_mock_pc(self, port_id=None, raw_name=None, arrival_utc=None):
        """Create a minimal mock PortCall for template matching tests."""
        pc = SimpleNamespace()
        pc.port_id = port_id
        pc.raw_port_name = raw_name
        pc.arrival_utc = arrival_utc or datetime.utcnow()
        return pc

    def test_russian_intermediary_sanctioned_match(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["russian_intermediary_sanctioned"]
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), ["russian"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["intermediary"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=10)), ["sanctioned"]),
        ]

        result = _match_pattern_template(
            classified, "russian_intermediary_sanctioned", pattern, categories
        )
        assert result is not None
        assert result["pattern_name"] == "russian_intermediary_sanctioned"
        assert result["base_score"] == 35
        assert result["hop_count"] == 3

    def test_russian_intermediary_match(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["russian_intermediary"]
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), ["russian"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["intermediary"]),
        ]

        result = _match_pattern_template(
            classified, "russian_intermediary", pattern, categories
        )
        assert result is not None
        assert result["base_score"] == 20
        assert result["hop_count"] == 2

    def test_sanctioned_intermediary_eu_match(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["sanctioned_intermediary_eu"]
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), ["sanctioned", "iranian"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["intermediary"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=10)), ["eu"]),
        ]

        result = _match_pattern_template(
            classified, "sanctioned_intermediary_eu", pattern, categories
        )
        assert result is not None
        assert result["base_score"] == 30

    def test_iranian_intermediary_asian_match(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["iranian_intermediary_asian"]
        now = datetime.utcnow()

        # IR matches both iranian and sanctioned; IN matches intermediary and asian
        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), ["iranian", "sanctioned"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["intermediary", "asian"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=10)), ["asian"]),
        ]

        result = _match_pattern_template(
            classified, "iranian_intermediary_asian", pattern, categories
        )
        assert result is not None
        assert result["base_score"] == 30

    def test_venezuelan_caribbean_match(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["venezuelan_caribbean_us"]
        now = datetime.utcnow()

        # VE matches venezuelan (not sanctioned in YAML since VE removed from sanctioned)
        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), ["venezuelan"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["caribbean"]),
        ]

        result = _match_pattern_template(
            classified, "venezuelan_caribbean_us", pattern, categories
        )
        assert result is not None
        assert result["base_score"] == 35

    def test_4_hop_multi_intermediary_match(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["russian_multi_hop"]
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=40)), ["russian"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), ["intermediary"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["intermediary"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=10)), ["sanctioned"]),
        ]

        result = _match_pattern_template(
            classified, "russian_multi_hop", pattern, categories
        )
        assert result is not None
        assert result["base_score"] == 45
        assert result["hop_count"] == 4

    def test_5_hop_match(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["russian_5_hop"]
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=50)), ["russian"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=40)), ["intermediary"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), ["intermediary"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["intermediary"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=10)), ["sanctioned"]),
        ]

        result = _match_pattern_template(
            classified, "russian_5_hop", pattern, categories
        )
        assert result is not None
        assert result["base_score"] == 50
        assert result["hop_count"] == 5

    def test_no_match_partial_sequence(self):
        """Pattern requires 3 hops but sequence only has 2 matching."""
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["russian_intermediary_sanctioned"]
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), ["russian"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["intermediary"]),
            # No sanctioned port
            (self._make_mock_pc(arrival_utc=now - timedelta(days=10)), ["eu"]),
        ]

        result = _match_pattern_template(
            classified, "russian_intermediary_sanctioned", pattern, categories
        )
        assert result is None

    def test_greedy_forward_scan_skips_non_matching(self):
        """Greedy scan should skip ports that don't match current hop."""
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["russian_intermediary_sanctioned"]
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(arrival_utc=now - timedelta(days=50)), ["russian"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=40)), ["eu"]),  # skip
            (self._make_mock_pc(arrival_utc=now - timedelta(days=30)), []),  # skip
            (self._make_mock_pc(arrival_utc=now - timedelta(days=20)), ["intermediary"]),
            (self._make_mock_pc(arrival_utc=now - timedelta(days=10)), ["sanctioned"]),
        ]

        result = _match_pattern_template(
            classified, "russian_intermediary_sanctioned", pattern, categories
        )
        assert result is not None
        assert result["hop_count"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# 4. Temporal bonus
# ═══════════════════════════════════════════════════════════════════════════


class TestTemporalBonus:
    def _make_mock_pc(self, arrival_utc):
        pc = SimpleNamespace()
        pc.arrival_utc = arrival_utc
        return pc

    def test_bonus_when_all_hops_under_threshold(self):
        from app.modules.route_laundering_detector import _compute_temporal_bonus

        now = datetime.utcnow()
        matched = [
            (self._make_mock_pc(now - timedelta(hours=96)), "russian"),
            (self._make_mock_pc(now - timedelta(hours=72)), "intermediary"),  # 24h gap
            (self._make_mock_pc(now - timedelta(hours=48)), "sanctioned"),  # 24h gap
        ]
        result = _compute_temporal_bonus(matched, threshold_hours=48)
        assert result == 10

    def test_no_bonus_when_gap_exceeds_threshold(self):
        from app.modules.route_laundering_detector import _compute_temporal_bonus

        now = datetime.utcnow()
        matched = [
            (self._make_mock_pc(now - timedelta(hours=200)), "russian"),
            (self._make_mock_pc(now - timedelta(hours=100)), "intermediary"),  # 100h gap
            (self._make_mock_pc(now - timedelta(hours=10)), "sanctioned"),  # 90h gap
        ]
        result = _compute_temporal_bonus(matched, threshold_hours=48)
        assert result == 0

    def test_no_bonus_with_single_port(self):
        from app.modules.route_laundering_detector import _compute_temporal_bonus

        now = datetime.utcnow()
        matched = [
            (self._make_mock_pc(now), "russian"),
        ]
        result = _compute_temporal_bonus(matched, threshold_hours=48)
        assert result == 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def _make_mock_pc(self, arrival_utc=None):
        pc = SimpleNamespace()
        pc.port_id = None
        pc.raw_port_name = None
        pc.arrival_utc = arrival_utc or datetime.utcnow()
        return pc

    def test_empty_port_calls(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["russian_intermediary"]
        result = _match_pattern_template([], "russian_intermediary", pattern, categories)
        assert result is None

    def test_all_unknown_ports(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = SAMPLE_YAML["patterns"]["russian_intermediary"]
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(now - timedelta(days=30)), []),
            (self._make_mock_pc(now - timedelta(days=20)), []),
        ]

        result = _match_pattern_template(
            classified, "russian_intermediary", pattern, categories
        )
        assert result is None

    def test_empty_hops_pattern(self):
        from app.modules.route_laundering_detector import _match_pattern_template

        categories = SAMPLE_YAML["port_categories"]
        pattern = {"hops": [], "base_score": 0}
        now = datetime.utcnow()

        classified = [
            (self._make_mock_pc(now), ["russian"]),
        ]

        result = _match_pattern_template(
            classified, "empty", pattern, categories
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 6. Intermediary merge
# ═══════════════════════════════════════════════════════════════════════════


class TestIntermediaryMerge:
    def test_merge_intermediaries_into_categories(self, yaml_config_path):
        """laundering_intermediaries.yaml countries merge into intermediary category."""
        import app.modules.route_laundering_detector as rl_mod

        # Inject intermediary config directly (as if YAML was loaded)
        rl_mod._INTERMEDIARY_CONFIG = {
            "intermediary_ports": [
                {"name": "Fujairah", "country": "AE"},
                {"name": "Sohar", "country": "OM"},
                {"name": "Algeciras", "country": "XX"},  # unique country not in YAML
            ]
        }

        categories = rl_mod._load_port_categories(yaml_config_path)
        # XX from intermediaries should be merged in
        assert "XX" in categories["intermediary"]
        # AE should still be present (from YAML + intermediaries)
        assert "AE" in categories["intermediary"]


# ═══════════════════════════════════════════════════════════════════════════
# 7. Integration: full detection with new patterns
# ═══════════════════════════════════════════════════════════════════════════


class TestIntegrationDetection:
    @pytest.fixture(autouse=True)
    def _inject_config(self, yaml_config_path):
        """Inject config and intermediary data."""
        import app.modules.route_laundering_detector as rl_mod

        rl_mod._INTERMEDIARY_CONFIG = {
            "intermediary_ports": [
                {"name": "Fujairah", "country": "AE"},
                {"name": "Sohar", "country": "OM"},
                {"name": "Ceyhan", "country": "TR"},
                {"name": "Jamnagar", "country": "IN"},
            ]
        }
        self._yaml_path = yaml_config_path
        yield
        rl_mod._reset_caches()

    def test_3_hop_detection_with_templates(self, db):
        from app.modules.route_laundering_detector import run_route_laundering_detection

        vessel = _make_vessel(db)
        ru_port = _make_port(db, name="Novorossiysk", country="RU")
        int_port = _make_port(db, name="Fujairah", country="AE")
        sanc_port = _make_port(db, name="Bandar Abbas", country="IR")

        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, ru_port.port_id, arrival_utc=now - timedelta(days=30))
        _make_port_call(db, vessel.vessel_id, int_port.port_id, arrival_utc=now - timedelta(days=20))
        _make_port_call(db, vessel.vessel_id, sanc_port.port_id, arrival_utc=now - timedelta(days=10))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            mock_settings.ROUTE_LAUNDERING_PATTERNS_CONFIG = self._yaml_path
            mock_settings.ROUTE_LAUNDERING_TEMPORAL_BONUS_ENABLED = True
            mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
            result = run_route_laundering_detection(db)

        assert result["status"] == "ok"
        assert result["anomalies_created"] == 1

        anomaly = (
            db.query(SpoofingAnomaly)
            .filter(SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING)
            .first()
        )
        assert anomaly is not None
        # Should match russian_intermediary_sanctioned (35) or higher
        assert anomaly.risk_score_component >= 35
        assert anomaly.evidence_json["hop_count"] >= 3

    def test_4_hop_detection(self, db):
        """4-hop pattern: Russian -> intermediary -> intermediary -> sanctioned."""
        from app.modules.route_laundering_detector import run_route_laundering_detection

        vessel = _make_vessel(db)
        ru_port = _make_port(db, name="Primorsk", country="RU")
        int_port_1 = _make_port(db, name="Fujairah", country="AE")
        int_port_2 = _make_port(db, name="Ceyhan", country="TR")
        sanc_port = _make_port(db, name="Latakia", country="SY")

        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, ru_port.port_id, arrival_utc=now - timedelta(days=40))
        _make_port_call(db, vessel.vessel_id, int_port_1.port_id, arrival_utc=now - timedelta(days=30))
        _make_port_call(db, vessel.vessel_id, int_port_2.port_id, arrival_utc=now - timedelta(days=20))
        _make_port_call(db, vessel.vessel_id, sanc_port.port_id, arrival_utc=now - timedelta(days=10))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            mock_settings.ROUTE_LAUNDERING_PATTERNS_CONFIG = self._yaml_path
            mock_settings.ROUTE_LAUNDERING_TEMPORAL_BONUS_ENABLED = True
            mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
            result = run_route_laundering_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = (
            db.query(SpoofingAnomaly)
            .filter(SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING)
            .first()
        )
        assert anomaly is not None
        # 4-hop (45) should beat 3-hop (35)
        assert anomaly.risk_score_component >= 45

    def test_disabled_returns_early(self, db):
        from app.modules.route_laundering_detector import run_route_laundering_detection

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = False
            result = run_route_laundering_detection(db)
        assert result == {"status": "disabled"}

    def test_temporal_bonus_applied_in_integration(self, db):
        """Temporal bonus should add points when inter-hop gaps are < threshold."""
        from app.modules.route_laundering_detector import run_route_laundering_detection

        vessel = _make_vessel(db)
        ru_port = _make_port(db, name="Novorossiysk", country="RU")
        int_port = _make_port(db, name="Fujairah", country="AE")
        sanc_port = _make_port(db, name="Bandar Abbas", country="IR")

        now = datetime.utcnow()
        # All hops within 24h of each other (under 48h threshold)
        _make_port_call(db, vessel.vessel_id, ru_port.port_id, arrival_utc=now - timedelta(hours=72))
        _make_port_call(db, vessel.vessel_id, int_port.port_id, arrival_utc=now - timedelta(hours=48))
        _make_port_call(db, vessel.vessel_id, sanc_port.port_id, arrival_utc=now - timedelta(hours=24))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            mock_settings.ROUTE_LAUNDERING_PATTERNS_CONFIG = self._yaml_path
            mock_settings.ROUTE_LAUNDERING_TEMPORAL_BONUS_ENABLED = True
            mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
            result = run_route_laundering_detection(db)

        assert result["anomalies_created"] == 1
        anomaly = (
            db.query(SpoofingAnomaly)
            .filter(SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING)
            .first()
        )
        assert anomaly is not None
        # 35 base + 10 temporal bonus = 45
        assert anomaly.risk_score_component == 45
        assert anomaly.evidence_json.get("temporal_bonus") == 10

    def test_no_pattern_no_anomaly(self, db):
        from app.modules.route_laundering_detector import run_route_laundering_detection

        vessel = _make_vessel(db)
        us_port = _make_port(db, name="Houston", country="US")
        de_port = _make_port(db, name="Hamburg", country="DE")

        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, us_port.port_id, arrival_utc=now - timedelta(days=30))
        _make_port_call(db, vessel.vessel_id, de_port.port_id, arrival_utc=now - timedelta(days=20))
        db.commit()

        with patch("app.modules.route_laundering_detector.settings") as mock_settings:
            mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
            mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
            mock_settings.ROUTE_LAUNDERING_PATTERNS_CONFIG = self._yaml_path
            mock_settings.ROUTE_LAUNDERING_TEMPORAL_BONUS_ENABLED = True
            mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
            result = run_route_laundering_detection(db)

        assert result["anomalies_created"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 8. Config toggle behavior
# ═══════════════════════════════════════════════════════════════════════════


class TestConfigToggle:
    def test_temporal_bonus_disabled_via_setting(self, db):
        """When temporal bonus disabled, no bonus points should be added."""
        import app.modules.route_laundering_detector as rl_mod
        from app.modules.route_laundering_detector import run_route_laundering_detection

        rl_mod._INTERMEDIARY_CONFIG = {
            "intermediary_ports": [
                {"name": "Fujairah", "country": "AE"},
            ]
        }

        vessel = _make_vessel(db)
        ru_port = _make_port(db, name="Novorossiysk", country="RU")
        int_port = _make_port(db, name="Fujairah", country="AE")
        sanc_port = _make_port(db, name="Bandar Abbas", country="IR")

        now = datetime.utcnow()
        _make_port_call(db, vessel.vessel_id, ru_port.port_id, arrival_utc=now - timedelta(hours=72))
        _make_port_call(db, vessel.vessel_id, int_port.port_id, arrival_utc=now - timedelta(hours=48))
        _make_port_call(db, vessel.vessel_id, sanc_port.port_id, arrival_utc=now - timedelta(hours=24))
        db.commit()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(SAMPLE_YAML, f)
            yaml_path = f.name

        try:
            with patch("app.modules.route_laundering_detector.settings") as mock_settings:
                mock_settings.ROUTE_LAUNDERING_DETECTION_ENABLED = True
                mock_settings.ROUTE_LAUNDERING_LOOKBACK_DAYS = 180
                mock_settings.ROUTE_LAUNDERING_PATTERNS_CONFIG = yaml_path
                mock_settings.ROUTE_LAUNDERING_TEMPORAL_BONUS_ENABLED = False  # disabled
                mock_settings.RISK_SCORING_CONFIG = "config/risk_scoring.yaml"
                result = run_route_laundering_detection(db)
        finally:
            os.unlink(yaml_path)

        assert result["anomalies_created"] == 1
        anomaly = (
            db.query(SpoofingAnomaly)
            .filter(SpoofingAnomaly.anomaly_type == SpoofingTypeEnum.ROUTE_LAUNDERING)
            .first()
        )
        # No temporal bonus -- score should be base only
        # The highest matching pattern should be russian_intermediary_sanctioned (35)
        # with IR also matching iranian_intermediary_asian if IN is in sequence,
        # but here AE is intermediary and IR is sanctioned, so 35 from russian_intermediary_sanctioned
        assert anomaly.risk_score_component == 35
        assert "temporal_bonus" not in anomaly.evidence_json
