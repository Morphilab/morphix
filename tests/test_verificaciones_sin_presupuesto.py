# tests/test_verificaciones_sin_presupuesto.py — la reserva del gate final
"""El presupuesto frena al MODELO, no a la verificación del motor.

Caso real: runs que agotaron su cap dejaron las
verificaciones finales — incl. los verificar_agregado — rechazadas con
"Presupuesto de tokens excedido": el run cerraba Parcial sin medir su
propia entrega. Las verificaciones del motor (until, tool-steps,
verificar_agregado) son subprocess: no consumen presupuesto LLM, así que
el adapter las ejecuta con skip_budget=True. El agent loop (las tools que
alimentan al modelo) sigue frenado en rojo — esa es la asimetría que este
archivo fija.
"""

import pytest


def _estado_agotado():
    from tools.orchestrator import _TokenBudgetState

    estado = _TokenBudgetState(max_budget=1000)
    estado.total = 999  # sin holgura para NINGUNA reserva de input
    return estado


@pytest.mark.asyncio
async def test_verificacion_del_motor_corre_con_presupuesto_agotado(monkeypatch):
    """El call_tool del adapter (until/tool-steps/agregado) mide de verdad
    aunque el presupuesto esté en rojo — antes devolvía el bloqueo."""
    import tools.orchestrator as tools_orch
    from orchestration.dsl.conformance import _clone_signature
    from orchestration.dsl.runtime_adapter import ProductionRuntime
    from tools.loader import load_global_tools

    load_global_tools()
    handler = tools_orch.tools_registry.get_tool("test_runner")

    async def fake_test_runner(**kwargs):
        return {"success": True, "output": "3 passed in 0.1s", "returncode": 0}

    if handler is not None and _clone_signature(handler) is not None:
        fake_test_runner.__signature__ = _clone_signature(handler)  # type: ignore[attr-defined] # noqa: B010
    monkeypatch.setitem(tools_orch.tools_registry._tools, "test_runner", fake_test_runner)

    ctx = tools_orch._token_budget_ctx.set(_estado_agotado())
    try:
        rt = ProductionRuntime(workspace="main", query="q")
        result = await rt.call_tool("test_runner", {"file_path": "tests/x.py"})
    finally:
        tools_orch._token_budget_ctx.reset(ctx)

    assert result["success"] is True, result
    # el enriquecimiento del adapter sigue operando sobre la medición real
    assert result.get("tests_all_pass") is True


@pytest.mark.asyncio
async def test_tool_del_agente_sigue_bloqueada_con_presupuesto_agotado():
    """Regresión de la asimetría: sin skip_budget (el camino del agent loop)
    el bloqueo por presupuesto sigue igual — frena al modelo, no al gate."""
    from tools.loader import load_global_tools
    from tools.orchestrator import _token_budget_ctx
    from tools.wrapper import safe_tool_call

    load_global_tools()
    ctx = _token_budget_ctx.set(_estado_agotado())
    try:
        result = await safe_tool_call("test_runner", {"file_path": "x.py"}, role="agent")
    finally:
        _token_budget_ctx.reset(ctx)

    assert result["success"] is False
    assert "Presupuesto de tokens excedido" in str(result.get("output"))
