"""Captura del chunk de usage en streaming OpenAI-compatible.

Con stream_options={"include_usage": True} el chunk de usage llega AL FINAL con
choices=[] — después del chunk de finish. El código anterior hacía break en
finish → usage jamás leído → add_llm_token_usage() nunca invocado en streaming.
"""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.controller import ModelsController


@pytest.fixture(autouse=True)
def _proveedor_fuera_de_offline(monkeypatch):
    """Los tests manejan el cliente vía mocks explícitos; el modo offline del
    entorno redirige get_client*_with_provider a un cliente Ollama real antes
    de llegar a los parches — la decisión offline se fija en False."""
    from core.config import settings
    from llm.provider import LLMProvider

    monkeypatch.setattr(settings, "offline_mode", False)
    monkeypatch.setattr(LLMProvider._offline_manager, "is_offline", lambda: False)


def _delta(content=None):
    return NS(content=content, reasoning_content=None, tool_calls=None)


def _chunk(delta=None, finish=None, usage=None):
    return NS(choices=[NS(delta=delta, finish_reason=finish)], usage=usage)


_USAGE = {
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 150,
}


@pytest.mark.asyncio
async def test_usage_chunk_after_finish_is_emitted():
    """El usage que llega DESPUÉS del finish debe emitirse como StreamChunk."""
    chunks = [
        _chunk(_delta(content="hola ")),
        _chunk(_delta(content="mundo")),
        _chunk(finish="stop"),  # sin usage
        _chunk(usage=NS(**_USAGE)),  # chunk final de usage, choices=[]
    ]

    async def _fake_stream():
        for ch in chunks:
            yield ch

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_fake_stream())

    mc = ModelsController()
    emitted = []
    async for sc in mc._stream_openai_async(
        client, "deepseek-v4-flash", [], 0.5, tools=None, tool_choice=None
    ):
        emitted.append(sc)

    usages = [sc.usage for sc in emitted if sc.usage]
    assert usages, f"ningún chunk de usage emitido: {emitted}"
    assert usages[-1]["prompt_tokens"] == 100
    assert usages[-1]["completion_tokens"] == 50


@pytest.mark.asyncio
async def test_call_stream_records_token_budget_with_late_usage():
    """call_stream debe invocar add_llm_token_usage con el total real."""
    from openai import AsyncOpenAI

    import llm.controller as ctrl_mod

    chunks = [
        _chunk(_delta(content="respuesta")),
        _chunk(finish="stop"),
        _chunk(usage=NS(**_USAGE)),
    ]

    async def _fake_stream():
        for ch in chunks:
            yield ch

    client = AsyncOpenAI(api_key="test", base_url="http://localhost:9")
    client.chat.completions.create = AsyncMock(return_value=_fake_stream())

    fake_settings = MagicMock()
    fake_settings.max_context_tokens = 100000
    fake_settings.model_roles = {
        "default": {"max_tokens": None, "tool_calling": True},
        "agent": {"max_tokens": None, "tool_calling": True},
    }
    fake_settings.tool_calling_global = True
    fake_settings.llm_max_retries = 0

    mc = ModelsController()
    with (
        patch("core.circuit_breaker.CircuitBreakerRegistry.get") as mock_cbget,
        patch("core.config.settings", fake_settings),
        patch("core.context_manager.ContextManager.estimate_tokens", return_value=10),
        patch.object(
            ctrl_mod.LLMProvider, "get_async_client", MagicMock(return_value=(client, "m", 0.5))
        ),
        patch("tools.orchestrator.add_llm_token_usage") as mock_add,
    ):
        cb = MagicMock()
        cb.allow_request.return_value = True
        mock_cbget.return_value = cb
        collected = []
        async for ch in mc.call_stream(messages=[{"role": "user", "content": "x"}], role="agent"):
            collected.append(ch)

    assert any(c.text for c in collected)
    mock_add.assert_called_once_with(150)  # 100 prompt + 50 completion


@pytest.mark.asyncio
async def test_finish_chunk_with_inline_usage_still_works():
    """Compatibilidad: providers que incluyen usage EN el chunk de finish."""
    chunks = [
        _chunk(_delta(content="ok")),
        _chunk(finish="stop", usage=NS(**_USAGE)),
    ]

    async def _fake_stream():
        for ch in chunks:
            yield ch

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_fake_stream())

    mc = ModelsController()
    emitted = [
        sc
        async for sc in mc._stream_openai_async(client, "m", [], 0.5, tools=None, tool_choice=None)
    ]
    usages = [sc.usage for sc in emitted if sc.usage]
    assert len(usages) == 1
    assert usages[0]["completion_tokens"] == 50


def test_track_budget_subtracts_cache_hits():
    """Los cache hits del prompt NO queman el presupuesto del workflow."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from llm.controller import ModelsController

    response = MagicMock()
    response.usage = SimpleNamespace(
        total_tokens=1000,
        prompt_cache_hit_tokens=800,
        prompt_cache_miss_tokens=200,
    )

    with patch("tools.orchestrator.add_llm_token_usage") as mock_add:
        ModelsController._track_budget(response)

    mock_add.assert_called_once_with(200)


def test_track_budget_without_cache_counts_full():
    from types import SimpleNamespace
    from unittest.mock import patch

    from llm.controller import ModelsController

    response = MagicMock()
    response.usage = SimpleNamespace(total_tokens=500, prompt_cache_hit_tokens=0)

    with patch("tools.orchestrator.add_llm_token_usage") as mock_add:
        ModelsController._track_budget(response)

    mock_add.assert_called_once_with(500)


def test_track_budget_openai_style_cached_tokens():
    """Formato OpenAI-compat: prompt_tokens_details.cached_tokens."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from llm.controller import ModelsController

    response = MagicMock()
    response.usage = SimpleNamespace(
        total_tokens=1000,
        prompt_cache_hit_tokens=0,
        prompt_tokens_details=SimpleNamespace(cached_tokens=900),
    )

    with patch("tools.orchestrator.add_llm_token_usage") as mock_add:
        ModelsController._track_budget(response)

    mock_add.assert_called_once_with(100)


def test_track_stream_usage_subtracts_cache_hits():
    from unittest.mock import patch

    from llm.controller import ModelsController

    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "prompt_cache_hit_tokens": 100,
        "prompt_cache_miss_tokens": 0,
    }
    with patch("tools.orchestrator.add_llm_token_usage") as mock_add:
        ModelsController._track_stream_usage(usage)

    mock_add.assert_called_once_with(50)  # 0 uncached prompt + 50 completion
