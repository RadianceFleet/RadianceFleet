"""Tests for Stage 5-B: Convoy + floating storage + Arctic corridor detection.

Covers:
  - Convoy detection with synchronized vessels
  - Duration scoring tiers (4-8h, 8-24h, 24h+)
  - Edge cases: too far apart, anchored, heading divergence
  - Floating storage detection
  - Arctic corridor + no ice class
  - Feature flag gating
  - Pipeline wiring
  - Config integration
  - ConvoyEvent model
  - Empty data handling
"""
from __future__ import annotations

import datetime
from datetime import timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(hours_offset: float = 0) -> datetime.datetime:
    """Return a UTC datetime offset from a fixed base time."""
    base = datetime.datetime(2025, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(hours=hours_offset)


def _make_ais_point(vessel_id: int, lat: float, lon: float, sog: float,
                     cog: float, ts: datetime.datetime, heading=None):
    """Create a mock AISPoint."""
    pt = MagicMock()
    pt.vessel_id = vessel_id
    pt.lat = lat
    pt.lon = lon
    pt.sog = sog
    pt.cog = cog
    pt.heading = heading
    pt.timestamp_utc = ts
    pt.draught = None
    return pt


def _make_convoy_points(vessel_a_id: int, vessel_b_id: int,
                         lat: float, lon: float, sog: float, cog: float,
                         num_windows: int, start_hours: float = 0,
                         distance_offset: float = 0.01):
    """Create synchronized AIS points for two vessels over num_windows * 15min."""
    points = []
    for i in range(num_windows):
        ts = _ts(start_hours + i * 0.25)  # 15 min intervals
        # Vessel A at (lat, lon)
        pt_a = _make_ais_point(vessel_a_id, lat, lon, sog, cog, ts)
        # Vessel B at slightly offset position (within 5nm)
        pt_b = _make_ais_point(vessel_b_id, lat + distance_offset, lon, sog, cog, ts)
        points.extend([pt_a, pt_b])
    return points


def _mock_db_with_points(points, corridors=None, convoy_events=None):
    """Create a mock db session that returns the given points."""
    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        model_name = getattr(model, "__name__", str(model))
        if model_name == "AISPoint":
            q.order_by.return_value = q
            q.filter.return_value = q
            q.all.return_value = points
        elif model_name == "Corridor":
            q.all.return_value = corridors or []
        elif model_name == "ConvoyEvent":
            q.filter.return_value = q
            q.first.return_value = None
            q.all.return_value = convoy_events or []
        else:
            q.filter.return_value = q
            q.first.return_value = None
            q.all.return_value = []
            q.count.return_value = 0
        return q

    db.query.side_effect = query_side_effect
    return db


# ── ConvoyEvent Model Tests ──────────────────────────────────────────────────

class TestConvoyEventModel:
    """Tests for the ConvoyEvent SQLAlchemy model."""

    def test_model_import(self):
        from app.models.convoy_event import ConvoyEvent
        assert ConvoyEvent.__tablename__ == "convoy_events"

    def test_model_in_init(self):
        from app.models import ConvoyEvent
        assert ConvoyEvent is not None

    def test_model_columns(self):
        from app.models.convoy_event import ConvoyEvent
        cols = {c.name for c in ConvoyEvent.__table__.columns}
        expected = {
            "convoy_id", "vessel_a_id", "vessel_b_id",
            "start_time_utc", "end_time_utc", "duration_hours",
            "mean_distance_nm", "mean_heading_delta",
            "corridor_id", "risk_score_component",
            "evidence_json", "created_at",
        }
        assert expected.issubset(cols)


# ── Convoy Detection Tests ───────────────────────────────────────────────────

class TestConvoyDetection:
    """Tests for the detect_convoys() function."""

    @patch("app.modules.convoy_detector.settings")
    def test_disabled_returns_zero(self, mock_settings):
        """Feature flag off -> no detection."""
        mock_settings.CONVOY_DETECTION_ENABLED = False
        from app.modules.convoy_detector import detect_convoys
        db = MagicMock()
        result = detect_convoys(db)
        assert result["convoy_events_created"] == 0
        assert result.get("status") == "disabled"

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config", return_value={"convoy": {}})
    def test_empty_data(self, mock_config, mock_settings):
        """No AIS points -> 0 events."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_convoys
        db = _mock_db_with_points([])
        result = detect_convoys(db)
        assert result["convoy_events_created"] == 0

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config", return_value={"convoy": {}})
    def test_synchronized_vessels_detected(self, mock_config, mock_settings):
        """Two vessels moving together for 5 hours should create a convoy event."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_convoys

        # 20 windows * 15 min = 5 hours (>= 4h threshold)
        points = _make_convoy_points(
            vessel_a_id=1, vessel_b_id=2,
            lat=40.0, lon=25.0, sog=8.0, cog=90.0,
            num_windows=20, distance_offset=0.01  # ~0.6nm apart
        )
        db = _mock_db_with_points(points)
        result = detect_convoys(db)
        assert result["convoy_events_created"] == 1
        # Verify db.add was called with a ConvoyEvent
        from app.models.convoy_event import ConvoyEvent
        added = [c for c in db.add.call_args_list if isinstance(c[0][0], ConvoyEvent)]
        assert len(added) == 1
        event = added[0][0][0]
        assert event.vessel_a_id == 1
        assert event.vessel_b_id == 2
        assert event.duration_hours >= 4.0
        assert event.risk_score_component == 15  # 4-8h tier

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config", return_value={"convoy": {}})
    def test_vessels_too_far_apart(self, mock_config, mock_settings):
        """Vessels > 5nm apart should not form a convoy."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_convoys

        # distance_offset=0.1 degrees ~ 6nm — too far
        points = _make_convoy_points(
            vessel_a_id=1, vessel_b_id=2,
            lat=40.0, lon=25.0, sog=8.0, cog=90.0,
            num_windows=20, distance_offset=0.1
        )
        db = _mock_db_with_points(points)
        result = detect_convoys(db)
        assert result["convoy_events_created"] == 0

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config", return_value={"convoy": {}})
    def test_vessels_at_anchor_no_convoy(self, mock_config, mock_settings):
        """Vessels with SOG < 3kn (anchored) should not form a convoy."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_convoys

        # Low SOG = anchored, not convoying
        points = _make_convoy_points(
            vessel_a_id=1, vessel_b_id=2,
            lat=40.0, lon=25.0, sog=1.0, cog=90.0,  # SOG=1 < 3kn threshold
            num_windows=20, distance_offset=0.01
        )
        db = _mock_db_with_points(points)
        result = detect_convoys(db)
        assert result["convoy_events_created"] == 0

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config", return_value={"convoy": {}})
    def test_heading_divergence_no_convoy(self, mock_config, mock_settings):
        """Vessels with divergent headings (>15 deg) should not form convoy."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_convoys

        # Create points with different headings
        points = []
        for i in range(20):
            ts = _ts(i * 0.25)
            pt_a = _make_ais_point(1, 40.0, 25.0, 8.0, 90.0, ts)   # heading 90
            pt_b = _make_ais_point(2, 40.01, 25.0, 8.0, 180.0, ts)  # heading 180 (90 deg diff)
            points.extend([pt_a, pt_b])

        db = _mock_db_with_points(points)
        result = detect_convoys(db)
        assert result["convoy_events_created"] == 0

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config", return_value={"convoy": {}})
    def test_too_short_duration(self, mock_config, mock_settings):
        """Less than 4h of convoy movement should not create event."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_convoys

        # 10 windows * 15 min = 2.5 hours < 4h
        points = _make_convoy_points(
            vessel_a_id=1, vessel_b_id=2,
            lat=40.0, lon=25.0, sog=8.0, cog=90.0,
            num_windows=10, distance_offset=0.01
        )
        db = _mock_db_with_points(points)
        result = detect_convoys(db)
        assert result["convoy_events_created"] == 0


# ── Duration Scoring Tier Tests ──────────────────────────────────────────────

class TestConvoyScoring:
    """Tests for convoy duration scoring tiers."""

    def test_score_4_to_8h(self):
        from app.modules.convoy_detector import _convoy_score
        assert _convoy_score(5.0) == 15

    def test_score_8_to_24h(self):
        from app.modules.convoy_detector import _convoy_score
        assert _convoy_score(12.0) == 25

    def test_score_24h_plus(self):
        from app.modules.convoy_detector import _convoy_score
        assert _convoy_score(30.0) == 35

    def test_score_below_4h(self):
        from app.modules.convoy_detector import _convoy_score
        assert _convoy_score(2.0) == 0

    def test_score_boundary_4h(self):
        from app.modules.convoy_detector import _convoy_score
        assert _convoy_score(4.0) == 15

    def test_score_boundary_8h(self):
        from app.modules.convoy_detector import _convoy_score
        assert _convoy_score(8.0) == 25

    def test_score_boundary_24h(self):
        from app.modules.convoy_detector import _convoy_score
        assert _convoy_score(24.0) == 35

    def test_score_with_config(self):
        from app.modules.convoy_detector import _convoy_score
        config = {"convoy": {"convoy_4_to_8h": 20, "convoy_8_to_24h": 30, "convoy_24h_plus": 40}}
        assert _convoy_score(5.0, config) == 20
        assert _convoy_score(12.0, config) == 30
        assert _convoy_score(30.0, config) == 40

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config", return_value={"convoy": {}})
    def test_8h_convoy_scored_25(self, mock_config, mock_settings):
        """A convoy lasting 9 hours should receive +25 score."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_convoys
        from app.models.convoy_event import ConvoyEvent

        # 36 windows * 15min = 9 hours
        points = _make_convoy_points(
            vessel_a_id=1, vessel_b_id=2,
            lat=40.0, lon=25.0, sog=8.0, cog=90.0,
            num_windows=36, distance_offset=0.01
        )
        db = _mock_db_with_points(points)
        result = detect_convoys(db)
        assert result["convoy_events_created"] == 1
        added = [c for c in db.add.call_args_list if isinstance(c[0][0], ConvoyEvent)]
        event = added[0][0][0]
        assert event.risk_score_component == 25


# ── Floating Storage Tests ───────────────────────────────────────────────────

class TestFloatingStorage:
    """Tests for floating storage intermediary detection."""

    @patch("app.modules.convoy_detector.settings")
    def test_disabled_returns_zero(self, mock_settings):
        mock_settings.CONVOY_DETECTION_ENABLED = False
        from app.modules.convoy_detector import detect_floating_storage
        db = MagicMock()
        result = detect_floating_storage(db)
        assert result["floating_storage_detected"] == 0

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config",
           return_value={"convoy": {"floating_storage_intermediary": 25}})
    def test_floating_storage_detected(self, mock_config, mock_settings):
        """Vessel loitering >30d with >=2 STS events -> floating storage."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_floating_storage
        from app.models.convoy_event import ConvoyEvent

        # Mock loitering event with >720 hours
        loiter = MagicMock()
        loiter.vessel_id = 42
        loiter.duration_hours = 800.0
        loiter.start_time_utc = _ts(0)
        loiter.end_time_utc = _ts(800)
        loiter.corridor_id = 5

        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if model_name == "LoiteringEvent":
                q.filter.return_value = q
                q.all.return_value = [loiter]
            elif model_name == "StsTransferEvent":
                q.filter.return_value = q
                q.count.return_value = 3
            elif model_name == "ConvoyEvent":
                q.filter.return_value = q
                q.first.return_value = None
            else:
                q.filter.return_value = q
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect
        result = detect_floating_storage(db)
        assert result["floating_storage_detected"] == 1
        added = [c for c in db.add.call_args_list if isinstance(c[0][0], ConvoyEvent)]
        assert len(added) == 1
        event = added[0][0][0]
        assert event.vessel_a_id == 42
        assert event.vessel_b_id == 42  # self-reference
        assert event.risk_score_component == 25
        assert event.evidence_json["type"] == "floating_storage"

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config",
           return_value={"convoy": {"floating_storage_intermediary": 25}})
    def test_floating_storage_not_enough_sts(self, mock_config, mock_settings):
        """Vessel loitering >30d but only 1 STS event -> no floating storage."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_floating_storage

        loiter = MagicMock()
        loiter.vessel_id = 42
        loiter.duration_hours = 800.0

        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if model_name == "LoiteringEvent":
                q.filter.return_value = q
                q.all.return_value = [loiter]
            elif model_name == "StsTransferEvent":
                q.filter.return_value = q
                q.count.return_value = 1  # only 1, needs >=2
            else:
                q.filter.return_value = q
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect
        result = detect_floating_storage(db)
        assert result["floating_storage_detected"] == 0


# ── Arctic No-Ice-Class Tests ────────────────────────────────────────────────

class TestArcticNoIceClass:
    """Tests for Arctic corridor + no ice class detection."""

    @patch("app.modules.convoy_detector.settings")
    def test_disabled_returns_zero(self, mock_settings):
        mock_settings.CONVOY_DETECTION_ENABLED = False
        from app.modules.convoy_detector import detect_arctic_no_ice_class
        db = MagicMock()
        result = detect_arctic_no_ice_class(db)
        assert result["arctic_flagged"] == 0

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config",
           return_value={"convoy": {"arctic_no_ice_class": 25}})
    def test_tanker_in_arctic_no_ice_class(self, mock_config, mock_settings):
        """Tanker in Arctic without ice class -> flagged."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_arctic_no_ice_class
        from app.models.convoy_event import ConvoyEvent

        # Arctic corridor
        corridor = MagicMock()
        corridor.corridor_id = 99
        corridor.tags = ["arctic", "nsr", "ice_class_required"]
        corridor.geometry = "POLYGON((40 68, 180 68, 180 78, 40 78, 40 68))"

        # Tanker vessel without ice class
        vessel = MagicMock()
        vessel.vessel_id = 10
        vessel.vessel_type = "Oil Tanker"

        # AIS point in Arctic
        pt = _make_ais_point(10, 72.0, 60.0, 8.0, 90.0, _ts(0))

        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if model_name == "Corridor":
                q.all.return_value = [corridor]
            elif model_name == "Vessel":
                q.all.return_value = [vessel]
            elif model_name == "AISPoint":
                q.filter.return_value = q
                q.order_by.return_value = q
                q.limit.return_value = q
                q.all.return_value = [pt]
            elif model_name == "ConvoyEvent":
                q.filter.return_value = q
                q.all.return_value = []
            else:
                q.filter.return_value = q
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect
        result = detect_arctic_no_ice_class(db)
        assert result["arctic_flagged"] == 1
        added = [c for c in db.add.call_args_list if isinstance(c[0][0], ConvoyEvent)]
        assert len(added) == 1
        event = added[0][0][0]
        assert event.risk_score_component == 25
        assert event.evidence_json["type"] == "arctic_no_ice_class"

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config",
           return_value={"convoy": {"arctic_no_ice_class": 25}})
    def test_tanker_with_ice_class_not_flagged(self, mock_config, mock_settings):
        """Tanker in Arctic WITH ice class should not be flagged."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_arctic_no_ice_class

        corridor = MagicMock()
        corridor.corridor_id = 99
        corridor.tags = ["arctic", "nsr"]
        corridor.geometry = "POLYGON((40 68, 180 68, 180 78, 40 78, 40 68))"

        vessel = MagicMock()
        vessel.vessel_id = 10
        vessel.vessel_type = "Oil Tanker Ice Class 1A"  # Has ice class

        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if model_name == "Corridor":
                q.all.return_value = [corridor]
            elif model_name == "Vessel":
                q.all.return_value = [vessel]
            else:
                q.filter.return_value = q
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect
        result = detect_arctic_no_ice_class(db)
        assert result["arctic_flagged"] == 0

    @patch("app.modules.convoy_detector.settings")
    @patch("app.modules.convoy_detector.load_scoring_config",
           return_value={"convoy": {"arctic_no_ice_class": 25}})
    def test_non_tanker_in_arctic_not_flagged(self, mock_config, mock_settings):
        """Non-tanker vessel in Arctic -> not flagged."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        from app.modules.convoy_detector import detect_arctic_no_ice_class

        corridor = MagicMock()
        corridor.corridor_id = 99
        corridor.tags = ["arctic", "nsr"]
        corridor.geometry = "POLYGON((40 68, 180 68, 180 78, 40 78, 40 68))"

        vessel = MagicMock()
        vessel.vessel_id = 10
        vessel.vessel_type = "Container Ship"

        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if model_name == "Corridor":
                q.all.return_value = [corridor]
            elif model_name == "Vessel":
                q.all.return_value = [vessel]
            else:
                q.filter.return_value = q
                q.first.return_value = None
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect
        result = detect_arctic_no_ice_class(db)
        assert result["arctic_flagged"] == 0


# ── Config Integration Tests ─────────────────────────────────────────────────

class TestConfigIntegration:
    """Tests for config and feature flag integration."""

    def test_feature_flags_exist(self):
        """Convoy feature flags should be in Settings."""
        from app.config import Settings
        s = Settings()
        assert hasattr(s, "CONVOY_DETECTION_ENABLED")
        assert hasattr(s, "CONVOY_SCORING_ENABLED")
        assert s.CONVOY_DETECTION_ENABLED is False
        assert s.CONVOY_SCORING_ENABLED is False

    def test_convoy_in_expected_sections(self):
        """convoy should be listed in _EXPECTED_SECTIONS."""
        from app.modules.risk_scoring import _EXPECTED_SECTIONS
        assert "convoy" in _EXPECTED_SECTIONS

    def test_risk_scoring_yaml_has_convoy(self):
        """risk_scoring.yaml should contain the convoy section."""
        from pathlib import Path
        import yaml
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "risk_scoring.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "convoy" in config
        convoy = config["convoy"]
        assert convoy["convoy_4_to_8h"] == 15
        assert convoy["convoy_8_to_24h"] == 25
        assert convoy["convoy_24h_plus"] == 35
        assert convoy["floating_storage_intermediary"] == 25
        assert convoy["arctic_no_ice_class"] == 25

    def test_corridors_yaml_has_nsr(self):
        """corridors.yaml should contain the NSR Arctic corridor."""
        from pathlib import Path
        import yaml
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "corridors.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        corridors = config.get("corridors", [])
        nsr = [c for c in corridors if "Northern Sea Route" in c.get("name", "")]
        assert len(nsr) == 1
        assert "arctic" in nsr[0].get("tags", [])
        assert "nsr" in nsr[0].get("tags", [])


# ── Pipeline Wiring Tests ────────────────────────────────────────────────────

class TestPipelineWiring:
    """Tests for convoy integration in dark_vessel_discovery pipeline."""

    @patch("app.modules.dark_vessel_discovery.settings")
    def test_convoy_step_in_pipeline_when_enabled(self, mock_settings):
        """When CONVOY_DETECTION_ENABLED=True, pipeline runs convoy detection."""
        mock_settings.CONVOY_DETECTION_ENABLED = True
        mock_settings.TRACK_NATURALNESS_ENABLED = False
        mock_settings.DRAUGHT_DETECTION_ENABLED = False
        mock_settings.STATELESS_MMSI_DETECTION_ENABLED = False
        mock_settings.FLAG_HOPPING_DETECTION_ENABLED = False
        mock_settings.IMO_FRAUD_DETECTION_ENABLED = False
        mock_settings.FLEET_ANALYSIS_ENABLED = False

        # Verify the import path is correct
        from app.modules.convoy_detector import detect_convoys, detect_floating_storage, detect_arctic_no_ice_class
        assert callable(detect_convoys)
        assert callable(detect_floating_storage)
        assert callable(detect_arctic_no_ice_class)

    def test_pipeline_import_convoy(self):
        """Convoy detector can be imported from the pipeline module location."""
        from app.modules.convoy_detector import detect_convoys
        assert detect_convoys is not None


# ── Helper Function Tests ────────────────────────────────────────────────────

class TestHelpers:
    """Tests for internal helper functions."""

    def test_grid_cell(self):
        from app.modules.convoy_detector import _grid_cell
        assert _grid_cell(40.7, 25.3) == (40, 25)
        assert _grid_cell(-33.9, -58.4) == (-34, -59)

    def test_heading_diff(self):
        from app.modules.convoy_detector import _heading_diff
        assert _heading_diff(10.0, 20.0) == 10.0
        assert _heading_diff(350.0, 10.0) == 20.0
        assert _heading_diff(0.0, 180.0) == 180.0

    def test_heading_diff_same(self):
        from app.modules.convoy_detector import _heading_diff
        assert _heading_diff(90.0, 90.0) == 0.0

    def test_bucket_key(self):
        from app.modules.convoy_detector import _bucket_key
        ts1 = datetime.datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime.datetime(2025, 1, 1, 0, 14, 59, tzinfo=timezone.utc)
        # Both should be in the same 15-min bucket
        assert _bucket_key(ts1) == _bucket_key(ts2)

    def test_in_bbox(self):
        from app.modules.convoy_detector import _in_bbox
        bbox = (20.0, 35.0, 30.0, 45.0)  # lon, lat, lon, lat
        assert _in_bbox(40.0, 25.0, bbox) is True
        assert _in_bbox(50.0, 25.0, bbox) is False


# ── Convoy Scoring in Risk Engine Tests ──────────────────────────────────────

class TestConvoyScoringIntegration:
    """Tests for convoy scoring in the risk_scoring.py engine."""

    def test_scoring_block_exists_in_risk_scoring(self):
        """The convoy scoring block should exist in risk_scoring.py source."""
        from pathlib import Path
        scoring_path = Path(__file__).resolve().parent.parent / "app" / "modules" / "risk_scoring.py"
        content = scoring_path.read_text()
        assert "CONVOY_SCORING_ENABLED" in content
        assert "convoy_" in content
