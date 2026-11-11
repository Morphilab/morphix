"""Bookkeeping del plan: fases plugin/aggregate, skips y fallos.

El RunState es la fuente de verdad del panel y del journal: un paso que
corrió no puede quedar `pending`, y un paso que no corrió por `when` no
puede parecer pendiente de ejecución.
"""

import pytest
from _dsl_engine_support import FakeRuntime, _agent, _compile

from orchestration.dsl.engine import AgentOutcome, WorkflowEngine


@pytest.mark.asyncio
async def test_plugin_y_aggregate_marcan_la_fase_delivered():
    import orchestration.dsl.plugins  # noqa: F401 — registro de primitivas

    rt = FakeRuntime(agent_output="## LISTA\n- a\n- b")
    cw = _compile(
        [
            _agent("extraer", outputs=["texto"]),
            {
                "id": "parse",
                "kind": "plugin",
                "plugin": "text_to_list",
                "params": {"var": "texto", "out": "items", "marker": "## LISTA"},
            },
            {"id": "resumen", "kind": "aggregate", "strategy": "result"},
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    plan = {i["id"]: i for i in res.run_state["plan"] if i["kind"] == "phase"}
    assert plan["parse"]["status"] == "delivered"
    assert plan["resumen"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_when_falso_marca_skipped_y_no_llama_al_agente():
    rt = FakeRuntime()
    cw = _compile([_agent("opcional", when="!$flag", goal="no")])
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "flag": True})
    item = next(i for i in res.run_state["plan"] if i["id"] == "opcional")
    assert item["status"] == "skipped"
    assert rt.agent_calls == []


@pytest.mark.asyncio
async def test_agente_que_falla_marca_la_fase_failed():
    class _Falla(FakeRuntime):
        async def call_agent(self, *a, **kw):
            raise RuntimeError("boom")

    rt = _Falla()
    cw = _compile([_agent("a")])
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.status == "failed"
    item = next(i for i in res.run_state["plan"] if i["id"] == "a")
    assert item["status"] == "failed"
    assert "boom" in (item["last_error"] or "")


@pytest.mark.asyncio
async def test_on_error_continue_marca_failed_y_sigue():
    class _FallaUna(FakeRuntime):
        async def call_agent(self, *a, **kw):
            if not self.agent_calls:
                self.agent_calls.append((a[0], a[1]))
                raise RuntimeError("boom")
            return await super().call_agent(*a, **kw)

    rt = _FallaUna()
    cw = _compile([_agent("a", on_error="continue"), _agent("b")])
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    assert res.status == "completed"
    plan = {i["id"]: i for i in res.run_state["plan"]}
    assert plan["a"]["status"] == "failed"
    assert plan["b"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_agente_stalled_deja_last_error():
    class _Stalled(FakeRuntime):
        async def call_agent(self, *a, **kw):
            return AgentOutcome(status="stalled", text="explorando", iterations=8, tokens=100)

    rt = _Stalled()
    cw = _compile([_agent("traza")])
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    item = next(i for i in res.run_state["plan"] if i["id"] == "traza")
    assert item["status"] == "unmeasured"
    assert item["last_error"] == "agente stalled"


@pytest.mark.asyncio
async def test_fallo_en_loop_marca_la_subtarea_failed():
    class _FallaSegunda(FakeRuntime):
        async def call_agent(self, *a, **kw):
            if len(self.agent_calls) == 1:
                raise RuntimeError("boom")
            return await super().call_agent(*a, **kw)

    rt = _FallaSegunda()
    cw = _compile(
        [
            {
                "id": "l",
                "kind": "loop",
                "over": "items",
                "max_iter": 2,
                "body": [_agent("a", goal="$item")],
            }
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "items": ["x", "y"]})
    assert res.status == "failed"
    plan = {i["path"]: i for i in res.run_state["plan"] if i["kind"] == "subtask"}
    assert plan["l#1"]["status"] == "delivered"
    assert plan["l#2"]["status"] == "failed"


@pytest.mark.asyncio
async def test_when_falso_no_duplica_la_nota_en_resume():
    rt = FakeRuntime()
    cw = _compile(
        [
            _agent("opcional", when="!$flag", goal="no"),
            {"id": "p", "kind": "checkpoint", "question": "¿sigo?"},
        ]
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "flag": True})
    assert res.status == "paused"
    rt2 = FakeRuntime()
    rt2.checkpoint_approved = True
    res2 = await WorkflowEngine(rt2).run(cw, resume=res.snapshot)
    item = next(i for i in res2.run_state["plan"] if i["id"] == "opcional")
    assert item["notes"].count("when falso: !$flag") == 1


class TestProducesMensajeYResiduo:
    """El mensaje de produces interpola los patrones y una entrega por
    verificación no conserva el last_error del intento fallido."""

    @pytest.mark.asyncio
    async def test_produces_interpola_los_patrones_en_el_mensaje(self):
        rt = FakeRuntime(tool_results={"ruta": {"path": "tests/test_x.py"}})
        cw = _compile(
            [
                {
                    "id": "ruta",
                    "kind": "tool",
                    "tool": "ruta",
                    "args": {},
                    "outputs": {"test_path": "path"},
                },
                _agent("impl", produces=["$test_path", "*.py"]),
            ]
        )
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        item = next(p for p in res.run_state["plan"] if p["id"] == "impl")
        assert item["status"] == "unmeasured"
        assert item["last_error"] == "faltan artefactos: tests/test_x.py, *.py"

    @pytest.mark.asyncio
    async def test_until_verde_limpia_el_error_de_produces(self):
        rt = FakeRuntime(
            tool_results={
                "test_runner": {
                    "tests_all_pass": True,
                    "passed_count": 2,
                    "failed_count": 0,
                    "total_count": 2,
                }
            }
        )
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {
                        "type": "tool",
                        "tool": "test_runner",
                        "check": "tests_all_pass",
                        "args": {"file_path": "tests/test_x.py"},
                    },
                    "body": [_agent("impl", produces=["tests/test_x.py", "*.py"])],
                }
            ]
        )
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        item = next(p for p in res.run_state["plan"] if p["id"] == "ciclo")
        assert item["status"] == "delivered"
        assert item["last_error"] is None
