"""Tests for multi-analyst workflow: auth, CRUD, JWT, and auth propagation."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _db_engine():
    """Shared in-memory SQLite engine (StaticPool for cross-thread access)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def _real_db(_db_engine):
    """Session bound to the shared in-memory engine."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=_db_engine)
    db = Session()
    yield db
    db.close()


@pytest.fixture
def real_client(_db_engine, _real_db):
    """TestClient wired to an in-memory SQLite DB."""
    from sqlalchemy.orm import sessionmaker

    from app.database import get_db
    from app.main import app

    Session = sessionmaker(bind=_db_engine)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    with patch("app.database.init_db"), TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _admin_token():
    """Create a valid admin JWT for testing."""
    from app.auth import create_token

    return create_token(0, "admin", "admin")


def _analyst_token(analyst_id=1, username="analyst1", role="analyst"):
    from app.auth import create_token

    return create_token(analyst_id, username, role)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_and_verify(self):
        from app.auth import hash_password, verify_password

        h = hash_password("secret123")
        assert h != "secret123"
        assert verify_password("secret123", h) is True
        assert verify_password("wrong", h) is False

    def test_verify_admin_password(self):
        from app.auth import verify_admin_password

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_PASSWORD = "admin-pass"
            assert verify_admin_password("admin-pass") is True
            assert verify_admin_password("wrong") is False


# ---------------------------------------------------------------------------
# JWT creation and validation
# ---------------------------------------------------------------------------


class TestJWT:
    def test_create_token_contains_fields(self):
        import jwt as pyjwt

        from app.auth import create_token

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = create_token(42, "alice", "senior_analyst")
            payload = pyjwt.decode(token, "test-secret", algorithms=["HS256"])
            assert payload["sub"] == "alice"
            assert payload["analyst_id"] == 42
            assert payload["role"] == "senior_analyst"
            assert payload["type"] == "access"

    def test_create_admin_token_backward_compat(self):
        import jwt as pyjwt

        from app.auth import create_admin_token

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = create_admin_token()
            payload = pyjwt.decode(token, "test-secret", algorithms=["HS256"])
            assert payload["sub"] == "admin"
            assert payload["analyst_id"] == 0
            assert payload["role"] == "admin"

    def test_require_auth_returns_identity(self):
        from fastapi.security import HTTPAuthorizationCredentials

        from app.auth import create_token, require_auth

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = create_token(5, "bob", "analyst")
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            result = require_auth(creds)
            assert result["analyst_id"] == 5
            assert result["username"] == "bob"
            assert result["role"] == "analyst"

    def test_require_auth_legacy_token(self):
        """Legacy tokens (sub=admin, no analyst_id) should still work."""
        from datetime import timedelta

        import jwt as pyjwt
        from fastapi.security import HTTPAuthorizationCredentials

        from app.auth import require_auth

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            payload = {
                "exp": datetime.now(UTC) + timedelta(minutes=30),
                "sub": "admin",
                "type": "access",
            }
            token = pyjwt.encode(payload, "test-secret", algorithm="HS256")
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            result = require_auth(creds)
            assert result["analyst_id"] == 0
            assert result["username"] == "admin"
            assert result["role"] == "admin"

    def test_require_admin_rejects_analyst(self):
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from app.auth import create_token, require_admin

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = create_token(1, "analyst1", "analyst")
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            with pytest.raises(HTTPException) as exc_info:
                require_admin(creds)
            assert exc_info.value.status_code == 403

    def test_require_senior_or_admin_accepts_senior(self):
        from fastapi.security import HTTPAuthorizationCredentials

        from app.auth import create_token, require_senior_or_admin

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = create_token(2, "senior1", "senior_analyst")
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            result = require_senior_or_admin(creds)
            assert result["role"] == "senior_analyst"

    def test_require_senior_or_admin_rejects_analyst(self):
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from app.auth import create_token, require_senior_or_admin

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = create_token(1, "analyst1", "analyst")
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            with pytest.raises(HTTPException) as exc_info:
                require_senior_or_admin(creds)
            assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Admin seeding
# ---------------------------------------------------------------------------


class TestAdminSeeding:
    def test_seed_creates_admin_when_table_empty(self, _real_db):
        from app.auth import verify_password
        from app.models.analyst import Analyst

        with patch("app.database.settings") as mock_settings:
            mock_settings.ADMIN_PASSWORD = "seed-pass-123"
            # Import and call seed function
            from sqlalchemy.orm import sessionmaker

            from app.database import _seed_admin_user

            Session = sessionmaker(bind=_real_db.get_bind())
            _seed_admin_user(Session)

        admin = _real_db.query(Analyst).filter(Analyst.username == "admin").first()
        assert admin is not None
        assert admin.display_name == "Administrator"
        assert admin.role in ("admin", "AnalystRoleEnum.ADMIN")
        assert verify_password("seed-pass-123", admin.password_hash)

    def test_seed_skips_when_no_password(self, _real_db):
        from app.models.analyst import Analyst

        with patch("app.database.settings") as mock_settings:
            mock_settings.ADMIN_PASSWORD = None
            from sqlalchemy.orm import sessionmaker

            from app.database import _seed_admin_user

            Session = sessionmaker(bind=_real_db.get_bind())
            _seed_admin_user(Session)
        assert _real_db.query(Analyst).count() == 0


# ---------------------------------------------------------------------------
# Analyst CRUD via API
# ---------------------------------------------------------------------------


class TestAnalystCRUD:
    def test_create_analyst(self, real_client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = _admin_token()
            resp = real_client.post(
                "/api/v1/admin/analysts",
                json={"username": "newuser", "password": "pass123", "display_name": "New User"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["username"] == "newuser"
            assert data["role"] == "analyst"
            assert data["is_active"] is True

    def test_create_duplicate_username(self, real_client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = _admin_token()
            real_client.post(
                "/api/v1/admin/analysts",
                json={"username": "dup", "password": "pass123"},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp = real_client.post(
                "/api/v1/admin/analysts",
                json={"username": "dup", "password": "pass456"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 409

    def test_list_analysts(self, real_client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = _admin_token()
            real_client.post(
                "/api/v1/admin/analysts",
                json={"username": "listuser", "password": "pass"},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp = real_client.get(
                "/api/v1/admin/analysts",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert any(a["username"] == "listuser" for a in data)

    def test_update_analyst(self, real_client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = _admin_token()
            create_resp = real_client.post(
                "/api/v1/admin/analysts",
                json={"username": "toupdate", "password": "pass"},
                headers={"Authorization": f"Bearer {token}"},
            )
            aid = create_resp.json()["analyst_id"]
            resp = real_client.patch(
                f"/api/v1/admin/analysts/{aid}",
                json={"role": "senior_analyst", "display_name": "Updated"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "updated"

    def test_deactivate_analyst(self, real_client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = _admin_token()
            create_resp = real_client.post(
                "/api/v1/admin/analysts",
                json={"username": "deact", "password": "pass"},
                headers={"Authorization": f"Bearer {token}"},
            )
            aid = create_resp.json()["analyst_id"]
            resp = real_client.patch(
                f"/api/v1/admin/analysts/{aid}",
                json={"is_active": False},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200

    def test_reset_password(self, real_client):
        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            token = _admin_token()
            create_resp = real_client.post(
                "/api/v1/admin/analysts",
                json={"username": "resetme", "password": "old"},
                headers={"Authorization": f"Bearer {token}"},
            )
            aid = create_resp.json()["analyst_id"]
            resp = real_client.post(
                f"/api/v1/admin/analysts/{aid}/reset-password",
                json={"password": "newpass"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "password_reset"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLogin:
    def test_login_with_username(self, real_client, _real_db):
        """DB-backed login with username+password."""
        from app.auth import hash_password
        from app.models.analyst import Analyst

        # Create analyst directly in DB
        a = Analyst(
            username="logintest",
            display_name="Login Test",
            password_hash=hash_password("mypass"),
            role="analyst",
            is_active=True,
        )
        _real_db.add(a)
        _real_db.commit()

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            resp = real_client.post(
                "/api/v1/admin/login",
                json={"username": "logintest", "password": "mypass"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "token" in data
            assert data["analyst"]["username"] == "logintest"

    def test_legacy_login(self, real_client):
        """Legacy login with ADMIN_PASSWORD only (no username)."""
        with (
            patch("app.auth.settings") as mock_auth_settings,
            patch("app.api.routes_admin.verify_admin_password") as mock_verify,
        ):
            mock_auth_settings.ADMIN_JWT_SECRET = "test-secret"
            mock_verify.return_value = True
            resp = real_client.post(
                "/api/v1/admin/login",
                json={"password": "admin-pass"},
            )
            assert resp.status_code == 200
            assert "token" in resp.json()

    def test_login_inactive_analyst_rejected(self, real_client, _real_db):
        from app.auth import hash_password
        from app.models.analyst import Analyst

        a = Analyst(
            username="inactive",
            password_hash=hash_password("pass"),
            role="analyst",
            is_active=False,
        )
        _real_db.add(a)
        _real_db.commit()

        with patch("app.auth.settings") as mock_settings:
            mock_settings.ADMIN_JWT_SECRET = "test-secret"
            resp = real_client.post(
                "/api/v1/admin/login",
                json={"username": "inactive", "password": "pass"},
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth propagation on alert write routes
# ---------------------------------------------------------------------------


class TestAuthPropagation:
    def test_alert_status_requires_auth(self, real_client):
        """POST /alerts/{id}/status should return 403/401 without valid token."""
        resp = real_client.post("/api/v1/alerts/1/status", json={"status": "under_review"})
        assert resp.status_code in (401, 403)

    def test_alert_verdict_requires_auth(self, real_client):
        resp = real_client.post("/api/v1/alerts/1/verdict", json={"verdict": "confirmed_tp"})
        assert resp.status_code in (401, 403)

    def test_alert_notes_requires_auth(self, real_client):
        resp = real_client.post("/api/v1/alerts/1/notes", json={"notes": "test"})
        assert resp.status_code in (401, 403)

    def test_alert_export_requires_auth(self, real_client):
        resp = real_client.post("/api/v1/alerts/1/export")
        assert resp.status_code in (401, 403)

    def test_bulk_status_requires_auth(self, real_client):
        resp = real_client.post(
            "/api/v1/alerts/bulk-status", json={"alert_ids": [1], "status": "under_review"}
        )
        assert resp.status_code in (401, 403)
