# tests/test_dsl_scorecard_run_state.py
"""Scorecard por subtareas y run report: entrega y proceso separados."""

import networkx as nx

from orchestration.aggregator import _build_programmatic_response
from orchestration.dsl.run_state import (
    BudgetState,
    PlanItem,
    RunMemory,
    RunState,
    Verification,
)
from orchestration.workflows.orchestrator import _dsl_scorecard


def _state_con(*, entregadas, pendientes=0, proceso_degradado=False):
    plan = []
    for i in range(entregadas):
        plan.append(
            PlanItem(
                kind="subtask",
                id=str(i + 1),
                goal=f"t{i + 1}",
                path=f"l#{i + 1}",
                status="delivered",
                attempts=1,
                tokens=1,
                iterations=1,
                files_written=["a.py"],
                verification=Verification("test_runner", {}, True, {}, ""),
            )
        )
    for i in range(pendientes):
        plan.append(
            PlanItem(
                kind="subtask",
                id=f"p{i + 1}",
                goal=f"p{i + 1}",
                path=f"l#p{i + 1}",
                status="blocked",
                last_error="budget",
            )
        )
    if proceso_degradado:
        plan.append(PlanItem(kind="phase", id="trazabilidad", goal="trazar", status="unmeasured"))
    return RunState(
        run_id="r",
        goal="g",
        plan=plan,
        budget=BudgetState(100, 50, 10, 5),
        replans=[],
        memory=RunMemory(),
    )


def test_scorecard_por_subtareas_y_dos_calidades():
    state = _state_con(entregadas=3, pendientes=1, proceso_degradado=True)
    card = _dsl_scorecard(state, tokens=100, elapsed_s=1, wall_s=1)
    assert card["unidad"] == "subtareas"
    assert card["run_id"] == "r"
    assert card["subtasks"] == 4
    assert card["entregadas"] == 3
    assert card["pendientes"] == 1
    assert card["calidad_entrega"] == "Parcial"
    assert card["calidad_proceso"] == "Parcial"
    assert card["calidad"] == "Parcial"


def test_scorecard_entrega_alta_proceso_parcial():
    state = _state_con(entregadas=4, pendientes=0, proceso_degradado=True)
    card = _dsl_scorecard(state, tokens=100, elapsed_s=1)
    assert card["calidad_entrega"] == "Alta"
    assert card["calidad_proceso"] == "Parcial"
    assert card["calidad"] == "Parcial"


def test_resultados_no_dicen_completado_con_pendientes():
    G = nx.DiGraph()
    G.add_node("1", task="a")
    G.add_node("2", task="b")
    out = _build_programmatic_response(
        "q",
        {
            "1": {"result": "ok", "status": "delivered"},
            "2": {"result": "", "status": "blocked"},
        },
        ["a.py"],
        G,
    )
    assert "Tarea parcial" in out
    assert "pendiente" in out.lower()
    assert "completo y funcional" not in out.lower()


def test_motivo_incluye_pendientes_y_agregado_rojo():
    state = _state_con(entregadas=1, pendientes=1)
    state.plan.append(
        PlanItem(
            kind="phase",
            id="verificar_agregado_final",
            goal="ver",
            status="failed",
            last_error="verificación en rojo: pasan=False",
        )
    )
    card = _dsl_scorecard(state, tokens=1, elapsed_s=1)
    assert "pendiente" in card["motivo"]
    assert "agregado en rojo" in card["motivo"]


def test_aggregate_failed_por_verificacion_no_por_nombre():
    state = _state_con(entregadas=1, pendientes=1)
    state.plan.append(
        PlanItem(
            kind="phase",
            id="gate",
            goal="verificar",
            status="failed",
            verification=Verification("test_runner", {}, False, {"failed_count": 2}, "2 failed"),
        )
    )
    card = _dsl_scorecard(state, tokens=1, elapsed_s=1)
    assert "agregado en rojo" in card["motivo"]
