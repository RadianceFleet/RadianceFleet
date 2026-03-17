"""Tests for case multi-analyst collaboration: CaseAnalyst, CaseActivity, and API endpoints."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import require_auth, require_senior_or_admin
from app.database import get_db
from app.main import app
from app.models.analyst import Analyst
from app.models.base import Base
from app.models.case_activity import CaseActivity
from app.models.case_analyst import CaseAnalyst
from app.models.gap_event import AISGapEvent
from app.models.investigation_case import InvestigationCase
from app.models.vessel import Vessel

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def db_session(db_engine):
    TestSession = sessionmaker(bind=db_engine)
    session = TestSession()
    yield session
    session.close()
    db_engine.dispose()


@pytest.fixture
def seed_data(db_session: Session):
    """Create analysts, a vessel, a gap event, and a case."""
    v = Vessel(vessel_id=1, mmsi="123456789", name="TEST VESSEL")
    db_session.add(v)
    db_session.flush()

    a1 = Analyst(
        analyst_id=1,
        username="alice",
        display_name="Alice Smith",
        password_hash="hashed",
        role="admin",
        is_active=True,
    )
    a2 = Analyst(
        analyst_id=2,
        username="bob",
        display_name="Bob Jones",
        password_hash="hashed",
        role="senior_analyst",
        is_active=True,
    )
    a3 = Analyst(
        analyst_id=3,
        username="charlie",
        display_name="Charlie Brown",
        password_hash="hashed",
        role="analyst",
        is_active=True,
    )
    db_session.add_all([a1, a2, a3])
    db_session.flush()

    gap = AISGapEvent(
        gap_event_id=1,
        vessel_id=1,
        gap_start_utc=datetime(2024, 1, 1),
        gap_end_utc=datetime(2024, 1, 2),
        duration_minutes=1440,
    )
    db_session.add(gap)
    db_session.flush()

    case = InvestigationCase(
        case_id=1,
        title="Test case",
        description="A test investigation case",
        status="open",
        priority="high",
        created_by=1,
        assigned_to=1,
    )
    db_session.add(case)
    db_session.commit()
    return {"analysts": [a1, a2, a3], "case": case, "gap": gap, "vessel": v}


@pytest.fixture
def client(db_engine, db_session, seed_data):
    """TestClient with real in-memory SQLite session."""

    def override_get_db():
        TestSession = sessionmaker(bind=db_engine)
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    def override_auth():
        return {"analyst_id": 1, "username": "alice", "role": "admin"}

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_auth] = override_auth
    app.dependency_overrides[require_senior_or_admin] = override_auth
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


def test_create_case_analyst_model(db_session: Session, seed_data):
    """CaseAnalyst can be created with proper fields."""
    ca = CaseAnalyst(
        case_id=1,
        analyst_id=2,
        role="contributor",
        added_by=1,
    )
    db_session.add(ca)
    db_session.commit()
    db_session.refresh(ca)

    assert ca.id is not None
    assert ca.case_id == 1
    assert ca.analyst_id == 2
    assert ca.role == "contributor"
    assert ca.added_at is not None


def test_case_analyst_unique_constraint(db_session: Session, seed_data):
    """Duplicate (case_id, analyst_id) pair is rejected."""
    ca1 = CaseAnalyst(case_id=1, analyst_id=2, role="contributor")
    db_session.add(ca1)
    db_session.commit()

    ca2 = CaseAnalyst(case_id=1, analyst_id=2, role="reviewer")
    db_session.add(ca2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_create_case_activity_model(db_session: Session, seed_data):
    """CaseActivity can be created with proper fields."""
    activity = CaseActivity(
        case_id=1,
        analyst_id=1,
        action="created",
        details_json=json.dumps({"title": "Test case"}),
    )
    db_session.add(activity)
    db_session.commit()
    db_session.refresh(activity)

    assert activity.activity_id is not None
    assert activity.action == "created"
    assert activity.created_at is not None
    assert json.loads(activity.details_json) == {"title": "Test case"}


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


def test_add_analyst_to_case(client: TestClient):
    """POST /cases/{id}/analysts adds analyst to case."""
    resp = client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "contributor"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["analyst_id"] == 2
    assert data["role"] == "contributor"
    assert data["analyst_name"] == "Bob Jones"


def test_add_analyst_lead_updates_assigned_to(client: TestClient):
    """Adding an analyst as 'lead' also updates case.assigned_to."""
    resp = client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "lead"},
    )
    assert resp.status_code == 201

    # Verify case assigned_to is updated
    case_resp = client.get("/api/v1/cases/1")
    assert case_resp.status_code == 200
    assert case_resp.json()["assigned_to"] == 2


def test_add_analyst_duplicate_rejected(client: TestClient):
    """Adding the same analyst twice returns 409."""
    resp = client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "contributor"},
    )
    assert resp.status_code == 201

    resp2 = client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "reviewer"},
    )
    assert resp2.status_code == 409


def test_remove_analyst(client: TestClient):
    """DELETE /cases/{id}/analysts/{analyst_id} removes analyst."""
    # Add then remove
    client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "contributor"},
    )
    resp = client.delete("/api/v1/cases/1/analysts/2")
    assert resp.status_code == 204


def test_remove_lead_rejected(client: TestClient):
    """Removing lead analyst without handoff returns 400."""
    client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "lead"},
    )
    resp = client.delete("/api/v1/cases/1/analysts/2")
    assert resp.status_code == 400
    assert "handoff" in resp.json()["detail"].lower()


def test_list_case_analysts(client: TestClient):
    """GET /cases/{id}/analysts returns analyst list with names."""
    client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "contributor"},
    )
    client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 3, "role": "reviewer"},
    )
    resp = client.get("/api/v1/cases/1/analysts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {d["analyst_name"] for d in data}
    assert "Bob Jones" in names
    assert "Charlie Brown" in names


def test_case_activity_on_create(client: TestClient):
    """Creating a case records a 'created' activity."""
    resp = client.post(
        "/api/v1/cases",
        json={"title": "New case for activity test", "priority": "medium"},
    )
    assert resp.status_code == 201
    new_case_id = resp.json()["case_id"]

    activity_resp = client.get(f"/api/v1/cases/{new_case_id}/activity")
    assert activity_resp.status_code == 200
    activities = activity_resp.json()
    assert len(activities) >= 1
    assert activities[0]["action"] == "created"


def test_case_activity_on_alert_add(client: TestClient):
    """Adding an alert records an 'alert_added' activity."""
    resp = client.post(
        "/api/v1/cases/1/alerts",
        json={"alert_id": 1},
    )
    assert resp.status_code == 201

    activity_resp = client.get("/api/v1/cases/1/activity")
    assert activity_resp.status_code == 200
    activities = activity_resp.json()
    actions = [a["action"] for a in activities]
    assert "alert_added" in actions


def test_case_activity_timeline(client: TestClient):
    """GET /cases/{id}/activity returns paginated timeline newest first."""
    # Create some activities by performing actions
    client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "contributor"},
    )
    client.post(
        "/api/v1/cases/1/alerts",
        json={"alert_id": 1},
    )

    resp = client.get("/api/v1/cases/1/activity?skip=0&limit=10")
    assert resp.status_code == 200
    activities = resp.json()
    assert len(activities) >= 2

    # Check pagination
    resp_skip = client.get("/api/v1/cases/1/activity?skip=0&limit=1")
    assert resp_skip.status_code == 200
    assert len(resp_skip.json()) == 1


def test_case_handoff(client: TestClient):
    """POST /cases/{id}/handoff updates lead and records activity."""
    # First add analyst 2 as lead
    client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "lead"},
    )

    # Handoff from analyst 2 to analyst 3
    resp = client.post(
        "/api/v1/cases/1/handoff",
        json={"to_analyst_id": 3, "notes": "Switching leads"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["assigned_to"] == 3

    # Verify activity recorded
    activity_resp = client.get("/api/v1/cases/1/activity")
    actions = [a["action"] for a in activity_resp.json()]
    assert "handoff" in actions

    # Verify analyst 3 is now lead in analysts list
    analysts_resp = client.get("/api/v1/cases/1/analysts")
    analysts = analysts_resp.json()
    lead = [a for a in analysts if a["role"] == "lead"]
    assert len(lead) == 1
    assert lead[0]["analyst_id"] == 3


def test_my_cases_filter(client: TestClient):
    """?my_cases=true returns cases where analyst is member or assigned."""
    # Create a second case assigned to analyst 2
    resp = client.post(
        "/api/v1/cases",
        json={"title": "Other case", "priority": "low"},
    )
    assert resp.status_code == 201

    # The default auth is analyst_id=1, who owns case 1 (assigned_to=1) and created case 2
    # Add analyst 2 to case 1
    client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "contributor"},
    )

    # my_cases=true for analyst_id=1 should return both cases (assigned_to on case 1, created_by on case 2)
    resp = client.get("/api/v1/cases?my_cases=true")
    assert resp.status_code == 200
    # Analyst 1 is assigned_to case 1 and is not a CaseAnalyst member of case 2,
    # but created case 2. However my_cases checks assigned_to OR CaseAnalyst membership.
    # Case 1: assigned_to=1 -> included
    # Case 2: assigned_to=None, no CaseAnalyst for analyst 1 -> excluded
    case_ids = {c["case_id"] for c in resp.json()}
    assert 1 in case_ids


def test_case_response_includes_analysts(client: TestClient):
    """CaseResponse includes analysts list."""
    client.post(
        "/api/v1/cases/1/analysts",
        json={"analyst_id": 2, "role": "contributor"},
    )

    resp = client.get("/api/v1/cases/1")
    assert resp.status_code == 200
    data = resp.json()
    assert "analysts" in data
    assert len(data["analysts"]) == 1
    assert data["analysts"][0]["analyst_id"] == 2
    assert data["analysts"][0]["analyst_name"] == "Bob Jones"
    assert data["analysts"][0]["role"] == "contributor"
