"""Tests for CircuitBreaker — pattern integrity and integration with LLM provider."""

import time
from unittest.mock import patch

import pytest

from core.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry


class TestCircuitBreaker:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert cb.state == "closed"
        assert cb.is_open is False
        assert cb.failure_count == 0
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "open"
        assert cb.is_open is True
        assert cb.failure_count == 3
        assert cb.allow_request() is False

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        for _ in range(2):
            cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.02)
        assert cb.allow_request() is True  # transitions to half_open
        assert cb.state == "half_open"

    def test_closes_after_success_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.02)
        cb.allow_request()  # half_open
        cb.record_success()  # should close
        assert cb.state == "closed"
        assert cb.failure_count == 0

    def test_allow_request_twice_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        assert cb.state == "open"
        time.sleep(0.02)
        assert cb.allow_request() is True  # first call: open → half_open
        assert cb.state == "half_open"
        assert cb.allow_request() is True  # second call: still half_open
        assert cb.state == "half_open"

    def test_reopens_after_failure_in_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        cb.record_failure()
        time.sleep(0.02)
        cb.allow_request()  # half_open
        cb.record_failure()  # should re-open
        assert cb.state == "open"
        assert cb.allow_request() is False

    def test_reset_after_success(self):
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            cb.record_failure()
        assert cb.failure_count == 4
        assert cb.state == "closed"  # not yet at threshold
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == "closed"

    def test_registry_returns_same_instance(self):
        CircuitBreakerRegistry.reset_all()
        cb1 = CircuitBreakerRegistry.get("deepseek")
        cb2 = CircuitBreakerRegistry.get("deepseek")
        assert cb1 is cb2

    def test_registry_different_providers(self):
        CircuitBreakerRegistry.reset_all()
        cb1 = CircuitBreakerRegistry.get("deepseek")
        cb2 = CircuitBreakerRegistry.get("openai")
        assert cb1 is not cb2

    def test_registry_get_all_states(self):
        CircuitBreakerRegistry.reset_all()
        cb = CircuitBreakerRegistry.get("deepseek")
        for _ in range(3):
            cb.record_failure()
        states = CircuitBreakerRegistry.get_all_states()
        assert "deepseek" in states
        assert states["deepseek"]["failures"] == 3

    def test_allow_request_always_true_when_closed(self):
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(10):
            assert cb.allow_request() is True


class TestCircuitBreakerIntegration:
    @pytest.mark.asyncio
    async def test_provider_skipped_when_circuit_open(self):
        """Verifica que LLMProvider salta un proveedor cuando su circuito está abierto."""
        CircuitBreakerRegistry.reset_all()
        cb = CircuitBreakerRegistry.get("deepseek")
        # Open the circuit
        for _ in range(5):
            cb.record_failure()

        with patch("llm.provider.settings") as mock_settings:
            mock_settings.model_roles = {
                "default": {"provider": "deepseek", "model": "test-model", "temperature": 0.7}
            }
            mock_settings.deepseek_api_key = "fake-key"
            mock_settings.openai_api_key = ""
            mock_settings.grok_api_key = ""
            mock_settings.llm_timeout = 30
            mock_settings.offline_mode = False

            # Should fall through to Ollama since deepseek circuit is open
            from llm.provider import LLMProvider

            with patch("llm.provider.LLMProvider._create_ollama_client") as mock_ollama:
                mock_ollama.return_value = ("ollama_client", "phi3", 0.5)
                result = LLMProvider.get_client("default", temperature=0.5)
                # Should have fallen back to Ollama
                mock_ollama.assert_called_once()
                assert result == ("ollama_client", "phi3", 0.5)
