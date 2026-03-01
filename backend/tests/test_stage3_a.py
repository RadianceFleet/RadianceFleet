"""Stage 3-A: AIS Destination Manipulation Detector -- tests.

Tests cover:
- Feature flag gating (disabled returns early)
- Blank/generic destination detection
- Frequent destination changes detection
- Small vessel (DWT < 5000) skip
- STS heading while declaring EU port
- Normal vessel with no anomaly
- Pipeline wiring in dark_vessel_discovery.py
- Integration: enum exists, feature flags exist, YAML section, _EXPECTED_SECTIONS
"""
from __future__ import annotations

import math
from datetime import datetime, date, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc)


def _make_vessel(vessel_id=1, mmsi="123456789", deadweight=80000, merged_into=None):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi
    v.deadweight = deadweight
    v.merged_into_vessel_id = merged_into
    v.name = "TEST VESSEL"
    return v


def _make_point(vessel_id=1, ts=None, lat=25.0, lon=56.0, cog=180.0, sog=12.0,
                raw_payload_ref=None):
    pt = MagicMock()
    pt.vessel_id = vessel_id
    pt.timestamp_utc = ts or _utcnow()
    pt.lat = lat
    pt.lon = lon
    pt.cog = cog
    pt.sog = sog
    pt.raw_payload_ref = raw_payload_ref
    return pt


def _make_port(name="ROTTERDAM", country="NL", is_eu=True):
    p = MagicMock()
    p.name = name
    p.country = country
    p.is_eu = is_eu
    return p


def _setup_db(vessels=None, ais_points=None, corridors=None, eu_ports=None,
              existing_anomaly=None):
    """Build a mock DB session with properly chained query mocks.

    The actual detector code uses these patterns:
      - Vessel:          db.query(Vessel).filter(...).filter(...).all()
      - Port:            db.query(Port).filter(...).all()
      - Corridor:        db.query(Corridor).filter(...).all()
      - AISPoint:        db.query(AISPoint).filter(...).order_by(...).all()
      - SpoofingAnomaly: db.query(SpoofingAnomaly).filter(...).first()
    """
    if vessels is None:
        vessels = []
    if ais_points is None:
        ais_points = []
    if corridors is None:
        corridors = []
    if eu_ports is None:
        eu_ports = []

    db = MagicMock()

    def side_effect_query(model):
        mock_chain = MagicMock()
        model_name = getattr(model, "__name__", str(model))

        if model_name == "Vessel":
            # db.query(Vessel).filter(...).filter(...).all()
            mock_chain.filter.return_value.filter.return_value.all.return_value = vessels
        elif model_name == "Port":
            # db.query(Port).filter(...).all()
            mock_chain.filter.return_value.all.return_value = eu_ports
        elif model_name == "Corridor":
            # db.query(Corridor).filter(...).all()
            mock_chain.filter.return_value.all.return_value = corridors
        elif model_name == "AISPoint":
            # db.query(AISPoint).filter(...).order_by(...).all()
            mock_chain.filter.return_value.order_by.return_value.all.return_value = ais_points
        elif model_name == "SpoofingAnomaly":
            # db.query(SpoofingAnomaly).filter(...).first()
            mock_chain.filter.return_value.first.return_value = existing_anomaly
        return mock_chain

    db.query.side_effect = side_effect_query
    return db


# ---------------------------------------------------------------------------
# Test 1: Disabled flag returns early
# ---------------------------------------------------------------------------

class TestDestinationDetectorDisabled:
    def test_disabled_returns_early(self):
        """When DESTINATION_DETECTION_ENABLED is False, detector returns immediately."""
        from app.modules.destination_detector import detect_destination_anomalies

        db = MagicMock()
        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = False
            result = detect_destination_anomalies(db)

        assert result["status"] == "disabled"
        assert result["anomalies_created"] == 0
        # DB should not have been queried for vessels
        db.query.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2-5: Blank destination detected
# ---------------------------------------------------------------------------

class TestBlankDestination:
    def _run_with_dest(self, dest_value):
        """Helper: run detector with a single vessel whose destination is dest_value."""
        from app.modules.destination_detector import detect_destination_anomalies

        vessel = _make_vessel()
        point = _make_point(raw_payload_ref=dest_value)

        db = _setup_db(
            vessels=[vessel],
            ais_points=[point],
            corridors=[],
            eu_ports=[],
            existing_anomaly=None,
        )

        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            result = detect_destination_anomalies(db)

        return result, db

    def test_none_destination(self):
        result, db = self._run_with_dest(None)
        assert result["blank_destination"] >= 1
        assert result["anomalies_created"] >= 1
        db.add.assert_called()

    def test_for_orders_destination(self):
        result, db = self._run_with_dest("FOR ORDERS")
        assert result["blank_destination"] >= 1

    def test_tba_destination(self):
        result, db = self._run_with_dest("TBA")
        assert result["blank_destination"] >= 1

    def test_empty_string_destination(self):
        result, db = self._run_with_dest("")
        assert result["blank_destination"] >= 1


# ---------------------------------------------------------------------------
# Test 6-7: Frequent destination changes
# ---------------------------------------------------------------------------

class TestFrequentDestinationChanges:
    def test_more_than_3_changes(self):
        """Vessel with >3 distinct destinations in 7 days triggers anomaly."""
        from app.modules.destination_detector import detect_destination_anomalies

        vessel = _make_vessel()
        now = _utcnow()

        # Create points with 4 different destinations (none blank)
        points = [
            _make_point(ts=now - timedelta(hours=i), raw_payload_ref=dest)
            for i, dest in enumerate(["ROTTERDAM", "SINGAPORE", "FUJAIRAH", "KALAMATA"])
        ]

        db = _setup_db(
            vessels=[vessel],
            ais_points=points,
            corridors=[],
            eu_ports=[],
            existing_anomaly=None,
        )

        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            result = detect_destination_anomalies(db)

        assert result["frequent_changes"] >= 1
        assert result["anomalies_created"] >= 1

    def test_3_or_fewer_changes_no_anomaly(self):
        """Vessel with <=3 distinct destinations does NOT trigger frequent-change anomaly."""
        from app.modules.destination_detector import detect_destination_anomalies

        vessel = _make_vessel()
        now = _utcnow()

        points = [
            _make_point(ts=now - timedelta(hours=i), raw_payload_ref=dest)
            for i, dest in enumerate(["ROTTERDAM", "SINGAPORE", "FUJAIRAH"])
        ]

        db = _setup_db(
            vessels=[vessel],
            ais_points=points,
            corridors=[],
            eu_ports=[],
            existing_anomaly=None,
        )

        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            result = detect_destination_anomalies(db)

        assert result["frequent_changes"] == 0


# ---------------------------------------------------------------------------
# Test 8: Small vessel (DWT < 5000) skipped
# ---------------------------------------------------------------------------

class TestSmallVesselSkipped:
    def test_low_dwt_skipped(self):
        """Vessels with DWT <= 5000 are excluded from the query."""
        from app.modules.destination_detector import detect_destination_anomalies

        # No vessels returned (DWT filter excludes them)
        db = _setup_db(vessels=[], corridors=[], eu_ports=[])

        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            result = detect_destination_anomalies(db)

        assert result["vessels_analysed"] == 0
        assert result["anomalies_created"] == 0


# ---------------------------------------------------------------------------
# Test 9: STS heading while declaring EU port
# ---------------------------------------------------------------------------

class TestSTSHeadingDeviation:
    def test_heading_toward_sts_declaring_eu(self):
        """Vessel heading toward STS zone while declaring EU port is flagged."""
        from app.modules.destination_detector import detect_destination_anomalies

        vessel = _make_vessel()
        now = _utcnow()

        # Vessel declares ROTTERDAM but COG points south (180) toward STS zone at (25, 56)
        # Vessel is at lat=30, lon=56, heading south
        points = [
            _make_point(
                ts=now - timedelta(hours=i),
                lat=30.0, lon=56.0,
                cog=180.0,  # heading south
                raw_payload_ref="ROTTERDAM",
            )
            for i in range(3)
        ]

        eu_port = _make_port(name="ROTTERDAM", country="NL", is_eu=True)

        # Build mock corridor with real geometry
        corr = MagicMock()
        corr.corridor_id = 1
        corr.name = "Gulf STS Zone"
        corr.corridor_type = "sts_zone"
        corr.geometry = "POINT(56.0 25.0)"

        db = _setup_db(
            vessels=[vessel],
            ais_points=points,
            corridors=[corr],
            eu_ports=[eu_port],
            existing_anomaly=None,
        )

        # Mock load_geometry inside _get_corridor_centers
        from shapely.geometry import Point
        mock_geom = Point(56.0, 25.0)

        with patch("app.modules.destination_detector.settings") as mock_settings, \
             patch("app.modules.destination_detector._get_corridor_centers") as mock_centers:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            mock_centers.return_value = [{
                "corridor_id": 1,
                "name": "Gulf STS Zone",
                "lat": 25.0,
                "lon": 56.0,
            }]
            result = detect_destination_anomalies(db)

        assert result["heading_deviation"] >= 1
        assert result["anomalies_created"] >= 1


# ---------------------------------------------------------------------------
# Test 10: Normal vessel -- no anomaly
# ---------------------------------------------------------------------------

class TestNormalVessel:
    def test_legitimate_vessel_no_anomaly(self):
        """Vessel with stable, legitimate destination produces no anomaly."""
        from app.modules.destination_detector import detect_destination_anomalies

        vessel = _make_vessel()
        now = _utcnow()

        # All points have same non-blank destination -> no blank, no frequent changes
        # No STS corridors -> no heading deviation
        points = [
            _make_point(
                ts=now - timedelta(hours=i),
                lat=45.0, lon=5.0,
                cog=350.0,
                raw_payload_ref="ROTTERDAM",
            )
            for i in range(3)
        ]

        eu_port = _make_port(name="ROTTERDAM", country="NL", is_eu=True)

        db = _setup_db(
            vessels=[vessel],
            ais_points=points,
            corridors=[],
            eu_ports=[eu_port],
            existing_anomaly=None,
        )

        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            result = detect_destination_anomalies(db)

        assert result["heading_deviation"] == 0
        assert result["frequent_changes"] == 0
        assert result["blank_destination"] == 0


# ---------------------------------------------------------------------------
# Test 11: Dedup -- existing anomaly not duplicated
# ---------------------------------------------------------------------------

class TestDedup:
    def test_existing_anomaly_not_duplicated(self):
        """If a DESTINATION_DEVIATION anomaly already exists, don't create another."""
        from app.modules.destination_detector import detect_destination_anomalies

        vessel = _make_vessel()
        point = _make_point(raw_payload_ref=None)  # blank dest
        existing_anomaly = MagicMock()

        db = _setup_db(
            vessels=[vessel],
            ais_points=[point],
            corridors=[],
            eu_ports=[],
            existing_anomaly=existing_anomaly,  # existing -> skip
        )

        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            result = detect_destination_anomalies(db)

        assert result["anomalies_created"] == 0


# ---------------------------------------------------------------------------
# Test 12: No AIS points -- vessel skipped
# ---------------------------------------------------------------------------

class TestNoAISPoints:
    def test_no_recent_points_skipped(self):
        """Vessel with no recent AIS points is skipped."""
        from app.modules.destination_detector import detect_destination_anomalies

        vessel = _make_vessel()

        db = _setup_db(
            vessels=[vessel],
            ais_points=[],  # empty -> skip
            corridors=[],
            eu_ports=[],
        )

        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            result = detect_destination_anomalies(db)

        assert result["vessels_analysed"] == 0
        assert result["anomalies_created"] == 0


# ---------------------------------------------------------------------------
# Test 13-14: Pipeline wiring
# ---------------------------------------------------------------------------

class TestPipelineWiring:
    def test_destination_detection_step_present_in_source(self):
        """dark_vessel_discovery.py contains destination_detection step."""
        import inspect
        from app.modules import dark_vessel_discovery
        source = inspect.getsource(dark_vessel_discovery.discover_dark_vessels)
        assert "destination_detection" in source
        assert "detect_destination_anomalies" in source

    def test_destination_detection_gated_by_flag(self):
        """Step is gated by DESTINATION_DETECTION_ENABLED."""
        import inspect
        from app.modules import dark_vessel_discovery
        source = inspect.getsource(dark_vessel_discovery.discover_dark_vessels)
        assert "DESTINATION_DETECTION_ENABLED" in source


# ---------------------------------------------------------------------------
# Test 15-16: Integration -- enum + feature flags
# ---------------------------------------------------------------------------

class TestIntegrationEnum:
    def test_destination_deviation_enum_exists(self):
        """SpoofingTypeEnum has DESTINATION_DEVIATION value."""
        from app.models.base import SpoofingTypeEnum
        assert hasattr(SpoofingTypeEnum, "DESTINATION_DEVIATION")
        assert SpoofingTypeEnum.DESTINATION_DEVIATION.value == "destination_deviation"

    def test_enum_is_string_type(self):
        from app.models.base import SpoofingTypeEnum
        assert isinstance(SpoofingTypeEnum.DESTINATION_DEVIATION.value, str)


class TestIntegrationFeatureFlags:
    def test_detection_flag_exists(self):
        """Settings has DESTINATION_DETECTION_ENABLED."""
        from app.config import Settings
        s = Settings(DATABASE_URL="sqlite:///test.db")
        assert hasattr(s, "DESTINATION_DETECTION_ENABLED")
        assert s.DESTINATION_DETECTION_ENABLED is False

    def test_scoring_flag_exists(self):
        """Settings has DESTINATION_SCORING_ENABLED."""
        from app.config import Settings
        s = Settings(DATABASE_URL="sqlite:///test.db")
        assert hasattr(s, "DESTINATION_SCORING_ENABLED")
        assert s.DESTINATION_SCORING_ENABLED is False


# ---------------------------------------------------------------------------
# Test 17: YAML section exists
# ---------------------------------------------------------------------------

class TestIntegrationYAML:
    def test_destination_section_in_yaml(self):
        """risk_scoring.yaml includes destination section."""
        import yaml
        from pathlib import Path

        config_path = Path(__file__).parent.parent.parent / "config" / "risk_scoring.yaml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        assert "destination" in cfg
        assert cfg["destination"]["heading_to_sts_declaring_eu"] == 40
        assert cfg["destination"]["blank_generic_destination"] == 10
        assert cfg["destination"]["destination_changes_3_in_7d"] == 20


# ---------------------------------------------------------------------------
# Test 18: _EXPECTED_SECTIONS includes "destination"
# ---------------------------------------------------------------------------

class TestIntegrationExpectedSections:
    def test_destination_in_expected_sections(self):
        """risk_scoring._EXPECTED_SECTIONS includes 'destination'."""
        from app.modules.risk_scoring import _EXPECTED_SECTIONS
        assert "destination" in _EXPECTED_SECTIONS


# ---------------------------------------------------------------------------
# Test 19: Shadow-mode exclusion
# ---------------------------------------------------------------------------

class TestShadowModeExclusion:
    def test_shadow_exclusion_in_scoring_source(self):
        """risk_scoring.py contains shadow-mode exclusion for destination_deviation."""
        import inspect
        from app.modules import risk_scoring
        source = inspect.getsource(risk_scoring)
        assert "DESTINATION_SCORING_ENABLED" in source
        assert "destination_deviation" in source


# ---------------------------------------------------------------------------
# Test 20: Postgres enum migration
# ---------------------------------------------------------------------------

class TestPostgresEnumMigration:
    def test_destination_deviation_in_migration(self):
        """database.py includes destination_deviation in Postgres enum migration."""
        import inspect
        from app.database import _run_migrations
        source = inspect.getsource(_run_migrations)
        assert "destination_deviation" in source


# ---------------------------------------------------------------------------
# Test 21-23: Bearing calculation
# ---------------------------------------------------------------------------

class TestBearingCalculation:
    def test_initial_bearing_north(self):
        """Bearing from (0,0) to (10,0) should be ~0 (north)."""
        from app.modules.destination_detector import _initial_bearing
        bearing = _initial_bearing(0.0, 0.0, 10.0, 0.0)
        assert abs(bearing) < 1.0 or abs(bearing - 360) < 1.0

    def test_initial_bearing_east(self):
        """Bearing from (0,0) to (0,10) should be ~90 (east)."""
        from app.modules.destination_detector import _initial_bearing
        bearing = _initial_bearing(0.0, 0.0, 0.0, 10.0)
        assert abs(bearing - 90.0) < 1.0

    def test_bearing_diff_symmetric(self):
        """Bearing difference should be symmetric."""
        from app.modules.destination_detector import _bearing_diff
        assert abs(_bearing_diff(10, 350) - _bearing_diff(350, 10)) < 0.001


# ---------------------------------------------------------------------------
# Test 24: Blank destination classification
# ---------------------------------------------------------------------------

class TestBlankClassification:
    def test_blank_patterns(self):
        from app.modules.destination_detector import _is_blank_or_generic
        assert _is_blank_or_generic(None) is True
        assert _is_blank_or_generic("") is True
        assert _is_blank_or_generic("FOR ORDERS") is True
        assert _is_blank_or_generic("tba") is True  # case insensitive
        assert _is_blank_or_generic("AT SEA") is True
        assert _is_blank_or_generic("ROTTERDAM") is False
        assert _is_blank_or_generic("SINGAPORE") is False


# ---------------------------------------------------------------------------
# Test 25: date_from / date_to parameters
# ---------------------------------------------------------------------------

class TestDateParameters:
    def test_with_explicit_dates(self):
        """Detector accepts explicit date_from and date_to."""
        from app.modules.destination_detector import detect_destination_anomalies

        db = _setup_db(vessels=[], corridors=[], eu_ports=[])

        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            result = detect_destination_anomalies(
                db,
                date_from=date(2025, 1, 1),
                date_to=date(2025, 1, 31),
            )

        assert result["status"] == "ok"
        assert result["anomalies_created"] == 0


# ---------------------------------------------------------------------------
# Test 26: Score values match YAML config
# ---------------------------------------------------------------------------

class TestScoreValues:
    def test_score_constants(self):
        """Score constants match YAML configuration."""
        from app.modules.destination_detector import (
            SCORE_HEADING_TO_STS,
            SCORE_BLANK_GENERIC,
            SCORE_FREQUENT_CHANGES,
        )
        assert SCORE_HEADING_TO_STS == 40
        assert SCORE_BLANK_GENERIC == 10
        assert SCORE_FREQUENT_CHANGES == 20
