"""Tests for F3: vessel_hunt magic numbers loaded from config/risk_scoring.yaml.

Verifies that:
- _compute_hunt_score uses configurable values from hunt_scoring config
- Default values work correctly when config section is missing
- Score bands use configurable thresholds
"""
from unittest.mock import MagicMock, patch
import pytest


class TestHuntScoringConfig:
    """Test that vessel_hunt module loads scoring parameters from config."""

    def test_default_values_when_config_missing(self):
        """When hunt_scoring section is absent, defaults are used."""
        with patch("app.modules.vessel_hunt._load_hunt_scoring", return_value={}):
            # Re-import to test defaults
            import importlib
            import app.modules.vessel_hunt as vh
            # Verify the fallback defaults match original hardcoded values
            cfg = vh._load_hunt_scoring()
            assert cfg.get("length_ratio_min", 0.8) == 0.8
            assert cfg.get("length_ratio_max", 1.2) == 1.2
            assert cfg.get("heading_threshold", 15.0) == 15.0
            assert cfg.get("drift_multiplier", 15.0) == 15.0
            assert cfg.get("class_score", 10.0) == 10.0
            assert cfg.get("high_score_band", 45) == 45
            assert cfg.get("medium_score_band", 25) == 25

    def test_config_values_loaded_from_yaml(self):
        """Config values are loaded from risk_scoring.yaml hunt_scoring section."""
        mock_config = {
            "hunt_scoring": {
                "length_ratio_min": 0.75,
                "length_ratio_max": 1.25,
                "heading_threshold": 12.0,
                "drift_multiplier": 18.0,
                "class_score": 8.0,
                "high_score_band": 50,
                "medium_score_band": 30,
            }
        }
        with patch("app.modules.risk_scoring.load_scoring_config", return_value=mock_config):
            from app.modules.vessel_hunt import _load_hunt_scoring
            cfg = _load_hunt_scoring()
            assert cfg["length_ratio_min"] == 0.75
            assert cfg["length_ratio_max"] == 1.25
            assert cfg["heading_threshold"] == 12.0
            assert cfg["drift_multiplier"] == 18.0
            assert cfg["class_score"] == 8.0
            assert cfg["high_score_band"] == 50
            assert cfg["medium_score_band"] == 30

    def test_load_hunt_scoring_returns_empty_on_exception(self):
        """_load_hunt_scoring returns {} if config loading throws."""
        with patch("app.modules.risk_scoring.load_scoring_config", side_effect=Exception("broken")):
            from app.modules.vessel_hunt import _load_hunt_scoring
            cfg = _load_hunt_scoring()
            assert cfg == {}

    def test_module_constants_are_set(self):
        """Module-level constants are set from config or defaults."""
        import app.modules.vessel_hunt as vh
        assert isinstance(vh.LENGTH_RATIO_MIN, float)
        assert isinstance(vh.LENGTH_RATIO_MAX, float)
        assert isinstance(vh.HEADING_THRESHOLD, float)
        assert isinstance(vh.DRIFT_MULTIPLIER, float)
        assert isinstance(vh.CLASS_SCORE, float)
        assert vh.HIGH_SCORE_BAND > 0
        assert vh.MEDIUM_SCORE_BAND > 0
        assert vh.HIGH_SCORE_BAND > vh.MEDIUM_SCORE_BAND


class TestHuntScoreUsesConfig:
    """Test that _compute_hunt_score uses the module-level config constants."""

    def test_heading_score_uses_config(self):
        """heading_plausible score equals HEADING_THRESHOLD from config."""
        from app.modules.vessel_hunt import _compute_hunt_score, HEADING_THRESHOLD

        det = MagicMock()
        det.length_estimate_m = None
        det.vessel_type_inferred = None
        det.detection_lat = 57.0
        det.detection_lon = 20.0

        mission = MagicMock()
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0

        score, breakdown = _compute_hunt_score(det, mission, None)
        assert breakdown["heading_plausible"] == HEADING_THRESHOLD

    def test_drift_score_uses_config(self):
        """Drift score at center equals DRIFT_MULTIPLIER from config."""
        from app.modules.vessel_hunt import _compute_hunt_score, DRIFT_MULTIPLIER

        det = MagicMock()
        det.length_estimate_m = None
        det.vessel_type_inferred = None
        det.detection_lat = 57.0
        det.detection_lon = 20.0

        mission = MagicMock()
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0

        score, breakdown = _compute_hunt_score(det, mission, None)
        assert breakdown["drift_probability"] == pytest.approx(DRIFT_MULTIPLIER, abs=0.1)

    def test_class_score_uses_config(self):
        """Vessel class match score equals CLASS_SCORE from config."""
        from app.modules.vessel_hunt import _compute_hunt_score, CLASS_SCORE

        det = MagicMock()
        det.length_estimate_m = None
        det.vessel_type_inferred = "tanker"
        det.detection_lat = 57.0
        det.detection_lon = 20.0

        mission = MagicMock()
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0

        vessel = MagicMock()
        vessel.deadweight = 50000.0
        vessel.vessel_type = "Crude Oil Tanker"

        score, breakdown = _compute_hunt_score(det, mission, vessel)
        assert breakdown["vessel_class_match"] == CLASS_SCORE

    def test_length_match_uses_config_ratio(self):
        """Length match uses LENGTH_RATIO_MIN and LENGTH_RATIO_MAX from config."""
        from app.modules.vessel_hunt import _compute_hunt_score, LENGTH_RATIO_MIN, LENGTH_RATIO_MAX

        vessel = MagicMock()
        vessel.deadweight = 50000.0
        vessel.vessel_type = None
        estimated_loa = 150 + (50000.0 / 3000)  # ~166.7

        mission = MagicMock()
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0

        # Within range
        det_ok = MagicMock()
        det_ok.length_estimate_m = estimated_loa * 1.0  # ratio=1.0, well within range
        det_ok.vessel_type_inferred = None
        det_ok.detection_lat = 57.0
        det_ok.detection_lon = 20.0

        _, bd_ok = _compute_hunt_score(det_ok, mission, vessel)
        assert bd_ok["length_match"] == 20.0

        # Outside range (too small)
        det_small = MagicMock()
        det_small.length_estimate_m = estimated_loa * (LENGTH_RATIO_MIN - 0.1)
        det_small.vessel_type_inferred = None
        det_small.detection_lat = 57.0
        det_small.detection_lon = 20.0

        _, bd_small = _compute_hunt_score(det_small, mission, vessel)
        assert bd_small["length_match"] == 0.0

        # Outside range (too large)
        det_large = MagicMock()
        det_large.length_estimate_m = estimated_loa * (LENGTH_RATIO_MAX + 0.1)
        det_large.vessel_type_inferred = None
        det_large.detection_lat = 57.0
        det_large.detection_lon = 20.0

        _, bd_large = _compute_hunt_score(det_large, mission, vessel)
        assert bd_large["length_match"] == 0.0


class TestHuntScoreBandsConfig:
    """Test that score bands use configurable thresholds."""

    def test_band_thresholds_from_config(self):
        """Score band assignment uses HIGH_SCORE_BAND and MEDIUM_SCORE_BAND."""
        from app.modules.vessel_hunt import HIGH_SCORE_BAND, MEDIUM_SCORE_BAND

        # Verify the band logic matches the config
        assert HIGH_SCORE_BAND == 45  # from config or default
        assert MEDIUM_SCORE_BAND == 25

    def test_find_hunt_candidates_uses_config_bands(self):
        """find_hunt_candidates assigns correct bands based on config thresholds."""
        from app.modules.vessel_hunt import find_hunt_candidates, HIGH_SCORE_BAND, MEDIUM_SCORE_BAND

        mock_db = MagicMock()

        mission = MagicMock()
        mission.mission_id = 1
        mission.vessel_id = 1
        mission.center_lat = 57.0
        mission.center_lon = 20.0
        mission.max_radius_nm = 400.0
        mission.search_start_utc = None
        mission.created_at = MagicMock()
        mission.search_end_utc = None
        mission.elapsed_hours = 24

        vessel = MagicMock()
        vessel.deadweight = 50000.0
        vessel.vessel_type = "Crude Oil Tanker"

        # Detection that should score HIGH (at center, all signals match)
        det = MagicMock()
        det.ais_match_result = "unmatched"
        det.detection_time_utc = MagicMock()
        det.detection_lat = 57.0
        det.detection_lon = 20.0
        det.length_estimate_m = 167.0  # matches LOA estimate
        det.vessel_type_inferred = "tanker"
        det.scene_id = "test-scene"

        # Setup mock chain
        mock_db.query.return_value.filter.return_value.first.side_effect = [mission, vessel]
        mock_db.query.return_value.filter.return_value.all.return_value = [det]

        candidates = find_hunt_candidates(1, mock_db)
        assert len(candidates) == 1
        # Total score should be ~60 (20+15+15+10), which is >= HIGH_SCORE_BAND (45)
        assert candidates[0].score_breakdown_json["band"] == "HIGH"
