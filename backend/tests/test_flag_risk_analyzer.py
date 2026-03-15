"""Tests for flag_risk_analyzer — data-driven per-flag risk scoring v2."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.flag_risk_profile import FlagRiskProfile
from app.models.gap_event import AISGapEvent
from app.models.psc_detention import PscDetention
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.modules.flag_risk_analyzer import (
    TRANSPARENCY_INDEX,
    _assign_tier,
    _compute_flag_hopping_score,
    _compute_fleet_composition_score,
    _compute_fp_rate_score,
    _compute_psc_detention_score,
    _get_transparency_score,
    compute_flag_risk_profiles,
    get_flag_risk_score,
    persist_profiles,
)


@pytest.fixture()
def db():
    """In-memory SQLite session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _make_vessel(db: Session, mmsi: str, flag: str, max_gap_score: int | None = None) -> Vessel:
    v = Vessel(mmsi=mmsi, flag=flag)
    db.add(v)
    db.flush()
    if max_gap_score is not None:
        now = datetime.utcnow()
        db.add(AISGapEvent(
            vessel_id=v.vessel_id,
            gap_start_utc=now - timedelta(hours=10),
            gap_end_utc=now,
            duration_minutes=600,
            risk_score=max_gap_score,
        ))
        db.flush()
    return v


# ── Tier assignment ──────────────────────────────────────────────────────────


class TestTierAssignment:
    def test_high_tier(self):
        assert _assign_tier(70) == "HIGH"
        assert _assign_tier(100) == "HIGH"
        assert _assign_tier(85.5) == "HIGH"

    def test_medium_tier(self):
        assert _assign_tier(40) == "MEDIUM"
        assert _assign_tier(69.9) == "MEDIUM"
        assert _assign_tier(55) == "MEDIUM"

    def test_low_tier(self):
        assert _assign_tier(0) == "LOW"
        assert _assign_tier(39.9) == "LOW"
        assert _assign_tier(20) == "LOW"


# ── Transparency score ───────────────────────────────────────────────────────


class TestTransparencyScore:
    def test_high_transparency_flags(self):
        for code in ["GB", "NO", "DK", "SE", "DE", "NL", "JP", "SG"]:
            assert _get_transparency_score(code) == 20

    def test_medium_transparency_flags(self):
        for code in ["GR", "CY", "MT", "HK", "BS"]:
            assert _get_transparency_score(code) == 50

    def test_low_transparency_flags(self):
        for code in ["PA", "LR", "MH", "KM", "TZ", "VU", "CM", "GQ", "PW", "TG"]:
            assert _get_transparency_score(code) == 80

    def test_unknown_flag_default(self):
        assert _get_transparency_score("XX") == 50

    def test_case_insensitive(self):
        assert _get_transparency_score("gb") == 20
        assert _get_transparency_score("pa") == 80


# ── PSC detention score ──────────────────────────────────────────────────────


class TestPscDetentionScore:
    def test_no_vessels(self, db):
        score, vc, dc = _compute_psc_detention_score(db, "XX")
        assert score == 0.0
        assert vc == 0
        assert dc == 0

    def test_vessels_no_detentions(self, db):
        _make_vessel(db, "123456789", "NO")
        _make_vessel(db, "123456790", "NO")
        db.commit()

        score, vc, dc = _compute_psc_detention_score(db, "NO")
        assert vc == 2
        assert dc == 0
        assert score == 0.0

    def test_high_detention_rate(self, db):
        v1 = _make_vessel(db, "111111111", "CM")
        v2 = _make_vessel(db, "111111112", "CM")
        # Add detentions
        from datetime import date

        db.add(PscDetention(
            vessel_id=v1.vessel_id, detention_date=date(2025, 1, 1),
            mou_source="paris_mou", data_source="test",
        ))
        db.add(PscDetention(
            vessel_id=v2.vessel_id, detention_date=date(2025, 2, 1),
            mou_source="paris_mou", data_source="test",
        ))
        db.commit()

        score, vc, dc = _compute_psc_detention_score(db, "CM")
        assert vc == 2
        assert dc == 2
        assert score > 0


# ── FP rate score ────────────────────────────────────────────────────────────


class TestFpRateScore:
    def test_no_gaps(self, db):
        _make_vessel(db, "222222222", "NO")
        db.commit()

        score, fp_rate = _compute_fp_rate_score(db, "NO")
        assert score == 50.0  # neutral
        assert fp_rate == 0.0

    def test_all_false_positives(self, db):
        v = _make_vessel(db, "222222223", "GB")
        now = datetime.utcnow()
        db.add(AISGapEvent(
            vessel_id=v.vessel_id,
            gap_start_utc=now - timedelta(hours=10),
            gap_end_utc=now,
            duration_minutes=600,
            is_false_positive=True,
        ))
        db.commit()

        score, fp_rate = _compute_fp_rate_score(db, "GB")
        assert fp_rate == 1.0
        assert score == 0.0  # all FP = low risk

    def test_no_false_positives(self, db):
        v = _make_vessel(db, "222222224", "PA")
        now = datetime.utcnow()
        db.add(AISGapEvent(
            vessel_id=v.vessel_id,
            gap_start_utc=now - timedelta(hours=10),
            gap_end_utc=now,
            duration_minutes=600,
            is_false_positive=False,
        ))
        db.commit()

        score, fp_rate = _compute_fp_rate_score(db, "PA")
        assert fp_rate == 0.0
        assert score == 100.0  # all genuine gaps = high risk


# ── Fleet composition score ──────────────────────────────────────────────────


class TestFleetCompositionScore:
    def test_no_vessels(self, db):
        assert _compute_fleet_composition_score(db, "XX") == 0.0

    def test_all_high_scoring(self, db):
        _make_vessel(db, "333333331", "PA", max_gap_score=80)
        _make_vessel(db, "333333332", "PA", max_gap_score=60)
        db.commit()

        score = _compute_fleet_composition_score(db, "PA")
        assert score == 100.0  # 100% high scoring

    def test_no_high_scoring(self, db):
        _make_vessel(db, "333333333", "NO", max_gap_score=10)
        _make_vessel(db, "333333334", "NO", max_gap_score=20)
        db.commit()

        score = _compute_fleet_composition_score(db, "NO")
        assert score == 0.0

    def test_mixed_fleet(self, db):
        _make_vessel(db, "333333335", "MT", max_gap_score=80)
        _make_vessel(db, "333333336", "MT", max_gap_score=10)
        _make_vessel(db, "333333337", "MT", max_gap_score=20)
        _make_vessel(db, "333333338", "MT", max_gap_score=60)
        db.commit()

        score = _compute_fleet_composition_score(db, "MT")
        # 2/4 = 50% high scoring -> 100.0 (capped)
        assert score == 100.0


# ── Flag hopping score ───────────────────────────────────────────────────────


class TestFlagHoppingScore:
    def test_no_vessels(self, db):
        assert _compute_flag_hopping_score(db, "XX") == 0.0

    def test_no_hopping(self, db):
        _make_vessel(db, "444444441", "CM")
        db.commit()

        score = _compute_flag_hopping_score(db, "CM")
        assert score == 0.0

    def test_recent_hopping(self, db):
        v = _make_vessel(db, "444444442", "CM")
        now = datetime.utcnow()
        db.add(VesselHistory(
            vessel_id=v.vessel_id,
            field_changed="flag",
            old_value="PA",
            new_value="CM",
            observed_at=now - timedelta(days=30),
        ))
        db.commit()

        score = _compute_flag_hopping_score(db, "CM")
        assert score == 100.0  # 1 change / 1 vessel = 100%

    def test_old_hopping_excluded(self, db):
        v = _make_vessel(db, "444444443", "PA")
        db.add(VesselHistory(
            vessel_id=v.vessel_id,
            field_changed="flag",
            old_value="LR",
            new_value="PA",
            observed_at=datetime.utcnow() - timedelta(days=400),
        ))
        db.commit()

        score = _compute_flag_hopping_score(db, "PA")
        assert score == 0.0  # >12 months ago


# ── Composite score computation ──────────────────────────────────────────────


class TestComputeProfiles:
    def test_compute_empty_db(self, db):
        profiles = compute_flag_risk_profiles(db)
        assert profiles == []

    def test_compute_single_flag(self, db):
        _make_vessel(db, "555555551", "PA", max_gap_score=10)
        db.commit()

        profiles = compute_flag_risk_profiles(db)
        assert len(profiles) == 1
        p = profiles[0]
        assert p.flag_code == "PA"
        assert 0 <= p.composite_score <= 100
        assert p.risk_tier in ("HIGH", "MEDIUM", "LOW")

    def test_composite_weights_sum_to_one(self):
        from app.config import settings
        total = (
            settings.FLAG_RISK_PSC_WEIGHT
            + settings.FLAG_RISK_FP_WEIGHT
            + settings.FLAG_RISK_FLEET_WEIGHT
            + settings.FLAG_RISK_HOPPING_WEIGHT
            + settings.FLAG_RISK_TRANSPARENCY_WEIGHT
        )
        assert abs(total - 1.0) < 0.001

    def test_evidence_json_valid(self, db):
        _make_vessel(db, "555555552", "GB", max_gap_score=5)
        db.commit()

        profiles = compute_flag_risk_profiles(db)
        p = profiles[0]
        evidence = json.loads(p.evidence_json)
        assert "weights" in evidence
        assert "component_scores" in evidence
        assert "raw" in evidence
        assert "computed_at" in evidence

    def test_multiple_flags(self, db):
        _make_vessel(db, "555555553", "PA", max_gap_score=80)
        _make_vessel(db, "555555554", "GB", max_gap_score=5)
        _make_vessel(db, "555555555", "CM", max_gap_score=90)
        db.commit()

        profiles = compute_flag_risk_profiles(db)
        flags = {p.flag_code for p in profiles}
        assert flags == {"PA", "GB", "CM"}


# ── Persistence ──────────────────────────────────────────────────────────────


class TestPersistence:
    def test_persist_and_lookup(self, db):
        _make_vessel(db, "666666661", "PA", max_gap_score=10)
        db.commit()

        profiles = compute_flag_risk_profiles(db)
        persist_profiles(db, profiles)

        result = get_flag_risk_score(db, "PA")
        assert result is not None
        assert result.flag_code == "PA"

    def test_lookup_missing(self, db):
        result = get_flag_risk_score(db, "XX")
        assert result is None

    def test_lookup_empty_flag(self, db):
        result = get_flag_risk_score(db, "")
        assert result is None

    def test_persist_replaces_existing(self, db):
        _make_vessel(db, "666666662", "NO", max_gap_score=5)
        db.commit()

        profiles1 = compute_flag_risk_profiles(db)
        persist_profiles(db, profiles1)
        count1 = db.query(FlagRiskProfile).count()

        profiles2 = compute_flag_risk_profiles(db)
        persist_profiles(db, profiles2)
        count2 = db.query(FlagRiskProfile).count()

        assert count1 == count2  # replaced, not duplicated


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_zero_vessels_flag(self, db):
        score, vc, dc = _compute_psc_detention_score(db, "ZZ")
        assert vc == 0
        assert score == 0.0

    def test_no_gap_events_vessels(self, db):
        _make_vessel(db, "777777771", "PA")
        db.commit()

        score = _compute_fleet_composition_score(db, "PA")
        assert score == 0.0  # no gap events = no score data

    def test_case_insensitive_lookup(self, db):
        _make_vessel(db, "777777772", "GB", max_gap_score=5)
        db.commit()

        profiles = compute_flag_risk_profiles(db)
        persist_profiles(db, profiles)

        result = get_flag_risk_score(db, "gb")
        assert result is not None
        assert result.flag_code == "GB"


# ── CLI command ──────────────────────────────────────────────────────────────


class TestCLICommand:
    def test_flag_risk_update_runs(self, db):
        """Test CLI command can be imported and invoked."""
        from typer.testing import CliRunner
        from app.cli_app import app

        runner = CliRunner()
        with patch("app.database.SessionLocal", return_value=db):
            result = runner.invoke(app, ["flag-risk", "update"])
        assert result.exit_code == 0
        assert "Computed" in result.output
        assert "Persisted" in result.output

    def test_flag_risk_dry_run(self, db):
        """Test --dry-run computes but doesn't persist."""
        _make_vessel(db, "888888881", "PA", max_gap_score=10)
        db.commit()

        from typer.testing import CliRunner
        from app.cli_app import app

        runner = CliRunner()
        with patch("app.database.SessionLocal", return_value=db):
            result = runner.invoke(app, ["flag-risk", "update", "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run" in result.output

        # Should not be persisted
        assert db.query(FlagRiskProfile).count() == 0


# ── V1/V2 toggle behavior ───────────────────────────────────────────────────


class TestV1V2Toggle:
    def test_v2_disabled_uses_v1(self, db):
        """When v2 is disabled, flag scoring uses v1 logic."""
        from app.modules.scoring_config import load_scoring_config

        config = load_scoring_config()
        flag_cfg = config.get("flag_state", {})

        # V1 keys must exist
        assert "white_list_flag" in flag_cfg
        assert "high_risk_registry" in flag_cfg

    def test_v2_enabled_uses_profile(self, db):
        """When v2 is enabled and profile exists, v2 score is used."""
        # Create a profile
        profile = FlagRiskProfile(
            flag_code="PA",
            psc_detention_score=60,
            fp_rate_score=80,
            fleet_composition_score=70,
            flag_hopping_score=50,
            transparency_score=80,
            composite_score=72,
            risk_tier="HIGH",
            vessel_count=10,
            detention_count=5,
            fp_rate=0.1,
        )
        db.add(profile)
        db.commit()

        result = get_flag_risk_score(db, "PA")
        assert result is not None
        assert result.risk_tier == "HIGH"
        assert result.composite_score == 72


# ── Integration with risk_scoring.py ─────────────────────────────────────────


class TestRiskScoringIntegration:
    def test_v2_flag_in_expected_sections(self):
        """flag_state_v2 is in expected sections list."""
        from app.modules.scoring_config import _EXPECTED_SECTIONS
        assert "flag_state_v2" in _EXPECTED_SECTIONS

    def test_v2_config_in_yaml(self):
        """flag_state_v2 section exists in scoring config."""
        from app.modules.scoring_config import load_scoring_config, reload_scoring_config

        # Force reload to pick up changes
        reload_scoring_config()
        config = load_scoring_config()
        assert "flag_state_v2" in config
        v2_cfg = config["flag_state_v2"]
        assert "high_risk" in v2_cfg
        assert "medium_risk_max" in v2_cfg
        assert "minimal_risk" in v2_cfg

    def test_config_has_v2_settings(self):
        """Config has FLAG_RISK_SCORING_V2_ENABLED and weight settings."""
        from app.config import Settings
        s = Settings()
        assert s.FLAG_RISK_SCORING_V2_ENABLED is False
        assert s.FLAG_RISK_PSC_WEIGHT == 0.25
        assert s.FLAG_RISK_FP_WEIGHT == 0.20
        assert s.FLAG_RISK_FLEET_WEIGHT == 0.25
        assert s.FLAG_RISK_HOPPING_WEIGHT == 0.15
        assert s.FLAG_RISK_TRANSPARENCY_WEIGHT == 0.15


# ── Model ────────────────────────────────────────────────────────────────────


class TestFlagRiskProfileModel:
    def test_model_creation(self, db):
        profile = FlagRiskProfile(
            flag_code="TG",
            psc_detention_score=45.0,
            fp_rate_score=70.0,
            fleet_composition_score=60.0,
            flag_hopping_score=30.0,
            transparency_score=80.0,
            composite_score=55.0,
            risk_tier="MEDIUM",
            vessel_count=5,
            detention_count=2,
            fp_rate=0.15,
            evidence_json='{"test": true}',
        )
        db.add(profile)
        db.commit()

        fetched = db.query(FlagRiskProfile).filter_by(flag_code="TG").one()
        assert fetched.composite_score == 55.0
        assert fetched.risk_tier == "MEDIUM"
        assert fetched.vessel_count == 5

    def test_unique_flag_code(self, db):
        db.add(FlagRiskProfile(flag_code="PA", composite_score=50, risk_tier="MEDIUM"))
        db.commit()

        from sqlalchemy.exc import IntegrityError
        db.add(FlagRiskProfile(flag_code="PA", composite_score=60, risk_tier="HIGH"))
        with pytest.raises(IntegrityError):
            db.commit()
