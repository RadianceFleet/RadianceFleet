"""Circuit breakers for external API clients.

Uses pybreaker to prevent cascading failures when external services are down.
Each breaker trips after 5 consecutive failures and resets after 60 seconds.
"""
from __future__ import annotations

import logging

import pybreaker

logger = logging.getLogger(__name__)


class LoggingListener(pybreaker.CircuitBreakerListener):
    def state_change(self, cb, old_state, new_state):
        logger.warning(
            "Circuit breaker '%s': %s -> %s", cb.name, old_state.name, new_state.name
        )


_listener = LoggingListener()

breakers = {
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
    "equasis": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="equasis", listeners=[_listener]
    ),
    "noaa": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="noaa", listeners=[_listener]
    ),
    "aishub": pybreaker.CircuitBreaker(
        fail_max=5, reset_timeout=60, name="aishub", listeners=[_listener]
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
}


def get_circuit_states() -> dict:
    """Return current state and fail count for all circuit breakers."""
    return {
        name: {"state": cb.current_state, "fail_count": cb.fail_counter}
        for name, cb in breakers.items()
    }
