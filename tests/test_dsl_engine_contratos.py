# tests/test_dsl_engine_contratos.py — split de test_dsl_engine (timeout/until/budget/decide/dag)
"""Contratos de fase reciente: timeout de agente, until tolerante y con
continue, presupuesto por-subtarea, decide tolerante y decompose dag."""


import pytest
from _dsl_engine_support import FakeRuntime, _agent, _compile

from orchestration.dsl.engine import WorkflowEngine


class TestTimeoutDeAgente:
    @pytest.mark.asyncio
    async def test_timeout_corta_y_falla_honesto(self):
        """Un runtime colgado no congela el paso: el timeout corta por
        intento, el retry aplica, y el agotamiento produce un fallo
        accionable (el run jamás queda colgado indefinidamente)."""
        import asyncio

        class RuntimeColgado(FakeRuntime):
            async def call_agent(self, agent, prompt, **kwargs):
                self.agent_calls.append((agent, prompt))
                await asyncio.sleep(5)
                return "nunca"

        rt = RuntimeColgado()
        cw = _compile([_agent("lento", retry_max=1)])
        cw.dsl.steps[0].timeout = 1
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})

        assert result.status == "failed"
        assert "sin responder" in str(result.failure)
        assert len(rt.agent_calls) == 2  # retry_max=1 → 2 intentos

    @pytest.mark.asyncio
    async def test_dentro_del_timeout_completa(self):
        """Un runtime que responde a tiempo no se ve afectado por el timeout."""
        rt = FakeRuntime(agent_output="a tiempo")
        cw = _compile([_agent("rapido", timeout=300)])
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})

        assert result.status == "completed"
        assert result.vars["last_output"] == "a tiempo"


class TestUntilAgentTolerante:
    @pytest.mark.asyncio
    async def test_respuesta_en_prosa_coincide_por_primer_token(self):
        """'No: el test está truncado...' es una respuesta legítima de
        enumeración — el matching exacto la rechazaba y caía al fallback."""
        rt = FakeRuntime(evaluate_answer="No: el test está truncado y no cubre la pereza")
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {
                        "type": "agent",
                        "question": "¿cumple?",
                        "expect": "NO",
                        "fallback": "fail",
                    },
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 1  # el NO honesto cerró el ciclo

    @pytest.mark.asyncio
    async def test_primera_palabra_con_puntuacion_cuenta(self):
        rt = FakeRuntime(evaluate_answer="Sí, pero con reservas menores")
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {
                        "type": "agent",
                        "question": "¿aprobado?",
                        "expect": "SÍ",
                        "fallback": "fail",
                    },
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_respuesta_genuinamente_distinta_sigue_fallando(self):
        rt = FakeRuntime(evaluate_answer="quizá")
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "until": {
                        "type": "agent",
                        "question": "¿aprobado?",
                        "expect": "SÍ",
                        "fallback": "fail",
                    },
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "failed"


class TestPresupuestoPorSubtarea:
    @pytest.mark.asyncio
    async def test_secuencial_scope_por_item(self):
        """Loop secuencial con budget_tokens: begin/end alrededor de CADA
        ítem (cada vuelta recibe su propio techo)."""
        rt = FakeRuntime()
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 5,
                    "over": "items",
                    "budget_tokens": 10000,
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"items": ["a", "b"]})
        assert result.status == "completed"
        begins = [b for kind, b in rt.budget_scopes if kind == "begin"]
        ends = [s for s in rt.budget_scopes if s[0] == "end"]
        assert begins == [10000, 10000]
        assert len(ends) == 2
        # alternancia estricta begin→end→begin→end
        kinds = [k for k, _ in rt.budget_scopes]
        assert kinds == ["begin", "end", "begin", "end"]

    @pytest.mark.asyncio
    async def test_paralelo_scope_por_lote(self):
        """Loop paralelo: los hermanos comparten el techo — un solo
        begin/end alrededor del lote (el estado de presupuesto es único)."""
        rt = FakeRuntime()
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 5,
                    "over": "items",
                    "parallel": True,
                    "budget_tokens": 20000,
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"items": ["a", "b", "c"]})
        assert result.status == "completed"
        kinds = [k for k, _ in rt.budget_scopes]
        assert kinds == ["begin", "end"]
        assert rt.budget_scopes[0] == ("begin", 20000)

    @pytest.mark.asyncio
    async def test_end_corre_aun_con_fallo_del_item(self):
        """Si el body del ítem falla, el finally restaura el techo igual."""
        rt = FakeRuntime()
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 3,
                    "over": "items",
                    "budget_tokens": 5000,
                    "body": [_agent("gen", on_error="abort")],
                }
            ]
        )
        rt.agent_output = ""  # salida vacía → EngineError en el step agent
        result = await WorkflowEngine(rt).run(cw, inputs={"items": ["a"]})
        assert result.status == "failed"
        kinds = [k for k, _ in rt.budget_scopes]
        assert kinds == ["begin", "end"]  # finally restauró antes de propagar


class TestUntilAgentContinue:
    @pytest.mark.asyncio
    async def test_rechazo_legitimo_alimenta_otra_vuelta(self):
        """fallback continue: el NO del evaluador NO cierra el loop — el
        feedback (last_output) queda disponible y el generador reintenta
        hasta el APROBADO o max_iter (reflexion multi-ronda real)."""
        rt = FakeRuntime()
        respuestas = iter(["No: falta manejo de acentos", "APROBADO"])
        evaluaciones = iter(["No", "APROBADO"])

        async def secuencial(agent, prompt, **kwargs):
            rt.agent_calls.append((agent, prompt))
            return next(respuestas)

        async def evalua(question, expect, context):
            return next(evaluaciones)

        rt.call_agent = secuencial
        rt.evaluate = evalua
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
                        "fallback": "continue",
                    },
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 2  # rechazo → 2ª vuelta aprobó

    @pytest.mark.asyncio
    async def test_continue_acotado_por_max_iter(self):
        """Sin aprobación nunca: el continue itera hasta max_iter y el loop
        termina (no cuelga, no falla por el rechazo)."""
        rt = FakeRuntime(agent_output="No: aún no")
        rt.evaluate_answer = "No"  # el crítico rechaza SIEMPRE
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
                        "fallback": "continue",
                    },
                    "body": [_agent("gen")],
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 3

    @pytest.mark.asyncio
    async def test_validator_acepta_continue(self):
        cw = _compile(
            [
                {
                    "id": "ciclo",
                    "kind": "loop",
                    "max_iter": 2,
                    "until": {
                        "type": "agent",
                        "question": "¿ok?",
                        "expect": "SÍ",
                        "fallback": "continue",
                    },
                    "body": [_agent("gen")],
                }
            ]
        )
        assert cw.dsl.steps[0].until.fallback == "continue"


class TestDecideTolerante:
    @pytest.mark.asyncio
    async def test_respuesta_en_prosa_cae_en_la_opcion(self):
        """'No: porque el acoplamiento sube' es un NO legítimo — el matching
        exacto lo mandaba al fallback y la rama correcta no corría."""
        rt = FakeRuntime(decide_answer="No: porque el acoplamiento sube")
        cw = _compile(
            [
                {
                    "id": "eleccion",
                    "kind": "decide",
                    "question": "¿usar cache?",
                    "options": ["SÍ", "NO"],
                    "fallback": "SÍ",
                    "branches": {"SÍ": [_agent("con_cache")], "NO": [_agent("sin_cache")]},
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 1
        assert rt.order[0] == "eleccion/NO/sin_cache"

    @pytest.mark.asyncio
    async def test_respuesta_exacta_sigue_funcionando(self):
        rt = FakeRuntime(decide_answer="NO")
        cw = _compile(
            [
                {
                    "id": "eleccion",
                    "kind": "decide",
                    "question": "¿usar cache?",
                    "options": ["SÍ", "NO"],
                    "fallback": "SÍ",
                    "branches": {"SÍ": [_agent("con_cache")], "NO": [_agent("sin_cache")]},
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.order[0] == "eleccion/NO/sin_cache"

    @pytest.mark.asyncio
    async def test_respuesta_genuinamente_distinta_va_al_fallback(self):
        rt = FakeRuntime(decide_answer="quizá")
        cw = _compile(
            [
                {
                    "id": "eleccion",
                    "kind": "decide",
                    "question": "¿usar cache?",
                    "options": ["SÍ", "NO"],
                    "fallback": "SÍ",
                    "branches": {"SÍ": [_agent("con_cache")], "NO": [_agent("sin_cache")]},
                }
            ]
        )
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
        assert result.status == "completed"
        assert rt.order[0] == "eleccion/SÍ/con_cache"


class TestDecomposeDag:
    @pytest.mark.asyncio
    async def test_validador_dag_niveles_y_ciclos(self):
        """La validación Kahn: niveles correctos, ids rotos y ciclos
        fallan loud con mensaje accionable."""
        from orchestration.decomposer import _validar_dag

        tasks = [
            {"id": "t1", "title": "a", "depends_on": []},
            {"id": "t2", "title": "b", "depends_on": ["t1"]},
            {"id": "t3", "title": "c", "depends_on": ["t1"]},
            {"id": "t4", "title": "d", "depends_on": ["t2", "t3"]},
        ]
        niveles = _validar_dag(tasks)
        assert [[t["id"] for t in n] for n in niveles] == [["t1"], ["t2", "t3"], ["t4"]]

        with pytest.raises(ValueError, match="inexistente"):
            _validar_dag([{"id": "t1", "title": "a", "depends_on": ["fantasma"]}])
        with pytest.raises(ValueError, match="circulares"):
            _validar_dag(
                [
                    {"id": "t1", "title": "a", "depends_on": ["t2"]},
                    {"id": "t2", "title": "b", "depends_on": ["t1"]},
                ]
            )
        with pytest.raises(ValueError, match="duplicados"):
            _validar_dag(
                [
                    {"id": "t1", "title": "a", "depends_on": []},
                    {"id": "t1", "title": "b", "depends_on": []},
                ]
            )

    @pytest.mark.asyncio
    async def test_plugin_dag_to_levels(self):
        """El plugin aplana el resultado del decompose dag en una variable
        de niveles lista para loop externo + loop interno sobre item."""
        from orchestration.dsl.plugins import _dag_to_levels

        class _Step:
            params = {"var": "dag", "out": "niveles"}

        class _State:
            vars = {
                "dag": {
                    "tasks": [],
                    "levels": [[{"id": "t1", "title": "base"}], [{"id": "t2", "title": "app"}]],
                }
            }

        state = _State()
        await _dag_to_levels(_Step(), state, None, "test")
        assert state.vars["niveles"] == [["base"], ["app"]]

        state2 = _State()
        state2.vars["dag"] = "no-es-dag"
        with pytest.raises(Exception, match="no es un resultado"):
            await _dag_to_levels(_Step(), state2, None, "test")


class TestDecomposeTaskDag:
    @pytest.mark.asyncio
    async def test_dag_con_llm_fake(self, monkeypatch):
        """decompose_task_dag contra el LLM parcheado: JSON válido → levels;
        JSON inválido o sin tasks → ValueError accionable."""
        import json

        from orchestration import decomposer as dec

        class _Resp:
            choices: list = []

        def _mk(content: str):
            r = _Resp()

            class _M:
                pass

            m = _M()
            m.content = content

            class _C:
                pass

            c = _C()
            c.message = m
            r.choices = [c]
            return r

        async def fake_call(**kwargs):
            return _mk(
                json.dumps(
                    {
                        "tasks": [
                            {"id": "t1", "title": "base", "depends_on": []},
                            {"id": "t2", "title": "app", "depends_on": ["t1"]},
                        ]
                    }
                )
            )

        monkeypatch.setattr(dec.models, "call", fake_call)
        monkeypatch.setattr(dec, "_build_project_context", lambda *a, **k: _async(""))
        out = await dec.decompose_task_dag("tarea", workspace="main")
        assert [[t["id"] for t in n] for n in out["levels"]] == [["t1"], ["t2"]]

        async def fake_bad(**kwargs):
            return _mk("no json")

        monkeypatch.setattr(dec.models, "call", fake_bad)
        with pytest.raises(ValueError, match="JSON de tasks"):
            await dec.decompose_task_dag("tarea", workspace="main")


async def _async(v):
    return v
