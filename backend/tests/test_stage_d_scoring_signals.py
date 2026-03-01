"""Stage D — Scoring Signal Gaps & Data Quality tests.

Tests:
  D1: flag_less_than_2y_AND_high_risk scoring (+20 when flag < 2y old and high_risk)
  D2: weather_correlator has Open-Meteo integration
  D3: flag_hopping_detector has dark-period gap correlation
  D4: YAML configs have expected entries
  D5: corridors.yaml has new corridor names
"""
import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# ── Helpers ──────────────────────────────────────────────────────────────────

# Locate config dir relative to this test file
_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


# ── D1: flag_less_than_2y_AND_high_risk scoring ─────────────────────────────

def _make_gap_for_scoring(
    duration_minutes=360,
    flag_risk="high_risk",
    year_built=2000,
    deadweight=120000,
    flag="CM",
):
    """Build a mock AISGapEvent suitable for compute_gap_score."""
    vessel = MagicMock()
    vessel.deadweight = deadweight
    vessel.flag_risk_category = flag_risk
    vessel.year_built = year_built
    vessel.ais_class = "A"
    vessel.flag = flag
    vessel.mmsi_first_seen_utc = None
    vessel.vessel_laid_up_30d = False
    vessel.vessel_laid_up_60d = False
    vessel.vessel_laid_up_in_sts_zone = False
    vessel.pi_coverage_status = "active"
    vessel.psc_detained_last_12m = False
    vessel.psc_major_deficiencies_last_12m = 0
    vessel.vessel_id = 42

    gap = MagicMock()
    gap.gap_event_id = 1
    gap.vessel_id = 42
    gap.duration_minutes = duration_minutes
    gap.impossible_speed_flag = False
    gap.velocity_plausibility_ratio = None
    gap.in_dark_zone = False
    gap.dark_zone_id = None
    gap.vessel = vessel
    gap.corridor = None
    gap.gap_start_utc = datetime(2026, 1, 15, 12, 0)
    gap.gap_end_utc = datetime(2026, 1, 16, 0, 0)
    gap.pre_gap_sog = None
    return gap


class TestD1FlagLessThan2yHighRisk:
    """D1: flag_less_than_2y_old_AND_high_risk scoring block."""

    def test_flag_2y_high_risk_signal_fires(self):
        """When flag changed < 730 days ago and flag_risk is high_risk, +20 is added."""
        from app.modules.risk_scoring import compute_gap_score, load_scoring_config

        config = load_scoring_config()
        gap = _make_gap_for_scoring(flag_risk="high_risk")

        # Mock db to return a recent flag change
        db = MagicMock()
        flag_change = MagicMock()
        flag_change.observed_at = datetime(2025, 6, 1)  # ~7 months ago
        flag_change.vessel_id = 42
        flag_change.field_changed = "flag"
        flag_change.old_value = "LR"
        flag_change.new_value = "CM"

        # Set up query chain for the flag_less_than_2y_AND_high_risk check
        # We need the db.query().filter().order_by().first() to return flag_change
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = flag_change
        # Other query chains return empty to avoid side effects
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0

        scoring_date = datetime(2026, 1, 15, 12, 0)
        score, breakdown = compute_gap_score(
            gap, config, db=db, scoring_date=scoring_date
        )

        assert "flag_less_than_2y_AND_high_risk" in breakdown
        assert breakdown["flag_less_than_2y_AND_high_risk"] == 20

    def test_flag_2y_not_high_risk_no_signal(self):
        """When flag_risk is NOT high_risk, the signal should NOT fire."""
        from app.modules.risk_scoring import compute_gap_score, load_scoring_config

        config = load_scoring_config()
        gap = _make_gap_for_scoring(flag_risk="low_risk")

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0

        scoring_date = datetime(2026, 1, 15, 12, 0)
        score, breakdown = compute_gap_score(
            gap, config, db=db, scoring_date=scoring_date
        )

        assert "flag_less_than_2y_AND_high_risk" not in breakdown

    def test_flag_old_high_risk_no_signal(self):
        """When flag change is > 730 days ago, the signal should NOT fire."""
        from app.modules.risk_scoring import compute_gap_score, load_scoring_config

        config = load_scoring_config()
        gap = _make_gap_for_scoring(flag_risk="high_risk")

        db = MagicMock()
        old_flag_change = MagicMock()
        old_flag_change.observed_at = datetime(2022, 1, 1)  # > 4 years ago
        old_flag_change.vessel_id = 42
        old_flag_change.field_changed = "flag"

        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = old_flag_change
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0

        scoring_date = datetime(2026, 1, 15, 12, 0)
        score, breakdown = compute_gap_score(
            gap, config, db=db, scoring_date=scoring_date
        )

        assert "flag_less_than_2y_AND_high_risk" not in breakdown


# ── D2: weather_correlator Open-Meteo integration ───────────────────────────

class TestD2WeatherCorrelator:
    """D2: weather_correlator has Open-Meteo API integration."""

    def test_open_meteo_url_present(self):
        """The module should reference Open-Meteo Archive API URL."""
        import app.modules.weather_correlator as wc
        assert hasattr(wc, "OPEN_METEO_ARCHIVE_URL")
        assert "archive-api.open-meteo.com" in wc.OPEN_METEO_ARCHIVE_URL

    def test_get_weather_function_exists(self):
        """get_weather() function exists and accepts lat/lon/timestamp."""
        from app.modules.weather_correlator import get_weather
        import inspect
        sig = inspect.signature(get_weather)
        params = list(sig.parameters.keys())
        assert "lat" in params
        assert "lon" in params
        assert "timestamp" in params

    def test_fetch_open_meteo_cached(self):
        """_fetch_open_meteo should use LRU cache."""
        from app.modules.weather_correlator import _fetch_open_meteo
        assert hasattr(_fetch_open_meteo, "cache_info"), \
            "_fetch_open_meteo should be decorated with @lru_cache"

    def test_compute_weather_deduction_storm(self):
        """Wind > 40kn should yield -15 deduction."""
        from app.modules.weather_correlator import compute_weather_deduction
        deduction, reason = compute_weather_deduction({"wind_speed_kn": 45.0})
        assert deduction == -15
        assert reason == "storm_conditions"

    def test_compute_weather_deduction_high_wind(self):
        """Wind > 25kn but <= 40kn should yield -8 deduction."""
        from app.modules.weather_correlator import compute_weather_deduction
        deduction, reason = compute_weather_deduction({"wind_speed_kn": 30.0})
        assert deduction == -8
        assert reason == "high_wind"

    def test_compute_weather_deduction_no_data(self):
        """Empty dict should yield 0 deduction."""
        from app.modules.weather_correlator import compute_weather_deduction
        deduction, reason = compute_weather_deduction({})
        assert deduction == 0
        assert reason == ""

    def test_get_weather_graceful_on_network_error(self):
        """get_weather should return empty dict when network fails."""
        from app.modules.weather_correlator import get_weather, _fetch_open_meteo
        # Clear cache to ensure fresh call
        _fetch_open_meteo.cache_clear()
        with patch("app.modules.weather_correlator.urlopen", side_effect=OSError("network error")):
            result = get_weather(55.0, 25.0, datetime(2025, 6, 15, 12, 0))
        assert result == {}


# ── D3: flag_hopping_detector dark-period gap correlation ────────────────────

class TestD3DarkPeriodFlagChange:
    """D3: flag_hopping_detector has gap correlation for dark-period flag changes."""

    def test_dark_period_flag_change_code_exists(self):
        """The flag_hopping_detector module should import AISGapEvent."""
        import app.modules.flag_hopping_detector as fhd
        source = Path(fhd.__file__).read_text()
        assert "AISGapEvent" in source
        assert "dark_period_flag_change" in source

    def test_gap_correlation_window(self):
        """The detector should check for gaps within +/-6h of flag change."""
        import app.modules.flag_hopping_detector as fhd
        source = Path(fhd.__file__).read_text()
        assert "timedelta(hours=6)" in source or "hours=6" in source


# ── D4: YAML configs populated with real data ────────────────────────────────

class TestD4YamlConfigs:
    """D4: YAML config files have expected entries."""

    def test_scrapped_vessels_count(self):
        """scrapped_vessels.yaml should have >= 15 entries."""
        with open(_CONFIG_DIR / "scrapped_vessels.yaml") as f:
            data = yaml.safe_load(f)
        imos = data.get("scrapped_imos", [])
        assert len(imos) >= 15, f"Expected >= 15 scrapped IMOs, got {len(imos)}"

    def test_scrapped_vessels_imo_format(self):
        """All scrapped IMOs should be 7-digit strings starting with 7-9."""
        with open(_CONFIG_DIR / "scrapped_vessels.yaml") as f:
            data = yaml.safe_load(f)
        for entry in data.get("scrapped_imos", []):
            imo = entry["imo"]
            assert len(imo) == 7, f"IMO {imo} is not 7 digits"
            assert imo[0] in "789", f"IMO {imo} does not start with 7-9"

    def test_fraudulent_registries_tiers(self):
        """fraudulent_registries.yaml should have tier_0, tier_1, and tier_2_monitored."""
        with open(_CONFIG_DIR / "fraudulent_registries.yaml") as f:
            data = yaml.safe_load(f)
        assert "tier_0_fraudulent" in data
        assert "tier_1_high_risk" in data
        assert "tier_2_monitored" in data

    def test_fraudulent_registries_comoros_sierra_leone_in_tier0(self):
        """KM (Comoros) and SL (Sierra Leone) should be in tier_0."""
        with open(_CONFIG_DIR / "fraudulent_registries.yaml") as f:
            data = yaml.safe_load(f)
        tier0_codes = {e["country_code"] for e in data["tier_0_fraudulent"]}
        assert "KM" in tier0_codes, "Comoros (KM) missing from tier_0"
        assert "SL" in tier0_codes, "Sierra Leone (SL) missing from tier_0"

    def test_fraudulent_registries_tier1_entries(self):
        """tier_1 should include VU, GA, BB, ST, KN, VC."""
        with open(_CONFIG_DIR / "fraudulent_registries.yaml") as f:
            data = yaml.safe_load(f)
        tier1_codes = {e["country_code"] for e in data["tier_1_high_risk"]}
        expected = {"VU", "GA", "BB", "ST", "KN", "VC"}
        assert expected.issubset(tier1_codes), f"Missing from tier_1: {expected - tier1_codes}"

    def test_fraudulent_registries_tier2_panama_liberia(self):
        """tier_2_monitored should include PA and LR."""
        with open(_CONFIG_DIR / "fraudulent_registries.yaml") as f:
            data = yaml.safe_load(f)
        tier2_codes = {e["country_code"] for e in data["tier_2_monitored"]}
        assert "PA" in tier2_codes, "Panama (PA) missing from tier_2_monitored"
        assert "LR" in tier2_codes, "Liberia (LR) missing from tier_2_monitored"

    def test_pi_clubs_fraudulent_count(self):
        """legitimate_pi_clubs.yaml known_fraudulent should have >= 8 entries."""
        with open(_CONFIG_DIR / "legitimate_pi_clubs.yaml") as f:
            data = yaml.safe_load(f)
        fraudulent = data.get("known_fraudulent", [])
        assert len(fraudulent) >= 8, f"Expected >= 8 fraudulent P&I entities, got {len(fraudulent)}"

    def test_bunkering_exclusions_count(self):
        """bunkering_exclusions.yaml should have >= 20 vessels."""
        with open(_CONFIG_DIR / "bunkering_exclusions.yaml") as f:
            data = yaml.safe_load(f)
        vessels = data.get("bunkering_vessels", [])
        assert len(vessels) >= 20, f"Expected >= 20 bunkering vessels, got {len(vessels)}"


# ── D5: corridors.yaml new corridors ─────────────────────────────────────────

class TestD5NewCorridors:
    """D5: corridors.yaml should include Batam/Bintan, Zhoushan/Ningbo, and Mundra."""

    def test_new_corridors_present(self):
        """All three new corridors should be present by name."""
        with open(_CONFIG_DIR / "corridors.yaml") as f:
            data = yaml.safe_load(f)
        names = {c["name"] for c in data.get("corridors", [])}
        assert any("Batam" in n or "Bintan" in n for n in names), \
            "Batam/Bintan corridor missing"
        assert any("Zhoushan" in n or "Ningbo" in n for n in names), \
            "Zhoushan/Ningbo corridor missing"
        assert any("Mundra" in n for n in names), \
            "Mundra corridor missing"

    def test_batam_corridor_geometry(self):
        """Batam/Bintan should be near lat ~1.0, lon ~104.5."""
        with open(_CONFIG_DIR / "corridors.yaml") as f:
            data = yaml.safe_load(f)
        for c in data.get("corridors", []):
            if "Batam" in c.get("name", "") or "Bintan" in c.get("name", ""):
                geom = c["geometry"]
                assert "104" in geom, "Batam corridor should be near lon 104"
                break
        else:
            pytest.fail("Batam/Bintan corridor not found")

    def test_zhoushan_corridor_type(self):
        """Zhoushan/Ningbo should be anchorage_holding type."""
        with open(_CONFIG_DIR / "corridors.yaml") as f:
            data = yaml.safe_load(f)
        for c in data.get("corridors", []):
            if "Zhoushan" in c.get("name", "") or "Ningbo" in c.get("name", ""):
                assert c["corridor_type"] == "anchorage_holding", \
                    f"Expected anchorage_holding, got {c['corridor_type']}"
                break
        else:
            pytest.fail("Zhoushan/Ningbo corridor not found")
