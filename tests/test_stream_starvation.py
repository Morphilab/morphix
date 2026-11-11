# tests/test_stream_starvation.py — paridad de starvation en streaming
"""El thinking model agota max_tokens en reasoning_content: el stream
termina con finish_reason='length', SIN content y SIN tool_calls. call()
detecta y reintenta con presupuesto duplicado desde hace tiempo —
call_stream no lo hacía y el loop trataba el turno vacío como respuesta
final (los finales vacíos de DeepSeek)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import llm.controller as controller_mod
from llm.controller import StreamChunk


def _starved_chunks():
    async def _gen(**kwargs):
        yield StreamChunk(reasoning_content="razono...", finish_reason=None)
        yield StreamChunk(finish_reason="length", is_done=True)
        # Secuencia REAL de producción: con include_usage el chunk de usage
        # llega DESPUÉS del finish, con is_done=True y finish_reason=None.
        # Sin el guard de loop._accumulate_stream este chunk pisa 'length'
        # a None y el starve-retry jamás dispara.
        yield StreamChunk(
            usage={
                "prompt_tokens": 3300,
                "completion_tokens": 4096,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
            is_done=True,
        )

    return _gen


def _good_chunks(text="listo"):
    async def _gen(**kwargs):
        yield StreamChunk(text=text, finish_reason="stop", is_done=True)
        # secuencia real: usage chunk final con finish_reason=None
        yield StreamChunk(
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
            is_done=True,
        )

    return _gen


@pytest.mark.asyncio
async def test_loop_reintenta_starved_stream_con_max_tokens_duplicado(monkeypatch):
    """Primera llamada starved → reintento de la MISMA iteración con
    max_tokens duplicado → la buena cierra el turno."""
    import orchestration.loop as loop_mod
    from core.config import settings

    settings.model_roles["agent"]["max_tokens"] = 4096

    llamadas = []

    class _FakeModels:
        @staticmethod
        def call_stream(**kwargs):
            llamadas.append(kwargs)
            if len(llamadas) == 1:
                return _starved_chunks()()
            return _good_chunks("resultado final")()

        @staticmethod
        async def call(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")
                ]
            )

    monkeypatch.setattr(loop_mod, "models", _FakeModels)

    result = await loop_mod.execute_agent_loop(
        task="haz algo complejo",
        agent_type="developer",
        history=[],
        allowed_tools=None,
        workspace="main",
        on_stream_chunk=AsyncMock(),
    )

    assert result["status"] == "completed"
    assert result["result"] == "resultado final"
    assert len(llamadas) == 2
    assert "max_tokens" not in llamadas[0]  # primera: sin override
    assert llamadas[1]["max_tokens"] == 8192  # segunda: 4096 × 2


@pytest.mark.asyncio
async def test_call_stream_honra_override_de_max_tokens(monkeypatch):
    """El caller (loop) pasa max_tokens duplicado: call_stream debe
    hacerlo llegar a _stream_openai_async (sin TypeError de duplicado)."""
    from openai import AsyncOpenAI

    controller = controller_mod.models
    capturadas: dict = {}

    async def fake_stream(self, client, model, messages, temp, tools, tool_choice, **kwargs):
        capturadas.update(kwargs)
        yield StreamChunk(text="ok", finish_reason="stop", is_done=True)

    monkeypatch.setattr(
        controller_mod.LLMProvider,
        "get_async_client_with_provider",
        lambda role, temperature: (AsyncOpenAI(api_key="sk-test"), "modelo-x", 0.7, "deepseek"),
    )
    monkeypatch.setattr(controller_mod.ModelsController, "_stream_openai_async", fake_stream)

    chunks = []
    async for chunk in controller.call_stream(
        messages=[{"role": "user", "content": "hola"}],
        role="agent",
        workspace="main",
        max_tokens=9999,
    ):
        chunks.append(chunk)

    assert any(c.text == "ok" for c in chunks)
    assert capturadas.get("max_tokens") == 9999


@pytest.mark.asyncio
async def test_call_stream_respeta_rate_limiter(monkeypatch):
    """Sin slot disponible, el stream no golpea al proveedor: entrega el
    aviso y termina (paridad con call())."""
    import llm.controller as controller_mod

    class _Limiter:
        async def acquire(self):
            return False

        async def wait_and_acquire(self, timeout=30):
            return False

    monkeypatch.setattr("core.rate_limiter.get_rate_limiter", lambda: _Limiter())
    # ni siquiera debe llegar a pedir cliente
    monkeypatch.setattr(
        controller_mod.LLMProvider,
        "get_async_client_with_provider",
        AsyncMock(side_effect=AssertionError("no debió pedir cliente")),
    )

    controller = controller_mod.models
    chunks = []
    async for chunk in controller.call_stream(
        messages=[{"role": "user", "content": "hola"}], role="agent", workspace="main"
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert "Rate limit excedido" in chunks[0].text


def _partial_starved_chunks():
    """Firma glm: fragmento cortado A MEDIA PALABRA + finish='length'.

    El razonamiento agotó el cap y el content salió incompleto — el caso de
    los mensajes truncados en el GUI chat (66 chars, "...las ram")."""

    async def _gen(**kwargs):
        yield StreamChunk(
            reasoning_content="razono mucho y el presupuesto se agota...", finish_reason=None
        )
        yield StreamChunk(
            text="El ref difiere del local. Déjame enumerar las ram", finish_reason=None
        )
        yield StreamChunk(finish_reason="length", is_done=True)
        # secuencia real: el usage chunk cierra el stream
        yield StreamChunk(
            usage={
                "prompt_tokens": 3300,
                "completion_tokens": 4096,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
            is_done=True,
        )

    return _gen


@pytest.mark.asyncio
async def test_loop_reintenta_starvation_parcial_con_max_tokens_duplicado(monkeypatch):
    """Texto PARCIAL + finish='length' sin tools: el proveedor declaró la
    respuesta incompleta — reintento con presupuesto duplicado aunque haya
    fragmento (antes solo se reintentaba el caso de texto VACÍO y el
    fragmento cortado a media palabra llegaba como respuesta final)."""
    import orchestration.loop as loop_mod
    from core.config import settings

    settings.model_roles["agent"]["max_tokens"] = 4096

    llamadas = []

    class _FakeModels:
        @staticmethod
        def call_stream(**kwargs):
            llamadas.append(kwargs)
            if len(llamadas) == 1:
                return _partial_starved_chunks()()
            return _good_chunks("respuesta completa")()

        @staticmethod
        async def call(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")
                ]
            )

    monkeypatch.setattr(loop_mod, "models", _FakeModels)

    result = await loop_mod.execute_agent_loop(
        task="estado del repo",
        agent_type="analista",
        history=[],
        allowed_tools=None,
        workspace="main",
        on_stream_chunk=AsyncMock(),
    )

    assert result["status"] == "completed"
    assert result["result"] == "respuesta completa"
    assert len(llamadas) == 2
    assert llamadas[1]["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_starvation_persistente_entrega_fragmento_con_marcador(monkeypatch):
    """Reintento agotado (sigue 'length'): el fragmento se entrega CON el
    marcador honesto de truncado — jamás en silencio como respuesta completa
    (un corte silencioso se leería como mensajes cortados a media palabra)."""
    import orchestration.loop as loop_mod
    from core.config import settings

    settings.model_roles["agent"]["max_tokens"] = 4096

    class _FakeModels:
        @staticmethod
        def call_stream(**kwargs):
            return _partial_starved_chunks()()

        @staticmethod
        async def call(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="x"), finish_reason="stop")
                ]
            )

    monkeypatch.setattr(loop_mod, "models", _FakeModels)

    result = await loop_mod.execute_agent_loop(
        task="estado del repo",
        agent_type="analista",
        history=[],
        allowed_tools=None,
        workspace="main",
        on_stream_chunk=AsyncMock(),
    )

    assert result["status"] == "completed"
    assert "las ram" in result["result"]  # el fragmento está
    assert "truncada" in result["result"]  # y el marcador honesto también
