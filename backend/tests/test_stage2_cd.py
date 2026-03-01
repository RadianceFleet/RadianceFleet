"""Tests for Stage 2-C (Stale AIS Data) and 2-D (At-Sea Extended Operations).

Covers:
  - Stale AIS sequence detection (repeating heading/SOG/COG)
  - STALE_AIS_DATA enum membership
  - At-sea operations scoring (no port call tiered scoring)
  - Pipeline wiring for stale AIS step
  - Integration: _EXPECTED_SECTIONS, feature flags, Postgres enum migration
"""
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_vessel(vessel_id=1, mmsi="241001234"):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi
    v.deadweight = 120000
    v.vessel_type = "Crude Oil Tanker"
    v.flag = "CM"
    v.flag_risk_category = MagicMock()
    v.flag_risk_category.value = "high_risk"
    v.year_built = 2000
    v.ais_class = MagicMock()
    v.ais_class.value = "A"
    v.imo = None
    v.name = "Test Vessel"
    v.mmsi_first_seen_utc = None
    v.psc_detained_last_12m = False
    return v


def _make_ais_point(
    vessel_id=1,
    timestamp=None,
    heading=180.0,
    sog=12.0,
    cog=180.0,
    lat=35.0,
    lon=25.0,
    nav_status=0,
    point_id=None,
):
    p = MagicMock()
    p.vessel_id = vessel_id
    p.timestamp_utc = timestamp or datetime(2025, 6, 1, 12, 0)
    p.heading = heading
    p.sog = sog
    p.cog = cog
    p.lat = lat
    p.lon = lon
    p.nav_status = nav_status
    p.ais_point_id = point_id or 1
    p.ais_class = "A"
    p.sog_delta = None
    p.cog_delta = None
    p.source = None
    p.raw_payload_ref = None
    p.draught = None
    return p


def _make_gap(vessel_id=1, duration_minutes=480, corridor=None, vessel=None):
    gap = MagicMock()
    gap.vessel_id = vessel_id
    gap.gap_event_id = 100
    gap.duration_minutes = duration_minutes
    gap.gap_start_utc = datetime(2025, 6, 1, 0, 0)
    gap.gap_end_utc = gap.gap_start_utc + timedelta(minutes=duration_minutes)
    gap.risk_score = 0
    gap.impossible_speed_flag = False
    gap.velocity_plausibility_ratio = 0.5
    gap.max_plausible_distance_nm = 200.0
    gap.actual_gap_distance_nm = 80.0
    gap.pre_gap_sog = 12.0
    gap.corridor_id = None
    gap.corridor = corridor
    gap.vessel = vessel or _make_vessel(vessel_id)
    gap.in_dark_zone = False
    gap.start_point = None
    gap.gap_off_lat = None
    gap.gap_off_lon = None
    return gap


# ── TestStaleAISEnum ─────────────────────────────────────────────────────────

class TestStaleAISEnum:
    def test_stale_ais_data_in_enum(self):
        from app.models.base import SpoofingTypeEnum
        assert hasattr(SpoofingTypeEnum, "STALE_AIS_DATA")
        assert SpoofingTypeEnum.STALE_AIS_DATA.value == "stale_ais_data"

    def test_stale_ais_data_is_last_before_comment(self):
        """STALE_AIS_DATA should exist in enum values."""
        from app.models.base import SpoofingTypeEnum
        values = [e.value for e in SpoofingTypeEnum]
        assert "stale_ais_data" in values


# ── TestStaleAISDetection ────────────────────────────────────────────────────

class TestStaleAISDetection:
    """Tests for detect_stale_ais_data in gap_detector.py."""

    def _build_stale_points(self, vessel_id=1, count=15, span_hours=3.0):
        """Build a sequence of AIS points with identical heading/SOG/COG."""
        base_time = datetime(2025, 6, 1, 0, 0)
        interval = timedelta(hours=span_hours / max(count - 1, 1))
        points = []
        for i in range(count):
            points.append(_make_ais_point(
                vessel_id=vessel_id,
                timestamp=base_time + interval * i,
                heading=180.0,
                sog=12.0,
                cog=180.0,
                point_id=i + 1,
            ))
        return points

    @patch("app.modules.gap_detector.settings")
    def test_stale_sequence_detected(self, mock_settings):
        """15 identical points over 3h should create a STALE_AIS_DATA anomaly."""
        mock_settings.STALE_AIS_DETECTION_ENABLED = True

        vessel = _make_vessel()
        points = self._build_stale_points(count=15, span_hours=3.0)

        db = MagicMock()
        # db.query(Vessel).all() -> [vessel]
        db.query.return_value.all.return_value = [vessel]
        # For AISPoint query chain: filter -> order_by -> filter -> filter -> all
        ais_query = MagicMock()
        ais_query.filter.return_value = ais_query
        ais_query.order_by.return_value = ais_query
        ais_query.all.return_value = points

        # Need to differentiate between Vessel query and AISPoint query
        call_count = [0]
        original_query = db.query

        def side_effect_query(model):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: db.query(Vessel).all()
                result = MagicMock()
                result.all.return_value = [vessel]
                return result
            else:
                # Subsequent calls: AISPoint queries or SpoofingAnomaly dedup
                result = MagicMock()
                result.filter.return_value = result
                result.order_by.return_value = result
                result.all.return_value = points
                result.first.return_value = None  # no existing anomaly (dedup)
                return result

        db.query.side_effect = side_effect_query

        from app.modules.gap_detector import detect_stale_ais_data
        result = detect_stale_ais_data(db)

        assert result["stale_ais_anomalies"] >= 1
        # Verify db.add was called with a SpoofingAnomaly
        assert db.add.called

    @patch("app.modules.gap_detector.settings")
    def test_short_sequence_ignored(self, mock_settings):
        """5 identical points (below threshold of 10) should not trigger."""
        mock_settings.STALE_AIS_DETECTION_ENABLED = True

        vessel = _make_vessel()
        points = self._build_stale_points(count=5, span_hours=1.0)

        db = MagicMock()
        call_count = [0]

        def side_effect_query(model):
            call_count[0] += 1
            if call_count[0] == 1:
                result = MagicMock()
                result.all.return_value = [vessel]
                return result
            else:
                result = MagicMock()
                result.filter.return_value = result
                result.order_by.return_value = result
                result.all.return_value = points
                result.first.return_value = None
                return result

        db.query.side_effect = side_effect_query

        from app.modules.gap_detector import detect_stale_ais_data
        result = detect_stale_ais_data(db)

        assert result["stale_ais_anomalies"] == 0
        assert not db.add.called

    @patch("app.modules.gap_detector.settings")
    def test_anchored_vessel_ignored(self, mock_settings):
        """Stale values at SOG=0.3 (anchored) should not trigger."""
        mock_settings.STALE_AIS_DETECTION_ENABLED = True

        vessel = _make_vessel()
        base_time = datetime(2025, 6, 1, 0, 0)
        points = []
        for i in range(15):
            points.append(_make_ais_point(
                vessel_id=1,
                timestamp=base_time + timedelta(minutes=i * 15),
                heading=180.0,
                sog=0.3,  # below 0.5 threshold
                cog=180.0,
                point_id=i + 1,
            ))

        db = MagicMock()
        call_count = [0]

        def side_effect_query(model):
            call_count[0] += 1
            if call_count[0] == 1:
                result = MagicMock()
                result.all.return_value = [vessel]
                return result
            else:
                result = MagicMock()
                result.filter.return_value = result
                result.order_by.return_value = result
                result.all.return_value = points
                result.first.return_value = None
                return result

        db.query.side_effect = side_effect_query

        from app.modules.gap_detector import detect_stale_ais_data
        result = detect_stale_ais_data(db)

        assert result["stale_ais_anomalies"] == 0

    @patch("app.modules.gap_detector.settings")
    def test_disabled_flag_skips(self, mock_settings):
        """When STALE_AIS_DETECTION_ENABLED=False, detection is skipped."""
        mock_settings.STALE_AIS_DETECTION_ENABLED = False

        db = MagicMock()

        from app.modules.gap_detector import detect_stale_ais_data
        result = detect_stale_ais_data(db)

        assert result["stale_ais_anomalies"] == 0
        assert result.get("skipped") is True
        assert not db.query.called


# ── TestAtSeaOperations ──────────────────────────────────────────────────────

class TestAtSeaOperations:
    """Tests for at-sea operations scoring in compute_gap_score."""

    def _score(self, days_since_port=None, scoring_enabled=True):
        """Helper to compute gap score with at-sea operations config.

        Args:
            days_since_port: Days since last port call (None = no port call)
            scoring_enabled: AT_SEA_OPERATIONS_SCORING_ENABLED flag
        """
        from app.modules.risk_scoring import compute_gap_score

        vessel = _make_vessel()
        gap = _make_gap(vessel=vessel)

        config = {
            "gap_duration": {"4h_to_8h": 12},
            "at_sea_operations": {
                "no_port_call_90d": 15,
                "no_port_call_180d": 25,
                "no_port_call_365d": 35,
            },
        }

        db = MagicMock()
        # Make all default query chains return safe values
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.join.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.scalar.return_value = 0

        # Build port call mock
        if days_since_port is not None:
            port_call = MagicMock()
            dep_time = gap.gap_start_utc - timedelta(days=days_since_port)
            port_call.departure_utc = dep_time
            # This is handled via the PortCall query chain in the at-sea logic
        else:
            port_call = None

        # We need to intercept the specific PortCall query
        original_query = db.query

        def query_side_effect(model, *args):
            model_name = getattr(model, "__name__", "") or getattr(model, "__tablename__", "")
            mock_q = MagicMock()
            mock_q.filter.return_value = mock_q
            mock_q.order_by.return_value = mock_q
            mock_q.join.return_value = mock_q
            mock_q.all.return_value = []
            mock_q.first.return_value = None
            mock_q.count.return_value = 0
            mock_q.scalar.return_value = 0

            if "PortCall" in str(model):
                mock_q.filter.return_value.order_by.return_value.first.return_value = port_call
            return mock_q

        db.query.side_effect = query_side_effect

        with patch("app.modules.risk_scoring.settings") as mock_settings, \
             patch("app.config.settings") as mock_config_settings:
            # Set all scoring flags
            mock_settings.AT_SEA_OPERATIONS_SCORING_ENABLED = scoring_enabled
            mock_settings.STALE_AIS_SCORING_ENABLED = False
            mock_settings.TRACK_NATURALNESS_SCORING_ENABLED = False
            mock_settings.DRAUGHT_SCORING_ENABLED = False
            mock_settings.STATELESS_MMSI_SCORING_ENABLED = False
            mock_settings.FLAG_HOPPING_SCORING_ENABLED = False
            mock_settings.IMO_FRAUD_SCORING_ENABLED = False
            mock_settings.FLEET_SCORING_ENABLED = False
            mock_config_settings.AT_SEA_OPERATIONS_SCORING_ENABLED = scoring_enabled
            mock_config_settings.STALE_AIS_SCORING_ENABLED = False
            mock_config_settings.TRACK_NATURALNESS_SCORING_ENABLED = False
            mock_config_settings.DRAUGHT_SCORING_ENABLED = False
            mock_config_settings.STATELESS_MMSI_SCORING_ENABLED = False
            mock_config_settings.FLAG_HOPPING_SCORING_ENABLED = False
            mock_config_settings.IMO_FRAUD_SCORING_ENABLED = False
            mock_config_settings.FLEET_SCORING_ENABLED = False

            score, breakdown = compute_gap_score(
                gap, config, db=db,
                scoring_date=datetime(2025, 6, 1, 12, 0),
            )
        return score, breakdown

    def test_no_port_call_365d(self):
        """No port call at all should give +35."""
        _, breakdown = self._score(days_since_port=None)
        assert breakdown.get("at_sea_no_port_call_365d") == 35

    def test_port_call_200d_ago(self):
        """Port call 200d ago should give +25 (180d tier)."""
        _, breakdown = self._score(days_since_port=200)
        assert breakdown.get("at_sea_no_port_call_180d") == 25

    def test_port_call_100d_ago(self):
        """Port call 100d ago should give +15 (90d tier)."""
        _, breakdown = self._score(days_since_port=100)
        assert breakdown.get("at_sea_no_port_call_90d") == 15

    def test_recent_port_call(self):
        """Port call 30d ago should give +0 (no at-sea signal)."""
        _, breakdown = self._score(days_since_port=30)
        assert "at_sea_no_port_call_90d" not in breakdown
        assert "at_sea_no_port_call_180d" not in breakdown
        assert "at_sea_no_port_call_365d" not in breakdown

    def test_disabled_flag(self):
        """When AT_SEA_OPERATIONS_SCORING_ENABLED=False, no at-sea signal."""
        _, breakdown = self._score(days_since_port=None, scoring_enabled=False)
        assert "at_sea_no_port_call_365d" not in breakdown


# ── TestPipelineWiring ───────────────────────────────────────────────────────

class TestPipelineWiring:
    """Verify stale AIS step is wired into the discovery pipeline."""

    def test_stale_ais_step_in_pipeline(self):
        """discover_dark_vessels source should reference stale AIS detection."""
        from app.modules import dark_vessel_discovery
        source = inspect.getsource(dark_vessel_discovery.discover_dark_vessels)
        assert "stale_ais_detection" in source
        assert "detect_stale_ais_data" in source

    def test_stale_ais_gated_by_flag(self):
        """Pipeline should gate stale AIS behind STALE_AIS_DETECTION_ENABLED."""
        from app.modules import dark_vessel_discovery
        source = inspect.getsource(dark_vessel_discovery.discover_dark_vessels)
        assert "STALE_AIS_DETECTION_ENABLED" in source


# ── TestIntegration ──────────────────────────────────────────────────────────

class TestIntegration:
    """Integration checks for expected sections, flags, and migration."""

    def test_expected_sections_includes_stale_ais(self):
        from app.modules.risk_scoring import _EXPECTED_SECTIONS
        assert "stale_ais" in _EXPECTED_SECTIONS

    def test_expected_sections_includes_at_sea_operations(self):
        from app.modules.risk_scoring import _EXPECTED_SECTIONS
        assert "at_sea_operations" in _EXPECTED_SECTIONS

    def test_feature_flags_exist(self):
        from app.config import Settings
        s = Settings()
        assert hasattr(s, "STALE_AIS_DETECTION_ENABLED")
        assert hasattr(s, "STALE_AIS_SCORING_ENABLED")
        assert hasattr(s, "AT_SEA_OPERATIONS_SCORING_ENABLED")
        # All default to False
        assert s.STALE_AIS_DETECTION_ENABLED is False
        assert s.STALE_AIS_SCORING_ENABLED is False
        assert s.AT_SEA_OPERATIONS_SCORING_ENABLED is False

    def test_postgres_enum_migration_has_stale_ais_data(self):
        """database.py Postgres migration should include 'stale_ais_data'."""
        from app import database
        source = inspect.getsource(database._run_migrations)
        assert "stale_ais_data" in source

    def test_shadow_mode_excludes_stale_ais(self):
        """risk_scoring.py should exclude stale_ais_data when scoring disabled."""
        from app.modules import risk_scoring
        source = inspect.getsource(risk_scoring.compute_gap_score)
        assert "stale_ais_data" in source
        assert "STALE_AIS_SCORING_ENABLED" in source
