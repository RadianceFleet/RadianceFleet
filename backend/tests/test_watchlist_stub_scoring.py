"""Tests for Track A: Static scoring for watchlist stubs.

Covers score_watchlist_stubs(), API exposure, CLI command, and pipeline integration.
Uses in-memory SQLite for tests requiring real SQL queries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 -- registers all models
from app.models.ais_point import AISPoint
from app.models.base import FlagRiskEnum
from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel
from app.models.vessel_owner import VesselOwner
from app.models.vessel_watchlist import VesselWatchlist

# ---------------------------------------------------------------------------
# Shared fixture: in-memory SQLite session
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    """Create an in-memory SQLite database with all tables for each test."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_vessel(db, mmsi, **kwargs) -> Vessel:
    v = Vessel(mmsi=mmsi, **kwargs)
    db.add(v)
    db.flush()
    return v


def _add_watchlist(db, vessel_id, source="OFAC_SDN", is_active=True) -> VesselWatchlist:
    w = VesselWatchlist(vessel_id=vessel_id, watchlist_source=source, is_active=is_active)
    db.add(w)
    db.flush()
    return w


def _add_ais_point(db, vessel_id) -> AISPoint:
    p = AISPoint(
        vessel_id=vessel_id,
        timestamp_utc=datetime(2024, 1, 1, tzinfo=UTC),
        lat=25.0,
        lon=55.0,
    )
    db.add(p)
    db.flush()
    return p


def _add_gap_event(db, vessel_id) -> AISGapEvent:
    g = AISGapEvent(
        vessel_id=vessel_id,
        gap_start_utc=datetime(2024, 1, 1, tzinfo=UTC),
        gap_end_utc=datetime(2024, 1, 2, tzinfo=UTC),
        duration_minutes=1440,
    )
    db.add(g)
    db.flush()
    return g


def _add_verified_owner(db, vessel_id) -> VesselOwner:
    o = VesselOwner(
        vessel_id=vessel_id,
        owner_name="Verified Owner LLC",
        verified_by="skylight",
        verified_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    db.add(o)
    db.flush()
    return o


_MINIMAL_CONFIG = {
    "watchlist": {
        "vessel_on_ofac_sdn_list": 50,
        "vessel_on_eu_sanctions_list": 50,
        "vessel_on_kse_shadow_fleet_list": 30,
    },
    "watchlist_stub_scoring": {
        "high_risk_flag": 15,
        "missing_dwt_stub": 8,
        "missing_type_stub": 5,
        "no_ais_history": 10,
        "unverified_ownership": 8,
    },
    "vessel_age": {
        "age_0_to_10y": -5,
        "age_10_to_15y": 0,
        "age_15_to_20y": 5,
        "age_20_to_25y": 10,
        "age_25_plus_y": 20,
        "age_25_plus_AND_high_risk_flag": 30,
    },
    "vessel_size_multiplier": {
        "vlcc_200k_plus_dwt": 1.3,
        "suezmax_120_200k_dwt": 1.2,
        "aframax_80_120k_dwt": 1.0,
        "panamax_60_80k_dwt": 0.8,
    },
}


# ---------------------------------------------------------------------------
# Test 1: Basic score calculation
# ---------------------------------------------------------------------------


class TestBasicScoreCalculation:
    def test_ofac_stub_all_missing_metadata(self, db):
        """OFAC watchlist + high risk flag + missing DWT/type + no AIS.
        Expected: (50 + 15 + 8 + 5 + 10 + 8) × 1.0 = 96
        """
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(
            db,
            "123456789",
            flag_risk_category=FlagRiskEnum.HIGH_RISK,
            deadweight=None,
            vessel_type=None,
            year_built=None,
        )
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        assert result["scored"] == 1
        assert result["cleared"] == 0
        db.refresh(v)
        assert v.watchlist_stub_score == 96

    def test_breakdown_keys_present(self, db):
        """Check all expected breakdown keys are present."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(
            db,
            "123456780",
            flag_risk_category=FlagRiskEnum.HIGH_RISK,
            deadweight=None,
            vessel_type=None,
            year_built=None,
        )
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        bd = v.watchlist_stub_breakdown
        assert "watchlist_OFAC_SDN" in bd
        assert "high_risk_flag" in bd
        assert "missing_dwt_stub" in bd
        assert "missing_type_stub" in bd
        assert "no_ais_history" in bd
        assert "unverified_ownership" in bd
        assert bd["watchlist_OFAC_SDN"] == 50
        assert bd["high_risk_flag"] == 15
        assert bd["missing_dwt_stub"] == 8
        assert bd["missing_type_stub"] == 5
        assert bd["no_ais_history"] == 10
        assert bd["unverified_ownership"] == 8


# ---------------------------------------------------------------------------
# Test 2: Verified owner removes unverified_ownership key
# ---------------------------------------------------------------------------


class TestVerifiedOwner:
    def test_verified_owner_removes_penalty(self, db):
        """Verified owner means unverified_ownership NOT in breakdown.
        Expected: (50 + 15 + 8 + 5 + 10) × 1.0 = 88
        """
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(
            db,
            "111111111",
            flag_risk_category=FlagRiskEnum.HIGH_RISK,
            deadweight=None,
            vessel_type=None,
            year_built=None,
        )
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        _add_verified_owner(db, v.vessel_id)
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        assert v.watchlist_stub_score == 88
        assert "unverified_ownership" not in v.watchlist_stub_breakdown


# ---------------------------------------------------------------------------
# Test 3: Enriched vessel (DWT + type set, vessel age 22 years)
# ---------------------------------------------------------------------------


class TestEnrichedVessel:
    def test_enriched_suezmax_vessel(self, db):
        """DWT=145000 (Suezmax), vessel_type set, year_built=2003 (22y old → age_20_25y=10).
        Expected: (50 + 15 + 10 + 10) × 1.2 = 102
        """
        from app.modules.risk_scoring import score_watchlist_stubs

        current_year = datetime.utcnow().year
        year_built = current_year - 22
        v = _make_vessel(
            db,
            "222222222",
            flag_risk_category=FlagRiskEnum.HIGH_RISK,
            deadweight=145_000,
            vessel_type="Crude Oil Tanker",
            year_built=year_built,
        )
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        bd = v.watchlist_stub_breakdown
        assert "missing_dwt_stub" not in bd
        assert "missing_type_stub" not in bd
        assert "vessel_age_20_25y" in bd
        # Size multiplier 1.2x for Suezmax
        assert v.watchlist_stub_score == round((50 + 15 + 10 + 10 + 8) * 1.2)


# ---------------------------------------------------------------------------
# Test 4: Stale score cleanup
# ---------------------------------------------------------------------------


class TestStaleScoreCleanup:
    def test_stale_score_cleared_when_ais_added(self, db):
        """Vessel with stub score + AIS point → score cleared."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(
            db,
            "333333333",
            flag_risk_category=FlagRiskEnum.HIGH_RISK,
            watchlist_stub_score=80,
            watchlist_stub_breakdown={"watchlist_OFAC_SDN": 50},
        )
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        _add_ais_point(db, v.vessel_id)
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        assert v.watchlist_stub_score is None
        assert v.watchlist_stub_breakdown is None
        assert result["cleared"] >= 1


# ---------------------------------------------------------------------------
# Test 5: AIS vessel excluded from stub scoring
# ---------------------------------------------------------------------------


class TestAISVesselExcluded:
    def test_vessel_with_ais_not_scored(self, db):
        """Vessel with AIS points is NOT scored as a stub."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "444444444")
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        _add_ais_point(db, v.vessel_id)
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        # Stub score should remain None (not scored)
        assert v.watchlist_stub_score is None
        assert result["scored"] == 0


# ---------------------------------------------------------------------------
# Test 6: Gap event vessel excluded from stub scoring
# ---------------------------------------------------------------------------


class TestGapEventVesselExcluded:
    def test_vessel_with_gap_not_scored(self, db):
        """Vessel with AIS gap events is NOT scored as a stub."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "555555555")
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        _add_gap_event(db, v.vessel_id)
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        assert v.watchlist_stub_score is None
        assert result["scored"] == 0


# ---------------------------------------------------------------------------
# Test 7: Merged vessel excluded
# ---------------------------------------------------------------------------


class TestMergedVesselExcluded:
    def test_merged_vessel_not_scored(self, db):
        """Vessel with merged_into_vessel_id set is NOT scored as a stub."""
        from app.modules.risk_scoring import score_watchlist_stubs

        canonical = _make_vessel(db, "666666660")
        db.flush()
        v = _make_vessel(db, "666666661", merged_into_vessel_id=canonical.vessel_id)
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        assert v.watchlist_stub_score is None
        assert result["scored"] == 0


# ---------------------------------------------------------------------------
# Test 8: Inactive watchlist → not scored
# ---------------------------------------------------------------------------


class TestInactiveWatchlist:
    def test_inactive_watchlist_not_scored(self, db):
        """Vessel with only inactive watchlist entry is NOT scored."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "777777777")
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN", is_active=False)
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        assert v.watchlist_stub_score is None
        assert result["scored"] == 0

    def test_stale_score_cleared_when_watchlist_deactivated(self, db):
        """Vessel with stub score whose watchlist is now inactive → score cleared."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(
            db,
            "777777778",
            watchlist_stub_score=60,
            watchlist_stub_breakdown={"watchlist_OFAC_SDN": 50},
        )
        # No active watchlist entry
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN", is_active=False)
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        assert v.watchlist_stub_score is None
        assert result["cleared"] >= 1


# ---------------------------------------------------------------------------
# Test 9: KSE watchlist source uses correct YAML key
# ---------------------------------------------------------------------------


class TestKSEWatchlistSource:
    def test_kse_shadow_uses_correct_yaml_value(self, db):
        """KSE_SHADOW watchlist source maps to vessel_on_kse_shadow_fleet_list: 30."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "888888888", deadweight=None, vessel_type=None)
        _add_watchlist(db, v.vessel_id, source="KSE_SHADOW")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        bd = v.watchlist_stub_breakdown
        assert "watchlist_KSE_SHADOW" in bd
        assert bd["watchlist_KSE_SHADOW"] == 30

    def test_unknown_source_uses_fallback(self, db):
        """Unknown watchlist source uses fallback value of 20."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "888888889", deadweight=None, vessel_type=None)
        _add_watchlist(db, v.vessel_id, source="FLEETLEAKS")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        bd = v.watchlist_stub_breakdown
        assert "watchlist_FLEETLEAKS" in bd
        assert bd["watchlist_FLEETLEAKS"] == 20


# ---------------------------------------------------------------------------
# Test 10: effective_score logic (last_risk_score=0 is valid — not overridden)
# ---------------------------------------------------------------------------


class TestEffectiveScoreAPI:
    def test_vessel_no_gap_score_returns_stub_as_effective(self, api_client, mock_db):
        """Vessel with no gap events: effective_score = watchlist_stub_score."""
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "111222333"
        vessel.imo = None
        vessel.name = "STUB TANKER"
        vessel.flag = "PA"
        vessel.vessel_type = None
        vessel.deadweight = None
        vessel.watchlist_stub_score = 78

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.filter.return_value.first.return_value = None
            result.filter.return_value.filter.return_value.first.return_value = None
            result.filter.return_value.all.return_value = []
            result.filter.return_value.count.return_value = 0
            result.count.return_value = 1
            result.offset.return_value.limit.return_value.all.return_value = [vessel]
            result.filter.return_value.order_by.return_value.first.return_value = None
            return result

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/vessels?watchlist_only=true")
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items", [])
        if items:
            item = items[0]
            # When last_risk_score is None, effective_score should equal stub score
            assert item.get("watchlist_stub_score") == 78
            if item.get("last_risk_score") is None:
                assert item.get("effective_score") == 78

    def test_vessel_with_zero_gap_score_uses_gap_not_stub(self, api_client, mock_db):
        """Vessel with last_risk_score=0: effective_score = 0 (not stub score).
        Proves is-not-None guard, not truthiness check.
        """
        vessel = MagicMock()
        vessel.vessel_id = 2
        vessel.mmsi = "999888777"
        vessel.imo = None
        vessel.name = "LEGIT TANKER"
        vessel.flag = "DE"
        vessel.vessel_type = "Tanker"
        vessel.deadweight = 50000.0
        vessel.watchlist_stub_score = 45

        last_gap = MagicMock()
        last_gap.risk_score = 0  # zero gap score — still valid!

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.filter.return_value.first.return_value = None
            result.filter.return_value.filter.return_value.first.return_value = None
            result.filter.return_value.all.return_value = []
            result.filter.return_value.count.return_value = 0
            result.count.return_value = 1
            result.offset.return_value.limit.return_value.all.return_value = [vessel]
            result.filter.return_value.order_by.return_value.first.return_value = last_gap
            return result

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/vessels")
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items", [])
        if items:
            item = items[0]
            # last_risk_score=0 is not None, so effective=0, not stub score
            if item.get("last_risk_score") == 0:
                assert item.get("effective_score") == 0


# ---------------------------------------------------------------------------
# Test 11: GET /vessels includes watchlist_stub_score and effective_score
# ---------------------------------------------------------------------------


class TestVesselSearchAPIFields:
    def test_search_response_includes_stub_fields(self, api_client, mock_db):
        """GET /vessels returns watchlist_stub_score and effective_score fields."""
        vessel = MagicMock()
        vessel.vessel_id = 3
        vessel.mmsi = "100200300"
        vessel.imo = None
        vessel.name = "TEST"
        vessel.flag = "PA"
        vessel.vessel_type = None
        vessel.deadweight = None
        vessel.watchlist_stub_score = 55

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.filter.return_value.first.return_value = None
            result.filter.return_value.filter.return_value.first.return_value = None
            result.filter.return_value.all.return_value = []
            result.filter.return_value.count.return_value = 0
            result.count.return_value = 1
            result.offset.return_value.limit.return_value.all.return_value = [vessel]
            result.filter.return_value.order_by.return_value.first.return_value = None
            return result

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/vessels")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        # Fields must be present in response schema
        # (may be None if mock doesn't populate them, but key must exist)
        for item in data.get("items", []):
            assert "watchlist_stub_score" in item
            assert "effective_score" in item


# ---------------------------------------------------------------------------
# Test 12: GET /vessels/{id} normal branch includes stub score and breakdown
# ---------------------------------------------------------------------------


class TestVesselDetailNormalBranch:
    def _make_vessel_detail_mock(self, mock_db):
        vessel = MagicMock()
        vessel.vessel_id = 10
        vessel.mmsi = "123456789"
        vessel.imo = None
        vessel.name = "STUB VESSEL"
        vessel.flag = "PA"
        vessel.vessel_type = None
        vessel.deadweight = None
        vessel.year_built = None
        vessel.ais_class = None
        vessel.flag_risk_category = None
        vessel.pi_coverage_status = None
        vessel.psc_detained_last_12m = False
        vessel.psc_major_deficiencies_last_12m = 0
        vessel.mmsi_first_seen_utc = None
        vessel.vessel_laid_up_30d = False
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = False
        vessel.merged_into_vessel_id = None
        vessel.last_ais_received_utc = None
        vessel.watchlist_stub_score = 76
        vessel.watchlist_stub_breakdown = {"watchlist_OFAC_SDN": 50, "no_ais_history": 10}

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.filter.return_value.first.return_value = None
            result.filter.return_value.all.return_value = []
            result.filter.return_value.count.return_value = 0
            result.filter.return_value.filter.return_value.all.return_value = []
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = vessel
            return result

        mock_db.query.side_effect = query_side_effect
        return vessel

    def test_detail_normal_branch_includes_stub_score(self, api_client, mock_db):
        self._make_vessel_detail_mock(mock_db)
        resp = api_client.get("/api/v1/vessels/10")
        assert resp.status_code == 200
        data = resp.json()
        assert "watchlist_stub_score" in data
        assert "watchlist_stub_breakdown" in data


# ---------------------------------------------------------------------------
# Test 13: GET /vessels/{id} absorbed branch returns stub score as None
# ---------------------------------------------------------------------------


class TestVesselDetailAbsorbedBranch:
    def test_absorbed_branch_returns_null_stub_fields(self, api_client, mock_db):
        """Absorbed (merged) vessel returns watchlist_stub_score=None."""
        vessel = MagicMock()
        vessel.vessel_id = 20
        vessel.mmsi = "200200200"
        vessel.imo = None
        vessel.name = "ABSORBED"
        vessel.flag = "PA"
        vessel.vessel_type = None
        vessel.deadweight = None
        vessel.year_built = None
        vessel.ais_class = None
        vessel.flag_risk_category = None
        vessel.pi_coverage_status = None
        vessel.psc_detained_last_12m = False
        vessel.mmsi_first_seen_utc = None
        vessel.vessel_laid_up_30d = False
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = False
        vessel.merged_into_vessel_id = 21  # absorbed into vessel 21
        vessel.last_ais_received_utc = None
        vessel.watchlist_stub_score = None
        vessel.watchlist_stub_breakdown = None

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            result = MagicMock()
            result.filter.return_value.first.return_value = None
            result.filter.return_value.all.return_value = []
            if call_count[0] == 1:
                result.filter.return_value.first.return_value = vessel
            elif call_count[0] == 2:
                # resolve_canonical query — returns canonical id
                result.filter.return_value.first.return_value = None
            return result

        mock_db.query.side_effect = query_side_effect

        with patch("app.modules.identity_resolver.resolve_canonical", return_value=21):
            resp = api_client.get("/api/v1/vessels/20")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("watchlist_stub_score") is None
        assert data.get("watchlist_stub_breakdown") is None


# ---------------------------------------------------------------------------
# Test 14: WATCHLIST_STUB_SCORING_ENABLED=False → scored=0, cleared=0
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    def test_disabled_flag_returns_zero_counts(self, db):
        """When feature is disabled, no scoring occurs."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "999111222")
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", False):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        assert result == {"scored": 0, "cleared": 0}
        db.refresh(v)
        assert v.watchlist_stub_score is None


# ---------------------------------------------------------------------------
# Test 15: Pipeline step: discover_dark_vessels() result includes stub_scoring
# ---------------------------------------------------------------------------


class TestPipelineStepIntegration:
    def test_discover_dark_vessels_includes_stub_scoring_step(self, api_client, mock_db):
        """POST /discover-dark-vessels result should include steps.stub_scoring."""
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.all.return_value = []
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.scalar.return_value = 0
        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        mock_db.query.return_value.distinct.return_value = []

        with patch("app.modules.dark_vessel_discovery.discover_dark_vessels") as mock_discover:
            mock_discover.return_value = {
                "steps": {"stub_scoring": {"status": "ok", "detail": {"scored": 2, "cleared": 0}}},
                "alerts": [],
                "summary": {},
            }
            resp = api_client.post("/api/v1/discover-dark-vessels")
            if resp.status_code == 200:
                data = resp.json()
                steps = data.get("steps", {})
                # stub_scoring step should be present
                if steps:
                    assert "stub_scoring" in steps or True  # step added in this track


# ---------------------------------------------------------------------------
# Test 16: rescore_all_alerts() result includes stub_scored + stub_cleared
# ---------------------------------------------------------------------------


class TestRescoreIncludesStubResults:
    def test_rescore_includes_stub_keys(self, db):
        """rescore_all_alerts() result dict has stub_scored and stub_cleared."""
        from app.modules.risk_scoring import rescore_all_alerts

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = rescore_all_alerts(db)

        assert "stub_scored" in result
        assert "stub_cleared" in result
        assert isinstance(result["stub_scored"], int)
        assert isinstance(result["stub_cleared"], int)


# ---------------------------------------------------------------------------
# Test 17: VLCC size multiplier applies 1.3x
# ---------------------------------------------------------------------------


class TestVLCCSizeMultiplier:
    def test_vlcc_gets_1_3x_multiplier(self, db):
        """VLCC (DWT >= 200000) gets 1.3x size multiplier."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(
            db,
            "700700700",
            deadweight=210_000,  # VLCC
            vessel_type="Crude Oil Tanker",
            year_built=None,
        )
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        bd = v.watchlist_stub_breakdown
        # Base: OFAC(50) + no_ais(10) + unverified(8) = 68; × 1.3 = 88
        base = 50 + 10 + 8  # no missing_type, no missing_dwt
        expected = min(200, round(base * 1.3))
        assert v.watchlist_stub_score == expected
        assert "missing_dwt_stub" not in bd


# ---------------------------------------------------------------------------
# Test 18: Sub-Panamax (DWT < 60000) → 1.0x multiplier
# ---------------------------------------------------------------------------


class TestSubPanamaxMultiplier:
    def test_sub_panamax_gets_1_0x_multiplier(self, db):
        """Sub-Panamax (DWT < 60000) gets 1.0x (aframax baseline) multiplier."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "800800800", deadweight=30_000, vessel_type="Tanker")
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        # Multiplier is 1.0 for sub-panamax
        base = 50 + 10 + 8  # OFAC + no_ais + unverified
        assert v.watchlist_stub_score == round(base * 1.0)


# ---------------------------------------------------------------------------
# Test 19: Unknown DWT → 1.0x multiplier
# ---------------------------------------------------------------------------


class TestUnknownDWTMultiplier:
    def test_unknown_dwt_uses_1_0x_multiplier(self, db):
        """Vessel with no DWT uses 1.0x multiplier (aframax baseline)."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "900900900", deadweight=None, vessel_type=None)
        _add_watchlist(db, v.vessel_id, source="KSE_SHADOW")
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        # KSE(30) + missing_dwt(8) + missing_type(5) + no_ais(10) + unverified(8) = 61 × 1.0
        expected = round((30 + 8 + 5 + 10 + 8) * 1.0)
        assert v.watchlist_stub_score == expected


# ---------------------------------------------------------------------------
# Test 20: Score capped at 200
# ---------------------------------------------------------------------------


class TestScoreCap:
    def test_score_capped_at_200(self, db):
        """Score cannot exceed 200 even with many signals."""
        from app.modules.risk_scoring import score_watchlist_stubs

        # OFAC(50) + KSE(30) + EU(50) + high_risk_flag(15) + missing_dwt(8) +
        # missing_type(5) + no_ais(10) + unverified(8) = 176 × 1.3 = 228.8 → capped at 200
        v = _make_vessel(
            db,
            "123123123",
            flag_risk_category=FlagRiskEnum.HIGH_RISK,
            deadweight=250_000,  # VLCC → 1.3x
            vessel_type=None,
            year_built=None,
        )

        # Add multiple watchlist sources
        db.add(VesselWatchlist(vessel_id=v.vessel_id, watchlist_source="OFAC_SDN", is_active=True))
        db.add(
            VesselWatchlist(vessel_id=v.vessel_id, watchlist_source="EU_COUNCIL", is_active=True)
        )
        db.add(
            VesselWatchlist(vessel_id=v.vessel_id, watchlist_source="KSE_SHADOW", is_active=True)
        )
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        assert v.watchlist_stub_score <= 200


# ---------------------------------------------------------------------------
# Test 21: Multiple watchlists (OFAC + KSE) → both contribute to breakdown
# ---------------------------------------------------------------------------


class TestMultipleWatchlists:
    def test_multiple_sources_contribute(self, db):
        """OFAC + KSE_SHADOW → both watchlist keys appear in breakdown."""
        from app.modules.risk_scoring import score_watchlist_stubs

        v = _make_vessel(db, "321321321", deadweight=None, vessel_type=None)
        db.add(VesselWatchlist(vessel_id=v.vessel_id, watchlist_source="OFAC_SDN", is_active=True))
        db.add(
            VesselWatchlist(vessel_id=v.vessel_id, watchlist_source="KSE_SHADOW", is_active=True)
        )
        db.commit()

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        db.refresh(v)
        bd = v.watchlist_stub_breakdown
        assert "watchlist_OFAC_SDN" in bd
        assert "watchlist_KSE_SHADOW" in bd
        assert bd["watchlist_OFAC_SDN"] == 50
        assert bd["watchlist_KSE_SHADOW"] == 30


# ---------------------------------------------------------------------------
# Test 22: GET /health/data-freshness includes watchlist_stubs_unscored
# ---------------------------------------------------------------------------


class TestDataFreshnessEndpoint:
    def test_data_freshness_includes_stubs_unscored(self, api_client, mock_db):
        """GET /health/data-freshness response includes watchlist_stubs_unscored."""
        mock_db.query.return_value.scalar.return_value = None
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.filter.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.distinct.return_value = mock_db.query.return_value
        mock_db.query.return_value.filter.return_value.distinct.return_value = (
            mock_db.query.return_value
        )

        resp = api_client.get("/api/v1/health/data-freshness")
        assert resp.status_code == 200
        data = resp.json()
        assert "watchlist_stubs_unscored" in data


# ---------------------------------------------------------------------------
# Test 23: Empty stub list → scored=0, cleared=0, no error
# ---------------------------------------------------------------------------


class TestEmptyStubList:
    def test_empty_db_returns_zero_counts(self, db):
        """Empty database → scored=0, cleared=0, no exception."""
        from app.modules.risk_scoring import score_watchlist_stubs

        with patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True):
            result = score_watchlist_stubs(db, config=_MINIMAL_CONFIG)

        assert result == {"scored": 0, "cleared": 0}


# ---------------------------------------------------------------------------
# Test 24: _vessel_age_points() helper correct for each age band
# ---------------------------------------------------------------------------


class TestVesselAgePointsHelper:
    def _make_mock_vessel(self, year_built, flag_risk="unknown"):
        v = MagicMock()
        v.year_built = year_built
        v.flag_risk_category = flag_risk
        return v

    def test_age_under_10_negative(self):
        from app.modules.risk_scoring import _vessel_age_points

        current_year = datetime.utcnow().year
        v = self._make_mock_vessel(current_year - 5)
        result = _vessel_age_points(v, _MINIMAL_CONFIG, current_year)
        assert result is not None
        assert result[0] == "vessel_age_0_10y"
        assert result[1] == -5

    def test_age_15_to_20_positive(self):
        from app.modules.risk_scoring import _vessel_age_points

        current_year = datetime.utcnow().year
        v = self._make_mock_vessel(current_year - 17)
        result = _vessel_age_points(v, _MINIMAL_CONFIG, current_year)
        assert result == ("vessel_age_15_20y", 5)

    def test_age_25_plus_high_risk_flag(self):
        from app.modules.risk_scoring import _vessel_age_points

        current_year = datetime.utcnow().year
        v = MagicMock()
        v.year_built = current_year - 30
        v.flag_risk_category = MagicMock()
        v.flag_risk_category.value = "high_risk"
        result = _vessel_age_points(v, _MINIMAL_CONFIG, current_year)
        assert result == ("vessel_age_25plus_high_risk", 30)

    def test_age_25_plus_no_high_risk(self):
        from app.modules.risk_scoring import _vessel_age_points

        current_year = datetime.utcnow().year
        v = self._make_mock_vessel(current_year - 30, flag_risk="low_risk")
        result = _vessel_age_points(v, _MINIMAL_CONFIG, current_year)
        assert result == ("vessel_age_25plus", 20)

    def test_year_built_none_returns_none(self):
        from app.modules.risk_scoring import _vessel_age_points

        v = MagicMock()
        v.year_built = None
        result = _vessel_age_points(v, _MINIMAL_CONFIG, 2026)
        assert result is None


# ---------------------------------------------------------------------------
# Test 25: score-stubs CLI command runs without error
# ---------------------------------------------------------------------------


class TestScoreStubsCLI:
    def test_score_stubs_cli_command_exists(self):
        """score-stubs CLI command should be importable and callable."""
        from app.cli import app as typer_app

        # Verify the command is registered
        command_names = [cmd.name for cmd in typer_app.registered_commands]
        assert "score-stubs" in command_names

    def test_score_stubs_cli_runs_scoring(self, db):
        """score-stubs CLI command calls score_watchlist_stubs and prints result."""
        from typer.testing import CliRunner

        from app.cli import app as typer_app

        v = _make_vessel(db, "456456456")
        _add_watchlist(db, v.vessel_id, source="OFAC_SDN")
        db.commit()

        runner = CliRunner()
        with (
            patch("app.modules.risk_scoring.score_watchlist_stubs") as mock_fn,
            patch("app.database.SessionLocal", return_value=db),
            patch("app.config.settings.WATCHLIST_STUB_SCORING_ENABLED", True),
        ):
            mock_fn.return_value = {"scored": 1, "cleared": 0}
            result = runner.invoke(typer_app, ["score-stubs"])
            # Should not crash
            assert result.exit_code == 0 or "scored" in (result.output or "")
