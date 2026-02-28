"""Tests for Phase C15-D19: ownership verification, paid provider stubs."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """MagicMock database session for test isolation."""
    session = MagicMock()
    # Default: query().filter().first() returns None (not found)
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    return session


@pytest.fixture
def api_client(mock_db):
    """TestClient with DB dependency overridden."""
    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ── VesselOwner Model Tests ──────────────────────────────────────────────────

def test_vessel_owner_new_fields():
    """VesselOwner model accepts verification fields (Phase C15-16)."""
    from app.models.vessel_owner import VesselOwner

    owner = VesselOwner(
        vessel_id=1,
        owner_name="Test Shipping Co",
        verified_by="analyst@example.com",
        verified_at=datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc),
        source_url="https://equasis.org/search?imo=1234567",
        verification_notes="Ownership confirmed via Equasis public records.",
    )

    assert owner.owner_name == "Test Shipping Co"
    assert owner.verified_by == "analyst@example.com"
    assert owner.verified_at == datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc)
    assert owner.source_url == "https://equasis.org/search?imo=1234567"
    assert owner.verification_notes == "Ownership confirmed via Equasis public records."


def test_vessel_owner_backwards_compatible():
    """VesselOwner creation works without new fields (backwards compatible)."""
    from app.models.vessel_owner import VesselOwner

    owner = VesselOwner(
        vessel_id=2,
        owner_name="Legacy Shipping Ltd",
        is_sanctioned=False,
    )

    assert owner.owner_name == "Legacy Shipping Ltd"
    assert owner.is_sanctioned is False
    # New fields default to None
    assert owner.verified_by is None
    assert owner.verified_at is None
    assert owner.source_url is None
    assert owner.verification_notes is None


# ── VerificationLog Model Tests ──────────────────────────────────────────────

def test_verification_log_model():
    """VerificationLog model creation with all fields."""
    from app.models.verification_log import VerificationLog

    log = VerificationLog(
        vessel_id=1,
        provider="skylight",
        response_status="success",
        cost_usd=0.0,
        result_summary="Test result",
    )

    assert log.vessel_id == 1
    assert log.provider == "skylight"
    assert log.response_status == "success"
    assert log.cost_usd == 0.0
    assert log.result_summary == "Test result"


# ── API: PATCH vessel owner ──────────────────────────────────────────────────

def test_patch_vessel_owner(api_client, mock_db):
    """PATCH /vessels/{id}/owner updates an existing owner record."""
    from app.models.vessel import Vessel
    from app.models.vessel_owner import VesselOwner

    # Setup: vessel exists, owner exists
    mock_vessel = MagicMock(spec=Vessel)
    mock_vessel.vessel_id = 1
    mock_vessel.mmsi = "123456789"

    mock_owner = MagicMock(spec=VesselOwner)
    mock_owner.vessel_id = 1
    mock_owner.owner_name = "Old Owner"
    mock_owner.is_sanctioned = False
    mock_owner.source_url = None
    mock_owner.verification_notes = None
    mock_owner.verified_by = None
    mock_owner.verified_at = None

    # First call (Vessel query) returns vessel, second call (VesselOwner query) returns owner
    def side_effect_query(model):
        q = MagicMock()
        if model is Vessel:
            q.filter.return_value.first.return_value = mock_vessel
        elif model is VesselOwner:
            q.filter.return_value.first.return_value = mock_owner
        else:
            q.filter.return_value.first.return_value = None
        return q
    mock_db.query.side_effect = side_effect_query

    response = api_client.patch(
        "/api/v1/vessels/1/owner",
        json={
            "owner_name": "New Owner Corp",
            "is_sanctioned": True,
            "source_url": "https://equasis.org/test",
            "notes": "Verified by public records",
            "verified_by": "analyst@example.com",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "updated"
    assert data["vessel_id"] == 1
    # Verify owner was mutated
    assert mock_owner.owner_name == "New Owner Corp"
    assert mock_owner.is_sanctioned is True
    assert mock_owner.source_url == "https://equasis.org/test"
    assert mock_owner.verification_notes == "Verified by public records"
    assert mock_owner.verified_by == "analyst@example.com"
    mock_db.commit.assert_called()


def test_patch_vessel_owner_creates_new(api_client, mock_db):
    """PATCH /vessels/{id}/owner creates a new record if none exists."""
    from app.models.vessel import Vessel
    from app.models.vessel_owner import VesselOwner

    mock_vessel = MagicMock(spec=Vessel)
    mock_vessel.vessel_id = 42
    mock_vessel.mmsi = "999999999"

    def side_effect_query(model):
        q = MagicMock()
        if model is Vessel:
            q.filter.return_value.first.return_value = mock_vessel
        elif model is VesselOwner:
            q.filter.return_value.first.return_value = None  # No existing owner
        else:
            q.filter.return_value.first.return_value = None
        return q
    mock_db.query.side_effect = side_effect_query

    response = api_client.patch(
        "/api/v1/vessels/42/owner",
        json={"owner_name": "New Shipping LLC", "verified_by": "test@test.com"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "updated"
    assert data["vessel_id"] == 42
    # Verify new owner was added to session
    mock_db.add.assert_called()
    mock_db.commit.assert_called()


def test_patch_vessel_owner_not_found(api_client, mock_db):
    """PATCH /vessels/{id}/owner returns 404 when vessel missing."""
    response = api_client.patch(
        "/api/v1/vessels/99999/owner",
        json={"owner_name": "Nobody"},
    )
    assert response.status_code == 404


# ── API: POST verify vessel ──────────────────────────────────────────────────

def test_verify_vessel_no_api_key(api_client, mock_db):
    """POST /vessels/{id}/verify returns graceful error when provider not configured."""
    from app.models.vessel import Vessel

    mock_vessel = MagicMock(spec=Vessel)
    mock_vessel.vessel_id = 1
    mock_vessel.mmsi = "123456789"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel

    response = api_client.post("/api/v1/vessels/1/verify?provider=skylight")

    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "skylight"
    assert data["success"] is False
    assert "SKYLIGHT_API_KEY" in data["error"]


def test_verify_vessel_unknown_provider(api_client, mock_db):
    """POST /vessels/{id}/verify returns error for unknown provider."""
    from app.models.vessel import Vessel

    mock_vessel = MagicMock(spec=Vessel)
    mock_vessel.vessel_id = 1
    mock_vessel.mmsi = "123456789"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel

    response = api_client.post("/api/v1/vessels/1/verify?provider=nonexistent")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Unknown provider" in data["error"]


def test_verify_vessel_not_found(api_client, mock_db):
    """POST /vessels/{id}/verify returns error when vessel not found."""
    response = api_client.post("/api/v1/vessels/99999/verify?provider=skylight")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "Vessel not found" in data["error"]


# ── API: GET verification budget ─────────────────────────────────────────────

def test_verification_budget(api_client, mock_db):
    """GET /verification/budget returns expected structure."""
    # Mock the scalar query for monthly spend (single .filter() with two conditions)
    mock_db.query.return_value.filter.return_value.scalar.return_value = None

    response = api_client.get("/api/v1/verification/budget")

    assert response.status_code == 200
    data = response.json()
    assert "monthly_budget_usd" in data
    assert "spent_usd" in data
    assert "remaining_usd" in data
    assert "providers" in data
    assert isinstance(data["providers"], list)
    assert "skylight" in data["providers"]
    assert "spire" in data["providers"]
    assert "seaweb" in data["providers"]
    assert data["spent_usd"] == 0.0
    assert data["remaining_usd"] == data["monthly_budget_usd"]


# ── Paid verification unit tests ─────────────────────────────────────────────

def test_budget_check():
    """Verify budget_exceeded response when budget is low."""
    from app.modules.paid_verification import verify_vessel, VerificationResult

    db = MagicMock()
    # Vessel exists
    mock_vessel = MagicMock()
    mock_vessel.vessel_id = 1
    mock_vessel.mmsi = "123456789"
    db.query.return_value.filter.return_value.first.return_value = mock_vessel

    # Monthly spend already at limit (single .filter() with two conditions)
    db.query.return_value.filter.return_value.scalar.return_value = 499.90

    with patch("app.modules.paid_verification.settings") as mock_settings:
        mock_settings.VERIFICATION_MONTHLY_BUDGET_USD = 500.0
        mock_settings.SPIRE_API_KEY = "test-key"
        result = verify_vessel(db, 1, provider_name="spire")

    assert result.success is False
    assert "budget exceeded" in result.error.lower()
    # Verify a log was created (flushed, not committed -- caller manages commit)
    db.add.assert_called()
    db.flush.assert_called()


def test_provider_stub_returns_error():
    """All provider stubs return descriptive errors when not configured."""
    from app.modules.paid_verification import _PROVIDERS

    mock_vessel = MagicMock()
    mock_vessel.mmsi = "123456789"

    for name, provider in _PROVIDERS.items():
        with patch("app.modules.paid_verification.settings") as mock_settings:
            mock_settings.SKYLIGHT_API_KEY = ""
            mock_settings.SPIRE_API_KEY = ""
            mock_settings.SEAWEB_API_KEY = ""
            result = provider.verify_vessel(mock_vessel)

        assert result.success is False
        assert result.error is not None
        assert len(result.error) > 10, f"Provider {name} error too short: {result.error}"


def test_verification_log_created():
    """Verify VerificationLog record is created after a verification call."""
    from app.modules.paid_verification import verify_vessel

    db = MagicMock()
    mock_vessel = MagicMock()
    mock_vessel.vessel_id = 1
    mock_vessel.mmsi = "123456789"
    db.query.return_value.filter.return_value.first.return_value = mock_vessel

    # No spend yet
    db.query.return_value.filter.return_value.scalar.return_value = 0.0

    with patch("app.modules.paid_verification.settings") as mock_settings:
        mock_settings.VERIFICATION_MONTHLY_BUDGET_USD = 500.0
        mock_settings.SKYLIGHT_API_KEY = ""
        result = verify_vessel(db, 1, provider_name="skylight")

    # Log should have been added
    db.add.assert_called()
    added_obj = db.add.call_args[0][0]
    from app.models.verification_log import VerificationLog
    assert isinstance(added_obj, VerificationLog)
    assert added_obj.vessel_id == 1
    assert added_obj.provider == "skylight"
    assert added_obj.response_status == "error"  # stub returns error
    db.flush.assert_called()  # flushed, not committed -- caller manages commit


def test_unknown_provider():
    """Verify error for invalid provider name."""
    from app.modules.paid_verification import verify_vessel

    db = MagicMock()
    mock_vessel = MagicMock()
    mock_vessel.vessel_id = 1
    db.query.return_value.filter.return_value.first.return_value = mock_vessel

    result = verify_vessel(db, 1, provider_name="totally_fake")

    assert result.success is False
    assert "Unknown provider" in result.error
    assert "totally_fake" in result.error
    # No log created for unknown provider
    db.add.assert_not_called()


# ── Settings tests ────────────────────────────────────────────────────────────

def test_settings_has_verification_keys():
    """Settings class includes paid verification config fields."""
    from app.config import Settings

    s = Settings(DATABASE_URL="sqlite:///test.db")
    assert hasattr(s, "SKYLIGHT_API_KEY")
    assert hasattr(s, "SPIRE_API_KEY")
    assert hasattr(s, "SEAWEB_API_KEY")
    assert hasattr(s, "VERIFICATION_MONTHLY_BUDGET_USD")
    assert s.VERIFICATION_MONTHLY_BUDGET_USD == 500.0
    assert s.SKYLIGHT_API_KEY == ""
    assert s.SPIRE_API_KEY == ""
    assert s.SEAWEB_API_KEY == ""


# ── VerificationResult dataclass tests ────────────────────────────────────────

def test_verification_result_defaults():
    """VerificationResult has sensible defaults."""
    from app.modules.paid_verification import VerificationResult

    result = VerificationResult(provider="test", success=True)
    assert result.data == {}
    assert result.cost_usd == 0.0
    assert result.error is None


def test_verification_result_with_data():
    """VerificationResult can store arbitrary data."""
    from app.modules.paid_verification import VerificationResult

    result = VerificationResult(
        provider="spire",
        success=True,
        data={"position": {"lat": 55.0, "lon": 25.0}, "timestamp": "2026-02-27T12:00:00Z"},
        cost_usd=0.50,
    )
    assert result.data["position"]["lat"] == 55.0
    assert result.cost_usd == 0.50


# ── get_budget_status tests ──────────────────────────────────────────────────

def test_get_budget_status_no_spend():
    """get_budget_status returns full remaining when no spend."""
    from app.modules.paid_verification import get_budget_status

    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = None

    with patch("app.modules.paid_verification.settings") as mock_settings:
        mock_settings.VERIFICATION_MONTHLY_BUDGET_USD = 500.0
        status = get_budget_status(db)

    assert status["monthly_budget_usd"] == 500.0
    assert status["spent_usd"] == 0.0
    assert status["remaining_usd"] == 500.0


def test_get_budget_status_partial_spend():
    """get_budget_status shows remaining after partial spend."""
    from app.modules.paid_verification import get_budget_status

    db = MagicMock()
    db.query.return_value.filter.return_value.scalar.return_value = 123.45

    with patch("app.modules.paid_verification.settings") as mock_settings:
        mock_settings.VERIFICATION_MONTHLY_BUDGET_USD = 500.0
        status = get_budget_status(db)

    assert status["spent_usd"] == 123.45
    assert status["remaining_usd"] == 376.55


# ── Provider instance tests ──────────────────────────────────────────────────

def test_provider_names():
    """Each provider returns the correct name."""
    from app.modules.paid_verification import SkylightProvider, SpireProvider, SeaWebProvider

    assert SkylightProvider().name() == "skylight"
    assert SpireProvider().name() == "spire"
    assert SeaWebProvider().name() == "seaweb"


def test_provider_costs():
    """Each provider returns the expected estimated cost."""
    from app.modules.paid_verification import SkylightProvider, SpireProvider, SeaWebProvider

    assert SkylightProvider().estimated_cost() == 0.0  # Free for NGOs
    assert SpireProvider().estimated_cost() == 0.50
    assert SeaWebProvider().estimated_cost() == 2.00
