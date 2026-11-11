# tests/test_dsl_interpolation_cap.py
"""El cap de interpolación es por workflow: sdd puede pasar la spec completa."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine

_SPEC = "S" * 5000


@pytest.mark.asyncio
async def test_max_interp_chars_del_workflow_permite_spec_larga():
    rt = FakeRuntime()
    cw = _compile(
        [{"id": "plan", "kind": "agent", "agent": "developer", "goal": "Planifica: $spec"}],
        max_interp_chars=8000,
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q", "spec": _SPEC})
    prompt = rt.agent_calls[0][1]
    assert _SPEC in prompt


@pytest.mark.asyncio
async def test_default_acota_a_2000():
    rt = FakeRuntime()
    cw = _compile(
        [{"id": "plan", "kind": "agent", "agent": "developer", "goal": "Planifica: $spec"}]
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q", "spec": _SPEC})
    prompt = rt.agent_calls[0][1]
    assert "S" * 2000 in prompt
    assert "S" * 2001 not in prompt


@pytest.mark.asyncio
async def test_decide_usa_max_interp_chars():
    rt = FakeRuntime(agent_output=_SPEC, decide_answer="a")
    seen = {}

    async def decide(question, options, context, *, step_id=""):
        seen["context"] = context
        return "a"

    rt.decide = decide
    cw = _compile(
        [
            {"id": "gen", "kind": "agent", "agent": "developer", "goal": "genera"},
            {
                "id": "elegir",
                "kind": "decide",
                "question": "¿a o b?",
                "options": ["a", "b"],
                "fallback": "a",
                "branches": {"a": [], "b": []},
            },
        ],
        max_interp_chars=8000,
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert seen["context"] == _SPEC


@pytest.mark.asyncio
async def test_until_agent_usa_max_interp_chars():
    rt = FakeRuntime(agent_output=_SPEC, evaluate_answer="SI")
    seen = {}

    async def evaluate(question, expect, context):
        seen["context"] = context
        return "SI"

    rt.evaluate = evaluate
    cw = _compile(
        [
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 2,
                "until": {
                    "type": "agent",
                    "question": "¿listo?",
                    "expect": "SI",
                    "fallback": "exit",
                },
                "body": [{"id": "gen", "kind": "agent", "agent": "developer", "goal": "genera"}],
            }
        ],
        max_interp_chars=8000,
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert seen["context"] == _SPEC


@pytest.mark.asyncio
async def test_parallel_loop_hereda_max_interp_chars():
    """El estado hijo del loop paralelo hereda el cap del workflow."""
    rt = FakeRuntime()
    cw = _compile(
        [
            {
                "id": "par",
                "kind": "loop",
                "over": "items",
                "parallel": True,
                "max_iter": 2,
                "body": [
                    {
                        "id": "impl",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "Planifica: $spec",
                    }
                ],
            }
        ],
        max_interp_chars=8000,
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q", "spec": _SPEC, "items": ["a", "b"]})
    prompts = [p for _agent, p in rt.agent_calls]
    assert len(prompts) == 2
    assert all(_SPEC in p for p in prompts)
