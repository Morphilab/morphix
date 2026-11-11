"""La reserva final del run paga al step de cierre que la declara.

`available_for_work` excluye la reserva final para que los loops no la
consuman; `AgentStep.use_reserve_final` abre un scope que sube el techo del
agent-loop SOLO para ese step y lo restaura al terminar.
"""

import pytest
from _dsl_engine_support import FakeRuntime, _agent, _compile

from orchestration.dsl.engine import WorkflowEngine
from tools.orchestrator import (
    ToolOrchestrator,
    get_token_budget_max,
    pop_budget_ceiling,
    push_budget_extra,
    set_token_budget_max,
)


def test_push_budget_extra_no_supera_el_techo_del_preset():
    """La reserva ya vive DENTRO del budget: abrirla no puede sumar
    otro presupuesto encima y romper el techo global."""
    ToolOrchestrator.reset_token_budget()
    try:
        set_token_budget_max(100)
        assert get_token_budget_max() == 100
        push_budget_extra(30)
        assert get_token_budget_max() == 100
        pop_budget_ceiling()
        assert get_token_budget_max() == 100
    finally:
        ToolOrchestrator.reset_token_budget()


@pytest.mark.asyncio
async def test_agente_con_use_reserve_final_ve_el_techo_del_preset():
    ToolOrchestrator.reset_token_budget()
    set_token_budget_max(1000)

    class _VeTecho(FakeRuntime):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.maxes = []

        async def call_agent(self, *a, **kw):
            self.maxes.append(get_token_budget_max())
            return await super().call_agent(*a, **kw)

    rt = _VeTecho()
    cw = _compile(
        [_agent("reparar", use_reserve_final=True)],
        budget_tokens=1000,
        budget_reserve_final=300,
    )
    try:
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert res.status == "completed"
        assert rt.maxes == [1000]
        assert get_token_budget_max() == 1000
    finally:
        ToolOrchestrator.reset_token_budget()


@pytest.mark.asyncio
async def test_la_reserva_gastada_no_se_puede_gastar_dos_veces():
    from orchestration.dsl.run_state import RunState

    rt = FakeRuntime(agent_output="ok", tokens_per_call=200)
    cw = _compile(
        [_agent("reparar", use_reserve_final=True)],
        budget_tokens=1000,
        budget_reserve_final=300,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    budget = res.run_state["budget"]
    assert budget["reserve_final_spent"] == 200
    state = RunState.from_dict(res.run_state)
    assert state.budget.available_for_work() == 700


@pytest.mark.asyncio
async def test_reserva_con_retry_cuenta_los_intentos(monkeypatch):
    from orchestration.dsl.run_state import RunState

    usage = {"n": 0}

    class _FallaUna(FakeRuntime):
        async def call_agent(self, *a, **kw):
            usage["n"] += 150
            if not self.agent_calls:
                self.agent_calls.append((a[0], a[1]))
                raise RuntimeError("boom")
            return await super().call_agent(*a, **kw)

    monkeypatch.setattr("tools.orchestrator.get_llm_token_usage", lambda: usage["n"])
    rt = _FallaUna(agent_output="ok", tokens_per_call=200)
    cw = _compile(
        [_agent("reparar", use_reserve_final=True, retry_max=1)],
        budget_tokens=1000,
        budget_reserve_final=300,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    budget = res.run_state["budget"]
    assert budget["reserve_final_spent"] == 300
    assert RunState.from_dict(res.run_state).budget.available_for_work() == 700


@pytest.mark.asyncio
async def test_la_reserva_no_supera_el_techo_con_trabajo_agotado(monkeypatch):
    """Con 700/1000 gastados, el step de cierre ve 1000 (no 1300): la reserva
    es parte del preset, no un extra sobre el global."""
    usage = {"n": 700}
    monkeypatch.setattr("tools.orchestrator.get_llm_token_usage", lambda: usage["n"])

    class _VeTecho(FakeRuntime):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.maxes = []

        async def call_agent(self, *a, **kw):
            self.maxes.append(get_token_budget_max())
            return await super().call_agent(*a, **kw)

    rt = _VeTecho(agent_output="ok", tokens_per_call=200)
    cw = _compile(
        [_agent("reparar", use_reserve_final=True)],
        budget_tokens=1000,
        budget_reserve_final=300,
    )
    ToolOrchestrator.reset_token_budget()
    set_token_budget_max(1000)
    try:
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert res.status == "completed"
        assert rt.maxes == [1000]
    finally:
        ToolOrchestrator.reset_token_budget()
