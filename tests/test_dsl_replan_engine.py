# tests/test_dsl_replan_engine.py
"""El loop dispara y aplica el replan con límites (replace/skip/stop/retry)."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine
from orchestration.dsl.run_state import ReplanDecision, ReplanItem


def _replan_cw(*, retry_on_verification=0):
    return _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 4,
                "retry_on_verification": retry_on_verification,
                "replan": {"max": 1, "max_new_items": 2},
                "body": [
                    {
                        "id": "a",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "$item",
                        "produces": ["x.py"],
                        "verified_by": "ver",
                    },
                    {
                        "id": "ver",
                        "kind": "tool",
                        "tool": "test_runner",
                        "args": {"file_path": "tests/x.py"},
                        "outputs": {"pasan": "tests_all_pass"},
                        "verdict": "pasan",
                    },
                ],
            }
        ]
    )


def _tool_sequence(*results):
    calls = {"n": 0}

    async def _tool(tool, args):
        idx = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        return results[idx]

    return _tool


@pytest.mark.asyncio
async def test_replan_replace_reemplaza_los_pendientes_y_corre_los_nuevos():
    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        replan_decision=ReplanDecision(
            action="replace",
            items=[ReplanItem(goal="n1"), ReplanItem(goal="n2")],
            reason="x",
        ),
    )
    rt.call_tool = _tool_sequence(
        {"tests_all_pass": False, "failed_count": 1},
        {"tests_all_pass": True, "passed_count": 1},
        {"tests_all_pass": True, "passed_count": 1},
    )
    res = await WorkflowEngine(rt).run(_replan_cw(), inputs={"query": "q", "items": ["orig"]})
    prompts = [c[1] for c in rt.agent_calls]
    assert "orig" in prompts[0]
    assert "n1" in prompts[1]
    assert "n2" in prompts[2]
    assert len(res.run_state["replans"]) == 1
    assert res.run_state["replans"][0]["action"] == "replace"


@pytest.mark.asyncio
async def test_replan_invalido_termina_en_stop():
    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        replan_decision=ReplanDecision(action="replace", items=[], reason="x"),
    )
    rt.call_tool = _tool_sequence({"tests_all_pass": False, "failed_count": 1})
    res = await WorkflowEngine(rt).run(_replan_cw(), inputs={"query": "q", "items": ["orig"]})
    assert res.run_state["replans"][0]["action"] == "stop"
    assert res.run_state["plan"][0]["status"] in ("failed", "blocked", "unmeasured")


@pytest.mark.asyncio
async def test_sin_budget_no_llama_al_replanner():
    rt = FakeRuntime(agent_output="ok", tokens_per_call=40000)
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "replan": {"max": 1, "max_new_items": 2, "min_remaining_tokens": 20000},
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ],
        budget_tokens=50000,
        budget_reserve_final=10000,
        budget_reserve_replan=5000,
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    assert rt.replan_calls == 0


@pytest.mark.asyncio
async def test_retry_on_verification_reintenta_una_vez_sin_replan():
    rt = FakeRuntime(agent_output="ok", files_written=["x.py"])
    rt.call_tool = _tool_sequence(
        {"tests_all_pass": False, "failed_count": 1},
        {"tests_all_pass": True, "passed_count": 1},
    )
    res = await WorkflowEngine(rt).run(
        _replan_cw(retry_on_verification=1),
        inputs={"query": "q", "items": ["orig"]},
    )
    assert len(rt.agent_calls) == 2
    assert "REINTENTO" in rt.agent_calls[1][1]
    assert rt.replan_calls == 0
    assert res.run_state["replans"] == []


@pytest.mark.asyncio
async def test_replace_no_deja_pendientes_fantasma():
    """El `over` se actualiza con el plan nuevo: sin eso la re-materialización
    re-agregaba los ítems originales como pendientes que nadie iba a correr."""
    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        replan_decision=ReplanDecision(
            action="replace", items=[ReplanItem(goal="n1")], reason="alcance menor"
        ),
    )
    rt.call_tool = _tool_sequence(
        {"tests_all_pass": False, "failed_count": 1},
        {"tests_all_pass": True, "passed_count": 1},
    )
    res = await WorkflowEngine(rt).run(
        _replan_cw(), inputs={"query": "q", "items": ["a", "b", "c"]}
    )
    subtasks = [p for p in res.run_state["plan"] if p["kind"] == "subtask"]
    assert [p["goal"] for p in subtasks] == ["n1"]
    assert all(p["status"] != "pending" for p in subtasks)


@pytest.mark.asyncio
async def test_replanner_recibe_el_tail_de_la_verificacion():
    """El planner decide sobre la EVIDENCIA; public=True le borraba el tail."""
    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        replan_decision=ReplanDecision(action="stop", reason="x"),
    )
    rt.call_tool = _tool_sequence(
        {"tests_all_pass": False, "failed_count": 1, "output": "1 failed: total 30 != 27"}
    )
    await WorkflowEngine(rt).run(_replan_cw(), inputs={"query": "q", "items": ["a"]})
    evidence = rt.replan_requests[0]["evidence"]
    assert "total 30 != 27" in evidence["verification"]["output_tail"]


@pytest.mark.asyncio
async def test_retry_hint_incluye_el_tail_y_el_artefacto_esperado():
    """El hint se arma ANTES de resetear la evidencia y nombra el artefacto."""
    rt = FakeRuntime(agent_output="ok", files_written=["x.py"])
    rt.call_tool = _tool_sequence(
        {"tests_all_pass": False, "failed_count": 1, "output": "1 failed: total 30 != 27"},
        {"tests_all_pass": True, "passed_count": 1},
    )
    await WorkflowEngine(rt).run(
        _replan_cw(retry_on_verification=1),
        inputs={"query": "q", "items": ["orig"]},
    )
    prompt = rt.agent_calls[1][1]
    assert "total 30 != 27" in prompt
    assert "x.py" in prompt


@pytest.mark.asyncio
async def test_skip_sin_evidencia_medida_termina_en_stop():
    from orchestration.dsl.run_state import SkipItem

    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        replan_decision=ReplanDecision(
            action="skip",
            skipped=[SkipItem(item_id="l#1", reason="x", satisfied_by="inventado")],
            reason="x",
        ),
    )
    rt.call_tool = _tool_sequence({"tests_all_pass": False, "failed_count": 1})
    res = await WorkflowEngine(rt).run(_replan_cw(), inputs={"query": "q", "items": ["a"]})
    assert res.run_state["replans"][0]["action"] == "stop"


@pytest.mark.asyncio
async def test_skip_con_archivo_del_run_marca_skipped():
    from orchestration.dsl.run_state import SkipItem

    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        replan_decision=ReplanDecision(
            action="skip",
            skipped=[SkipItem(item_id="l#1", reason="x", satisfied_by="x.py")],
            reason="x",
        ),
    )
    rt.call_tool = _tool_sequence({"tests_all_pass": False, "failed_count": 1})
    res = await WorkflowEngine(rt).run(_replan_cw(), inputs={"query": "q", "items": ["a"]})
    item = next(p for p in res.run_state["plan"] if p["kind"] == "subtask")
    assert item["status"] == "skipped"
    assert item["satisfied_by"] == "x.py"


@pytest.mark.asyncio
async def test_replan_no_se_llama_sin_margen_de_budget():
    """El replanner no puede comerse la reserva final: sin margen, stop."""
    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        tokens_per_call=8000,
        replan_decision=ReplanDecision(action="replace", items=[ReplanItem(goal="n1")], reason="x"),
    )
    rt.call_tool = _tool_sequence({"tests_all_pass": False, "failed_count": 1})
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "retry_on_verification": 0,
                "replan": {"max": 1},
                "body": [
                    {
                        "id": "a",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "$item",
                        "produces": ["x.py"],
                        "verified_by": "ver",
                    },
                    {
                        "id": "ver",
                        "kind": "tool",
                        "tool": "test_runner",
                        "args": {"file_path": "tests/x.py"},
                        "outputs": {"pasan": "tests_all_pass"},
                        "verdict": "pasan",
                    },
                ],
            }
        ],
        budget_tokens=20000,
        budget_reserve_final=5000,
        budget_reserve_replan=5000,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a"]})
    assert rt.replan_calls == 0
    assert res.run_state["replans"][0]["action"] == "stop"
    assert "budget" in res.run_state["replans"][0]["reason"]


@pytest.mark.asyncio
async def test_trigger_budget_llama_al_replanner_antes_de_bloquear():
    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        tokens_per_call=8000,
        replan_decision=ReplanDecision(action="stop", reason="sin budget para el resto"),
    )
    rt.call_tool = _tool_sequence({"tests_all_pass": True, "passed_count": 1})
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 5,
                "replan": {"max": 1, "triggers": ["budget"]},
                "body": [
                    {
                        "id": "a",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "$item",
                        "produces": ["x.py"],
                        "verified_by": "ver",
                    },
                    {
                        "id": "ver",
                        "kind": "tool",
                        "tool": "test_runner",
                        "args": {"file_path": "tests/x.py"},
                        "outputs": {"pasan": "tests_all_pass"},
                        "verdict": "pasan",
                    },
                ],
            }
        ],
        budget_tokens=30000,
        budget_reserve_final=5000,
        budget_reserve_replan=5000,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b", "c"]})
    assert rt.replan_calls == 1
    assert res.run_state["replans"][0]["trigger"] == "budget"
    assert res.run_state["replans"][0]["action"] == "stop"
    statuses = [p["status"] for p in res.run_state["plan"] if p["kind"] == "subtask"]
    assert statuses.count("blocked") == 1


@pytest.mark.asyncio
async def test_budget_con_replan_default_llama_al_replanner():
    rt = FakeRuntime(
        agent_output="ok",
        files_written=["x.py"],
        tokens_per_call=8000,
        replan_decision=ReplanDecision(action="stop", reason="sin budget"),
    )
    rt.call_tool = _tool_sequence({"tests_all_pass": True, "passed_count": 1})
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 5,
                "replan": {"max": 1},
                "body": [
                    {
                        "id": "a",
                        "kind": "agent",
                        "agent": "developer",
                        "goal": "$item",
                        "produces": ["x.py"],
                        "verified_by": "ver",
                    },
                    {
                        "id": "ver",
                        "kind": "tool",
                        "tool": "test_runner",
                        "args": {"file_path": "tests/x.py"},
                        "outputs": {"pasan": "tests_all_pass"},
                        "verdict": "pasan",
                    },
                ],
            }
        ],
        budget_tokens=30000,
        budget_reserve_final=5000,
        budget_reserve_replan=5000,
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b", "c"]})
    assert rt.replan_calls == 1
    assert res.run_state["replans"][0]["trigger"] == "budget"
    assert res.run_state["replans"][0]["action"] == "stop"


@pytest.mark.asyncio
async def test_bloque_run_state_lleva_el_intento_del_reintento_de_ciclo():
    """retry_on_verification re-entra el ciclo con el `for` del step en 1:
    el bloque de la 2ª ejecución no puede repetir el intento anterior."""
    rt = FakeRuntime(agent_output="ok", files_written=["x.py"])
    rt.call_tool = _tool_sequence(
        {"tests_all_pass": False, "failed_count": 1},
        {"tests_all_pass": True, "passed_count": 1},
    )
    await WorkflowEngine(rt).run(
        _replan_cw(retry_on_verification=1),
        inputs={"query": "q", "items": ["orig"]},
    )

    assert len(rt.agent_contexts) == 2
    assert "intento 1" in rt.agent_contexts[0]
    assert "intento 2" in rt.agent_contexts[1]
