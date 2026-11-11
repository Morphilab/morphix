# tests/test_dsl_veredicto_verificacion.py
"""Contrato del VEREDICTO de verificación (`ToolStep.verdict`).

Antes de este campo, un preset que declaraba `outputs: {pasan: tests_all_pass}`
escribía la var y **nadie la leía**: la verificación por-subtarea se calculaba y
se descartaba, así que el scorecard contaba "completadas" con tests rojos en
disco (y el agregador las incluía como buenas).

El campo cierra el contrato: un valor falsy reprueba el step y la entrega de su
misma iteración (mismo prefijo de camino cualificado).
"""

import pytest
from _dsl_engine_support import FakeRuntime, _doc

from orchestration.dsl.compiler import compile_workflow
from orchestration.dsl.engine import WorkflowEngine


def _doc_veredicto(*, verdict=True, commit_after: list[str] | None = None) -> dict:
    """Loop over con body [agente, verificación] — la forma real de los presets."""
    verificar: dict = {
        "id": "verificar",
        "kind": "tool",
        "tool": "test_runner",
        "args": {"file_path": "tests/test_x.py"},
        "outputs": {"pasan": "tests_all_pass"},
    }
    if verdict is not True:
        verificar["verdict"] = verdict
    doc = _doc(
        [
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 5,
                "over": "subtasks",
                "body": [
                    {"id": "subtarea", "kind": "agent", "agent": "developer", "goal": "$item"},
                    verificar,
                ],
            },
            {"id": "agregar", "kind": "aggregate", "strategy": "confidence"},
        ],
        inputs=["query", "subtasks"],
        defaults={"subtasks": ["tarea_a", "tarea_b"]},
    )
    if commit_after:
        doc["commit_after"] = commit_after
    return doc


class _RecordingRuntime(FakeRuntime):
    def __init__(self, **over):
        super().__init__(**over)
        self.commit_calls: list[str] = []

    async def commit_after_step(self, step_id: str) -> None:
        self.commit_calls.append(step_id)


def _estados(rt: FakeRuntime) -> dict[str, str]:
    """status por id de los resultados que recibió el agregador."""
    return {str(r["id"]): str(r["status"]) for r in rt.aggregate_results[-1]}


def _emitidos(rt: FakeRuntime) -> set[tuple[str, str]]:
    return {(str(e.get("path")), str(e.get("status"))) for e in rt.emits if e.get("kind") == "step"}


class TestVeredictoEnRojo:
    @pytest.mark.asyncio
    async def test_rojo_reprueba_el_step_y_la_entrega_del_ciclo(self):
        """El step verificador sale 'error' y el resultado de su ciclo queda
        'failed': el agregador deja de contar la entrega como buena."""
        rt = FakeRuntime(tool_results={"test_runner": {"tests_all_pass": False}})
        cw = compile_workflow(_doc_veredicto(verdict="pasan"))
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "x", "subtasks": ["a", "b"]})

        assert res.status == "completed"  # un rojo NO tira el run
        emitidos = _emitidos(rt)
        assert ("ciclo#1/verificar", "error") in emitidos
        assert ("ciclo#2/verificar", "error") in emitidos
        assert ("ciclo#1/verificar", "completed") not in emitidos
        estados = _estados(rt)
        assert estados["ciclo#1/subtarea"] == "failed"
        assert estados["ciclo#2/subtarea"] == "failed"
        assert any("verificación en rojo" in e for e in res.errors)

    @pytest.mark.asyncio
    async def test_verde_no_cambia_nada(self):
        """No-regresión: con el veredicto en verde el run es el de antes."""
        rt = FakeRuntime(tool_results={"test_runner": {"tests_all_pass": True}})
        cw = compile_workflow(_doc_veredicto(verdict="pasan"))
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "x", "subtasks": ["a"]})

        assert res.status == "completed"
        assert res.errors == []
        emitidos = _emitidos(rt)
        assert ("ciclo#1/verificar", "completed") in emitidos
        assert ("ciclo#1/verificar", "error") not in emitidos
        assert _estados(rt)["ciclo#1/subtarea"] == "delivered"

    @pytest.mark.asyncio
    async def test_sin_verdict_declarado_comportamiento_previo(self):
        """Backward compatible: un tool-step sin `verdict` sigue ignorando el
        rojo — ningún preset que no lo declare cambia de comportamiento."""
        rt = FakeRuntime(tool_results={"test_runner": {"tests_all_pass": False}})
        cw = compile_workflow(_doc_veredicto(verdict=None))
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "x", "subtasks": ["a"]})

        assert res.status == "completed"
        assert res.errors == []
        assert ("ciclo#1/verificar", "completed") in _emitidos(rt)
        assert _estados(rt)["ciclo#1/subtarea"] == "delivered"

    @pytest.mark.asyncio
    async def test_verificacion_que_no_corrio_no_reprueba(self):
        """`None` = no hubo medición (p.ej. contrato TEST_PATH incumplido, que
        el adapter ya marca como degradado): reprobar ahí sobre-reportaba
        entregas que nadie llegó a evaluar."""
        rt = FakeRuntime(
            tool_results={"test_runner": {"success": False, "output": "sin file_path"}}
        )
        cw = compile_workflow(_doc_veredicto(verdict="pasan"))
        res = await WorkflowEngine(rt).run(cw, inputs={"query": "x", "subtasks": ["a"]})
        assert res.status == "completed"
        emitidos = _emitidos(rt)
        assert ("ciclo#1/verificar", "completed") in emitidos
        assert _estados(rt)["ciclo#1/subtarea"] == "delivered"


class TestVerdictMalDeclarado:
    def test_verdict_que_no_es_output_es_error_semantico(self):
        """Un `verdict` fantasma dejaría la verificación muda: el validador lo
        caza en compilación en vez de degradar en silencio."""
        from orchestration.dsl.validator import validate_workflow

        cw = compile_workflow(_doc_veredicto(verdict="var_fantasma"))
        errores = validate_workflow(cw)
        assert any("var_fantasma" in e and "verdict" in e for e in errores)

    def test_verdict_correcto_valida(self):
        from orchestration.dsl.validator import validate_workflow

        cw = compile_workflow(_doc_veredicto(verdict="pasan"))
        assert validate_workflow(cw) == []


class TestVeredictoNoCommitea:
    @pytest.mark.asyncio
    async def test_commit_after_no_commitea_con_rojo(self):
        """Commitear una entrega reprobada contradice el contrato de
        commit_after ('solo con subtarea completada')."""
        rt = _RecordingRuntime(tool_results={"test_runner": {"tests_all_pass": False}})
        cw = compile_workflow(_doc_veredicto(verdict="pasan", commit_after=["verificar"]))
        await WorkflowEngine(rt).run(cw, inputs={"query": "x", "subtasks": ["a", "b"]})
        assert rt.commit_calls == []

    @pytest.mark.asyncio
    async def test_commit_after_commitea_con_verde(self):
        rt = _RecordingRuntime(tool_results={"test_runner": {"tests_all_pass": True}})
        cw = compile_workflow(_doc_veredicto(verdict="pasan", commit_after=["verificar"]))
        await WorkflowEngine(rt).run(cw, inputs={"query": "x", "subtasks": ["a"]})
        assert rt.commit_calls == ["verificar"]


@pytest.mark.asyncio
async def test_tool_de_veredicto_top_level_guarda_verification_y_falla():
    rt = FakeRuntime(tool_results={"test_runner": {"tests_all_pass": False, "failed_count": 2}})
    cw = compile_workflow(
        _doc(
            [
                {
                    "id": "ver",
                    "kind": "tool",
                    "tool": "test_runner",
                    "args": {"file_path": "tests/x.py"},
                    "outputs": {"pasan": "tests_all_pass"},
                    "verdict": "pasan",
                }
            ]
        )
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    item = next(i for i in res.run_state["plan"] if i["id"] == "ver")
    assert item["status"] == "failed"
    assert item["verification"]["passed"] is False
    assert item["verification"]["counts"]["failed_count"] == 2


@pytest.mark.asyncio
async def test_verified_by_verde_limpia_el_last_error_de_produces():
    rt = FakeRuntime(
        agent_output="ok",
        files_written=[],
        tool_results={"test_runner": {"tests_all_pass": True}},
    )
    cw = compile_workflow(
        _doc(
            [
                {
                    "id": "a",
                    "kind": "agent",
                    "agent": "developer",
                    "goal": "x",
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
            ]
        )
    )
    res = await WorkflowEngine(rt).run(cw, inputs={"query": "q"})
    item = next(i for i in res.run_state["plan"] if i["id"] == "a")
    assert item["status"] == "delivered"
    assert item["last_error"] is None
