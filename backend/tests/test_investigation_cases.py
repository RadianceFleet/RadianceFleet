"""Tests for investigation cases — models, API, grouping, and edge cases."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.analyst import Analyst
from app.models.base import Base
from app.models.case_alert import CaseAlert
from app.models.gap_event import AISGapEvent
from app.models.investigation_case import InvestigationCase
from app.models.sts_transfer import StsTransferEvent
from app.models.vessel import Vessel
from app.modules.case_grouper import suggest_case_grouping
from app.schemas.cases import CaseCreate, CaseResponse, CaseSuggestion, CaseUpdate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """In-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def sample_vessel(db_session: Session):
    """Create a sample vessel."""
    v = Vessel(vessel_id=1, mmsi="123456789", name="TEST TANKER")
    db_session.add(v)
    db_session.commit()
    return v


@pytest.fixture
def sample_analyst(db_session: Session):
    """Create a sample analyst."""
    a = Analyst(
        analyst_id=1,
        username="alice",
        display_name="Alice Smith",
        password_hash="hashed",
        role="analyst",
        is_active=True,
    )
    db_session.add(a)
    db_session.commit()
    return a


@pytest.fixture
def sample_alerts(db_session: Session, sample_vessel):
    """Create sample AIS gap alerts."""
    now = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    alerts = []
    for i in range(5):
        a = AISGapEvent(
            gap_event_id=i + 1,
            vessel_id=1,
            gap_start_utc=now + timedelta(days=i),
            gap_end_utc=now + timedelta(days=i, hours=6),
            duration_minutes=360,
            risk_score=50,
            status="new",
            corridor_id=1 if i < 3 else 2,
        )
        db_session.add(a)
        alerts.append(a)
    db_session.commit()
    return alerts


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestInvestigationCaseModel:
    def test_create_case(self, db_session: Session):
        """Can create a basic investigation case."""
        case = InvestigationCase(
            title="Suspicious STS activity in Laconian Gulf",
            status="open",
            priority="high",
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        assert case.case_id is not None
        assert case.title == "Suspicious STS activity in Laconian Gulf"
        assert case.status == "open"
        assert case.priority == "high"
        assert case.created_at is not None

    def test_create_case_with_fk(self, db_session: Session, sample_vessel, sample_analyst):
        """Case with vessel_id and assigned_to references."""
        case = InvestigationCase(
            title="Case for vessel 1",
            status="open",
            priority="medium",
            vessel_id=1,
            assigned_to=1,
            created_by=1,
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        assert case.vessel_id == 1
        assert case.assigned_to == 1
        assert case.created_by == 1

    def test_create_case_alert_link(self, db_session: Session, sample_alerts):
        """Can create a case-alert junction record."""
        case = InvestigationCase(title="Test case", status="open", priority="medium")
        db_session.add(case)
        db_session.commit()

        link = CaseAlert(case_id=case.case_id, alert_id=1)
        db_session.add(link)
        db_session.commit()
        db_session.refresh(link)

        assert link.id is not None
        assert link.case_id == case.case_id
        assert link.alert_id == 1

    def test_case_alert_unique_constraint(self, db_session: Session, sample_alerts):
        """Duplicate case-alert link raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        case = InvestigationCase(title="Test case", status="open", priority="medium")
        db_session.add(case)
        db_session.commit()

        link1 = CaseAlert(case_id=case.case_id, alert_id=1)
        db_session.add(link1)
        db_session.commit()

        link2 = CaseAlert(case_id=case.case_id, alert_id=1)
        db_session.add(link2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_case_tags_json(self, db_session: Session):
        """Tags stored as JSON string."""
        tags = ["shadow-fleet", "sts-zone"]
        case = InvestigationCase(
            title="Tagged case",
            status="open",
            priority="low",
            tags_json=json.dumps(tags),
        )
        db_session.add(case)
        db_session.commit()
        db_session.refresh(case)

        parsed = json.loads(case.tags_json)
        assert parsed == ["shadow-fleet", "sts-zone"]


# ---------------------------------------------------------------------------
# API tests (using TestClient with mock overrides from conftest)
# ---------------------------------------------------------------------------


class TestCaseAPI:
    def test_create_case(self, api_client, mock_db):
        """POST /api/v1/cases creates a case."""
        mock_case = MagicMock()
        mock_case.case_id = 1
        mock_case.title = "New investigation"
        mock_case.description = None
        mock_case.status = "open"
        mock_case.priority = "medium"
        mock_case.assigned_to = None
        mock_case.created_by = 1
        mock_case.vessel_id = None
        mock_case.corridor_id = None
        mock_case.tags_json = None
        mock_case.created_at = datetime(2024, 1, 1)
        mock_case.updated_at = datetime(2024, 1, 1)

        mock_db.refresh = lambda obj: None
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()

        # After commit+refresh the route uses the case object directly
        original_add = mock_db.add

        def capture_add(obj):
            # Copy mock_case attributes to the added object won't work easily,
            # so we patch refresh to set attributes
            pass

        mock_db.add = capture_add

        def fake_refresh(obj):
            obj.case_id = 1
            obj.created_at = datetime(2024, 1, 1)
            obj.updated_at = datetime(2024, 1, 1)

        mock_db.refresh = fake_refresh

        resp = api_client.post(
            "/api/v1/cases",
            json={"title": "New investigation", "priority": "medium"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "New investigation"
        assert data["status"] == "open"
        assert data["case_id"] == 1

    def test_list_cases(self, api_client, mock_db):
        """GET /api/v1/cases returns list."""
        mock_case = MagicMock()
        mock_case.case_id = 1
        mock_case.title = "Test"
        mock_case.description = None
        mock_case.status = "open"
        mock_case.priority = "medium"
        mock_case.assigned_to = None
        mock_case.created_by = None
        mock_case.vessel_id = None
        mock_case.corridor_id = None
        mock_case.tags_json = None
        mock_case.created_at = datetime(2024, 1, 1)
        mock_case.updated_at = datetime(2024, 1, 1)

        # Without filters, the route calls db.query(IC).order_by(...).all()
        mock_db.query.return_value.order_by.return_value.all.return_value = [mock_case]
        mock_db.query.return_value.filter.return_value.count.return_value = 0

        resp = api_client.get("/api/v1/cases")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Test"

    def test_get_case_not_found(self, api_client, mock_db):
        """GET /api/v1/cases/999 returns 404."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/cases/999")
        assert resp.status_code == 404

    def test_update_case(self, api_client, mock_db):
        """PATCH /api/v1/cases/{id} updates fields."""
        mock_case = MagicMock()
        mock_case.case_id = 1
        mock_case.title = "Updated title"
        mock_case.description = "New desc"
        mock_case.status = "in_progress"
        mock_case.priority = "high"
        mock_case.assigned_to = None
        mock_case.created_by = None
        mock_case.vessel_id = None
        mock_case.corridor_id = None
        mock_case.tags_json = None
        mock_case.created_at = datetime(2024, 1, 1)
        mock_case.updated_at = datetime(2024, 1, 2)

        mock_db.query.return_value.filter.return_value.first.return_value = mock_case
        mock_db.query.return_value.filter.return_value.count.return_value = 2
        mock_db.refresh = lambda obj: None

        resp = api_client.patch(
            "/api/v1/cases/1",
            json={"title": "Updated title", "status": "in_progress"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated title"
        assert data["status"] == "in_progress"

    def test_add_alert_to_case_cap(self, api_client, mock_db):
        """POST /api/v1/cases/{id}/alerts respects 30-alert cap."""
        mock_case = MagicMock()
        mock_case.case_id = 1
        mock_alert = MagicMock()
        mock_alert.gap_event_id = 99

        # First call: filter for case, second: filter for alert, third: count, fourth: existing check
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_case,
            mock_alert,
            None,  # no existing link
        ]
        mock_db.query.return_value.filter.return_value.count.return_value = 30  # at cap

        resp = api_client.post(
            "/api/v1/cases/1/alerts",
            json={"alert_id": 99},
        )
        assert resp.status_code == 400
        assert "cap" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Case grouping tests
# ---------------------------------------------------------------------------


class TestCaseGrouper:
    def test_suggest_same_vessel(self, db_session: Session, sample_alerts):
        """Suggests alerts from same vessel within 7 days."""
        result = suggest_case_grouping(db_session, 1)
        # Alert 1 is vessel_id=1, should find alerts 2-5 (all within 7 days)
        alert_ids = {r["alert_id"] for r in result}
        assert 2 in alert_ids
        assert 3 in alert_ids

    def test_suggest_same_corridor(self, db_session: Session, sample_alerts):
        """Suggests alerts from same corridor within 48 hours."""
        # Alert 1 has corridor_id=1, alert 2 also corridor_id=1 and is 1 day away
        result = suggest_case_grouping(db_session, 1)
        found = [r for r in result if r["alert_id"] == 2]
        assert len(found) == 1
        # Should have boosted score from both vessel + corridor match
        assert "+same_corridor_48h" in found[0]["reason"]

    def test_suggest_no_matches(self, db_session: Session):
        """No suggestions when alert has no related alerts."""
        # Create isolated alert
        v = Vessel(vessel_id=99, mmsi="999999999", name="LONELY")
        db_session.add(v)
        db_session.commit()
        a = AISGapEvent(
            gap_event_id=100,
            vessel_id=99,
            gap_start_utc=datetime(2020, 1, 1, tzinfo=UTC),
            gap_end_utc=datetime(2020, 1, 1, 6, 0, tzinfo=UTC),
            duration_minutes=360,
            risk_score=30,
            status="new",
        )
        db_session.add(a)
        db_session.commit()

        result = suggest_case_grouping(db_session, 100)
        assert result == []

    def test_suggest_nonexistent_alert(self, db_session: Session):
        """Returns empty for nonexistent alert."""
        result = suggest_case_grouping(db_session, 999)
        assert result == []

    def test_suggest_excludes_critical(self, db_session: Session, sample_vessel):
        """Critical alerts (score >= 80) are excluded from suggestions."""
        now = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        a1 = AISGapEvent(
            gap_event_id=201,
            vessel_id=1,
            gap_start_utc=now,
            gap_end_utc=now + timedelta(hours=6),
            duration_minutes=360,
            risk_score=50,
            status="new",
        )
        a2 = AISGapEvent(
            gap_event_id=202,
            vessel_id=1,
            gap_start_utc=now + timedelta(days=1),
            gap_end_utc=now + timedelta(days=1, hours=6),
            duration_minutes=360,
            risk_score=90,  # critical
            status="new",
        )
        db_session.add_all([a1, a2])
        db_session.commit()

        result = suggest_case_grouping(db_session, 201)
        alert_ids = {r["alert_id"] for r in result}
        assert 202 not in alert_ids

    def test_suggest_sts_partner(self, db_session: Session, sample_vessel):
        """Suggests alerts from STS partner vessels."""
        v2 = Vessel(vessel_id=2, mmsi="987654321", name="PARTNER")
        db_session.add(v2)
        db_session.commit()

        now = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
        a1 = AISGapEvent(
            gap_event_id=301, vessel_id=1,
            gap_start_utc=now, gap_end_utc=now + timedelta(hours=6),
            duration_minutes=360, risk_score=50, status="new",
        )
        a2 = AISGapEvent(
            gap_event_id=302, vessel_id=2,
            gap_start_utc=now + timedelta(days=30),  # outside 7-day vessel window
            gap_end_utc=now + timedelta(days=30, hours=6),
            duration_minutes=360, risk_score=40, status="new",
        )
        db_session.add_all([a1, a2])
        db_session.commit()

        sts = StsTransferEvent(
            sts_id=1, vessel_1_id=1, vessel_2_id=2,
            detection_type="visible_visible",
            start_time_utc=now, end_time_utc=now + timedelta(hours=2),
            risk_score_component=10,
        )
        db_session.add(sts)
        db_session.commit()

        result = suggest_case_grouping(db_session, 301)
        alert_ids = {r["alert_id"] for r in result}
        assert 302 in alert_ids


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestCaseSchemas:
    def test_case_create_defaults(self):
        """CaseCreate has sensible defaults."""
        c = CaseCreate(title="Test case")
        assert c.priority == "medium"
        assert c.tags is None
        assert c.vessel_id is None

    def test_case_update_partial(self):
        """CaseUpdate allows partial updates."""
        u = CaseUpdate(status="closed")
        assert u.status == "closed"
        assert u.title is None

    def test_case_response_model(self):
        """CaseResponse validates correctly."""
        r = CaseResponse(
            case_id=1,
            title="Test",
            description=None,
            status="open",
            priority="medium",
            assigned_to=None,
            assigned_to_username=None,
            created_by=None,
            vessel_id=None,
            corridor_id=None,
            tags=["foo"],
            alert_count=3,
            created_at=datetime(2024, 1, 1),
            updated_at=datetime(2024, 1, 1),
        )
        assert r.alert_count == 3
        assert r.tags == ["foo"]

    def test_case_suggestion_model(self):
        """CaseSuggestion serializes related alerts."""
        s = CaseSuggestion(
            alert_id=1,
            related_alerts=[
                {"alert_id": 2, "reason": "same_vessel_7d", "score": 80},
            ],
        )
        assert len(s.related_alerts) == 1
        assert s.related_alerts[0].score == 80
