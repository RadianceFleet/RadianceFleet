"""Tests for Settings model validation (admin auth consistency)."""

import pytest

from app.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Prevent OS env vars from leaking into Settings during tests."""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)


class TestAdminAuthValidation:
    """Verify the model_validator that guards ADMIN_PASSWORD / ADMIN_JWT_SECRET."""

    def test_error_when_password_set_without_secret(self):
        with pytest.raises(ValueError, match="ADMIN_JWT_SECRET must be set"):
            Settings(
                ADMIN_PASSWORD="hunter2",
                ADMIN_JWT_SECRET=None,
                _env_file=None,
            )

    def test_error_when_password_nonempty_without_secret(self):
        """Non-empty string should also trigger the check."""
        with pytest.raises(ValueError, match="ADMIN_JWT_SECRET must be set"):
            Settings(
                ADMIN_PASSWORD="x",
                ADMIN_JWT_SECRET="",
                _env_file=None,
            )

    def test_ok_when_both_set(self):
        s = Settings(
            ADMIN_PASSWORD="hunter2",
            ADMIN_JWT_SECRET="abc123",
            _env_file=None,
        )
        assert s.ADMIN_PASSWORD == "hunter2"
        assert s.ADMIN_JWT_SECRET == "abc123"

    def test_ok_when_neither_set(self):
        s = Settings(
            ADMIN_PASSWORD=None,
            ADMIN_JWT_SECRET=None,
            _env_file=None,
        )
        assert s.ADMIN_PASSWORD is None
        assert s.ADMIN_JWT_SECRET is None

    def test_ok_when_only_secret_set(self):
        s = Settings(
            ADMIN_PASSWORD=None,
            ADMIN_JWT_SECRET="abc123",
            _env_file=None,
        )
        assert s.ADMIN_JWT_SECRET == "abc123"

    def test_ok_when_password_empty_string(self):
        """Empty-string password is treated as 'not set'."""
        s = Settings(
            ADMIN_PASSWORD="",
            ADMIN_JWT_SECRET=None,
            _env_file=None,
        )
        assert s.ADMIN_PASSWORD == ""
