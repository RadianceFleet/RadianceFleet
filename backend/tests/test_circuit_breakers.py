"""Tests for circuit breaker integration."""
import pybreaker
import pytest

from app.modules.circuit_breakers import LoggingListener, breakers, get_circuit_states


class TestCircuitBreakerModule:
    def setup_method(self):
        """Reset all breakers before each test."""
        for cb in breakers.values():
            cb.close()

    def test_all_breakers_exist(self):
        expected = {"gfw", "aisstream", "barentswatch", "digitraffic", "kystverket", "equasis"}
        assert set(breakers.keys()) == expected

    def test_breaker_defaults(self):
        for name, cb in breakers.items():
            assert cb.fail_max == 5, f"{name} fail_max wrong"
            assert cb.reset_timeout == 60, f"{name} reset_timeout wrong"
            assert cb.name == name

    def test_get_circuit_states_all_closed(self):
        states = get_circuit_states()
        assert len(states) == 6
        for name, info in states.items():
            assert info["state"] == "closed"
            assert info["fail_count"] == 0

    def test_breaker_opens_after_failures(self):
        cb = breakers["gfw"]

        def failing():
            raise ConnectionError("timeout")

        for _ in range(5):
            with pytest.raises((ConnectionError, pybreaker.CircuitBreakerError)):
                cb.call(failing)

        assert cb.current_state == "open"
        states = get_circuit_states()
        assert states["gfw"]["state"] == "open"

    def test_breaker_rejects_when_open(self):
        cb = breakers["digitraffic"]

        def failing():
            raise ConnectionError("timeout")

        for _ in range(5):
            with pytest.raises((ConnectionError, pybreaker.CircuitBreakerError)):
                cb.call(failing)

        with pytest.raises(pybreaker.CircuitBreakerError):
            cb.call(lambda: "ok")

    def test_breaker_passes_on_success(self):
        cb = breakers["barentswatch"]
        result = cb.call(lambda: "success")
        assert result == "success"
        assert cb.current_state == "closed"
        assert cb.fail_counter == 0

    def test_logging_listener(self):
        listener = LoggingListener()
        # Just verify it's callable without error
        class FakeCB:
            name = "test"
        class FakeState:
            name = "closed"
        class FakeState2:
            name = "open"
        listener.state_change(FakeCB(), FakeState(), FakeState2())

    def test_independent_breakers(self):
        """Failures in one breaker don't affect others."""
        def failing():
            raise ConnectionError("timeout")

        for _ in range(5):
            with pytest.raises((ConnectionError, pybreaker.CircuitBreakerError)):
                breakers["gfw"].call(failing)

        assert breakers["gfw"].current_state == "open"
        assert breakers["aisstream"].current_state == "closed"
        assert breakers["equasis"].current_state == "closed"

    def test_breaker_call_passes_args(self):
        cb = breakers["equasis"]

        def add(a, b, extra=0):
            return a + b + extra

        result = cb.call(add, 2, 3, extra=10)
        assert result == 15


class TestHealthEndpointIncludesBreakers:
    def test_main_health_function(self):
        """Verify the health() function returns circuit breaker state."""
        from app.main import health

        data = health()
        assert "circuit_breakers" in data
        assert "gfw" in data["circuit_breakers"]
        assert data["circuit_breakers"]["gfw"]["state"] == "closed"
        assert data["circuit_breakers"]["gfw"]["fail_count"] == 0
        assert len(data["circuit_breakers"]) == 6
