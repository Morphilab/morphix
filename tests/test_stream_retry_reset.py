"""Reintento de call_stream sin duplicar la salida parcial ya emitida."""

from unittest.mock import MagicMock, patch

import pytest

from llm.controller import ModelsController, StreamChunk
from orchestration.loop import _accumulate_stream


@pytest.mark.asyncio
async def test_accumulator_truncates_on_reset():
    """reset=True trunca el buffer del acumulador."""

    async def gen():
        yield StreamChunk(text="parcial ")
        yield StreamChunk(text="emitida ")
        yield StreamChunk(reset=True)
        yield StreamChunk(text="versión final")
        yield StreamChunk(is_done=True, finish_reason="stop")

    text, calls, finish, _ = await _accumulate_stream(gen(), None)
    assert text == "versión final", f"NV-L6: acumulado incorrecto: {text!r}"


@pytest.mark.asyncio
async def test_call_stream_emits_reset_before_retry():
    """call_stream emite reset=True al reintentar tras un fallo mid-stream."""
    from openai import AsyncOpenAI

    import llm.controller as ctrl_mod

    class ExplodingStream:
        def __init__(self):
            self.n = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            self.n += 1
            if self.n == 1:
                return type("C", (), {"choices": [], "usage": None})()  # no-op
            raise RuntimeError("corte a mitad del stream")

    calls = {"n": 0}
    client = AsyncOpenAI(api_key="t", base_url="http://localhost:9")

    def fake_create(**kwargs):
        calls["n"] += 1
        return ExplodingStream()

    client.chat.completions.create = fake_create

    fake_settings = MagicMock()
    fake_settings.max_context_tokens = 100000
    fake_settings.model_roles = {
        "default": {"max_tokens": None, "tool_calling": True},
        "agent": {"max_tokens": None, "tool_calling": True},
    }
    fake_settings.tool_calling_global = True
    fake_settings.llm_max_retries = 1

    mc = ModelsController()
    resets = []
    with (
        patch("core.circuit_breaker.CircuitBreakerRegistry.get") as mock_cbget,
        patch("core.config.settings", fake_settings),
        patch("core.context_manager.ContextManager.estimate_tokens", return_value=10),
        patch.object(
            ctrl_mod.LLMProvider,
            "get_async_client_with_provider",
            MagicMock(return_value=(client, "m", 0.5, "ollama")),
        ),
    ):
        cb = MagicMock()
        cb.allow_request.return_value = True
        mock_cbget.return_value = cb
        async for ch in mc.call_stream(messages=[{"role": "user", "content": "x"}], role="agent"):
            if ch.reset:
                resets.append(ch)

    assert len(resets) >= 1, "NV-L6: ningún chunk reset emitido antes del reintento"
