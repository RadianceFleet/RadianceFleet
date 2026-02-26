"""Shared HTTP retry utility with status-code filtering.

Retries only on transient server errors and rate limits. Never retries
client errors (401, 403, 404, 422) which indicate auth/config problems.

Usage:
    from app.utils.http_retry import retry_request

    resp = retry_request(client.get, url, headers=headers)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

# Status codes safe to retry (transient server issues)
_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Exceptions that indicate transient network issues
_RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException, OSError)

# Default backoff delays in seconds
DEFAULT_DELAYS: list[float] = [2, 5, 15]


def retry_request(
    request_fn: Callable[..., httpx.Response],
    *args: Any,
    delays: list[float] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Execute an httpx request function with automatic retry on transient failures.

    Args:
        request_fn: Bound method like ``client.get``, ``client.post``, etc.
        *args: Positional args forwarded to request_fn (typically the URL).
        delays: List of backoff delays in seconds. Default [2, 5, 15].
            For rate-limited APIs, pass longer delays like [60, 120, 180].
        **kwargs: Keyword args forwarded to request_fn (headers, params, etc.).

    Returns:
        httpx.Response on success.

    Raises:
        httpx.HTTPStatusError: On non-retryable HTTP errors (4xx except 429).
        httpx.ConnectError / httpx.TimeoutException: After all retries exhausted.
    """
    if delays is None:
        delays = DEFAULT_DELAYS

    last_exc: Exception | None = None

    for attempt in range(1 + len(delays)):
        try:
            resp = request_fn(*args, **kwargs)

            if resp.status_code < 400:
                return resp

            if resp.status_code not in _RETRYABLE_STATUS_CODES:
                resp.raise_for_status()

            # Retryable status — check if we have retries left
            if attempt >= len(delays):
                resp.raise_for_status()

            # Parse Retry-After for 429 responses
            delay = delays[attempt]
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except (ValueError, TypeError):
                        pass

            logger.warning(
                "HTTP %d from %s — retrying in %.0fs (attempt %d/%d)",
                resp.status_code,
                _url_for_log(args),
                delay,
                attempt + 1,
                len(delays),
            )
            time.sleep(delay)

        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt >= len(delays):
                raise
            delay = delays[attempt]
            logger.warning(
                "%s for %s — retrying in %.0fs (attempt %d/%d)",
                type(exc).__name__,
                _url_for_log(args),
                delay,
                attempt + 1,
                len(delays),
            )
            time.sleep(delay)

    # Should not reach here, but satisfy type checker
    if last_exc:
        raise last_exc
    raise RuntimeError("retry_request exhausted retries without result")


def _url_for_log(args: tuple) -> str:
    """Extract a loggable URL from request args."""
    if args and isinstance(args[0], (str, httpx.URL)):
        return str(args[0])[:120]
    return "<unknown>"
