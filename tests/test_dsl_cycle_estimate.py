"""El gate de budget estima el costo del PRÓXIMO ciclo con lo observado.

Un ciclo de 50k no puede arrancar con 20k disponibles solo porque el piso
global es 8k; y un preset puede declarar un piso mayor para sus loops.
"""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_ciclo_no_arranca_si_el_costo_observado_no_cabe():
    rt = FakeRuntime(agent_output="ok", tokens_per_call=50000)
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 3,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ],
        budget_tokens=120000,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b", "c"]})
    statuses = [p["status"] for p in res.run_state["plan"] if p["kind"] == "subtask"]
    assert statuses == ["delivered", "delivered", "blocked"]
    assert len(rt.agent_calls) == 2


@pytest.mark.asyncio
async def test_cycle_token_estimate_del_preset_es_el_piso():
    rt = FakeRuntime(agent_output="ok", tokens_per_call=0)
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 1,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ],
        budget_tokens=20000,
        cycle_token_estimate=30000,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a"]})
    assert [p["status"] for p in res.run_state["plan"] if p["kind"] == "subtask"] == ["blocked"]
    assert rt.agent_calls == []
