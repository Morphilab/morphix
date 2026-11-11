# tests/test_dsl_loop_until_over.py
"""`until` con `over`: el early-exit intencional no marca el loop degradado."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_until_con_over_sale_antes_sin_degradar():
    rt = FakeRuntime(agent_output="LISTO")
    cw = _compile(
        [
            {
                "id": "ciclo",
                "kind": "loop",
                "over": "items",
                "max_iter": 3,
                "until": {"type": "tool", "tool": "test_runner", "check": "ok"},
                "body": [
                    {"id": "impl", "kind": "agent", "agent": "developer", "goal": "haz $item"}
                ],
            }
        ]
    )
    rt.tool_results["test_runner"] = {"ok": True}
    result = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b", "c"]})
    assert result.status == "completed"
    assert not any("loop:ciclo" in d for d in rt._degraded_steps)
    assert len(rt.agent_calls) == 1


@pytest.mark.asyncio
async def test_over_truncado_por_max_iter_sigue_degradando():
    rt = FakeRuntime()
    cw = _compile(
        [
            {
                "id": "ciclo",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "body": [
                    {"id": "impl", "kind": "agent", "agent": "developer", "goal": "haz $item"}
                ],
            }
        ]
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b", "c"]})
    assert any("loop:ciclo" in d for d in rt._degraded_steps)


@pytest.mark.asyncio
async def test_until_con_over_resume_no_degrada():
    """El early-exit tras un resume tampoco marca el loop degradado."""
    rt = FakeRuntime(agent_output="LISTO")
    cw = _compile(
        [
            {
                "id": "ciclo",
                "kind": "loop",
                "over": "items",
                "max_iter": 3,
                "until": {"type": "tool", "tool": "test_runner", "check": "ok"},
                "body": [
                    {"id": "impl", "kind": "agent", "agent": "developer", "goal": "haz $item"}
                ],
            }
        ]
    )
    rt.tool_results["test_runner"] = {"ok": True}
    resume = {
        "vars": {"query": "q", "items": ["a", "b", "c"]},
        "loop_state": {"ciclo": 1},
        "completed": [],
    }
    result = await WorkflowEngine(rt).run(cw, resume=resume)
    assert result.status == "completed"
    assert not any("loop:ciclo" in d for d in rt._degraded_steps)
    assert len(rt.agent_calls) == 1
