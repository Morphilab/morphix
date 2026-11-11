# tests/test_dsl_run_state.py
"""Modelo de estado del run: serialización, public y run report."""

from orchestration.dsl.run_state import (
    BudgetState,
    PlanItem,
    RunMemory,
    RunState,
    Verification,
    build_run_report,
)


def test_run_state_roundtrip_conserva_plan_evidencia_y_budget():
    state = RunState(
        run_id="r1",
        goal="demo",
        plan=[
            PlanItem(
                kind="subtask",
                id="1",
                goal="a",
                path="loop#1",
                status="delivered",
                attempts=1,
                tokens=10,
                iterations=2,
                files_written=["a.py"],
                verification=Verification(
                    "test_runner", {"file_path": "tests/a.py"}, True, {"passed": 3}, "ok"
                ),
            ),
            PlanItem(
                kind="phase",
                id="trazabilidad",
                goal="trazar",
                path=None,
                status="unmeasured",
                attempts=1,
                tokens=5,
                iterations=1,
                files_written=[],
                verification=None,
                satisfied_by=None,
                notes=[],
                last_error="sin medición",
            ),
        ],
        budget=BudgetState(limit=100, spent=20, reserve_final=10, reserve_replan=5),
        replans=[],
        memory=RunMemory(),
    )
    clone = RunState.from_dict(state.to_dict())
    assert clone.plan[0].status == "delivered"
    assert clone.plan[0].verification.passed is True
    assert clone.plan[1].kind == "phase"
    assert clone.budget.available_for_work() == 65


def test_public_omite_tails_y_cap_notes():
    state = RunState(
        run_id="r",
        goal="g",
        plan=[
            PlanItem(
                kind="subtask",
                id="1",
                goal="a",
                path="l#1",
                status="delivered",
                attempts=1,
                tokens=1,
                iterations=1,
                files_written=["a.py"],
                verification=Verification("test_runner", {}, True, {}, "tail-secreto"),
            )
        ],
        budget=BudgetState(1, 0, 0, 0),
        replans=[],
        memory=RunMemory(),
    )
    pub = state.public()
    assert "output_tail" not in str(pub)
    assert "tail-secreto" not in str(pub)


def test_build_run_report_pone_la_calidad_primero_y_cuenta_subtareas():
    state = RunState(
        run_id="r",
        goal="g",
        plan=[
            PlanItem(
                kind="subtask",
                id="1",
                goal="a",
                path="l#1",
                status="delivered",
                attempts=1,
                tokens=1,
                iterations=1,
                files_written=["a.py"],
                verification=Verification("test_runner", {}, True, {}, ""),
                satisfied_by=None,
                notes=[],
                last_error=None,
            ),
            PlanItem(
                kind="subtask",
                id="2",
                goal="b",
                path="l#2",
                status="blocked",
                attempts=0,
                tokens=0,
                iterations=0,
                files_written=[],
                verification=None,
                satisfied_by=None,
                notes=[],
                last_error="budget",
            ),
        ],
        budget=BudgetState(100, 80, 10, 5),
        replans=[],
        memory=RunMemory(),
    )
    report = build_run_report(
        state,
        {
            "calidad": "Parcial",
            "calidad_entrega": "Parcial",
            "calidad_proceso": "Alta",
            "degradadas": 0,
        },
        "detalle del run",
    )
    assert report.startswith("⚠️ **Calidad: Parcial**")
    assert "entrega 1/2" in report
    assert "pendiente" in report.lower()
    assert "detalle del run" in report


def test_build_run_report_lista_las_fases_no_entregadas():
    state = RunState(
        run_id="r",
        goal="g",
        plan=[
            PlanItem(kind="subtask", id="1", goal="a", path="l#1", status="delivered"),
            PlanItem(
                kind="phase",
                id="trazabilidad",
                goal="trazar",
                status="unmeasured",
                last_error="agente stalled",
            ),
        ],
        budget=BudgetState(100, 10, 0, 0),
        replans=[],
        memory=RunMemory(),
    )
    report = build_run_report(
        state,
        {
            "calidad": "Parcial",
            "calidad_entrega": "Alta",
            "calidad_proceso": "Parcial",
            "degradadas": 1,
        },
        "body",
    )
    assert "Proceso:" in report
    assert "trazabilidad" in report
    assert "agente stalled" in report


def test_available_for_work_descuenta_solo_la_reserva_no_gastada():
    b = BudgetState(limit=100, spent=20, reserve_final=10, reserve_replan=5, reserve_final_spent=4)
    assert b.available_for_work() == 69


def test_build_run_report_detalle_vacio_usa_sin_medicion():
    state = RunState(
        run_id="r",
        goal="g",
        plan=[PlanItem(kind="phase", id="l", goal="loop", status="blocked")],
        budget=BudgetState(100, 10, 0, 0),
        replans=[],
        memory=RunMemory(),
    )
    report = build_run_report(
        state,
        {"calidad": "Parcial", "calidad_entrega": "Parcial", "calidad_proceso": "Parcial"},
        "body",
    )
    assert "l (blocked: sin medición)" in report


def test_skip_por_contrato_no_cuenta_en_el_denominador():
    """Un descarte por contrato queda visible pero no es entrega pendiente."""
    state = RunState(
        run_id="r",
        goal="g",
        plan=[
            PlanItem(kind="subtask", id="1", goal="a", path="l#1", status="delivered"),
            PlanItem(
                kind="subtask",
                id="2",
                goal="test",
                path="l#2",
                status="skipped",
                notes=["descartado por contrato del preset (no ejecutado)"],
            ),
        ],
        budget=BudgetState(1000, 0, 0, 0),
    )
    counts = state.counts()
    assert counts["total"] == 1
    assert counts["delivered"] == 1
    assert counts["skipped"] == 1


def test_skip_con_artefacto_entregado_cuenta_como_delivered():
    state = RunState(
        run_id="r",
        goal="g",
        plan=[
            PlanItem(kind="subtask", id="1", goal="a", path="l#1", status="delivered"),
            PlanItem(
                kind="subtask",
                id="2",
                goal="README",
                path="l#2",
                status="skipped",
                satisfied_by="README.md",
                notes=["artefacto ya entregado por el run (README.md)"],
            ),
        ],
        budget=BudgetState(1000, 0, 0, 0),
    )
    counts = state.counts()
    assert counts["total"] == 2
    assert counts["delivered"] == 2


def test_skip_por_contrato_con_evidencia_cuenta_una_sola_vez():
    """Contrato + artefacto existente: entra al denominador y a delivered
    (no se excluye de total y se cuenta en delivered a la vez)."""
    state = RunState(
        run_id="r",
        goal="g",
        plan=[
            PlanItem(kind="subtask", id="1", goal="a", path="l#1", status="delivered"),
            PlanItem(kind="subtask", id="2", goal="b", path="l#2", status="delivered"),
            PlanItem(kind="subtask", id="3", goal="c", path="l#3", status="delivered"),
            PlanItem(
                kind="subtask",
                id="4",
                goal="tests",
                path="l#4",
                status="skipped",
                satisfied_by="tests/test_x.py",
                notes=[
                    "descartado por contrato del preset (no ejecutado)",
                    "artefacto ya entregado por el run (tests/test_x.py)",
                ],
            ),
        ],
        budget=BudgetState(1000, 0, 0, 0),
    )
    counts = state.counts()
    assert counts["total"] == 4
    assert counts["delivered"] == 4
    assert counts["skipped"] == 1
