"""Tests for the shared HTTP retry utility."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.utils.http_retry import retry_request, _RETRYABLE_STATUS_CODES


def _make_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    """Create a fake httpx.Response with the given status code."""
    resp = httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "https://example.com/test"),
        headers=headers or {},
    )
    return resp


class TestRetryOnTransientErrors:
    """Retry on 503 → eventual success."""

    @patch("app.utils.http_retry.time")
    def test_retry_503_then_success(self, mock_time):
        mock_time.sleep = MagicMock()
        mock_time.monotonic = MagicMock(return_value=0)

        call_count = 0

        def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _make_response(503)
            return _make_response(200)

        resp = retry_request(fake_get, "https://example.com/test", delays=[0, 0, 0])
        assert resp.status_code == 200
        assert call_count == 3

    @patch("app.utils.http_retry.time")
    def test_retry_all_retryable_codes(self, mock_time):
        mock_time.sleep = MagicMock()
        for code in _RETRYABLE_STATUS_CODES:
            call_count = 0

            def fake_get(url, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return _make_response(code)
                return _make_response(200)

            resp = retry_request(fake_get, "https://example.com", delays=[0])
            assert resp.status_code == 200


class TestNoRetryOnClientErrors:
    """Never retry 401, 403, 404, 422."""

    def test_no_retry_401(self):
        def fake_get(url, **kwargs):
            return _make_response(401)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            retry_request(fake_get, "https://example.com/test", delays=[0, 0])
        assert exc_info.value.response.status_code == 401

    def test_no_retry_404(self):
        def fake_get(url, **kwargs):
            return _make_response(404)

        with pytest.raises(httpx.HTTPStatusError):
            retry_request(fake_get, "https://example.com/test", delays=[0, 0])

    def test_no_retry_422(self):
        def fake_get(url, **kwargs):
            return _make_response(422)

        with pytest.raises(httpx.HTTPStatusError):
            retry_request(fake_get, "https://example.com/test", delays=[0, 0])


class TestRetryAfterHeader:
    """Retry-After header is respected for 429 responses."""

    @patch("app.utils.http_retry.time")
    def test_retry_after_header_respected(self, mock_time):
        mock_time.sleep = MagicMock()
        call_count = 0

        def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_response(429, headers={"Retry-After": "30"})
            return _make_response(200)

        resp = retry_request(fake_get, "https://example.com", delays=[5, 5])
        assert resp.status_code == 200
        # Should have used max(5, 30) = 30 as the delay
        mock_time.sleep.assert_called_once()
        actual_delay = mock_time.sleep.call_args[0][0]
        assert actual_delay == 30


class TestNetworkExceptions:
    """Retryable exceptions (ConnectError, Timeout) are retried; others propagate."""

    @patch("app.utils.http_retry.time")
    def test_retry_connect_error(self, mock_time):
        mock_time.sleep = MagicMock()
        call_count = 0

        def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused")
            return _make_response(200)

        resp = retry_request(fake_get, "https://example.com", delays=[0])
        assert resp.status_code == 200

    @patch("app.utils.http_retry.time")
    def test_retry_timeout_error(self, mock_time):
        mock_time.sleep = MagicMock()
        call_count = 0

        def fake_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("read timeout")
            return _make_response(200)

        resp = retry_request(fake_get, "https://example.com", delays=[0])
        assert resp.status_code == 200

    def test_non_retryable_exception_propagates(self):
        def fake_get(url, **kwargs):
            raise ValueError("bad argument")

        with pytest.raises(ValueError, match="bad argument"):
            retry_request(fake_get, "https://example.com", delays=[0, 0])


class TestExhaustedRetries:
    """After all retries exhausted, the error propagates."""

    @patch("app.utils.http_retry.time")
    def test_exhausted_retries_raises(self, mock_time):
        mock_time.sleep = MagicMock()

        def fake_get(url, **kwargs):
            return _make_response(503)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            retry_request(fake_get, "https://example.com", delays=[0, 0])
        assert exc_info.value.response.status_code == 503

    @patch("app.utils.http_retry.time")
    def test_exhausted_connect_retries_raises(self, mock_time):
        mock_time.sleep = MagicMock()

        def fake_get(url, **kwargs):
            raise httpx.ConnectError("refused")

        with pytest.raises(httpx.ConnectError):
            retry_request(fake_get, "https://example.com", delays=[0])
