# tests/test_dsl_engine_run_state.py
"""RunState en el motor: plan materializado, evidencia y snapshot/resume."""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_loop_over_materializa_un_subtask_por_item():
    rt = FakeRuntime()
    cw = _compile(
        [
            {"id": "prev", "kind": "agent", "agent": "developer", "goal": "x"},
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 3,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            },
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    subtasks = [i for i in res.run_state["plan"] if i["kind"] == "subtask"]
    assert [i["goal"] for i in subtasks] == ["a", "b"]
    assert all(i["status"] == "delivered" for i in subtasks)


@pytest.mark.asyncio
async def test_until_sin_over_es_un_subtask_y_rojo_es_phase():
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            {"id": "rojo", "kind": "agent", "agent": "developer", "goal": "test"},
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 2,
                "until": {"type": "tool", "tool": "test_runner", "check": "ok"},
                "body": [{"id": "impl", "kind": "agent", "agent": "developer", "goal": "impl"}],
            },
        ]
    )
    rt.tool_results["test_runner"] = {"ok": True}
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    plan = res.run_state["plan"]
    assert any(i["id"] == "rojo" and i["kind"] == "phase" for i in plan)
    assert any(i["id"] == "ciclo" and i["kind"] == "subtask" for i in plan)


@pytest.mark.asyncio
async def test_loop_paralelo_materializa_y_marca_los_dos_items():
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "parallel": True,
                "max_iter": 2,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    subtasks = [i for i in res.run_state["plan"] if i["kind"] == "subtask"]
    assert [i["goal"] for i in subtasks] == ["a", "b"]
    assert all(i["status"] == "delivered" for i in subtasks)


@pytest.mark.asyncio
async def test_snapshot_en_pausa_incluye_run_state_y_resume_lo_restaura():
    rt = FakeRuntime()
    cw = _compile(
        [
            {"id": "a", "kind": "agent", "agent": "developer", "goal": "x"},
            {"id": "p", "kind": "checkpoint", "question": "¿sigo?"},
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.status == "paused"
    assert "run_state" in res.snapshot
    assert "agent_attempts" in res.snapshot
    rt2 = FakeRuntime()
    rt2.checkpoint_approved = True
    res2 = await WorkflowEngine(rt2).run(cw, resume=res.snapshot)
    assert res2.status == "completed"
    assert res2.run_state["run_id"] == res.snapshot["run_state"]["run_id"]


@pytest.mark.asyncio
async def test_agent_step_recibe_bloque_run_state_y_ve_el_paso_anterior():
    """La 2ª vuelta del loop ve el plan, el item anterior delivered y sus archivos."""
    rt = FakeRuntime(
        agent_output="ok",
        files_written_sequence=[["conversor.py"], ["tests/test_conversor.py"]],
    )
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ]
    )
    await WorkflowEngine(rt).run(
        cw, inputs={"query": "conversor de monedas", "items": ["implementar", "testear"]}
    )

    assert len(rt.agent_contexts) == 2
    primero, segundo = rt.agent_contexts
    assert "<RUN_STATE>" in primero
    assert "Goal: conversor de monedas" in primero
    assert "Current: implementar (intento 1)" in primero
    # la segunda vuelta ya ve el entregable de la primera
    assert "1. ✅ implementar" in segundo
    assert "2. 🔵 testear" in segundo
    assert "conversor.py" in segundo


@pytest.mark.asyncio
async def test_bloque_run_state_lleva_el_intento_del_reintento():
    """Un reintento no es un step nuevo: el bloque declara el intento real."""
    from orchestration.dsl.engine import AgentOutcome

    class _RetryRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(agent_output="ok")
            self.calls = 0

        async def call_agent(self, agent, prompt, *, extra_context="", **kwargs):
            self.calls += 1
            self.agent_contexts.append(extra_context)
            if self.calls == 1:
                raise RuntimeError("fallo transitorio")
            return AgentOutcome(
                status="completed", text="ok", files_written=[], iterations=1, tokens=0
            )

    rt = _RetryRuntime()
    cw = _compile(
        [
            {
                "id": "a",
                "kind": "agent",
                "agent": "developer",
                "goal": "haz algo",
                "retry_max": 1,
            }
        ]
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q"})

    assert len(rt.agent_contexts) == 2
    assert "Current: haz algo (intento 1)" in rt.agent_contexts[0]
    assert "Current: haz algo (intento 2)" in rt.agent_contexts[1]


@pytest.mark.asyncio
async def test_agent_step_interpola_el_goal_del_plan_y_del_bloque():
    """El plan se sembraba con el goal crudo: el agente y el reporte leían
    `$query`/`$spec` como placeholder en vez del valor real."""
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [{"id": "revisar", "kind": "agent", "agent": "developer", "goal": "Revisa $query"}]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "la API de notas"})

    assert res.run_state["plan"][0]["goal"] == "Revisa la API de notas"
    assert "Current: Revisa la API de notas" in rt.agent_contexts[0]


def test_state_restaura_agent_attempts_del_snapshot():
    """El contador de intentos viaja en el snapshot: un resume no repite 1."""
    from orchestration.dsl.engine import _State

    state = _State({}, {"agent_attempts": {"paso": 2}})
    assert state.agent_attempts == {"paso": 2}


@pytest.mark.asyncio
async def test_until_verde_recupera_los_stalls_de_sus_vueltas():
    """El until es la verificación del loop: si cierra verde, un stall de una
    vuelta deja de ser degradado y pasa a recuperadas (tdd con suite verde)."""
    from _dsl_engine_support import FakeRuntime, _compile

    from orchestration.dsl.engine import AgentOutcome, WorkflowEngine

    class _Stall(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.recovered: list[str] = []

        async def call_agent(self, agent, prompt, **kwargs):
            self.calls += 1
            return AgentOutcome(
                status="stalled" if self.calls == 1 else "completed",
                text="x",
                files_written=[],
                iterations=1,
                tokens=1,
            )

        def recover_steps(self, prefix: str) -> None:
            self.recovered.append(prefix)

    rt = _Stall()
    cw = _compile(
        [
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 3,
                "until": {"type": "tool", "tool": "check", "check": "ok"},
                "body": [{"id": "implementar", "kind": "agent", "agent": "developer", "goal": "x"}],
            }
        ]
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert "ciclo#" in rt.recovered


@pytest.mark.asyncio
async def test_el_engine_persiste_un_journal_por_step_de_nivel_superior():
    """Un crash a mitad no pierde el progreso: tras cada step top-level se
    escribe el journal parcial (los anidados de un loop no duplican el I/O)."""
    from _dsl_engine_support import FakeRuntime, _compile

    from orchestration.dsl.engine import WorkflowEngine

    class _Persist(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.persisted: list[int] = []

        def persist_run(self, run_state, step: int) -> None:
            self.persisted.append(step)

    rt = _Persist()
    cw = _compile(
        [
            {"id": "uno", "kind": "agent", "agent": "developer", "goal": "a"},
            {"id": "dos", "kind": "agent", "agent": "developer", "goal": "b"},
        ]
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert rt.persisted == [1, 2]


@pytest.mark.asyncio
async def test_loop_sin_over_no_se_marca_delivered_hasta_terminar():
    """Un loop ciego es UNA unidad de entrega: su estado no puede pasar a
    delivered cuando termina el primer agent-step de la ronda 1 (la GUI lo
    mostraba entregado con rondas pendientes)."""
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            {
                "id": "rondas",
                "kind": "loop",
                "max_iter": 3,
                "body": [
                    {"id": "op", "kind": "agent", "agent": "developer", "goal": "ronda $iter"}
                ],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})

    for emit in rt.emits:
        if emit.get("path") == "rondas":
            continue  # el emit del cierre del loop sí puede traer delivered
        plan = {i["id"]: i["status"] for i in (emit.get("run_state") or {}).get("plan", [])}
        assert plan.get("rondas") != "delivered", (
            f"el loop se marcó entregado antes de terminar: {emit.get('path')} "
            f"status={emit.get('status')} plan={plan}"
        )

    final = {i["id"]: i["status"] for i in res.run_state["plan"]}
    assert final["rondas"] == "delivered"


@pytest.mark.asyncio
async def test_loop_con_over_sigue_marcando_su_ciclo_delivered():
    """El ciclo de un loop con over sí es la unidad de entrega de sus steps."""
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    subtasks = [i for i in res.run_state["plan"] if i["kind"] == "subtask"]
    assert [i["status"] for i in subtasks] == ["delivered", "delivered"]


@pytest.mark.asyncio
async def test_loop_sin_over_conserva_resultados_completed_para_el_agregador():
    """El status del resultado es el del agent call, no el del loop entero.

    Antes el loop ciego se marcaba delivered y el resultado heredaba ese
    status; al corregir el plan, heredar "running" habría dejado al agregador
    con 0 entregas a mitad del debate.
    """
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            {
                "id": "rondas",
                "kind": "loop",
                "max_iter": 2,
                "body": [
                    {"id": "op", "kind": "agent", "agent": "developer", "goal": "ronda $iter"}
                ],
            },
            {"id": "resumen", "kind": "aggregate", "strategy": "confidence"},
        ]
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    statuses = [r["status"] for r in rt.aggregate_results[-1]]
    assert statuses == ["completed", "completed"]


@pytest.mark.asyncio
async def test_loop_con_over_sigue_poniendo_el_status_del_ciclo_en_los_resultados():
    rt = FakeRuntime(agent_output="ok")
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "$item"}],
            },
            {"id": "resumen", "kind": "aggregate", "strategy": "confidence"},
        ]
    )
    await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["a", "b"]})
    statuses = [r["status"] for r in rt.aggregate_results[-1]]
    assert statuses == ["delivered", "delivered"]
