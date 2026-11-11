# tests/test_stream_fantasma.py — tool_calls fantasma + fallback marcado
"""Dos firmas de proveedor que el loop debe absorber:

1. El stream declara finish='tool_calls' SIN entregar ningún delta (con o
   sin texto acompañante). Sin guard el loop lo trataba como respuesta
   final: VACÍA, o prosa huérfana que anuncia una acción que nunca ocurre.
2. El texto de error del fallback de streaming NO es respuesta del modelo:
   viaja marcado con system_notice y el loop reintenta o falla honesto.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from llm.controller import StreamChunk


def _phantom_chunks(with_text=True):
    """finish='tool_calls' con CERO deltas entregados — el asesino de la sesión."""

    async def _gen(**kwargs):
        if with_text:
            yield StreamChunk(text="Leo el test para saber qué espera.", finish_reason=None)
        yield StreamChunk(finish_reason="tool_calls", is_done=True)
        yield StreamChunk(
            usage={
                "prompt_tokens": 3300,
                "completion_tokens": 218,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
            is_done=True,
        )

    return _gen


def _good_chunks(text="resultado final"):
    async def _gen(**kwargs):
        yield StreamChunk(text=text, finish_reason="stop", is_done=True)
        yield StreamChunk(
            usage={
                "prompt_tokens": 3400,
                "completion_tokens": 30,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
            is_done=True,
        )

    return _gen


class _FakeModels:
    def __init__(self, gens):
        self._gens = list(gens)

    def call_stream(self, **kwargs):
        idx = min(len(self._gens) - 1, _FakeModels.calls)
        _FakeModels.calls += 1
        g = self._gens[idx]
        return g() if callable(g) else g


_FakeModels.calls = 0


def _fake_models(*gens):
    _FakeModels.calls = 0
    return _FakeModels(gens)


class TestPhantomToolCalls:
    @pytest.mark.asyncio
    async def test_phantom_reintenta_y_recupera(self, monkeypatch):
        """El proveedor declara tool_calls sin deltas → reintento de la MISMA
        iteración → la respuesta buena cierra el turno. La prosa huérfana de
        la primera llamada NO es el resultado."""
        import orchestration.loop as loop_mod

        fake = _fake_models(_phantom_chunks(), _good_chunks("resultado final"))
        monkeypatch.setattr(loop_mod, "models", fake)

        result = await loop_mod.execute_agent_loop(
            task="continúa el ciclo TDD",
            agent_type="developer",
            history=[],
            allowed_tools=None,
            workspace="main",
            on_stream_chunk=AsyncMock(),
        )

        assert result["status"] == "completed"
        assert result["result"] == "resultado final"
        assert _FakeModels.calls == 2

    @pytest.mark.asyncio
    async def test_phantom_persistente_reintenta_una_vez(self, monkeypatch):
        """El fantasma persiste tras el reintento: no se buclea — UN reintento
        por run; el resto cae a la maquinaria existente (final vacío → el
        engine reintenta el paso o falla honesto)."""
        import orchestration.loop as loop_mod

        fake = _fake_models(_phantom_chunks(), _phantom_chunks())
        monkeypatch.setattr(loop_mod, "models", fake)

        result = await loop_mod.execute_agent_loop(
            task="continúa el ciclo TDD",
            agent_type="developer",
            history=[],
            allowed_tools=None,
            workspace="main",
            on_stream_chunk=AsyncMock(),
        )

        assert _FakeModels.calls == 2  # un reintento, no más
        # tras agotar el reintento el loop entrega lo que el modelo produjo
        # (prosa, sin tool ejecutada) — el downstream (until/verdict) juzga
        # la entrega; lo que NUNCA pasa es buclear contra el fantasma.

    @pytest.mark.asyncio
    async def test_tool_calls_reales_no_reintentan(self, monkeypatch):
        """Guard de regresión: un stream CON tool calls entregadas no toca el
        camino del fantasma — el loop ejecuta la tool normalmente."""

        async def _gen(**kwargs):
            yield StreamChunk(tool_name="load_skill", tool_call_id="call_1")
            yield StreamChunk(tool_arguments='{"name": "tdd"}', tool_call_id="call_1")
            yield StreamChunk(finish_reason="tool_calls", is_done=True)

        import orchestration.loop as loop_mod

        fake = _fake_models(_gen(), _good_chunks("cerré con la skill cargada"))
        monkeypatch.setattr(loop_mod, "models", fake)

        result = await loop_mod.execute_agent_loop(
            task="carga la skill tdd",
            agent_type="developer",
            history=[],
            allowed_tools=["load_skill"],
            workspace="main",
            on_stream_chunk=AsyncMock(),
        )

        assert _FakeModels.calls == 2  # 1ª ejecuta la tool, 2ª cierra
        assert result["result"] == "cerré con la skill cargada"


class TestFallbackMarcado:
    @pytest.mark.asyncio
    async def test_aviso_del_fallback_no_es_respuesta(self, monkeypatch):
        """El texto de error del controller (fallback de streaming) viaja
        marcado: el loop reintenta y recupera — el aviso JAMÁS es la
        respuesta del agente (repro Ollama del 12-oct)."""

        async def _notice_gen(**kwargs):
            yield StreamChunk(
                text="❌ Ollama también falló. Verifica que esté corriendo.",
                finish_reason="stop",
                is_done=True,
                system_notice=True,
            )

        import orchestration.loop as loop_mod

        fake = _fake_models(_notice_gen, _good_chunks("respuesta del modelo"))
        monkeypatch.setattr(loop_mod, "models", fake)

        result = await loop_mod.execute_agent_loop(
            task="haz algo",
            agent_type="developer",
            history=[],
            allowed_tools=None,
            workspace="main",
            on_stream_chunk=AsyncMock(),
        )

        assert result["result"] == "respuesta del modelo"
        assert _FakeModels.calls == 2

    @pytest.mark.asyncio
    async def test_aviso_persistente_levanta_denied(self, monkeypatch):
        """Aviso tras reintento: LLMDeniedError — fallo honesto que el engine
        convierte en retry de paso, no un run 'completado' con el texto del
        error como entrega."""

        async def _notice_gen(**kwargs):
            yield StreamChunk(
                text="❌ Streaming error: proveedor caído",
                finish_reason="stop",
                is_done=True,
                system_notice=True,
            )

        import orchestration.loop as loop_mod
        from orchestration.loop import LLMDeniedError

        fake = _fake_models(_notice_gen, _notice_gen())
        monkeypatch.setattr(loop_mod, "models", fake)

        with pytest.raises(LLMDeniedError):
            await loop_mod.execute_agent_loop(
                task="haz algo",
                agent_type="developer",
                history=[],
                allowed_tools=None,
                workspace="main",
                on_stream_chunk=AsyncMock(),
            )


def _empty_stop_chunks():
    """Firma stop-vacío: finish='stop', 0 texto, 0 tools — el residuo que ni
    el starve-retry (length) ni el guard del fantasma (tool_calls) atrapan;
    el loop lo re-prompta."""

    async def _gen(**kwargs):
        yield StreamChunk(finish_reason="stop", is_done=True)
        yield StreamChunk(
            usage={
                "prompt_tokens": 3300,
                "completion_tokens": 10,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
            is_done=True,
        )

    return _gen


class TestNudgeFinalVacio:
    """El final vacío recibe un re-prompt pidiendo el cierre explícito en
    vez de entregar '' como respuesta del paso."""

    @pytest.mark.asyncio
    async def test_nudge_recupera_final_vacio(self, monkeypatch):
        import orchestration.loop as loop_mod

        fake = _fake_models(_empty_stop_chunks(), _good_chunks("resultado recuperado"))
        monkeypatch.setattr(loop_mod, "models", fake)

        result = await loop_mod.execute_agent_loop(
            task="resume tu trabajo",
            agent_type="developer",
            history=[],
            allowed_tools=None,
            workspace="main",
            on_stream_chunk=AsyncMock(),
        )

        assert result["status"] == "completed"
        assert result["result"] == "resultado recuperado"
        assert _FakeModels.calls == 2

    @pytest.mark.asyncio
    async def test_nudge_viaja_en_el_prompt_de_la_recuperacion(self, monkeypatch):
        """La segunda llamada lleva el mensaje de cierre explícito."""
        import orchestration.loop as loop_mod

        vistos: list[dict] = []

        class _Captura(_FakeModels):
            def call_stream(self, **kwargs):
                vistos.append(kwargs)
                return super().call_stream(**kwargs)

        # el contador que lee call_stream vive en _FakeModels (la subclase
        # no lo hereda dinámicamente) — reset al atributo correcto
        _FakeModels.calls = 0
        fake = _Captura((_empty_stop_chunks(), _good_chunks("listo")))
        monkeypatch.setattr(loop_mod, "models", fake)

        await loop_mod.execute_agent_loop(
            task="resume tu trabajo",
            agent_type="developer",
            history=[],
            allowed_tools=None,
            workspace="main",
            on_stream_chunk=AsyncMock(),
        )

        assert any(
            m.get("role") == "user" and "terminó sin respuesta" in (m.get("content") or "")
            for m in vistos[-1]["messages"]
        )

    @pytest.mark.asyncio
    async def test_nudge_una_sola_vez_luego_vacio_persistente(self, monkeypatch):
        """El nudge no buclea: tras una recuperación fallida el final vacío se
        entrega como estaba (el motor reintenta el paso o falla honesto)."""
        import orchestration.loop as loop_mod

        fake = _fake_models(_empty_stop_chunks(), _empty_stop_chunks())
        monkeypatch.setattr(loop_mod, "models", fake)

        result = await loop_mod.execute_agent_loop(
            task="resume tu trabajo",
            agent_type="developer",
            history=[],
            allowed_tools=None,
            workspace="main",
            on_stream_chunk=AsyncMock(),
        )

        assert _FakeModels.calls == 2
        assert result["status"] == "completed"
        assert result["result"] == ""

    @pytest.mark.asyncio
    async def test_nudge_en_ruta_nativa(self, monkeypatch):
        """Paridad del re-prompt en la ruta call() (sin streaming)."""

        class _NativeFake:
            calls = 0

            async def call(self, **kwargs):
                _NativeFake.calls += 1
                if _NativeFake.calls == 1:
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                message=SimpleNamespace(content=""),
                                finish_reason="stop",
                            )
                        ]
                    )
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="respuesta nativa"),
                            finish_reason="stop",
                        )
                    ]
                )

        import orchestration.loop as loop_mod

        monkeypatch.setattr(loop_mod, "models", _NativeFake())

        result = await loop_mod.execute_agent_loop(
            task="resume tu trabajo",
            agent_type="developer",
            history=[],
            allowed_tools=["load_skill"],
            workspace="main",
        )

        assert _NativeFake.calls == 2
        assert result["result"] == "respuesta nativa"


def _simple_response(content="x", finish="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)]
    )


class TestControllerFallbackMarcaError:
    """El controller marca system_notice cuando el fallback devuelve el
    objeto de error normalizado (content='❌ ...' con .error seteado)."""

    @pytest.mark.asyncio
    async def test_fallback_con_error_normalizado_viaja_marcado(self, monkeypatch):
        import llm.controller as controller_mod

        # hermeticidad: el limiter es un singleton global — otro test puede
        # dejarlo sin slots y su aviso (no el del fallback) llegaría primero
        class _LimiterSiempre:
            async def acquire(self):
                return True

            async def wait_and_acquire(self, timeout=30):
                return True

        monkeypatch.setattr("core.rate_limiter.get_rate_limiter", lambda: _LimiterSiempre())

        controller = controller_mod.models
        chunks = []
        err_resp = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="❌ Ollama también falló."),
                    finish_reason="stop",
                )
            ],
            error="ollama_down",
        )

        async def _fake_stream_openai(
            self, client, model, messages, temp, tools, tool_choice, **kw
        ):
            # primera pasada: el stream siempre 'falla' → fuerza el fallback
            raise RuntimeError("stream roto")
            yield  # pragma: no cover — generator

        async def _fake_call(self, *a, **kw):
            return err_resp

        monkeypatch.setattr(
            controller_mod.LLMProvider,
            "get_async_client_with_provider",
            lambda role, temperature=None: (object(), "m", 0.7, "ollama"),
        )
        monkeypatch.setattr(
            controller_mod.ModelsController, "_stream_openai_async", _fake_stream_openai
        )
        # hermeticidad: tests previos que parchean "…models.call" con
        # monkeypatch dejan un bound method COMO attr de instancia (el undo
        # de pytest lo restaura ahí), que sombrea cualquier parche de clase.
        # Parchear la instancia con el método YA BOUND sombrea el leak y
        # despacha con self correcto.
        monkeypatch.setattr(controller, "call", _fake_call.__get__(controller))

        async for chunk in controller.call_stream(
            messages=[{"role": "user", "content": "hola"}], role="agent", workspace="main"
        ):
            chunks.append(chunk)

        assert chunks and getattr(chunks[-1], "system_notice", None) is True
        assert "Ollama también falló" in (chunks[-1].text or "")
