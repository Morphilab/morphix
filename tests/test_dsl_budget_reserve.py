# tests/test_dsl_budget_reserve.py
"""Budget con reserva: sin ciclos de 0 iteraciones y con cierre garantizado."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_loop_no_crea_ciclos_sin_budget_y_marca_pendientes():
    rt = FakeRuntime(agent_output="ok", tokens_per_call=40000)
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
        budget_tokens=50000,
        budget_reserve_final=10000,
        budget_reserve_replan=5000,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b", "c"]})
    plan = [i for i in res.run_state["plan"] if i["kind"] == "subtask"]
    assert [i["status"] for i in plan] == ["delivered", "blocked", "blocked"]
    assert len(rt.agent_calls) == 1
    assert res.run_state["budget"]["spent"] == 40000


@pytest.mark.asyncio
async def test_reserva_final_permite_el_step_post_loop():
    rt = FakeRuntime(agent_output="ok", tokens_per_call=40000)
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            },
            {"id": "traza", "kind": "agent", "agent": "analista", "goal": "traza"},
        ],
        budget_tokens=90000,
        budget_reserve_final=20000,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    assert res.status == "completed"
    assert any("traza" in c[1] for c in rt.agent_calls)


@pytest.mark.asyncio
async def test_loop_paralelo_no_arranca_mas_items_de_los_que_el_budget_paga():
    """El gate por lote no puede lanzar 5 hermanos con budget para 2."""
    rt = FakeRuntime(agent_output="ok", tokens_per_call=8000)
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 5,
                "parallel": True,
                "parallel_max": 5,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ],
        budget_tokens=30000,
        budget_reserve_final=5000,
        budget_reserve_replan=5000,
    )
    res = await WorkflowEngine(rt).run(
        cw, inputs={"query": "q", "items": ["a", "b", "c", "d", "e"]}
    )
    assert len(rt.agent_calls) == 2  # (30k - 10k reservas) / 8k por ciclo
    statuses = [p["status"] for p in res.run_state["plan"] if p["kind"] == "subtask"]
    assert statuses.count("blocked") == 3
