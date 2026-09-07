# tests/test_routine_tool_guards.py — REC-4
"""Rutinas autocontenidas: ROUTINE_DENIED_TOOLS aplicado de verdad.

Antes: ROUTINE_DENIED_TOOLS era control muerto — execute_prompt corría con
allowed_tools=None (sin restricción) y la intercepción de ask_clarification
vivía ANTES del gate de allowlist (pausas huérfanas de nadie)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestration import bots_clock
from orchestration import loop as loop_mod
from orchestration.context import clarification_denied

_cfg = loop_mod.AgentLoopConfig()


def _clarification_call() -> list[dict]:
    return [
        {"name": "ask_clarification", "arguments": {"question": "¿qué?", "options": []}, "id": "c1"}
    ]


def _run_tool_calls(tool_calls, messages):
    return asyncio_run(
        loop_mod._execute_tool_calls_and_check_stall(
            tool_calls=tool_calls,
            messages=messages,
            files_written=[],
            actions_taken=0,
            iteration_modified=False,
            consecutive_stalls=0,
            iteration=0,
            config=_cfg,
            project_root=".",
            workspace="main",
            events=None,
            repeat_tracker={},
            provider_kind="openai",
            allowed_tools=None,
        )
    )


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def settings_default_workflow() -> str:
    from core.config import settings

    return settings.default_workflow


class TestIntercepcionClarificacion:
    def test_sin_denegacion_pausa_como_siempre(self):
        """Control: fuera de rutinas la clarificación sigue pausando."""
        messages: list = []
        result = _run_tool_calls(_clarification_call(), messages)
        assert isinstance(result, dict)
        assert result["status"] == "clarification_needed"

    def test_con_denegacion_responde_y_no_pausa(self):
        """En una rutina ask_clarification se deniega con un
        tool_result accionable y el turno continúa (sin pausa huérfana)."""
        messages: list = []
        token = clarification_denied.set(True)
        try:
            result = _run_tool_calls(_clarification_call(), messages)
        finally:
            clarification_denied.reset(token)
        assert not isinstance(result, dict), (
            "REC-4: la rutina pausó con ask_clarification — nadie reanudará "
            "ese workflow (sesión huérfana)"
        )
        assert any(
            "no disponible" in str(m) for m in messages
        ), "la denegación debe explicarse al modelo como tool_result"


class TestExecutePromptRutina:
    @pytest.mark.asyncio
    async def test_rutina_de_bot_va_por_runner_sin_pausas_ni_dm(self, monkeypatch):
        """REC-4 desacoplado: la rutina de bot corre por
        bots_runner con transport='routine' — sin pausas (defensa REC-4 por
        transporte) y sin protocolo DM (dm_enabled=False forzado)."""

        captured: dict = {}

        async def _fake_run_bot_turn(slug, query, conv_id, **kwargs):
            captured["slug"] = slug
            captured["transport"] = kwargs.get("transport")
            captured["dm_enabled"] = kwargs.get("dm_enabled")
            return "respuesta rutina"

        import orchestration.bots_runner as _br

        monkeypatch.setattr(_br, "run_bot_turn", _fake_run_bot_turn)

        async def _conv(*a, **k):
            return 1

        monkeypatch.setattr(bots_clock, "_conversation_for_history", _conv)

        # persistencia del intercambio: mock del session context manager
        # (hermético — antes tocaba main.message REAL y dependía de que la
        # conversation 1 existiera en la BD del entorno)
        db = MagicMock()
        db.__aenter__ = AsyncMock(return_value=db)
        db.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("core.database.get_async_session", lambda: db)

        await bots_clock.execute_prompt("hola", "history", "botsl", "Rutina X")

        assert captured.get("transport") == "routine"
        assert captured.get("dm_enabled") is False, "rutinas sin protocolo DM (REC-4)"
        assert captured.get("slug") == "botsl"

    @pytest.mark.asyncio
    async def test_rutina_sin_bot_mantiene_deny_de_clarificacion(self, monkeypatch):
        """Rutina sin bot (prompt temporizado): clarification_denied activo y
        NUNCA pasa por el orquestador."""
        import orchestration.bots_clock as bc

        captured: dict = {}

        async def fake_loop(**kwargs):
            captured["clarif_denied"] = clarification_denied.get()
            return {"status": "completed", "result": "ok"}

        monkeypatch.setattr("orchestration.loop.execute_agent_loop", fake_loop)

        class _Boom:
            def __init__(self, *a, **k):
                raise AssertionError("la rutina sin bot NO debe pasar por el orquestador")

        monkeypatch.setattr("orchestration.workflows.orchestrator.WorkflowOrchestrator", _Boom)

        async def _conv(*a, **k):
            return 1

        monkeypatch.setattr(bc, "_conversation_for_history", _conv)

        db = MagicMock()
        db.__aenter__ = AsyncMock(return_value=db)
        db.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr("core.database.get_async_session", lambda: db)

        await bc.execute_prompt("hola", "history", None, "Rutina X")

        assert (
            captured.get("clarif_denied") is True
        ), "REC-4: el flag de denegación de clarificaciones no estaba activo"


@pytest.mark.asyncio
async def test_rutina_history_sin_bot_es_agente_simple_no_workflow(monkeypatch):
    """RC-Botmode: una rutina history SIN bot es un PROMPT
    temporizado al asistente — jamas un workflow orquestado. El camino previo
    (run_full_workflow con default_workflow) heredaba project.required del DSL
    'development' y cada corrida terminaba en el error de proyecto."""
    import orchestration.bots_clock as bc

    captured: dict = {}

    async def fake_loop(**kwargs):
        captured.update(kwargs)
        return {"status": "completed", "result": "refran de la abuela"}

    monkeypatch.setattr("orchestration.loop.execute_agent_loop", fake_loop)

    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("la rutina history sin bot NO debe pasar por el orquestador")

    monkeypatch.setattr("orchestration.workflows.orchestrator.WorkflowOrchestrator", _Boom)

    async def _conv(*a, **k):
        return 1

    monkeypatch.setattr(bc, "_conversation_for_history", _conv)

    added: list = []

    class _FakeSession:
        def add(self, obj):
            added.append(obj)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("core.database.get_async_session", lambda: _FakeSession())

    await bc.execute_prompt("dime un refran", "history", None, "refran")

    assert (
        captured.get("agent_type") == "conversacional"
    ), "la rutina corre como turno conversacional del asistente"
    assert "[Rutina refran]" in str(captured.get("task"))
    roles = [getattr(m, "role", None) for m in added]
    assert roles.count("user") == 1 and roles.count("assistant") == 1
