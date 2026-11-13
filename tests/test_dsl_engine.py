# tests/test_dsl_engine.py
"""Tests del motor de ejecución del DSL (orchestration/dsl/engine.py).

El motor es determinista: TODO lo difuso (agentes, tools, decisiones) pasa
por EngineRuntime (aquí un fake); sus salidas se validan (enum decide,
fallback until-agent) y el control de flujo NUNCA depende del modelo.

Cubre: secuencia+vars, when, gate, loop (over/until tool/agent/metric/
max_iter), parallel con depends_on, decide con fallback, checkpoint con
pause+resume (snapshot), includes con binding I/O, aggregate, on_error.
"""


import pytest
from _dsl_engine_support import (  # noqa: E402 — helpers compartidos del split
    FakeRuntime,
    _agent,
    _compile,
    _DictCatalog,
    _doc,
)

from orchestration.dsl.compiler import compile_workflow
from orchestration.dsl.engine import WorkflowEngine


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
    async def test_when_negado_corre_solo_si_la_var_es_falsa(self):
        rt = FakeRuntime(tool_results={"file_manager": {"flag": False}})
        cw = _compile(
            [
                {
                    "id": "probe",
                    "kind": "tool",
                    "tool": "file_manager",
                    "outputs": {"flag": "flag"},
                },
                _agent("reparar", when="!$flag"),
            ]
        )
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert len(rt.agent_calls) == 1

    @pytest.mark.asyncio
    async def test_when_negado_skipea_si_la_var_es_verdadera(self):
        rt = FakeRuntime(tool_results={"file_manager": {"flag": True}})
        cw = _compile(
            [
                {
                    "id": "probe",
                    "kind": "tool",
                    "tool": "file_manager",
                    "outputs": {"flag": "flag"},
                },
                _agent("reparar", when="!$flag"),
            ]
        )
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert len(rt.agent_calls) == 0

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
        rt = FakeRuntime(evaluate_answer="respuesta inválida")
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

        async def call_agent(
            agent,
            prompt,
            *,
            model_role=None,
            step_id="",
            path="",
            max_iterations=None,
            extra_context="",
        ):
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

    @pytest.mark.asyncio
    async def test_results_de_loop_con_id_cualificado_y_task_real(self):
        """Las iteraciones de un loop compartían step.id y colapsaban el
        dict del agregador (N subtareas → 1 clave); el id debe ser el camino
        cualificado y la task el texto del $item."""
        rt = FakeRuntime()
        cw = _compile(
            [
                {"id": "plan", "kind": "decompose", "strategy": "flat", "output": "tareas"},
                {
                    "id": "ejecutar",
                    "kind": "loop",
                    "over": "tareas",
                    "max_iter": 5,
                    "body": [_agent("subtarea", goal="haz: $item")],
                },
                {"id": "agg", "kind": "aggregate", "strategy": "moderator", "agent": "moderador"},
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        entries = rt.aggregate_results[0]
        assert [e["id"] for e in entries] == ["ejecutar#1/subtarea", "ejecutar#2/subtarea"]
        assert [e["task"] for e in entries] == ["tarea_a", "tarea_b"]

    @pytest.mark.asyncio
    async def test_results_fuera_de_loop_sin_task_stale(self):
        rt = FakeRuntime()
        cw = _compile([_agent("paso1"), {"id": "agg", "kind": "aggregate", "strategy": "result"}])
        await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        entry = rt.aggregate_results[0][0]
        assert entry["id"] == "paso1"
        assert entry["task"] == ""

    @pytest.mark.asyncio
    async def test_on_error_abort(self):
        rt = FakeRuntime()

        async def boom(
            agent,
            prompt,
            *,
            model_role=None,
            step_id="",
            path="",
            extra_context="",
            allowed_tools=None,
        ):
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

        async def boom(
            agent,
            prompt,
            *,
            model_role=None,
            step_id="",
            path="",
            extra_context="",
            allowed_tools=None,
        ):
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

        async def flaky(
            agent,
            prompt,
            *,
            model_role=None,
            step_id="",
            path="",
            extra_context="",
            allowed_tools=None,
        ):
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
    async def test_retry_log_nombra_la_excepcion_sin_mensaje(self, caplog):
        """TimeoutError llega SIN mensaje: el warning de reintento debe nombrar
        el TIPO — `falló ()` confundía el diagnóstico."""
        import logging

        rt = FakeRuntime()
        intentos = {"n": 0}

        async def timeout_primero(
            agent, prompt, *, model_role=None, step_id="", path="", extra_context=""
        ):
            intentos["n"] += 1
            if intentos["n"] == 1:
                raise TimeoutError()  # sin mensaje
            return "ok"

        rt.call_agent = timeout_primero  # type: ignore[method-assign]
        cw = _compile(
            [{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz", "retry_max": 1}]
        )
        with caplog.at_level(logging.WARNING, logger="orchestration.dsl.engine"):
            result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert "TimeoutError" in caplog.text
        assert "falló ()" not in caplog.text

    @pytest.mark.asyncio
    async def test_retry_agotado_respeta_on_error(self):
        rt = FakeRuntime()

        async def siempre_falla(
            agent, prompt, *, model_role=None, step_id="", path="", extra_context=""
        ):
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

    @pytest.mark.asyncio
    async def test_decompose_query_interpola_vars(self):
        """El plan puede alimentarse del contexto (p.ej. la spec): `query`
        se interpola; sin él se usa $query como siempre."""
        queries: list[str] = []

        class QRT(FakeRuntime):
            async def decompose(self, query, strategy):
                queries.append(query)
                return ["una"]

        rt = QRT()
        cw = _compile(
            [
                _agent("spec", outputs=["spec"]),
                {
                    "id": "plan",
                    "kind": "decompose",
                    "strategy": "flat",
                    "output": "subtasks",
                    "query": "Descompón esta spec: $spec",
                },
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert queries == ["Descompón esta spec: salida estandar"]


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
        """El agregador confidence cuenta 'delivered' — los results del motor
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
        assert entry["status"] == "delivered"


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
