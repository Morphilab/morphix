# tests/test_dsl_doc_consistency.py — guard de la superficie DSL en docs públicas
"""El conteo de kinds y los campos del schema citados en las docs viven
aquí pineados contra el código — el fragment del 10-18 pasó la suite 2595
porque nadie guardaba esta superficie: solo el README se actualizó a mano."""


import re
import typing
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_orchestration_layer_cuenta_los_kinds_reales():
    """Todo conteo de step kinds citado en la doc de capas == el union real
    del schema (un kind nuevo que no actualice la doc falla aquí)."""
    from orchestration.dsl.schema import StepSpec

    # Annotated[union, Field(...)]: los args del Annotated son (union, Field);
    # los MIEMBROS viven en los args de la unión interna.
    actual = len(typing.get_args(typing.get_args(StepSpec)[0]))
    text = _read("docs/architecture/layers/orchestration-layer.md")
    claims = set(re.findall(r"union of (\d+) step kinds", text))
    claims |= set(re.findall(r"Step kinds \((\d+)\)", text))
    assert claims, "la doc de capas debe citar el conteo de kinds (patrones conocidos)"
    for claim in claims:
        assert int(claim) == actual, (
            f"orchestration-layer.md cita {claim} kinds; el schema tiene {actual} — "
            "actualiza la doc (o el conteo cambió sin querer)"
        )


def test_adding_workflows_cubre_campos_del_schema():
    """La referencia de campos de la guía del desarrollador menciona la
    superficie clave del schema — una congelación silenciosa falla aquí."""
    text = _read("docs/developer-guide/adding-workflows.md")
    faltan = [
        campo
        for campo in (
            "budget_tokens",
            "budget_reserve_final",
            "budget_reserve_replan",
            "produces",
            "verified_by",
            "on_blocker",
            "replan",
            "retry_on_verification",
            "min_output_chars",
            "verdict",
            "fragment",
            "parallel_max",
        )
        if campo not in text
    ]
    assert not faltan, f"adding-workflows.md no documenta: {faltan}"


def test_orchestration_layer_documenta_el_estado_del_run():
    """El contrato de entrega/replan/stop es parte del motor, no un detalle
    del preset: la doc de capas debe describirlo."""
    text = _read("docs/architecture/layers/orchestration-layer.md")
    faltan = [
        campo
        for campo in (
            "RunState",
            "produces",
            "verified_by",
            "replan",
            "budget_reserve_final",
            "stopped",
        )
        if campo not in text
    ]
    assert not faltan, f"orchestration-layer.md no documenta: {faltan}"


def test_tools_md_no_contradice_allowlists_internas():
    """delegate_task ESTÁ en allowlists internas (developer +
    development/coordinated/sdd): la fila de tools.md no puede afirmar lo
    contrario — la MISMA página ya la lista en su tabla de agentes."""
    text = _read("docs/user-guide/tools.md")
    assert (
        "not part of internal agent allowlists" not in text
    ), "tools.md contradice su propia tabla de agentes sobre delegate_task"
