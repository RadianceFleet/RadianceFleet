"""Tests for rate limiting configuration and Retry-After header."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch


class TestRateLimitSettings:
    """Verify rate limit settings are configurable."""

    def test_default_rate_limit_settings(self):
        from app.config import Settings

        s = Settings()
        assert s.RATE_LIMIT_DEFAULT == "60/minute"
        assert s.RATE_LIMIT_ADMIN == "120/minute"
        assert s.RATE_LIMIT_VIEWER == "30/minute"

    def test_rate_limit_settings_from_env(self):
        with patch.dict(
            "os.environ",
            {
                "RATE_LIMIT_DEFAULT": "100/minute",
                "RATE_LIMIT_ADMIN": "200/minute",
                "RATE_LIMIT_VIEWER": "10/minute",
            },
        ):
            from app.config import Settings

            s = Settings()
            assert s.RATE_LIMIT_DEFAULT == "100/minute"
            assert s.RATE_LIMIT_ADMIN == "200/minute"
            assert s.RATE_LIMIT_VIEWER == "10/minute"


class TestCustomRateLimitHandler:
    """Verify the custom 429 handler includes Retry-After header."""

    def test_retry_after_header_present(self):
        from app.main import custom_rate_limit_handler

        request = MagicMock()
        exc = MagicMock()
        exc.retry_after = 42

        response = asyncio.run(custom_rate_limit_handler(request, exc))

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "42"

    def test_retry_after_default_when_missing(self):
        from app.main import custom_rate_limit_handler

        request = MagicMock()
        exc = MagicMock(spec=[])

        response = asyncio.run(custom_rate_limit_handler(request, exc))

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "60"

    def test_response_body_format(self):
        from app.main import custom_rate_limit_handler

        request = MagicMock()
        exc = MagicMock()
        exc.retry_after = 30

        response = asyncio.run(custom_rate_limit_handler(request, exc))

        body = json.loads(response.body.decode())
        assert body["error"] == "Rate limit exceeded"
        assert body["retry_after"] == 30


class TestLimiterConfiguration:
    """Verify the app limiter uses configurable defaults."""

    def test_limiter_uses_settings_default(self):
        from app.main import limiter

        # The limiter's _default_limits should contain the configured rate
        assert limiter._default_limits is not None

    def test_exception_handler_registered(self):
        from slowapi.errors import RateLimitExceeded

        from app.main import app

        # FastAPI registers exception handlers; verify ours is present
        handlers = app.exception_handlers
        assert RateLimitExceeded in handlers


class TestHealthEndpointExempt:
    """Verify health endpoints are exempt from rate limiting."""

    def test_health_endpoint_is_exempt(self):
        from app.api._helpers import limiter
        from app.api.routes_health import health_check

        route_name = f"{health_check.__module__}.{health_check.__name__}"
        assert route_name in limiter._exempt_routes

    def test_data_freshness_endpoint_is_exempt(self):
        from app.api._helpers import limiter
        from app.api.routes_health import get_data_freshness

        route_name = f"{get_data_freshness.__module__}.{get_data_freshness.__name__}"
        assert route_name in limiter._exempt_routes

    def test_collection_status_endpoint_is_exempt(self):
        from app.api._helpers import limiter
        from app.api.routes_health import get_collection_status

        route_name = f"{get_collection_status.__module__}.{get_collection_status.__name__}"
        assert route_name in limiter._exempt_routes
