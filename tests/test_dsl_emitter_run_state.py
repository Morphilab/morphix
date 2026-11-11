# tests/test_dsl_emitter_run_state.py
"""Stats del run DSL: el panel recibe el RunState público, no solo pasos."""

from orchestration.dsl.run_state import BudgetState, PlanItem, RunMemory, RunState
from orchestration.dsl.runtime_adapter import ProductionRuntime


def _state_con(*, entregadas=1, pendientes=0):
    plan = [
        PlanItem(kind="subtask", id=str(i), goal=f"t{i}", path=f"l#{i}", status="delivered")
        for i in range(entregadas)
    ] + [
        PlanItem(kind="subtask", id=f"p{i}", goal=f"p{i}", path=f"l#p{i}", status="pending")
        for i in range(pendientes)
    ]
    return RunState(
        run_id="r",
        goal="g",
        plan=plan,
        budget=BudgetState(100, 10, 5, 5),
        replans=[],
        memory=RunMemory(),
    )


def test_stats_payload_incluye_run_state_publico():
    rt = ProductionRuntime(workspace="main")
    rt._last_run_state = _state_con(entregadas=1).public()
    payload = rt._stats_payload()
    assert payload["run_state"]["plan"][0]["status"] == "delivered"
    assert "output_tail" not in str(payload["run_state"])


def test_stats_payload_sin_run_state_no_rompe():
    rt = ProductionRuntime(workspace="main")
    payload = rt._stats_payload()
    assert payload.get("run_state") is None
    assert payload["subtask_list"] == []


def test_emit_guarda_run_state_y_lo_expone_en_el_siguiente_payload():
    rt = ProductionRuntime(workspace="main")
    state = _state_con(entregadas=1).public()
    rt.emit(kind="step", path="l#1", status="completed", run_state=state)
    assert rt._last_run_state is not None
    assert rt._last_run_state["run_id"] == "r"
    payload = rt._stats_payload()
    assert payload["run_state"]["run_id"] == "r"
