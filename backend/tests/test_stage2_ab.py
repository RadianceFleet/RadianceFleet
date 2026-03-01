"""Stage 2-A/2-B tests: P&I validation and fraudulent registry scoring.

Tests cover:
  - P&I validation: legitimate club (no points), unknown insurer (+25),
    known fraudulent (+40), no insurer (+15), disabled flag (0)
  - Fraudulent registry: tier 0 (+40), tier 1 (+20), normal flag (0), disabled (0)
  - YAML configs: both files parse, have required keys, last_updated present
  - Integration: _EXPECTED_SECTIONS includes new sections, feature flags exist

All tests are unit-level: no database required.
"""
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from app.modules.risk_scoring import (
    compute_gap_score,
    load_scoring_config,
    _EXPECTED_SECTIONS,
    _load_pi_clubs_config,
    _load_fraudulent_registries_config,
)
from app.config import Settings

# Resolve config directory relative to this test file (tests/ -> backend/ -> config/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"

# Sample PI clubs data for mocking the YAML loader
_MOCK_PI_CLUBS_DATA = {
    "last_updated": "2026-03-01",
    "legitimate_clubs": [
        {"name": "Skuld", "short": "Skuld"},
        {"name": "Assuranceforeningen Gard", "short": "Gard"},
        {"name": "The Standard Club", "short": "Standard"},
    ],
    "known_fraudulent": [
        "Pacific Maritime Insurance",
        "Global Shipping Guarantee Corp",
    ],
}

# Sample fraudulent registries data for mocking the YAML loader
_MOCK_FR_DATA = {
    "last_updated": "2026-03-01",
    "tier_0_fraudulent": [
        {"country_code": "CM", "name": "Cameroon"},
        {"country_code": "BO", "name": "Bolivia"},
        {"country_code": "MD", "name": "Moldova"},
    ],
    "tier_1_high_risk": [
        {"country_code": "TG", "name": "Togo"},
        {"country_code": "TZ", "name": "Tanzania"},
        {"country_code": "PW", "name": "Palau"},
        {"country_code": "CK", "name": "Cook Islands"},
    ],
}


# ── Mock gap factory ────────────────────────────────────────────────────────


def _make_gap(
    duration_minutes=360,
    deadweight=100_000,
    flag_risk="unknown",
    flag=None,
    ais_class="A",
    pi_coverage_status="active",
):
    """Build a minimal mock AISGapEvent for scoring tests."""
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk
    vessel.year_built = None
    vessel.ais_class = ais_class
    vessel.flag = flag
    vessel.mmsi = "123456789"
    vessel.mmsi_first_seen_utc = None
    vessel.vessel_laid_up_30d = False
    vessel.vessel_laid_up_60d = False
    vessel.vessel_laid_up_in_sts_zone = False
    vessel.pi_coverage_status = pi_coverage_status
    vessel.psc_detained_last_12m = False
    vessel.psc_major_deficiencies_last_12m = 0
    vessel.vessel_id = 1
    vessel.name = "TEST VESSEL"
    vessel.vessel_type = "crude_oil_tanker"
    vessel.imo = None

    gap = MagicMock()
    gap.gap_event_id = 1
    gap.vessel_id = 1
    gap.duration_minutes = duration_minutes
    gap.impossible_speed_flag = False
    gap.velocity_plausibility_ratio = None
    gap.in_dark_zone = False
    gap.dark_zone_id = None
    gap.vessel = vessel
    gap.corridor = None
    gap.corridor_id = None
    gap.gap_start_utc = datetime(2026, 1, 15, 12, 0)
    gap.gap_end_utc = datetime(2026, 1, 16, 0, 0)
    gap.start_point = None
    gap.gap_off_lat = None
    gap.gap_off_lon = None
    gap.max_plausible_distance_nm = None
    return gap


def _mock_db_for_pi(pi_club_name):
    """Create a mock DB session that returns a VesselOwner with the given pi_club_name."""
    db = MagicMock()
    owner_mock = MagicMock()
    owner_mock.pi_club_name = pi_club_name
    owner_mock.is_sanctioned = False
    owner_mock.owner_id = 1

    # VesselOwner query chain
    def query_side_effect(model):
        q = MagicMock()
        model_name = getattr(model, "__name__", str(model))
        if "VesselOwner" in model_name:
            q.filter.return_value.first.return_value = owner_mock
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
            q.filter.return_value.count.return_value = 0
            q.filter.return_value.scalar.return_value = 0
        q.filter.return_value.order_by.return_value.first.return_value = None
        q.filter.return_value.order_by.return_value.all.return_value = []
        return q

    db.query.side_effect = query_side_effect
    return db


def _settings_patch(**overrides):
    """Create a context manager that patches app.config.settings for scoring flags.

    The _scoring_settings inside compute_gap_score does `from app.config import settings`,
    so we must patch app.config.settings (not app.modules.risk_scoring.settings).
    """
    defaults = {
        "PI_VALIDATION_SCORING_ENABLED": False,
        "FRAUDULENT_REGISTRY_SCORING_ENABLED": False,
        "TRACK_NATURALNESS_SCORING_ENABLED": False,
        "DRAUGHT_SCORING_ENABLED": False,
        "STATELESS_MMSI_SCORING_ENABLED": False,
        "FLAG_HOPPING_SCORING_ENABLED": False,
        "IMO_FRAUD_SCORING_ENABLED": False,
        "DARK_STS_SCORING_ENABLED": False,
        "FLEET_SCORING_ENABLED": False,
        "RISK_SCORING_CONFIG": "config/risk_scoring.yaml",
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ── TestPIValidation ────────────────────────────────────────────────────────


class TestPIValidation:
    """P&I club validation scoring tests."""

    @patch("app.modules.risk_scoring._load_pi_clubs_config")
    def test_legitimate_club_no_points(self, mock_pi_loader):
        """Vessel with legitimate IG P&I club should get no pi_validation points."""
        mock_pi_loader.return_value = _MOCK_PI_CLUBS_DATA
        config = load_scoring_config()
        gap = _make_gap()
        db = _mock_db_for_pi("Skuld")

        mock_s = _settings_patch(PI_VALIDATION_SCORING_ENABLED=True)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config, db=db)

        assert "pi_known_fraudulent" not in breakdown
        assert "pi_unknown_insurer" not in breakdown
        assert "pi_no_insurer" not in breakdown

    @patch("app.modules.risk_scoring._load_pi_clubs_config")
    def test_unknown_insurer_25_points(self, mock_pi_loader):
        """Unknown (non-IG) insurer should score +25."""
        mock_pi_loader.return_value = _MOCK_PI_CLUBS_DATA
        config = load_scoring_config()
        gap = _make_gap()
        db = _mock_db_for_pi("Shady Marine Insurance Ltd")

        mock_s = _settings_patch(PI_VALIDATION_SCORING_ENABLED=True)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config, db=db)

        assert "pi_unknown_insurer" in breakdown
        assert breakdown["pi_unknown_insurer"] == 25

    @patch("app.modules.risk_scoring._load_pi_clubs_config")
    def test_known_fraudulent_40_points(self, mock_pi_loader):
        """Known fraudulent insurer should score +40."""
        mock_pi_loader.return_value = _MOCK_PI_CLUBS_DATA
        config = load_scoring_config()
        gap = _make_gap()
        db = _mock_db_for_pi("Pacific Maritime Insurance")

        mock_s = _settings_patch(PI_VALIDATION_SCORING_ENABLED=True)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config, db=db)

        assert "pi_known_fraudulent" in breakdown
        assert breakdown["pi_known_fraudulent"] == 40

    @patch("app.modules.risk_scoring._load_pi_clubs_config")
    def test_no_insurer_15_points(self, mock_pi_loader):
        """Missing/empty P&I club should score +15."""
        mock_pi_loader.return_value = _MOCK_PI_CLUBS_DATA
        config = load_scoring_config()
        gap = _make_gap()
        db = _mock_db_for_pi(None)

        mock_s = _settings_patch(PI_VALIDATION_SCORING_ENABLED=True)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config, db=db)

        assert "pi_no_insurer" in breakdown
        assert breakdown["pi_no_insurer"] == 15

    @patch("app.modules.risk_scoring._load_pi_clubs_config")
    def test_disabled_flag_no_points(self, mock_pi_loader):
        """When PI_VALIDATION_SCORING_ENABLED is False, no pi_validation points."""
        mock_pi_loader.return_value = _MOCK_PI_CLUBS_DATA
        config = load_scoring_config()
        gap = _make_gap()
        db = _mock_db_for_pi("Pacific Maritime Insurance")

        mock_s = _settings_patch(PI_VALIDATION_SCORING_ENABLED=False)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config, db=db)

        assert "pi_known_fraudulent" not in breakdown
        assert "pi_unknown_insurer" not in breakdown
        assert "pi_no_insurer" not in breakdown


# ── TestFraudulentRegistry ──────────────────────────────────────────────────


class TestFraudulentRegistry:
    """Fraudulent flag registry scoring tests."""

    @patch("app.modules.risk_scoring._load_fraudulent_registries_config")
    def test_tier_0_fraudulent_40_points(self, mock_fr_loader):
        """Tier 0 fraudulent registry (e.g., Cameroon) should score +40."""
        mock_fr_loader.return_value = _MOCK_FR_DATA
        config = load_scoring_config()
        gap = _make_gap(flag="CM")

        mock_s = _settings_patch(FRAUDULENT_REGISTRY_SCORING_ENABLED=True)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config)

        assert "fraudulent_registry_tier_0" in breakdown
        assert breakdown["fraudulent_registry_tier_0"] == 40

    @patch("app.modules.risk_scoring._load_fraudulent_registries_config")
    def test_tier_1_high_risk_20_points(self, mock_fr_loader):
        """Tier 1 high-risk registry (e.g., Togo) should score +20."""
        mock_fr_loader.return_value = _MOCK_FR_DATA
        config = load_scoring_config()
        gap = _make_gap(flag="TG")

        mock_s = _settings_patch(FRAUDULENT_REGISTRY_SCORING_ENABLED=True)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config)

        assert "fraudulent_registry_tier_1" in breakdown
        assert breakdown["fraudulent_registry_tier_1"] == 20

    @patch("app.modules.risk_scoring._load_fraudulent_registries_config")
    def test_normal_flag_no_points(self, mock_fr_loader):
        """A normal flag (e.g., Norway) should not trigger fraudulent registry scoring."""
        mock_fr_loader.return_value = _MOCK_FR_DATA
        config = load_scoring_config()
        gap = _make_gap(flag="NO")

        mock_s = _settings_patch(FRAUDULENT_REGISTRY_SCORING_ENABLED=True)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config)

        assert "fraudulent_registry_tier_0" not in breakdown
        assert "fraudulent_registry_tier_1" not in breakdown

    @patch("app.modules.risk_scoring._load_fraudulent_registries_config")
    def test_disabled_flag_no_points(self, mock_fr_loader):
        """When FRAUDULENT_REGISTRY_SCORING_ENABLED is False, no points."""
        mock_fr_loader.return_value = _MOCK_FR_DATA
        config = load_scoring_config()
        gap = _make_gap(flag="CM")

        mock_s = _settings_patch(FRAUDULENT_REGISTRY_SCORING_ENABLED=False)
        with patch("app.config.settings", mock_s), \
             patch("app.modules.risk_scoring.settings", mock_s):
            score, breakdown = compute_gap_score(gap, config)

        assert "fraudulent_registry_tier_0" not in breakdown
        assert "fraudulent_registry_tier_1" not in breakdown


# ── TestYAMLConfigs ─────────────────────────────────────────────────────────


class TestYAMLConfigs:
    """Verify YAML config files parse correctly with required structure."""

    def test_legitimate_pi_clubs_parses(self):
        """legitimate_pi_clubs.yaml should parse and have required keys."""
        config_path = _CONFIG_DIR / "legitimate_pi_clubs.yaml"
        assert config_path.exists(), f"Missing config file: {config_path}"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert "last_updated" in data
        assert "legitimate_clubs" in data
        assert "known_fraudulent" in data
        assert len(data["legitimate_clubs"]) == 13  # 13 IG P&I clubs
        assert len(data["known_fraudulent"]) >= 2

    def test_legitimate_pi_clubs_structure(self):
        """Each legitimate club entry should have 'name' and 'short' fields."""
        config_path = _CONFIG_DIR / "legitimate_pi_clubs.yaml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        for club in data["legitimate_clubs"]:
            assert "name" in club, f"Missing 'name' in club entry: {club}"
            assert "short" in club, f"Missing 'short' in club entry: {club}"

    def test_fraudulent_registries_parses(self):
        """fraudulent_registries.yaml should parse and have required keys."""
        config_path = _CONFIG_DIR / "fraudulent_registries.yaml"
        assert config_path.exists(), f"Missing config file: {config_path}"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert "last_updated" in data
        assert "tier_0_fraudulent" in data
        assert "tier_1_high_risk" in data
        assert len(data["tier_0_fraudulent"]) >= 3  # 3 original + KM, SL added in Stage D
        assert len(data["tier_1_high_risk"]) >= 4  # 4 original + Stage D additions

    def test_fraudulent_registries_structure(self):
        """Each registry entry should have 'country_code' and 'name' fields."""
        config_path = _CONFIG_DIR / "fraudulent_registries.yaml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        for entry in data["tier_0_fraudulent"] + data["tier_1_high_risk"]:
            assert "country_code" in entry, f"Missing 'country_code': {entry}"
            assert "name" in entry, f"Missing 'name': {entry}"

    def test_risk_scoring_yaml_has_new_sections(self):
        """risk_scoring.yaml should contain pi_validation and fraudulent_registry sections."""
        config_path = _CONFIG_DIR / "risk_scoring.yaml"
        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert "pi_validation" in data, "Missing pi_validation section in risk_scoring.yaml"
        assert "fraudulent_registry" in data, "Missing fraudulent_registry section in risk_scoring.yaml"
        # Verify specific keys
        assert data["pi_validation"]["unknown_insurer"] == 25
        assert data["pi_validation"]["known_fraudulent"] == 40
        assert data["pi_validation"]["no_insurer"] == 15
        assert data["fraudulent_registry"]["tier_0_fraudulent"] == 40
        assert data["fraudulent_registry"]["tier_1_high_risk"] == 20


# ── TestIntegration ─────────────────────────────────────────────────────────


class TestIntegration:
    """Integration checks for Stage 2-A/2-B wiring."""

    def test_expected_sections_includes_pi_validation(self):
        """_EXPECTED_SECTIONS should include 'pi_validation'."""
        assert "pi_validation" in _EXPECTED_SECTIONS

    def test_expected_sections_includes_fraudulent_registry(self):
        """_EXPECTED_SECTIONS should include 'fraudulent_registry'."""
        assert "fraudulent_registry" in _EXPECTED_SECTIONS

    def test_feature_flags_exist(self):
        """Settings class should have all Stage 2-A/2-B feature flags."""
        s = Settings()
        assert hasattr(s, "PI_VALIDATION_DETECTION_ENABLED")
        assert hasattr(s, "PI_VALIDATION_SCORING_ENABLED")
        assert hasattr(s, "FRAUDULENT_REGISTRY_DETECTION_ENABLED")
        assert hasattr(s, "FRAUDULENT_REGISTRY_SCORING_ENABLED")

    def test_feature_flags_default_true(self):
        """All Stage 2-A/2-B feature flags should default to True (E6: stable detectors)."""
        s = Settings()
        assert s.PI_VALIDATION_DETECTION_ENABLED is True
        assert s.PI_VALIDATION_SCORING_ENABLED is True
        assert s.FRAUDULENT_REGISTRY_DETECTION_ENABLED is True
        assert s.FRAUDULENT_REGISTRY_SCORING_ENABLED is True

    def test_pi_clubs_config_loader(self):
        """_load_pi_clubs_config should return parsed YAML data when file exists."""
        import app.modules.risk_scoring as rs
        rs._PI_CLUBS_CONFIG = None  # Force reload
        config_path = _CONFIG_DIR / "legitimate_pi_clubs.yaml"
        with patch("app.modules.risk_scoring.settings") as mock_s:
            mock_s.RISK_SCORING_CONFIG = str(config_path.parent / "risk_scoring.yaml")
            data = _load_pi_clubs_config()
        assert "legitimate_clubs" in data
        assert "known_fraudulent" in data

    def test_fraudulent_registries_config_loader(self):
        """_load_fraudulent_registries_config should return parsed YAML data when file exists."""
        import app.modules.risk_scoring as rs
        rs._FRAUDULENT_REGISTRIES_CONFIG = None  # Force reload
        config_path = _CONFIG_DIR / "fraudulent_registries.yaml"
        with patch("app.modules.risk_scoring.settings") as mock_s:
            mock_s.RISK_SCORING_CONFIG = str(config_path.parent / "risk_scoring.yaml")
            data = _load_fraudulent_registries_config()
        assert "tier_0_fraudulent" in data
        assert "tier_1_high_risk" in data
