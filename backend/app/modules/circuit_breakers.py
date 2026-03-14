"""Circuit breakers for external API clients.

Uses pybreaker to prevent cascading failures when external services are down.
Each breaker trips after 5 consecutive failures and resets after 60 seconds.

States:
  - closed: healthy, requests pass through normally.
  - open: failing, all requests short-circuit with CircuitBreakerError.
  - half-open: testing, one request is allowed through to probe recovery.

Exposed via GET /health/circuits. See get_circuit_states().
"""

from __future__ import annotations

import logging

import pybreaker

logger = logging.getLogger(__name__)


class LoggingListener(pybreaker.CircuitBreakerListener):
    """Logs state transitions (e.g. closed->open) at WARNING level."""

    def state_change(self, cb, old_state, new_state):
        logger.warning("Circuit breaker '%s': %s -> %s", cb.name, old_state.name, new_state.name)


_listener = LoggingListener()

breakers = {
    # -- AIS data sources: when open, ingestion falls back to other feeds --
    "gfw": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="gfw", listeners=[_listener]
    ),
    "aisstream": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="aisstream", listeners=[_listener]
    ),
    "barentswatch": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="barentswatch", listeners=[_listener]
    ),
    "digitraffic": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="digitraffic", listeners=[_listener]
    ),
    "kystverket": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="kystverket", listeners=[_listener]
    ),
    "aishub": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="aishub", listeners=[_listener]
    ),
    # -- Vessel registry / enrichment: when open, enrichment fields go stale --
    "equasis": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="equasis", listeners=[_listener]
    ),
    "datalastic": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="datalastic", listeners=[_listener]
    ),
    # -- Environmental / regulatory data --
    "noaa": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="noaa", listeners=[_listener]
    ),
    "dma": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="dma", listeners=[_listener]
    ),
    "crea": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="crea", listeners=[_listener]
    ),
    "copernicus": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="copernicus", listeners=[_listener]
    ),
    # -- Satellite imagery providers: when open, order submission is blocked --
    "planet": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="planet", listeners=[_listener]
    ),
    "capella": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="capella", listeners=[_listener]
    ),
    "maxar": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="maxar", listeners=[_listener]
    ),
    "umbra": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="umbra", listeners=[_listener]
    ),
    # -- VIIRS nighttime lights: when open, VIIRS collection is blocked --
    "viirs": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="viirs", listeners=[_listener]
    ),
    # -- Sanctions screening --
    "yente": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="yente", listeners=[_listener]
    ),
    # -- Beneficial ownership transparency --
    "opencorporates": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="opencorporates", listeners=[_listener]
    ),
}


def get_circuit_states() -> dict:
    """Return current state and fail count for all circuit breakers."""
    return {
        name: {"state": cb.current_state, "fail_count": cb.fail_counter}
        for name, cb in breakers.items()
    }
