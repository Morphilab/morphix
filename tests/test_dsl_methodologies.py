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
from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow
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
        # Los presets de producto pueden referenciar fragmentos de body
        # (kind: fragment): compilar con el catálogo real los expande.
        cw = compile_workflow(_preset(name), catalog=FileSystemCatalog())
        errores = validate_workflow(cw)
        assert errores == [], f"{name}: {errores}"


class TestBddE2E:
    @pytest.mark.asyncio
    async def test_historias_parseadas_y_ciclo_por_item(self):
        rt = FakeRuntime(agent_output="## LISTA\n- login con email\n- recuperar contraseña")
        rt.tool_results = {"test_runner": {"tests_all_pass": True}}
        cw = compile_workflow(_preset("bdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "auth"})
        assert result.status == "completed"
        # historias + revisar_historias + 2 items × (test fallido + implementar)
        assert len(rt.agent_calls) == 6
        # una verificación por ítem (2) + la intermedia del AGREGADO + la
        # FINAL (4ª, el gate de calidad): FakeRuntime no expone files_written,
        # así que el recolector entrega lista vacía — comportamiento
        # documentado, no una suite global accidental.
        rutas_item = [t[1]["file_path"] for t in rt.tool_calls if t[1].get("file_path")]
        assert rutas_item == ["tests/test_historia_1.py", "tests/test_historia_2.py"]
        assert rt.tool_calls[2][1].get("file_paths") == []
        assert rt.tool_calls[3][1].get("file_paths") == []
        assert len(rt.tool_calls) == 4


class TestDomainTddE2E:
    @pytest.mark.asyncio
    async def test_plugin_text_to_list_alimenta_loop(self):
        rt = FakeRuntime(agent_output="## LISTA\n- escenario a\n- escenario b")
        rt.tool_results = {"test_runner": {"tests_all_pass": True}}
        cw = compile_workflow(_preset("domain_tdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "carrito"})
        assert result.status == "completed"
        # modelo + escenarios + 2 escenarios × 2 agentes = 6 agentes
        assert len(rt.agent_calls) == 6
        # una verificación por escenario (2) + la intermedia del AGREGADO +
        # la FINAL (4ª, el gate), con la lista vacía por FakeRuntime sin
        # files_written (comportamiento documentado del recolector).
        rutas_item = [t[1]["file_path"] for t in rt.tool_calls if t[1].get("file_path")]
        assert rutas_item == ["tests/test_escenario_1.py", "tests/test_escenario_2.py"]
        assert rt.tool_calls[2][1].get("file_paths") == []
        assert rt.tool_calls[3][1].get("file_paths") == []
        assert len(rt.tool_calls) == 4


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

        async def grab(
            agent,
            prompt,
            *,
            model_role=None,
            step_id="",
            path="",
            extra_context="",
            allowed_tools=None,
        ):
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
        rt.tool_results = {"test_runner": {"tests_all_pass": True}}
        cw = compile_workflow(_preset("sdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "facturación"})
        assert result.status == "completed"
        # redactar_spec + revisar_spec + escribir_tests + 2 subtareas +
        # trazabilidad = 6 agentes (corregir_spec y cerrar_brechas saltados)
        assert len(rt.agent_calls) == 6

    @pytest.mark.asyncio
    async def test_gate_bloqueante_dispara_correccion(self):
        rt = FakeRuntime(agent_output="hallazgo [Bloqueante] spec incompleta")
        rt.tool_results = {"test_runner": {"tests_all_pass": True}}
        cw = compile_workflow(_preset("sdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "facturación"})
        assert result.status == "completed"
        # con bloqueantes corren corregir_spec y cerrar_brechas también (+2)
        assert len(rt.agent_calls) == 8

    @pytest.mark.asyncio
    async def test_corregir_spec_actualiza_la_var_spec(self):
        """La corrección debe llegar a la implementación: `corregir_spec`
        exporta `spec`, así que $spec posterior es la corregida."""
        seq = iter(
            [
                "spec original",
                "hallazgo [Bloqueante] falta alcance",
                "spec corregida con alcance",
                "implemento subtarea",
                "implemento subtarea",
                "traza limpia sin brechas",
                "cierre",
            ]
        )

        class SeqRT(FakeRuntime):
            async def call_agent(self, agent, prompt, **kw):
                self.agent_calls.append((agent, prompt))
                return next(seq, "salida")

        rt = SeqRT()
        rt.tool_results = {"test_runner": {"tests_all_pass": True}}
        cw = compile_workflow(_preset("sdd"))
        result = await WorkflowEngine(rt).run(cw, inputs={"query": "facturación"})
        assert result.status == "completed"
        assert result.vars["spec"] == "spec corregida con alcance"


class TestAgregadoReparado:
    """El intermedio rojo solo mide; el reparador lo pone verde y el gate final
    cierra Alta. Un rojo reparado no puede quedar como proceso degradado."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "preset,agent_output,files",
        [
            (
                "bdd",
                "## LISTA\n- HU-1: como usuario quiero X",
                ["tests/test_historia_1.py", "src/x.py"],
            ),
            (
                "domain_tdd",
                "## LISTA\n- Escenario 1: alta de usuario",
                ["tests/test_escenario_1.py", "src/x.py"],
            ),
            (
                "sdd",
                "spec y plan",
                ["tests/test_subtarea_1.py", "tests/test_subtarea_2.py", "src/x.py"],
            ),
            (
                "development",
                "plan y código",
                ["tests/test_subtarea_1.py", "tests/test_subtarea_2.py", "src/x.py"],
            ),
            (
                "coordinated",
                "plan y código",
                ["tests/test_subtarea_1.py", "tests/test_subtarea_2.py", "src/x.py"],
            ),
        ],
    )
    async def test_agregado_rojo_reparado_cierra_alta(self, preset, agent_output, files):
        from orchestration.workflows.orchestrator import _dsl_scorecard

        class _RepairRT(FakeRuntime):
            def __init__(self):
                super().__init__(agent_output=agent_output, files_written=files)
                self.aggregate_checks = 0

            async def call_tool(self, tool, args):
                self.tool_calls.append((tool, args))
                if tool == "test_runner" and "file_paths" in args:
                    self.aggregate_checks += 1
                    green = self.aggregate_checks > 1
                    return {
                        "tests_all_pass": green,
                        "passed_count": 2 if green else 1,
                        "failed_count": 0 if green else 1,
                    }
                return {"tests_all_pass": True, "passed_count": 1, "failed_count": 0}

        rt = _RepairRT()
        result = await WorkflowEngine(rt).run(
            compile_workflow(_preset(preset), catalog=FileSystemCatalog()),
            inputs={"query": "facturación"},
        )
        assert result.status == "completed"
        assert any("ROJO" in prompt for _, prompt in rt.agent_calls), preset
        steps = {p["id"]: p for p in result.run_state["plan"]}
        assert steps["verificar_agregado"]["verification"]["passed"] is False
        assert steps["reparar_agregado"]["status"] == "delivered"
        assert steps["verificar_agregado_final"]["verification"]["passed"] is True
        card = _dsl_scorecard(result.run_state, tokens=1, elapsed_s=1)
        assert card["calidad"] == "Alta", (preset, card)
