# tests/test_dsl_conformance.py
"""Conformance suite EJECUTABLE del DSL.

El harness vive en PRODUCTO (`orchestration/dsl/conformance.py`) para que el
CLI (`validate --conformance`) y los tests usen EL MISMO simulador. Este
módulo descubre TODOS los presets (producto + workspaces + skeletons CLI) y
afirma que corren a completed con las firmas reales de las tools.

Si rompes el contrato (p.ej. quitas `args` de un until con test_runner), esta
suite ES el test de regresión: el fake con firma real lanza TypeError → el
until jamás se cumple → el loop agota max_iter → falla el assert de
completed/file_path.
"""

from pathlib import Path

import pytest
import yaml

from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow
from orchestration.dsl.conformance import simulate_preset, structural_feedback_errors

REPO = Path(__file__).resolve().parent.parent


# ── Descubrimiento de presets ──────────────────────────────────────────────


def _product_presets() -> list[tuple[str, str, dict]]:
    """(nombre, workspace, raw) de los presets de producto."""
    out = []
    for yml in sorted((REPO / "templates" / "workflows").glob("*.yaml")):
        if yml.name.startswith("_"):
            continue  # _FULL_TEMPLATE es referencia documental
        raw = yaml.safe_load(yml.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("version") == 1:
            out.append((raw.get("name") or yml.stem, "main", raw))
    return out


def _workspace_presets() -> list[tuple[str, str, dict]]:
    out = []
    ws_base = REPO / "templates" / "workspaces"
    if not ws_base.is_dir():
        return out
    for ws_dir in sorted(p for p in ws_base.iterdir() if p.is_dir()):
        wf_dir = ws_dir / "workflows"
        if not wf_dir.is_dir():
            continue
        for yml in sorted(wf_dir.glob("*.yaml")):
            raw = yaml.safe_load(yml.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("version") == 1:
                out.append((raw.get("name") or yml.stem, ws_dir.name, raw))
    return out


def _skeleton_presets() -> list[tuple[str, str, dict]]:
    """Los esqueletos que `cli new` sirve a usuarios — deben correr."""
    from orchestration.dsl.cli import _DEFAULT_TOOLS, _SKELETONS

    out = []
    for stype, skeleton in _SKELETONS.items():
        raw = {
            "version": 1,
            "name": f"skeleton_{stype}",
            "description": skeleton["description"],
            "agents": {"allowed": skeleton["agents"]},
            "tools": {"allowed": _DEFAULT_TOOLS[stype]},
            "project": {"required": False},
            "skills": False,
            "steps": skeleton["steps"],
        }
        out.append((stype, "main", raw))
    return out


ALL_PRESETS = _product_presets() + _workspace_presets() + _skeleton_presets()


def test_conformance_descubre_presets():
    """Guard de cobertura: si el descubrimiento cae, hay un gap silencioso."""
    productos = len(_product_presets())
    assert productos >= 8, f"solo {productos} presets de producto descubiertos"
    assert ALL_PRESETS, "la conformance no descubrió ningún preset"


# ── La conformance ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "nombre,workspace,raw", ALL_PRESETS, ids=[f"{p[1]}:{p[0]}" for p in ALL_PRESETS]
)
async def test_preset_corre_a_completed_con_firmas_reales(nombre: str, workspace: str, raw: dict):
    report = await simulate_preset(raw, workspace=workspace)
    assert report.semantic_errors == [], f"'{nombre}': {report.semantic_errors}"
    assert report.ok, (
        f"preset '{nombre}' NO completa: status={report.status} "
        f"failure={report.failure} errors={report.errors} "
        f"rc1={report.test_runner_sin_file_path}"
    )
    assert (
        not report.test_runner_sin_file_path
    ), f"preset '{nombre}': test_runner llamado sin file_path (RC1)"


# ── Guard estructural: loops ciegos CON feedback ───────────────────────


@pytest.mark.parametrize(
    "nombre,workspace,raw", ALL_PRESETS, ids=[f"{p[1]}:{p[0]}" for p in ALL_PRESETS]
)
def test_loops_ciegos_consumen_feedback(nombre, workspace, raw):
    cw = compile_workflow(raw, catalog=FileSystemCatalog(workspace))
    errores = structural_feedback_errors(cw.dsl)
    assert not errores, f"preset '{nombre}':\n" + "\n".join(f"• {e}" for e in errores)


# ── el goal del TDD consume contexto de iteración ─────────────────────


def test_tdd_goal_referencia_contexto_iteracion():
    raw = yaml.safe_load(
        (REPO / "templates" / "workflows" / "tdd.yaml").read_text(encoding="utf-8")
    )
    goal = raw["steps"][0]["body"][0]["goal"]
    assert (
        "$iter" in goal and "$last_output" in goal
    ), "el goal del TDD debe consumir $iter/$max_iter/$last_output (RC2)"
