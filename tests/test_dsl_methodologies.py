# tests/test_dsl_methodologies.py
"""Tests de los presets metodológicos (BDD/SDD/Reflexion/Domain-TDD/EDD).

1. Cada preset del catálogo compila + valida (guard de expresividad).
2. E2E con FakeRuntime: domain_tdd (plugin text_to_list), reflexion
   (until-agent con fallback), bdd (loop over + until tool).
"""

from pathlib import Path

import pytest
import yaml

from orchestration.dsl import plugins as _plugins  # noqa: F401 — registra built-ins
from orchestration.dsl.compiler import compile_workflow
from orchestration.dsl.engine import WorkflowEngine
from orchestration.dsl.validator import validate_workflow
from tests.test_dsl_engine import FakeRuntime

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "workflows"


def _preset(name: str) -> dict:
    return yaml.safe_load((TEMPLATES_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


class TestPresetsValidos:
    @pytest.mark.parametrize(
        "name",
        [
            "bdd",
            "sdd",
            "reflexion",
            "domain_tdd",
            "edd",
            "development",
            "tdd",
            "collaborative",
            "coordinated",
        ],
    )
    def test_preset_compila_y_valida(self, name):
        cw = compile_workflow(_preset(name))
        errores = validate_workflow(cw)
        assert errores == [], f"{name}: {errores}"


class TestBddE2E:
    @pytest.mark.asyncio
    async def test_historias_parseadas_y_ciclo_por_item(self):
        rt = FakeRuntime(agent_output="1. login con email\n2. recuperar contraseña")
        rt.tool_results = {"test_runner": {"tests_all_pass": True}}
        cw = compile_workflow(_preset("bdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "auth"})
        assert result.status == "completed"
        # historias + 2 items × (test fallido + implementar) = 5 agentes
        assert len(rt.agent_calls) == 5
        # el plugin text_to_list corrió la tool por cada ítem (2)
        assert len(rt.tool_calls) == 2


class TestDomainTddE2E:
    @pytest.mark.asyncio
    async def test_plugin_text_to_list_alimenta_loop(self):
        rt = FakeRuntime(agent_output="escenario a\nescenario b")
        rt.tool_results = {"test_runner": {"tests_all_pass": True}}
        cw = compile_workflow(_preset("domain_tdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "carrito"})
        assert result.status == "completed"
        # modelo + escenarios + 2 escenarios × 2 agentes = 6; 2 tool calls
        assert len(rt.agent_calls) == 6
        assert len(rt.tool_calls) == 2


class TestReflexionE2E:
    @pytest.mark.asyncio
    async def test_aprobado_en_primera(self):
        rt = FakeRuntime(evaluate_answer="APROBADO")
        cw = compile_workflow(_preset("reflexion"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "diseño de API"})
        assert result.status == "completed"
        # 1 iteración: generador + crítico
        assert len(rt.agent_calls) == 2

    @pytest.mark.asyncio
    async def test_respuesta_invalida_fallback_exit(self):
        rt = FakeRuntime(evaluate_answer="pues depende, no sé")
        cw = compile_workflow(_preset("reflexion"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "diseño de API"})
        # fallback exit → completa sin colgar
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_critica_se_propaga_al_generador(self):
        rt = FakeRuntime(evaluate_answer="RECHAZADO")
        prompts = []

        async def grab(agent, prompt, *, model_role=None, step_id="", path=""):
            prompts.append((agent, prompt))
            return "una crítica: mejora el manejo de errores"

        rt.call_agent = grab  # type: ignore[method-assign]
        cw = compile_workflow(_preset("reflexion"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "API"})
        assert result.status == "completed"
        gen_prompts = [p for a, p in prompts if a == "developer"]
        assert len(gen_prompts) >= 1
        # sin $critica sin interpolar: init_vars la arranca vacía
        assert "$critica" not in gen_prompts[0]
        if len(gen_prompts) > 1:
            assert "mejora el manejo de errores" in gen_prompts[1]


class TestEddE2E:
    @pytest.mark.asyncio
    async def test_metric_loop_hasta_passed_5(self):
        rt = FakeRuntime()
        rt.tool_results = {
            "test_runner": {
                "passed_count": 5,
                "failed_count": 0,
                "error_count": 0,
                "tests_all_pass": True,
            }
        }
        cw = compile_workflow(_preset("edd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "ordenar"})
        assert result.status == "completed"
        assert len(rt.agent_calls) == 2  # definir_evals + 1 iteración


class TestSddE2E:
    @pytest.mark.asyncio
    async def test_gate_spec_sin_bloqueantes_avanza(self):
        rt = FakeRuntime(agent_output="spec limpia, sin hallazgos")
        cw = compile_workflow(_preset("sdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "facturación"})
        assert result.status == "completed"
        # redactar_spec + revisar_spec + 2 subtareas + trazabilidad = 5 agentes
        # (corregir_spec y cerrar_brechas saltados: sin [Bloqueante])
        assert len(rt.agent_calls) == 5

    @pytest.mark.asyncio
    async def test_gate_bloqueante_dispara_correccion(self):
        rt = FakeRuntime(agent_output="hallazgo [Bloqueante] spec incompleta")
        cw = compile_workflow(_preset("sdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "facturación"})
        assert result.status == "completed"
        # con bloqueantes corren corregir_spec y cerrar_brechas también
        assert len(rt.agent_calls) == 7
