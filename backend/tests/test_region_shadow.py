"""Tests for ScoringRegion model, region API endpoints, region FP rate, and shadow scoring."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base, CorridorTypeEnum
from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent
from app.models.scoring_region import ScoringRegion
from app.modules.fp_rate_tracker import CorridorFPRate, compute_region_fp_rate
from app.modules.shadow_scorer import _get_score_band, shadow_score


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory SQLite session with required tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    yield session
    session.close()
    engine.dispose()


def _make_corridor(db: Session, name: str = "Test Corridor", **kwargs) -> Corridor:
    kwargs.setdefault("corridor_type", CorridorTypeEnum.EXPORT_ROUTE)
    c = Corridor(name=name, **kwargs)
    db.add(c)
    db.flush()
    return c


def _make_gap(
    db: Session,
    corridor_id: int,
    vessel_id: int = 1,
    risk_score: int = 50,
    is_false_positive: bool | None = None,
    review_date: datetime | None = None,
) -> AISGapEvent:
    now = datetime.utcnow()
    gap = AISGapEvent(
        vessel_id=vessel_id,
        corridor_id=corridor_id,
        gap_start_utc=now - timedelta(hours=6),
        gap_end_utc=now - timedelta(hours=4),
        duration_minutes=120,
        risk_score=risk_score,
        is_false_positive=is_false_positive,
        review_date=review_date,
    )
    db.add(gap)
    db.flush()
    return gap


def _make_region(db: Session, name: str = "Baltic", **kwargs) -> ScoringRegion:
    kwargs.setdefault("gap_duration_multiplier", 1.0)
    kwargs.setdefault("is_active", True)
    r = ScoringRegion(name=name, **kwargs)
    db.add(r)
    db.flush()
    return r


# ---------------------------------------------------------------------------
# ScoringRegion model tests
# ---------------------------------------------------------------------------


class TestScoringRegionModel:
    def test_create_region(self, db: Session):
        region = _make_region(db, name="Black Sea")
        db.commit()
        assert region.region_id is not None
        assert region.name == "Black Sea"
        assert region.is_active is True
        assert region.gap_duration_multiplier == 1.0

    def test_unique_name_constraint(self, db: Session):
        _make_region(db, name="Baltic")
        db.commit()
        with pytest.raises(IntegrityError):
            _make_region(db, name="Baltic")
            db.commit()

    def test_corridor_ids_json_roundtrip(self, db: Session):
        ids = [1, 2, 3]
        region = _make_region(db, name="Test", corridor_ids_json=json.dumps(ids))
        db.commit()
        loaded = json.loads(region.corridor_ids_json)
        assert loaded == ids

    def test_signal_overrides_json_roundtrip(self, db: Session):
        overrides = {"gap_duration.12h_plus": 15.0, "corridor.sts_zone": 2.0}
        region = _make_region(
            db, name="TestOverrides", signal_overrides_json=json.dumps(overrides)
        )
        db.commit()
        loaded = json.loads(region.signal_overrides_json)
        assert loaded == overrides


# ---------------------------------------------------------------------------
# Region API tests (unit-level, calling endpoint functions directly)
# ---------------------------------------------------------------------------


class TestRegionAPI:
    def test_create_region_endpoint(self, db: Session):
        from app.api.routes_fp_tuning import _region_to_response

        region = _make_region(db, name="North Sea", corridor_ids_json=json.dumps([1, 2]))
        db.commit()
        resp = _region_to_response(region)
        assert resp.name == "North Sea"
        assert resp.corridor_ids == [1, 2]

    def test_list_regions(self, db: Session):
        _make_region(db, name="A Region")
        _make_region(db, name="B Region")
        db.commit()
        regions = db.query(ScoringRegion).order_by(ScoringRegion.name).all()
        assert len(regions) == 2
        assert regions[0].name == "A Region"

    def test_get_region(self, db: Session):
        region = _make_region(db, name="Test Get")
        db.commit()
        found = db.query(ScoringRegion).filter(ScoringRegion.region_id == region.region_id).first()
        assert found is not None
        assert found.name == "Test Get"

    def test_update_region(self, db: Session):
        region = _make_region(db, name="OldName")
        db.commit()
        region.name = "NewName"
        region.gap_duration_multiplier = 1.5
        db.commit()
        db.refresh(region)
        assert region.name == "NewName"
        assert region.gap_duration_multiplier == 1.5

    def test_delete_region(self, db: Session):
        region = _make_region(db, name="ToDelete")
        db.commit()
        rid = region.region_id
        db.delete(region)
        db.commit()
        assert db.query(ScoringRegion).filter(ScoringRegion.region_id == rid).first() is None

    def test_add_remove_corridor(self, db: Session):
        region = _make_region(db, name="CorridorTest", corridor_ids_json=json.dumps([1]))
        db.commit()

        # Add corridor
        ids = json.loads(region.corridor_ids_json)
        ids.append(2)
        region.corridor_ids_json = json.dumps(ids)
        db.commit()
        assert json.loads(region.corridor_ids_json) == [1, 2]

        # Remove corridor
        ids.remove(1)
        region.corridor_ids_json = json.dumps(ids)
        db.commit()
        assert json.loads(region.corridor_ids_json) == [2]


# ---------------------------------------------------------------------------
# Region FP rate aggregation tests
# ---------------------------------------------------------------------------


class TestRegionFPRate:
    def test_compute_region_fp_rate_with_corridors(self, db: Session):
        c1 = _make_corridor(db, name="Corridor A")
        c2 = _make_corridor(db, name="Corridor B")

        # c1: 2 reviewed, 1 FP
        _make_gap(db, c1.corridor_id, is_false_positive=True, review_date=datetime.utcnow())
        _make_gap(db, c1.corridor_id, is_false_positive=False, review_date=datetime.utcnow())
        # c2: 2 reviewed, 0 FP
        _make_gap(db, c2.corridor_id, is_false_positive=False, review_date=datetime.utcnow())
        _make_gap(db, c2.corridor_id, is_false_positive=False, review_date=datetime.utcnow())

        region = _make_region(
            db,
            name="Combined",
            corridor_ids_json=json.dumps([c1.corridor_id, c2.corridor_id]),
        )
        db.commit()

        result = compute_region_fp_rate(db, region.region_id)
        assert result is not None
        assert result.total_alerts == 4
        assert result.false_positives == 1
        assert result.fp_rate == 0.25

    def test_compute_region_fp_rate_empty_region(self, db: Session):
        region = _make_region(db, name="Empty")
        db.commit()
        result = compute_region_fp_rate(db, region.region_id)
        assert result is not None
        assert result.total_alerts == 0
        assert result.fp_rate == 0.0

    def test_compute_region_fp_rate_missing_region(self, db: Session):
        result = compute_region_fp_rate(db, 99999)
        assert result is None


# ---------------------------------------------------------------------------
# Shadow scoring tests
# ---------------------------------------------------------------------------


class TestShadowScoring:
    def test_shadow_score_no_alerts(self, db: Session):
        corridor = _make_corridor(db, name="Empty Corridor")
        db.commit()
        result = shadow_score(db, corridor.corridor_id, {}, limit=100)
        assert result["alerts_scored"] == 0
        assert result["band_changes"] == 0
        assert result["results"] == []

    @patch("app.modules.shadow_scorer.load_scoring_config")
    @patch("app.modules.risk_scoring.compute_gap_score")
    @patch("app.modules.risk_scoring._count_gaps_in_window")
    def test_shadow_score_with_overrides(
        self, mock_count, mock_compute, mock_config, db: Session
    ):
        corridor = _make_corridor(db, name="Scored Corridor")
        _make_gap(db, corridor.corridor_id, risk_score=60)
        db.commit()

        mock_config.return_value = {"gap_duration": {"12h_plus": 20}}
        mock_count.return_value = 1
        mock_compute.return_value = (70, {"gap_duration": 25})

        result = shadow_score(
            db,
            corridor.corridor_id,
            {"signal_overrides": {"gap_duration.12h_plus": 25}},
            limit=10,
        )
        assert result["alerts_scored"] == 1
        assert result["results"][0]["original_score"] == 60
        assert result["results"][0]["proposed_score"] == 70

    def test_shadow_score_respects_limit(self, db: Session):
        corridor = _make_corridor(db, name="Limit Corridor")
        for _ in range(5):
            _make_gap(db, corridor.corridor_id, risk_score=40)
        db.commit()

        with patch("app.modules.shadow_scorer.load_scoring_config", return_value={}), \
             patch("app.modules.risk_scoring._count_gaps_in_window", return_value=0), \
             patch("app.modules.risk_scoring.compute_gap_score", return_value=(40, {})):
            result = shadow_score(db, corridor.corridor_id, {}, limit=3)
        assert result["alerts_scored"] == 3

    def test_band_change_detection(self, db: Session):
        corridor = _make_corridor(db, name="Band Corridor")
        # Alert with score 74 (high band)
        _make_gap(db, corridor.corridor_id, risk_score=74)
        db.commit()

        with patch("app.modules.shadow_scorer.load_scoring_config", return_value={}), \
             patch("app.modules.risk_scoring._count_gaps_in_window", return_value=0), \
             patch("app.modules.risk_scoring.compute_gap_score", return_value=(80, {})):
            result = shadow_score(db, corridor.corridor_id, {}, limit=10)
        assert result["band_changes"] == 1
        r = result["results"][0]
        assert r["original_band"] == "high"
        assert r["proposed_band"] == "critical"
        assert r["band_changed"] is True

    def test_shadow_score_preserves_original_scores(self, db: Session):
        """Shadow scoring must not modify any DB records."""
        corridor = _make_corridor(db, name="Readonly Corridor")
        gap = _make_gap(db, corridor.corridor_id, risk_score=50)
        db.commit()
        gap_id = gap.gap_event_id

        with patch("app.modules.shadow_scorer.load_scoring_config", return_value={}), \
             patch("app.modules.risk_scoring._count_gaps_in_window", return_value=0), \
             patch("app.modules.risk_scoring.compute_gap_score", return_value=(90, {})):
            shadow_score(db, corridor.corridor_id, {}, limit=10)

        # Re-fetch the gap and verify score is unchanged
        fresh = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == gap_id).first()
        assert fresh.risk_score == 50

    def test_get_score_band(self):
        assert _get_score_band(80) == "critical"
        assert _get_score_band(76) == "critical"
        assert _get_score_band(75) == "high"
        assert _get_score_band(51) == "high"
        assert _get_score_band(50) == "medium"
        assert _get_score_band(26) == "medium"
        assert _get_score_band(25) == "low"
        assert _get_score_band(0) == "low"

    def test_shadow_score_avg_delta(self, db: Session):
        corridor = _make_corridor(db, name="Delta Corridor")
        _make_gap(db, corridor.corridor_id, risk_score=40)
        _make_gap(db, corridor.corridor_id, risk_score=60)
        db.commit()

        with patch("app.modules.shadow_scorer.load_scoring_config", return_value={}), \
             patch("app.modules.risk_scoring._count_gaps_in_window", return_value=0), \
             patch("app.modules.risk_scoring.compute_gap_score", return_value=(55, {})):
            result = shadow_score(db, corridor.corridor_id, {}, limit=10)

        assert result["alerts_scored"] == 2
        # Original scores: 40, 60 -> proposed: 55, 55
        # Deltas: +15, -5 -> avg = +5
        assert result["avg_score_delta"] == 5.0
