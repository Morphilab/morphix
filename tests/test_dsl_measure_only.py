"""Un tool-step de solo medición registra su veredicto sin reprobar el proceso.

El agregado intermedio de bdd/domain_tdd/sdd no declara `verdict` (el gate es
el step final, después del reparador), pero un rojo medido no es "sin
medición": el plan debe guardar `Verification(passed=False)` y dejar que el
gate final decida.
"""

import pytest
from _dsl_engine_support import FakeRuntime, _compile

from orchestration.dsl.engine import WorkflowEngine
from orchestration.workflows.orchestrator import _dsl_scorecard


def _medir(pasan: bool) -> dict:
    return {
        "id": "medir",
        "kind": "tool",
        "tool": "test_runner",
        "args": {"file_path": "tests/x.py"},
        "outputs": {"pasan": "tests_all_pass"},
    }


@pytest.mark.asyncio
async def test_tool_sin_verdict_con_output_bool_registra_la_medicion():
    rt = FakeRuntime(
        tool_results={
            "test_runner": {
                "tests_all_pass": False,
                "passed_count": 1,
                "failed_count": 1,
                "total_count": 2,
            }
        }
    )
    res = await WorkflowEngine(rt).run(_compile([_medir(False)]), inputs={"query": "q"})
    item = next(p for p in res.run_state["plan"] if p["id"] == "medir")
    assert item["status"] == "delivered"
    assert item["verification"]["passed"] is False
    assert item["verification"]["counts"]["failed_count"] == 1
    card = _dsl_scorecard(res.run_state, tokens=1, elapsed_s=1)
    assert card["calidad_proceso"] == "Alta"


@pytest.mark.asyncio
async def test_tool_sin_verdict_sin_medicion_sigue_unmeasured():
    rt = FakeRuntime(tool_results={"test_runner": {"success": False}})
    res = await WorkflowEngine(rt).run(
        _compile(
            [
                {
                    "id": "falla",
                    "kind": "tool",
                    "tool": "test_runner",
                    "args": {"file_path": "tests/x.py"},
                }
            ]
        ),
        inputs={"query": "q"},
    )
    item = next(p for p in res.run_state["plan"] if p["id"] == "falla")
    assert item["status"] == "unmeasured"


@pytest.mark.asyncio
async def test_tool_sin_verdict_con_bool_sin_conteos_sigue_unmeasured():
    """Un bool de una tool que no mide (p.ej. success de una escritura) no es
    una medición: sin conteos, el fallo sigue visible."""
    rt = FakeRuntime(tool_results={"file_manager": {"success": False}})
    res = await WorkflowEngine(rt).run(
        _compile(
            [
                {
                    "id": "escribir",
                    "kind": "tool",
                    "tool": "file_manager",
                    "args": {"file_path": "x.py"},
                    "outputs": {"ok": "success"},
                }
            ]
        ),
        inputs={"query": "q"},
    )
    item = next(p for p in res.run_state["plan"] if p["id"] == "escribir")
    assert item["status"] == "unmeasured"
    assert item["verification"] is None
