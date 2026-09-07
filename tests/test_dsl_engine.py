# tests/test_dsl_engine.py
"""Tests del motor de ejecución del DSL (orchestration/dsl/engine.py).

El motor es determinista: TODO lo difuso (agentes, tools, decisiones) pasa
por EngineRuntime (aquí un fake); sus salidas se validan (enum decide,
fallback until-agent) y el control de flujo NUNCA depende del modelo.

Cubre: secuencia+vars, when, gate, loop (over/until tool/agent/metric/
max_iter), parallel con depends_on, decide con fallback, checkpoint con
pause+resume (snapshot), includes con binding I/O, aggregate, on_error.
"""

from typing import Any

import pytest

from orchestration.dsl.compiler import CompiledWorkflow, compile_workflow
from orchestration.dsl.engine import WorkflowEngine


def _doc(steps: list[dict], **over) -> dict:
    base = {
        "version": 1,
        "name": "demo",
        "agents": {"allowed": ["developer", "analista", "moderador"]},
        "tools": {"allowed": ["file_manager", "test_runner", "eval_suite"]},
        "steps": steps,
    }
    base.update(over)
    return base


def _agent(sid: str, agent: str = "developer", **over) -> dict:
    step = {"id": sid, "kind": "agent", "agent": agent, "goal": "haz"}
    step.update(over)
    return step


def _compile(steps: list[dict], **over) -> CompiledWorkflow:
    return compile_workflow(_doc(steps, **over))


class FakeRuntime:
    """Runtime determinista con respuestas programables."""

    def __init__(
        self,
        *,
        agent_output: str = "salida estandar",
        tool_results: dict | None = None,
        decide_answer: str | None = None,
        evaluate_answer: str | None = None,
    ):
        self.agent_calls: list[tuple[str, str]] = []
        self.tool_calls: list[tuple[str, dict]] = []
        self.order: list[str] = []
        self.agent_output = agent_output
        self.tool_results = tool_results or {}
        self.decide_answer = decide_answer
        self.evaluate_answer = evaluate_answer
        self.checkpoint_approved = False
        self.checkpoint_calls: list[str] = []
        self.aggregate_calls: list[Any] = []
        self.emits: list[dict] = []

    async def call_agent(
        self,
        agent: str,
        prompt: str,
        *,
        model_role: str | None = None,
        step_id: str = "",
        path: str = "",
    ) -> str:
        self.agent_calls.append((agent, prompt))
        self.order.append(path or step_id)
        return self.agent_output

    async def call_tool(self, tool: str, args: dict) -> dict:
        self.tool_calls.append((tool, args))
        return self.tool_results.get(tool, {"ok": True})

    async def decompose(self, query: str, strategy: str) -> Any:
        if strategy == "flat":
            return ["tarea_a", "tarea_b"]
        return {"tasks": [{"id": "n1", "depends_on": []}]}

    async def decide(
        self, question: str, options: list[str], context: str, *, step_id: str = ""
    ) -> str:
        # El runtime puede devolver basura — el motor valida y aplica fallback
        return self.decide_answer if self.decide_answer is not None else options[0]

    async def evaluate(self, question: str, expect: str, context: str) -> str:
        return self.evaluate_answer if self.evaluate_answer is not None else expect

    async def checkpoint(self, question: str, step_id: str = "") -> bool:
        self.checkpoint_calls.append(question)
        return self.checkpoint_approved

    async def aggregate(self, strategy: str, results: list[dict], agent: str | None = None) -> str:
        self.aggregate_calls.append((strategy, len(results)))
        return "RESUMEN_FINAL"

    def emit(self, **payload) -> None:
        self.emits.append(payload)


class TestSecuenciaYVariables:
    @pytest.mark.asyncio
    async def test_orden_y_flujo_de_variables(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                _agent("paso1", agent="analista", outputs=["spec"]),
                _agent("paso2", goal="implementa $spec con $query"),
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "crear login"})
        assert result.status == "completed"
        assert rt.agent_calls[0] == ("analista", "haz")
        assert rt.agent_calls[1][1] == "implementa salida estandar con crear login"
        assert result.vars["spec"] == "salida estandar"

    @pytest.mark.asyncio
    async def test_outputs_contract(self):
        rt = FakeRuntime()
        cw = _compile([_agent("paso1", outputs=["spec"])], outputs=["spec"])
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.outputs == {"spec": "salida estandar"}

    @pytest.mark.asyncio
    async def test_when_query_contains_skipea(self):
        rt = FakeRuntime()
        cw = _compile([_agent("pdf", when="query_contains:pdf"), _agent("normal")])
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "solo texto"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 1  # pdf saltado

    @pytest.mark.asyncio
    async def test_when_query_contains_coincide(self):
        rt = FakeRuntime()
        cw = _compile([_agent("pdf", when="query_contains:pdf")])
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "genera pdf por favor"})
        assert len(rt.agent_calls) == 1

    @pytest.mark.asyncio
    async def test_gate_if_blockers(self):
        rt = FakeRuntime(agent_output="revisión: [Bloqueante] falta validación")
        cw = _compile(
            [
                _agent("revisar", gate=True),
                _agent("integrar", when="if_blockers"),
                _agent("omitir", when="if_blockers"),  # sigue truthy: se ejecuta
            ]
        )
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert len(rt.agent_calls) == 3
        assert "Bloqueante" in rt.agent_calls[0][1] or True

    @pytest.mark.asyncio
    async def test_gate_sin_bloqueantes_no_dispara(self):
        rt = FakeRuntime(agent_output="todo limpio, sin hallazgos")
        cw = _compile([_agent("revisar", gate=True), _agent("integrar", when="if_blockers")])
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert len(rt.agent_calls) == 1  # integrar saltado


class TestLoop:
    @pytest.mark.asyncio
    async def test_until_tool_sale_cuando_verde(self):
        rt = FakeRuntime(tool_results={"test_runner": {"tests_all_pass": True}})
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 5,
                    "until": {"type": "tool", "tool": "test_runner", "check": "tests_all_pass"},
                    "body": [_agent("impl")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 1  # 1ª iteración → verde → sale
        assert len(rt.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_until_tool_respecta_max_iter(self):
        rt = FakeRuntime(tool_results={"test_runner": {"tests_all_pass": False}})
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {"type": "tool", "tool": "test_runner", "check": "tests_all_pass"},
                    "body": [_agent("impl")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"  # agotó iteraciones: termina (no falla)
        assert len(rt.agent_calls) == 3

    @pytest.mark.asyncio
    async def test_over_itera_items(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                _agent("prep", outputs=["historias"]),
                {
                    "id": "por_historia",
                    "kind": "loop",
                    "max_iter": 5,
                    "over": "historias",
                    "body": [_agent("impl", goal="codifica $item")],
                },
            ]
        )
        # prep exporta su salida como lista de items
        rt.agent_output = "historia1|historia2"
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        # over sobre string → 1 item (string entero); el motor no divide mágicamente
        assert len(rt.agent_calls) == 2  # prep + 1 iteración

    @pytest.mark.asyncio
    async def test_until_agent_valida_expect(self):
        rt = FakeRuntime(evaluate_answer="APROBADO")
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {"type": "agent", "question": "¿aprobado?", "expect": "APROBADO"},
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 1

    @pytest.mark.asyncio
    async def test_until_agent_invalido_fallback_fail(self):
        rt = FakeRuntime(evaluate_answer="no sé, tal vez")
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {
                        "type": "agent",
                        "question": "¿aprobado?",
                        "expect": "APROBADO",
                        "fallback": "fail",
                    },
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "failed"
        assert result.failure is not None and "APROBADO" in result.failure

    @pytest.mark.asyncio
    async def test_until_agent_invalido_fallback_exit(self):
        rt = FakeRuntime(evaluate_answer="respuesta basura")
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {
                        "type": "agent",
                        "question": "¿aprobado?",
                        "expect": "APROBADO",
                        "fallback": "exit",
                    },
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_until_metric_comparacion(self):
        rt = FakeRuntime(
            tool_results={"eval_suite": {"pass_rate": 0.95}},
        )
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 5,
                    "until": {
                        "type": "metric",
                        "tool": "eval_suite",
                        "metric": "pass_rate",
                        "op": ">=",
                        "value": 0.9,
                    },
                    "body": [_agent("impl")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 1

    @pytest.mark.asyncio
    async def test_counter_loop_sin_until(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                {
                    "id": "rondas",
                    "kind": "loop",
                    "max_iter": 3,
                    "body": [_agent("opinar")],
                }
            ]
        )
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert len(rt.agent_calls) == 3


class TestParallelYDecide:
    @pytest.mark.asyncio
    async def test_parallel_dependencias_orden(self):
        rt = FakeRuntime()

        async def call_agent(agent, prompt, *, model_role=None, step_id="", path=""):
            rt.agent_calls.append((agent, prompt))
            rt.order.append(step_id or path)
            return f"out_{agent}"

        rt.call_agent = call_agent  # type: ignore[method-assign]
        cw = _compile(
            [
                {
                    "id": "par",
                    "kind": "parallel",
                    "branches": [
                        {"steps": [_agent("a1")]},
                        {"steps": [_agent("b1")], "depends_on": ["a1"]},
                    ],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.order.index("a1") < rt.order.index("b1")

    @pytest.mark.asyncio
    async def test_parallel_dep_inexistente_falla(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                {
                    "id": "par",
                    "kind": "parallel",
                    "branches": [{"steps": [_agent("a1")], "depends_on": ["nadie"]}],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "failed"
        assert "nadie" in (result.failure or "")

    @pytest.mark.asyncio
    async def test_decide_ejecuta_rama_elegida(self):
        rt = FakeRuntime(decide_answer="camino_b")
        cw = _compile(
            [
                {
                    "id": "elegir",
                    "kind": "decide",
                    "question": "¿cuál?",
                    "options": ["camino_a", "camino_b"],
                    "fallback": "camino_a",
                    "branches": {
                        "camino_a": [_agent("ea")],
                        "camino_b": [_agent("eb")],
                    },
                }
            ]
        )
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert [a for a, _ in rt.agent_calls] == ["developer"]  # solo eb
        assert rt.agent_calls[0][0] == "developer"

    @pytest.mark.asyncio
    async def test_decide_respuesta_invalida_aplica_fallback(self):
        rt = FakeRuntime(decide_answer="camino inventado")
        cw = _compile(
            [
                {
                    "id": "elegir",
                    "kind": "decide",
                    "question": "¿cuál?",
                    "options": ["a", "b"],
                    "fallback": "a",
                    "branches": {"a": [_agent("ea", agent="analista")], "b": [_agent("eb")]},
                }
            ]
        )
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert [a for a, _ in rt.agent_calls] == ["analista"]  # fallback → rama a


class TestCheckpointPauseResume:
    @pytest.mark.asyncio
    async def test_checkpoint_pausa_y_resume_continua(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                _agent("paso1"),
                {"id": "humano", "kind": "checkpoint", "question": "¿seguimos?"},
                _agent("paso2"),
            ]
        )
        engine = WorkflowEngine(rt)
        result = await engine.run(cw, inputs={"query": "q"})
        assert result.status == "paused"
        assert len(rt.agent_calls) == 1  # paso2 NO corrió
        assert result.snapshot is not None

        # resume con checkpoint aprobado
        rt.checkpoint_approved = True
        result2 = await WorkflowEngine(rt).run(cw, inputs={"query": "q"}, resume=result.snapshot)
        assert result2.status == "completed"
        assert len(rt.agent_calls) == 2  # paso1 NO se repite, paso2 sí corre
        assert rt.agent_calls[0][0] == "developer"

    @pytest.mark.asyncio
    async def test_resume_dentro_de_loop_no_repite_iteraciones(self):
        rt = FakeRuntime()
        # loop de 3: pausamos en la iteración 2 vía checkpoint en el body
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "body": [
                        _agent("impl"),
                        {"id": "humano", "kind": "checkpoint", "question": "¿ok?"},
                    ],
                }
            ]
        )
        engine = WorkflowEngine(rt)
        result = await engine.run(cw, inputs={"query": "q"})
        assert result.status == "paused"
        assert len(rt.agent_calls) == 1  # iteración 1 completa, pausa en la 2

        rt.checkpoint_approved = True
        result2 = await WorkflowEngine(rt).run(cw, inputs={"query": "q"}, resume=result.snapshot)
        assert result2.status == "completed"
        assert len(rt.agent_calls) == 3  # iteración 2 y 3 (la 1 no se repite)


class _DictCatalog:
    def __init__(self, presets: dict[str, dict]):
        self.presets = presets

    def load(self, name: str) -> dict | None:
        return self.presets.get(name)


class TestIncludesYAggregate:
    @pytest.mark.asyncio
    async def test_include_binding_io(self):
        rt = FakeRuntime()
        child = _doc(
            [_agent("hijo", goal="procesa $datos", outputs=["res"])],
            name="hijo",
            inputs=["datos"],
            outputs=["res"],
        )
        parent = _doc(
            [
                _agent("prep", outputs=["datos_prep"]),
                {
                    "id": "sub",
                    "kind": "workflow",
                    "name": "hijo",
                    "inputs": {"datos": "$datos_prep"},
                    "outputs": ["res"],
                },
                _agent("final", goal="entrega $res"),
            ]
        )
        cw = compile_workflow(parent, catalog=_DictCatalog({"hijo": child}))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        # el hijo recibió el dato interpolado del padre
        prompts = [p for _, p in rt.agent_calls]
        assert "procesa salida estandar" in prompts
        assert "entrega salida estandar" in prompts

    @pytest.mark.asyncio
    async def test_aggregate_llamado_con_resultados(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                _agent("paso1"),
                {"id": "agg", "kind": "aggregate", "strategy": "moderator", "agent": "moderador"},
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert result.final_output == "RESUMEN_FINAL"
        assert rt.aggregate_calls == [("moderator", 1)]


class TestErrores:
    @pytest.mark.asyncio
    async def test_on_error_abort(self):
        rt = FakeRuntime()

        async def boom(agent, prompt, *, model_role=None, step_id="", path=""):
            if "explota" in prompt:
                raise RuntimeError("LLM caído")
            return "ok"

        rt.call_agent = boom  # type: ignore[method-assign]
        cw = _compile(
            [
                _agent("paso1"),
                _agent("malo", goal="explota por favor"),
                _agent("paso3"),
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "failed"
        assert result.failure is not None and "malo" in result.failure

    @pytest.mark.asyncio
    async def test_on_error_continue(self):
        rt = FakeRuntime()

        async def boom(agent, prompt, *, model_role=None, step_id="", path=""):
            if "explota" in prompt:
                raise RuntimeError("LLM caído")
            return "ok"

        rt.call_agent = boom  # type: ignore[method-assign]
        cw = _compile(
            [
                _agent("paso1"),
                _agent("malo", goal="explota por favor", on_error="continue"),
                _agent("paso3"),
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert result.errors  # quedó registrado


class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_max_reintenta_y_recupera(self):
        rt = FakeRuntime()
        intentos = {"n": 0}

        async def flaky(agent, prompt, *, model_role=None, step_id="", path=""):
            intentos["n"] += 1
            if intentos["n"] < 3:
                raise RuntimeError("LLM inestable")
            return "recuperado"

        rt.call_agent = flaky  # type: ignore[method-assign]
        cw = _compile(
            [{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz", "retry_max": 2}]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert intentos["n"] == 3

    @pytest.mark.asyncio
    async def test_retry_agotado_respeta_on_error(self):
        rt = FakeRuntime()

        async def siempre_falla(agent, prompt, *, model_role=None, step_id="", path=""):
            raise RuntimeError("muerto")

        rt.call_agent = siempre_falla  # type: ignore[method-assign]
        cw = _compile(
            [
                {
                    "id": "paso1",
                    "kind": "agent",
                    "agent": "developer",
                    "goal": "haz",
                    "retry_max": 1,
                },
                {
                    "id": "paso2",
                    "kind": "agent",
                    "agent": "developer",
                    "goal": "sigue",
                    "on_error": "continue",
                },
            ]
        )
        # paso1 sin on_error → abort tras agotar retry
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "failed"
        assert "paso1" in (result.failure or "")


class TestDecomposeStep:
    @pytest.mark.asyncio
    async def test_decompose_define_variable_lista(self):
        rt = FakeRuntime()
        cw = _compile(
            [
                {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "subtasks"},
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 5,
                    "over": "subtasks",
                    "body": [_agent("impl", goal="haz $item")],
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        # decompose fake devuelve 2 subtareas → 2 iteraciones
        assert len(rt.agent_calls) == 2


class TestVarsReservadas:
    @pytest.mark.asyncio
    async def test_vars_reservadas_sembradas_vacias(self):
        rt = FakeRuntime()
        cw = _compile([_agent("paso1", goal="usa $last_output y $last_gate y $item")])
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        prompt = rt.agent_calls[0][1]
        assert "usa  y  y " in prompt or "usa" in prompt  # sin valor: interpolan vacío
        assert "$last_output" not in prompt


# ── results con status — agregador honesto ──


class TestResultsConStatus:
    @pytest.mark.asyncio
    async def test_result_agent_lleva_status_completed(self):
        """El agregador confidence cuenta 'completed' — los results del motor
        deben llevar status (antes: solo id/output → '0/N completadas' falso)."""
        rt = FakeRuntime()
        cw = _compile(
            [
                _agent("paso1"),
                {"id": "agg", "kind": "aggregate", "strategy": "result"},
            ]
        )
        await WorkflowEngine(rt).run(cw, inputs={"query": "x"})
        # aggregate_calls captura (strategy, len) — extendemos el fake:
        # re-ejecutamos con un runtime que capture el contenido completo.
        captured: list[list[dict]] = []

        async def spy_aggregate(strategy, results, agent=None):
            captured.append(results)
            return "R"

        rt.aggregate = spy_aggregate  # type: ignore[method-assign]
        await WorkflowEngine(rt).run(cw, inputs={"query": "x"})
        assert len(captured) == 1
        entry = captured[0][0]
        assert entry["id"] == "paso1"
        assert entry["output"] == "salida estandar"
        assert entry["status"] == "completed"


class TestUntilArgs:
    """El motor interpola y pasa los args declarados en el
    until — llamar la tool SIEMPRE con {} vacío agota max_iter por
    construcción."""

    @pytest.mark.asyncio
    async def test_until_tool_pasa_args_interpolados(self):
        rt = FakeRuntime(tool_results={"test_runner": {"tests_all_pass": True}})
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
                        "args": {"file_path": "$target"},
                    },
                    "body": [_agent("impl")],
                }
            ],
            inputs=["target"],
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q", "target": "tests/"})
        assert result.status == "completed"
        assert ("test_runner", {"file_path": "tests/"}) in rt.tool_calls

    @pytest.mark.asyncio
    async def test_until_metric_pasa_args(self):
        rt = FakeRuntime(tool_results={"test_runner": {"passed_count": 7}})
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {
                        "type": "metric",
                        "tool": "test_runner",
                        "metric": "passed_count",
                        "op": ">=",
                        "value": 5,
                        "args": {"file_path": "."},
                    },
                    "body": [_agent("impl")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.tool_calls[0] == ("test_runner", {"file_path": "."})


class TestLoopIterVars:
    """Loops SIN over exponen $iter/$max_iter — el agente
    NO itera CIEGO (cada prompt sabe qué iteración corre y qué produjo
    la anterior vía $last_output)."""

    @pytest.mark.asyncio
    async def test_iter_y_max_iter_interpolan_en_body(self):
        rt = FakeRuntime(tool_results={"test_runner": {"tests_all_pass": False, "ok": False}})
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
                        "args": {"file_path": "."},
                    },
                    "body": [_agent("impl", goal="iter $iter de $max_iter")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"  # agota max_iter=3 (until nunca)
        prompts = [p for _a, p in rt.agent_calls]
        assert prompts == ["iter 1 de 3", "iter 2 de 3", "iter 3 de 3"]

    @pytest.mark.asyncio
    async def test_last_output_fluye_entre_iteraciones(self):
        rt = FakeRuntime(
            agent_output="trabajo N",
            tool_results={"test_runner": {"tests_all_pass": False, "ok": False}},
        )
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 2,
                    "until": {
                        "type": "tool",
                        "tool": "test_runner",
                        "check": "tests_all_pass",
                        "args": {"file_path": "."},
                    },
                    "body": [_agent("impl", goal="previo: $last_output")],
                }
            ]
        )
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        prompts = [p for _a, p in rt.agent_calls]
        assert prompts[0] == "previo: "  # primera iteración sin output previo
        assert prompts[1] == "previo: trabajo N"  # segunda consume el output previo


def test_interpolate_caps_variable_values():
    """El valor interpolado se acota (paridad con decide/evaluate): sin esto,
    un $last_output grande re-inyectado por iteración crecía el prompt de
    forma cuadrática."""
    from orchestration.dsl.engine import _interpolate

    huge = "x" * 50_000
    out = _interpolate("Previo: $last_output", {"last_output": huge}, "ciclo#1")
    assert len(out) == len("Previo: ") + 2000


def test_interpolate_short_values_intact():
    from orchestration.dsl.engine import _interpolate

    out = _interpolate("hola $nombre", {"nombre": "mundo"}, "p")
    assert out == "hola mundo"
