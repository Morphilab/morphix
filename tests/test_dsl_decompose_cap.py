"""El cap de ítems del decompose trunca ANTES del loop (no a mitad)."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_decompose_max_items_trunca_antes_del_loop():
    rt = FakeRuntime()  # decompose devuelve ["tarea_a", "tarea_b"]
    cw = _compile(
        [
            {
                "id": "plan",
                "kind": "decompose",
                "strategy": "flat",
                "output": "subtasks",
                "max_items": 1,
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.vars["subtasks"] == ["tarea_a"]
    item = next(p for p in res.run_state["plan"] if p["id"] == "plan")
    # el cap es por contrato: la truncación queda visible en la nota, pero no
    # degrada el proceso ni marca el step como sin medición.
    assert item["status"] == "delivered"
    assert "truncado" in " ".join(item["notes"])
    from orchestration.workflows.orchestrator import _dsl_scorecard

    assert _dsl_scorecard(res.run_state, tokens=1, elapsed_s=1)["calidad"] == "Alta"


@pytest.mark.asyncio
async def test_decompose_sin_max_items_no_trunca():
    rt = FakeRuntime()
    cw = _compile([{"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"}])
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.vars["subtasks"] == ["tarea_a", "tarea_b"]
