"""Tests for ownership transparency analysis (SPV detection, jurisdiction hopping)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _make_vessel(db: Session, vessel_id: int = 1) -> MagicMock:
    """Insert a vessel stub and return it."""
    from app.models.vessel import Vessel

    v = Vessel(vessel_id=vessel_id, mmsi="123456789", name="TEST TANKER")
    db.add(v)
    db.commit()
    return v


def _make_owner(
    db: Session,
    vessel_id: int = 1,
    owner_name: str = "SHADOW SHIPPING LTD",
    country: str = "PA",
    opencorporates_url: str | None = None,
    incorporation_jurisdiction: str | None = None,
    incorporation_date: datetime | None = None,
    is_spv: bool = False,
    spv_indicators_json: str | None = None,
):
    from app.models.vessel_owner import VesselOwner

    owner = VesselOwner(
        vessel_id=vessel_id,
        owner_name=owner_name,
        country=country,
        opencorporates_url=opencorporates_url,
        incorporation_jurisdiction=incorporation_jurisdiction,
        incorporation_date=incorporation_date,
        is_spv=is_spv,
        spv_indicators_json=spv_indicators_json,
    )
    db.add(owner)
    db.commit()
    return owner


# ---------------------------------------------------------------------------
# Disabled
# ---------------------------------------------------------------------------


class TestDisabled:
    def test_enrich_returns_disabled_when_off(self, db):
        with patch("app.modules.ownership_transparency.settings") as mock_settings:
            mock_settings.OPENCORPORATES_ENABLED = False
            from app.modules.ownership_transparency import enrich_vessel_ownership

            result = enrich_vessel_ownership(db, vessel_id=1)
            assert result["disabled"] is True
            assert result["enriched"] == 0


# ---------------------------------------------------------------------------
# SPV Detection
# ---------------------------------------------------------------------------


class TestSPVDetection:
    def test_spv_secrecy_plus_single_vessel(self):
        """SPV = secrecy jurisdiction + single vessel company."""
        from app.modules.ownership_transparency import analyze_spv

        result = analyze_spv(
            owner_name="SHADOW SHIPPING LTD",
            jurisdiction_code="PA",
            vessel_count=1,
        )
        assert result["is_spv"] is True
        assert "secrecy_jurisdiction" in result["indicators"]
        assert "single_vessel_company" in result["indicators"]
        assert result["score_components"]["spv"] == 15
        assert result["score_components"]["secrecy_jurisdiction"] == 10

    def test_spv_secrecy_plus_recent_incorporation(self):
        """SPV = secrecy jurisdiction + recent incorporation."""
        from app.modules.ownership_transparency import analyze_spv

        now = datetime.utcnow()
        result = analyze_spv(
            owner_name="SHELL CO LTD",
            jurisdiction_code="MH",
            incorporation_date=now - timedelta(days=365),
            vessel_acquisition_date=now,
            vessel_count=5,  # Not single vessel
        )
        assert result["is_spv"] is True
        assert "secrecy_jurisdiction" in result["indicators"]
        assert "recent_incorporation" in result["indicators"]

    def test_not_spv_legitimate_company(self):
        """Non-secrecy jurisdiction + multiple vessels = not SPV."""
        from app.modules.ownership_transparency import analyze_spv

        result = analyze_spv(
            owner_name="MAERSK LINE AS",
            jurisdiction_code="DK",
            vessel_count=200,
            incorporation_date=datetime(2000, 1, 1),
            vessel_acquisition_date=datetime(2020, 1, 1),
        )
        assert result["is_spv"] is False
        assert "secrecy_jurisdiction" not in result["indicators"]

    def test_secrecy_only_not_spv(self):
        """Secrecy jurisdiction alone (no other indicator) is not SPV."""
        from app.modules.ownership_transparency import analyze_spv

        # Old incorporation, multiple vessels, no nominee
        result = analyze_spv(
            owner_name="OLD CORP",
            jurisdiction_code="PA",
            incorporation_date=datetime(2000, 1, 1),
            vessel_acquisition_date=datetime(2024, 1, 1),
            vessel_count=10,
            officers=[{"name": "Jane Doe", "position": "director"}],
        )
        assert result["is_spv"] is False

    def test_spv_compound_shell(self):
        """3+ indicators triggers compound bonus."""
        from app.modules.ownership_transparency import analyze_spv

        now = datetime.utcnow()
        result = analyze_spv(
            owner_name="NOMINEE SHIPPING LTD",
            jurisdiction_code="VG",
            incorporation_date=now - timedelta(days=180),
            vessel_acquisition_date=now,
            vessel_count=1,
            officers=[{"name": "Corporate Secretarial Services Ltd", "position": "director"}],
        )
        assert result["is_spv"] is True
        assert len(result["indicators"]) >= 3
        assert result["score_components"].get("spv_shell_compound") == 25


# ---------------------------------------------------------------------------
# Jurisdiction Hopping
# ---------------------------------------------------------------------------


class TestJurisdictionHopping:
    def test_hopping_detected(self, db):
        """2+ jurisdiction changes detected."""
        _make_vessel(db)
        _make_owner(db, owner_name="Owner A", incorporation_jurisdiction="PA")
        _make_owner(db, owner_name="Owner B", incorporation_jurisdiction="MH")
        _make_owner(db, owner_name="Owner C", incorporation_jurisdiction="VG")

        from app.modules.ownership_transparency import detect_jurisdiction_hopping

        result = detect_jurisdiction_hopping(db, vessel_id=1)
        assert result["detected"] is True
        assert result["hop_count"] >= 2
        assert len(result["jurisdictions"]) == 3

    def test_no_hopping(self, db):
        """Single jurisdiction = no hopping."""
        _make_vessel(db)
        _make_owner(db, owner_name="Owner A", incorporation_jurisdiction="NO")
        _make_owner(db, owner_name="Owner B", incorporation_jurisdiction="NO")

        from app.modules.ownership_transparency import detect_jurisdiction_hopping

        result = detect_jurisdiction_hopping(db, vessel_id=1)
        assert result["detected"] is False
        assert result["hop_count"] == 0


# ---------------------------------------------------------------------------
# Skip Already Enriched
# ---------------------------------------------------------------------------


class TestSkipEnriched:
    def test_skip_enriched_owners(self, db):
        """Owners with opencorporates_url set are skipped."""
        _make_vessel(db)
        _make_owner(
            db,
            owner_name="ALREADY ENRICHED",
            opencorporates_url="https://opencorporates.com/companies/pa/12345",
        )

        with patch("app.modules.ownership_transparency.settings") as mock_settings:
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_MONTHLY_QUOTA = 500

            from app.modules.ownership_transparency import enrich_vessel_ownership

            result = enrich_vessel_ownership(db, vessel_id=1)
            assert result["skipped"] == 1
            assert result["enriched"] == 0


# ---------------------------------------------------------------------------
# Fuzzy Matching
# ---------------------------------------------------------------------------


class TestFuzzyMatching:
    def test_fuzzy_match_finds_close_name(self, db):
        """Fuzzy matching finds company with slightly different name."""
        _make_vessel(db)
        _make_owner(db, owner_name="SHADOW SHIPPING LIMITED")

        mock_search_results = [
            {
                "name": "SHADOW SHIPPING LTD",
                "company_number": "99999",
                "jurisdiction_code": "pa",
                "opencorporates_url": "https://opencorporates.com/companies/pa/99999",
                "incorporation_date": "2023-06-01",
                "registered_address": "",
                "current_status": "Active",
            }
        ]

        with (
            patch("app.modules.ownership_transparency.settings") as mock_settings,
            patch(
                "app.modules.opencorporates_client.search_companies",
                return_value=mock_search_results,
            ),
            patch(
                "app.modules.opencorporates_client.fetch_officers",
                return_value=[],
            ),
        ):
            mock_settings.OPENCORPORATES_ENABLED = True
            mock_settings.OPENCORPORATES_MONTHLY_QUOTA = 500

            from app.modules.ownership_transparency import enrich_vessel_ownership

            result = enrich_vessel_ownership(db, vessel_id=1)
            assert result["enriched"] == 1


# ---------------------------------------------------------------------------
# Scoring Integration
# ---------------------------------------------------------------------------


class TestScoringIntegration:
    def test_scoring_spv_owner(self, db):
        """SPV owner generates scoring signals."""
        _make_vessel(db)
        _make_owner(
            db,
            owner_name="SPV COMPANY",
            incorporation_jurisdiction="PA",
            is_spv=True,
            spv_indicators_json=json.dumps(["secrecy_jurisdiction", "single_vessel_company", "recent_incorporation"]),
        )

        from app.modules.ownership_transparency import score_ownership_transparency

        config = {
            "ownership_transparency": {
                "spv": 15,
                "secrecy_jurisdiction": 10,
                "recent_incorporation": 10,
                "jurisdiction_hopping": 20,
                "nominee_director": 15,
                "spv_shell_compound": 25,
            }
        }

        breakdown = score_ownership_transparency(db, vessel_id=1, config=config)
        assert breakdown.get("ownership_spv") == 15
        assert breakdown.get("ownership_secrecy_jurisdiction") == 10
        assert breakdown.get("ownership_recent_incorporation") == 10
        assert breakdown.get("ownership_spv_shell_compound") == 25

    def test_scoring_secrecy_jurisdiction_no_spv(self, db):
        """Secrecy jurisdiction without SPV still scores."""
        _make_vessel(db)
        _make_owner(
            db,
            owner_name="NORMAL COMPANY",
            incorporation_jurisdiction="PA",
            is_spv=False,
        )

        from app.modules.ownership_transparency import score_ownership_transparency

        config = {
            "ownership_transparency": {
                "secrecy_jurisdiction": 10,
            }
        }

        breakdown = score_ownership_transparency(db, vessel_id=1, config=config)
        assert breakdown.get("ownership_secrecy_jurisdiction") == 10
        assert "ownership_spv" not in breakdown

    def test_scoring_jurisdiction_hopping(self, db):
        """Jurisdiction hopping generates scoring signal."""
        _make_vessel(db)
        _make_owner(db, owner_name="Owner A", incorporation_jurisdiction="PA")
        _make_owner(db, owner_name="Owner B", incorporation_jurisdiction="MH")
        _make_owner(db, owner_name="Owner C", incorporation_jurisdiction="VG")

        from app.modules.ownership_transparency import score_ownership_transparency

        config = {
            "ownership_transparency": {
                "jurisdiction_hopping": 20,
            }
        }

        breakdown = score_ownership_transparency(db, vessel_id=1, config=config)
        assert breakdown.get("ownership_jurisdiction_hopping") == 20

    def test_scoring_no_owners(self, db):
        """No owners = empty breakdown."""
        _make_vessel(db)

        from app.modules.ownership_transparency import score_ownership_transparency

        breakdown = score_ownership_transparency(db, vessel_id=1)
        assert breakdown == {}
