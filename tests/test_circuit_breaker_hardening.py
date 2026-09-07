"""Sonda única half-open en el breaker + atribución correcta bajo fallback."""

import time
from unittest.mock import MagicMock, patch

import pytest

from core.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry


def _open_breaker(recovery_timeout: float = 0.2) -> CircuitBreaker:
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=recovery_timeout)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open
    return cb


def test_half_open_allows_only_one_probe():
    """HALF_OPEN permite UNA sonda; la 2ª petición concurrente se rechaza."""
    cb = _open_breaker()
    time.sleep(0.25)
    assert cb.allow_request() is True  # sonda
    assert cb.allow_request() is False, "segunda petición aceptada en half-open"
    assert cb.allow_request() is False


def test_half_open_probe_failure_reopens():
    """Sonda fallida → circuito vuelve a OPEN inmediatamente."""
    cb = _open_breaker(recovery_timeout=0.1)
    time.sleep(0.15)
    assert cb.allow_request() is True
    cb.record_failure()
    assert cb.state == "open"


def test_half_open_probe_success_closes():
    """Sonda exitosa → CLOSED y contador de fallos reseteado."""
    cb = _open_breaker(recovery_timeout=0.1)
    time.sleep(0.15)
    assert cb.allow_request() is True
    cb.record_success()
    assert cb.state == "closed"
    assert cb.failure_count == 0
    assert cb.allow_request() is True


@pytest.mark.asyncio
async def test_get_client_with_provider_attributes_fallback():
    """Con el breaker del primario OPEN, el proveedor EFECTIVO es ollama."""
    import llm.provider as prov_mod

    CircuitBreakerRegistry.reset_all()
    ds_cb = CircuitBreakerRegistry.get("deepseek")
    for _ in range(10):
        ds_cb.record_failure()
    assert ds_cb.is_open

    with (
        patch.object(prov_mod.settings, "offline_mode", False),
        patch.object(prov_mod.LLMProvider._offline_manager, "is_offline", return_value=False),
        patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-test", "OPENAI_API_KEY": ""}),
    ):
        result = prov_mod.LLMProvider.get_client_with_provider("agent", 0.5)

    assert len(result) == 4
    client, model, temp, effective = result
    assert effective == "ollama", f"atribución incorrecta: {effective}"


@pytest.mark.asyncio
async def test_call_stream_does_not_block_when_primary_open():
    """call_stream unifica política — intenta ruta efectiva, no bloquea."""
    from llm.controller import ModelsController

    mc = ModelsController()

    fake_client = MagicMock()  # NO es AsyncOpenAI → rama Ollama
    fake_client.chat.side_effect = RuntimeError("ollama caído también")

    with (
        patch(
            "llm.controller.LLMProvider.get_async_client",
            MagicMock(return_value=(fake_client, "m", 0.5)),
        ),
        patch(
            "llm.controller.LLMProvider.get_async_client_with_provider",
            MagicMock(return_value=(fake_client, "m", 0.5, "ollama")),
        ),
        patch("core.circuit_breaker.CircuitBreakerRegistry") as mock_reg,
        patch("core.config.settings.llm_max_retries", 0),
        patch.object(type(mc), "_max_retries", 0, create=True),
    ):
        cb = MagicMock()
        cb.allow_request.return_value = False  # breaker primario OPEN
        mock_reg.get.return_value = cb

        chunks = []
        async for ch in mc.call_stream(messages=[{"role": "user", "content": "x"}], role="agent"):
            chunks.append(ch)

        texts = [c.text or "" for c in chunks]
        joined = "".join(texts)
        assert (
            "circuit breaker" not in joined.lower()
        ), f"call_stream bloquea con breaker OPEN en vez de intentar ruta efectiva: {joined[:120]}"
