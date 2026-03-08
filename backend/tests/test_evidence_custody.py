"""Tests for evidence chain-of-custody: export sets exported_by, approve/reject endpoints."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.auth import require_auth, require_senior_or_admin
from app.database import get_db
from app.main import app


def _make_auth_override(role="analyst", analyst_id=1, username="test_analyst"):
    """Return a dependency override function for require_auth or require_senior_or_admin."""

    def _override():
        return {"analyst_id": analyst_id, "username": username, "role": role}

    return _override


def _make_mock_card(**kwargs):
    card = MagicMock()
    defaults = {
        "evidence_card_id": 10,
        "gap_event_id": 1,
        "version": 1,
        "export_format": "json",
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
        "exported_by": None,
        "approved_by": None,
        "approved_at": None,
        "approval_status": "draft",
        "approval_notes": None,
    }
    for k, v in {**defaults, **kwargs}.items():
        setattr(card, k, v)
    return card


def _make_mock_gap(**kwargs):
    gap = MagicMock()
    defaults = {
        "gap_event_id": 1,
        "vessel_id": 1,
        "gap_start_utc": datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        "gap_end_utc": datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
        "duration_minutes": 720,
        "risk_score": 80,
        "risk_breakdown_json": {"H1": 30, "H2": 50},
        "status": "under_review",
        "corridor_id": None,
        "max_plausible_distance_nm": 100.0,
        "actual_gap_distance_nm": 50.0,
        "velocity_plausibility_ratio": 0.5,
        "impossible_speed_flag": False,
        "analyst_notes": "",
    }
    for k, v in {**defaults, **kwargs}.items():
        setattr(gap, k, v)
    return gap


@pytest.fixture
def mock_db():
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    return session


@pytest.fixture
def client_with_auth(mock_db):
    """TestClient with DB + auth overrides for analyst role."""

    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_auth] = _make_auth_override("analyst", 42, "jane")
    app.dependency_overrides[require_senior_or_admin] = _make_auth_override(
        "senior", 99, "senior_bob"
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client_viewer_only(mock_db):
    """TestClient where require_senior_or_admin is NOT overridden (should 403)."""

    def override_get_db():
        yield mock_db

    # Only override get_db and require_auth — leave require_senior_or_admin as-is
    # so that the real dependency runs and rejects non-senior tokens.
    app.dependency_overrides[get_db] = override_get_db
    # Don't override require_senior_or_admin — it will fail because there's no valid Bearer token
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


class TestExportSetsExportedBy:
    """Export endpoint sets exported_by from JWT auth."""

    def test_export_sets_exported_by(self, client_with_auth, mock_db):
        mock_gap = _make_mock_gap()
        mock_vessel = MagicMock()
        mock_vessel.mmsi = "123456789"
        mock_vessel.imo = "IMO1234567"
        mock_vessel.name = "TEST VESSEL"
        mock_vessel.flag = "PA"
        mock_vessel.vessel_type = "tanker"
        mock_vessel.ais_source = "aisstream"

        mock_card = _make_mock_card()

        def query_side_effect(model):
            q = MagicMock()
            model_name = getattr(model, "__name__", str(model))
            if model_name == "AISGapEvent":
                q.filter.return_value.first.return_value = mock_gap
            elif model_name == "Vessel":
                q.filter.return_value.first.return_value = mock_vessel
            elif model_name == "EvidenceCard":
                q.filter.return_value.order_by.return_value.first.return_value = mock_card
            elif model_name == "Corridor":
                q.filter.return_value.first.return_value = None
            elif model_name == "AISPoint":
                q.filter.return_value.order_by.return_value.first.return_value = None
            elif model_name == "SatelliteCheck":
                q.filter.return_value.order_by.return_value.first.return_value = None
            else:
                q.filter.return_value.first.return_value = None
                q.filter.return_value.order_by.return_value.first.return_value = None
            return q

        mock_db.query.side_effect = query_side_effect

        response = client_with_auth.post("/api/v1/alerts/1/export?format=json")
        assert response.status_code == 200
        # Verify the card was updated with exported_by from auth
        assert mock_card.exported_by == 42
        assert mock_card.approval_status == "draft"


class TestApproveEndpoint:
    """POST /evidence-cards/{card_id}/approve sets approved_by, approved_at, status."""

    def test_approve_sets_fields(self, client_with_auth, mock_db):
        mock_card = _make_mock_card(approval_status="draft")
        mock_db.query.return_value.filter.return_value.first.return_value = mock_card

        response = client_with_auth.post("/api/v1/evidence-cards/10/approve")
        assert response.status_code == 200
        data = response.json()
        assert data["approval_status"] == "approved"
        assert mock_card.approved_by == 99  # senior_bob's analyst_id
        assert mock_card.approved_at is not None
        assert mock_card.approval_status == "approved"

    def test_approve_404_not_found(self, client_with_auth, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        response = client_with_auth.post("/api/v1/evidence-cards/999/approve")
        assert response.status_code == 404

    def test_double_approve_returns_400(self, client_with_auth, mock_db):
        mock_card = _make_mock_card(approval_status="approved")
        mock_db.query.return_value.filter.return_value.first.return_value = mock_card

        response = client_with_auth.post("/api/v1/evidence-cards/10/approve")
        assert response.status_code == 400
        assert "Already approved" in response.json()["detail"]


class TestRejectEndpoint:
    """POST /evidence-cards/{card_id}/reject sets rejection notes."""

    def test_reject_sets_notes(self, client_with_auth, mock_db):
        mock_card = _make_mock_card(approval_status="draft")
        mock_db.query.return_value.filter.return_value.first.return_value = mock_card

        response = client_with_auth.post(
            "/api/v1/evidence-cards/10/reject",
            json={"notes": "Insufficient evidence for this claim"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["approval_status"] == "rejected"
        assert mock_card.approval_status == "rejected"
        assert mock_card.approval_notes == "Insufficient evidence for this claim"
        assert mock_card.approved_by == 99


class TestNonSeniorForbidden:
    """Non-senior/non-admin gets 403 (or 401/403) on approve."""

    def test_approve_without_auth_gets_error(self, client_viewer_only, mock_db):
        """Without a valid Bearer token, the real require_senior_or_admin raises 401/403."""
        response = client_viewer_only.post("/api/v1/evidence-cards/10/approve")
        # Should be 401 (no token) or 403 (wrong role)
        assert response.status_code in (401, 403)
