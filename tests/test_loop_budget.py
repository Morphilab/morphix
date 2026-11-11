# tests/test_loop_budget.py — reserva atómica de llamadas LLM
"""El presupuesto en rojo corta ANTES de despachar la llamada LLM.

El chequeo de cabecera de iteración dejaba pasar el overcommit de pasos
paralelos (todos leían 179.x y todos despachaban → overshoot del techo);
la reserva del input estimado la cierra con la misma semántica atómica
que las tools (reserve → respuesta → refund; el uso real lo carga el
tracking).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _resp():
    """Respuesta mínima sin usage: el tracking no agrega tokens."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="listo"))])


@pytest.mark.asyncio
async def test_llm_no_se_despacha_con_presupuesto_sin_holgura(monkeypatch):
    from unittest.mock import AsyncMock

    import orchestration.loop as loop_mod
    import tools.orchestrator as tools_orch
    from tools.orchestrator import _TokenBudgetState

    estado = _TokenBudgetState(max_budget=1000)
    estado.total = 999  # 1 token de holgura: ninguna reserva de input cabe
    ctx_token = tools_orch._token_budget_ctx.set(estado)
    try:
        despachada = AsyncMock(return_value={"status": "completed", "result": "x"})
        monkeypatch.setattr("orchestration.loop.models.call", despachada)

        result = await loop_mod.execute_agent_loop(
            task="haz algo",
            agent_type="conversacional",
            history=[],
            allowed_tools=None,
            workspace="main",
        )
    finally:
        tools_orch._token_budget_ctx.reset(ctx_token)

    assert result["status"] == "stalled"
    assert "Presupuesto" in result["result"]
    despachada.assert_not_awaited()


@pytest.mark.asyncio
async def test_reserva_se_devuelve_tras_la_respuesta(monkeypatch):
    """La reserva es transitoria: tras la respuesta (sin usage reportado),
    el total del estado vuelve a su nivel previo."""
    from unittest.mock import AsyncMock

    import orchestration.loop as loop_mod
    import tools.orchestrator as tools_orch
    from tools.orchestrator import _TokenBudgetState

    estado = _TokenBudgetState(max_budget=1_000_000)
    estado.total = 100
    ctx_token = tools_orch._token_budget_ctx.set(estado)
    try:
        monkeypatch.setattr("orchestration.loop.models.call", AsyncMock(return_value=_resp()))
        result = await loop_mod.execute_agent_loop(
            task="haz algo",
            agent_type="conversacional",
            history=[],
            allowed_tools=None,
            workspace="main",
        )
    finally:
        tools_orch._token_budget_ctx.reset(ctx_token)

    assert result["status"] == "completed"
    assert result["result"] == "listo"
    # la reserva del input se devolvió: sin usage reportado, el total
    # vuelve exactamente a su nivel previo
    assert estado.total == 100


class _Resp:
    def __init__(self, content, tool_calls=None):
        self.choices = [
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason="stop",
            )
        ]


@pytest.mark.asyncio
async def test_verificacion_verde_al_tope_declara_completado(monkeypatch):
    """El patrón 'stalled con tests verdes': si la ÚLTIMA verificación está
    en verde, el tope de iteraciones se cierra como COMPLETED por evidencia
    — no como stalled."""
    from unittest.mock import AsyncMock

    import orchestration.loop as loop_mod
    import tools.orchestrator as tools_orch
    from tools.orchestrator import _TokenBudgetState

    estado = _TokenBudgetState(max_budget=1_000_000)
    ctx_token = tools_orch._token_budget_ctx.set(estado)

    # 7 iteraciones llamando test_runner (verde) sin declarar completado:
    # la 8ª (tope) debe cerrar COMPLETED por evidencia
    respuesta = _Resp(
        "hago verificación",
        tool_calls=[
            SimpleNamespace(
                id="1",
                function=SimpleNamespace(
                    name="test_runner", arguments='{"file_path": "tests/test_x.py"}'
                ),
            )
        ],
    )
    monkeypatch.setattr("orchestration.loop.models.call", AsyncMock(return_value=respuesta))
    monkeypatch.setattr(
        "orchestration.loop._execute_single_tool_call",
        AsyncMock(return_value=("3 passed, 0 failed", False, "", True)),
    )

    try:
        result = await loop_mod.execute_agent_loop(
            task="verifica",
            agent_type="developer",
            history=[],
            allowed_tools=["test_runner"],
            workspace="main",
        )
    finally:
        tools_orch._token_budget_ctx.reset(ctx_token)

    assert result["status"] == "completed"
    assert "verificación en verde" in result["result"].lower()


class TestTokensPorTarea:
    def test_contador_aislado_por_tarea(self):
        """El ContextVar de tokens LLM se copia por tarea: bajo gather,
        cada loop de agente cuenta solo sus propias llamadas (el estado
        de presupuesto compartido por referencia no sirve de métrica)."""
        import asyncio

        from tools.orchestrator import add_llm_token_usage, get_llm_call_tokens

        async def tarea():
            add_llm_token_usage(100)
            return get_llm_call_tokens()

        async def main():
            add_llm_token_usage(5)
            resultados = await asyncio.gather(tarea(), tarea())
            return get_llm_call_tokens(), resultados

        total, resultados = asyncio.run(main())
        # cada tarea parte de la base heredada (5) y suma solo lo suyo (100)
        assert resultados == [105, 105]
        # la escritura de las tareas NO se propaga al contexto padre: el
        # delta medido dentro de cada loop es exacto aunque corran en gather
        assert total == 5


def _mock_budget_settings(max_budget: int = 100_000):
    """Mock de settings con el presupuesto fijo (hermeticidad: sin esto el
    fake toca el limiter/techo global)."""
    s = MagicMock()
    s.tool_max_tokens_per_workflow = max_budget
    s.tool_enable_token_budget = True
    return s


class TestPresupuestoPorSubtarea:
    def test_push_acota_y_pop_restaura(self):
        """El techo por-subtarea = min(techo vigente, consumo_al_entrar +
        budget); pop restaura el techo previo (LIFO)."""
        from tools.orchestrator import (
            ToolOrchestrator,
            _token_budget_ctx,
            add_llm_token_usage,
            pop_budget_ceiling,
            push_budget_ceiling,
        )

        with patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=100_000)):
            ToolOrchestrator.reset_token_budget()
            state = _token_budget_ctx.get()
            add_llm_token_usage(30_000)  # consumo inicial del run
            assert state.max_budget == 100_000

            push_budget_ceiling(20_000)
            assert state.max_budget == 50_000  # 30k consumido + 20k de techo

            add_llm_token_usage(10_000)  # la subtarea gasta
            assert state.total == 40_000

            pop_budget_ceiling()
            assert state.max_budget == 100_000  # techo del run restaurado

    def test_push_sin_estado_es_noop(self):
        from tools import orchestrator as orch

        orch._token_budget_ctx.set(None)
        orch.push_budget_ceiling(5_000)  # no debe lanzar
        orch.pop_budget_ceiling()


class TestAdapterBudgetScope:
    @pytest.mark.asyncio
    async def test_wrappers_del_adapter_push_pop(self, monkeypatch):
        """ProductionRuntime.begin/end_budget_scope delegan en push/pop del
        orchestrator (bajo el motor real acotan cada vuelta del loop)."""
        from orchestration.dsl.runtime_adapter import ProductionRuntime
        from tools.orchestrator import ToolOrchestrator, _token_budget_ctx

        with patch("tools.orchestrator.settings", _mock_budget_settings(max_budget=100_000)):
            ToolOrchestrator.reset_token_budget()
            rt = ProductionRuntime.__new__(ProductionRuntime)  # sin deps de sesión
            await rt.begin_budget_scope(10_000)
            assert _token_budget_ctx.get().max_budget == 10_000
            await rt.end_budget_scope()
            assert _token_budget_ctx.get().max_budget == 100_000
