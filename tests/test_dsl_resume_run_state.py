# tests/test_dsl_resume_run_state.py
"""Resume con RunState: budget/replans persistidos y sin repetir entregas."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_resume_restaura_budget_y_no_repite_delivered():
    rt = FakeRuntime(agent_output="ok", tokens_per_call=100)
    cw = _compile(
        [
            {"id": "a", "kind": "agent", "agent": "developer", "goal": "x"},
            {"id": "p", "kind": "checkpoint", "question": "¿sigo?"},
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.status == "paused"
    a = next(i for i in res.run_state["plan"] if i["id"] == "a")
    assert a["status"] == "delivered"
    assert res.run_state["budget"]["spent"] == 100

    rt2 = FakeRuntime(agent_output="ok", tokens_per_call=100)
    rt2.checkpoint_approved = True
    res2 = await WorkflowEngine(rt2).run(cw, resume=res.snapshot)
    assert res2.status == "completed"
    a2 = next(i for i in res2.run_state["plan"] if i["id"] == "a")
    assert a2["status"] == "delivered"
    assert res2.run_state["budget"]["spent"] == 100
    assert rt2.agent_calls == []


@pytest.mark.asyncio
async def test_resume_viejo_sin_run_state_no_crashea():
    rt = FakeRuntime(agent_output="ok")
    cw = _compile([{"id": "a", "kind": "agent", "agent": "developer", "goal": "x"}])
    res = await WorkflowEngine(rt).run(
        cw, resume={"vars": {"query": "q"}, "completed": [], "loop_state": {}}
    )
    assert res.status == "completed"
    assert res.run_state["run_id"]


@pytest.mark.asyncio
async def test_resume_restaura_replans_y_budget_persistido():
    rt = FakeRuntime(agent_output="ok", tokens_per_call=100)
    cw = _compile([{"id": "a", "kind": "agent", "agent": "developer", "goal": "x"}])
    resume = {
        "vars": {"query": "q"},
        "completed": [],
        "loop_state": {},
        "run_state": {
            "run_id": "r-persist",
            "goal": "q",
            "plan": [],
            "budget": {
                "limit": 1000,
                "spent": 50,
                "reserve_final": 10,
                "reserve_replan": 5,
            },
            "replans": [
                {
                    "at": "x",
                    "trigger": "budget",
                    "reason": "r",
                    "action": "stop",
                    "before": [],
                    "after": [],
                    "tokens": 0,
                }
            ],
            "memory": {},
        },
    }
    res = await WorkflowEngine(rt).run(cw, resume=resume)
    assert res.status == "completed"
    assert res.run_state["run_id"] == "r-persist"
    assert res.run_state["replans"][0]["action"] == "stop"
    assert res.run_state["budget"]["spent"] == 150
