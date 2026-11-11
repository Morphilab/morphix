# tests/test_resto_s2.py — cierre de §2 de la investigación workflows
"""Contratos: el catálogo reporta el YAML roto real (no 'no existe'), los
hijos de un grupo de delegación corren CONCURRENTES, y el detalle GUI
expande los fragments del flujo."""

import os

import pytest

# ── Task 1: YAML roto honesto en el catálogo ───────────────────────────────


def test_fragment_yaml_roto_falla_con_el_error_real(tmp_path):
    """Editar un workflow y romper el YAML produce el error de sintaxis con
    el path — no 'No existe el fragmento'. El diagnóstico del flujo EDITAR
    no puede mentir justo cuando se necesita.

    El catálogo apunta los candidatos a tmp SIN tocar el singleton `paths`
    (la doctrina del repo: parchear la clase/instancia de PathResolver es
    frágil ante estado global de la suite — contamina tests posteriores).
    """
    from orchestration.dsl.compiler import FileSystemCatalog, compile_workflow

    class _CatalogTmp(FileSystemCatalog):
        def __init__(self, root):
            super().__init__(None)
            self._root = root

        def _candidate_paths(self, name):
            return [self._root / f"{name}.yaml"]

    roto = tmp_path / "_roto.yaml"
    roto.write_text("version: 1\nsteps: [ {{{ roto", encoding="utf-8")

    doc = {
        "version": 1,
        "steps": [{"id": "cuerpo", "kind": "fragment", "include": "_roto"}],
    }
    with pytest.raises(ValueError, match="YAML inválido") as ei:
        compile_workflow(doc, catalog=_CatalogTmp(tmp_path))
    assert str(tmp_path) in str(ei.value), "el error nombra el archivo roto"


# ── Task 2: hijos del grupo de delegación CONCURRENTES ─────────────────────


@pytest.mark.asyncio
async def test_hijos_del_grupo_corren_concurrentes():
    """El gather de _run_unit tenía el await DENTRO de la comprehension: los
    hijos del mismo grupo corrían SECUENCIALES (contradiciendo el docstring
    del módulo) — la concurrencia real solo existía entre unidades."""

    import orchestration.delegation as dl
    from tests.test_delegation import _cfg, _fake_executor

    concurrent = {"now": 0, "max": 0}
    executor = _fake_executor(delay=0.05, concurrent=concurrent)

    out = await dl.delegate_task(
        tasks=[{"goal": "a", "group": "g"}, {"goal": "b", "group": "g"}],
        executor=executor,
        config=_cfg(),
    )
    assert out["status"] == "completed"
    assert concurrent["max"] == 2, "los hijos del grupo deben solaparse en vuelo"


# ── Task 3: fragment visible en el detalle GUI ──────────────────────────────

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_kind_summary_expande_fragments():
    """El conteo de kinds contaba 'fragment: 1' y perdía la composición real
    (el IR reemplaza el fragmento por sus steps) — development/coordinated
    subreportaban sus agent/tool internos."""
    from desktop.services.workflow_view import load_workflow_view

    view = load_workflow_view("main", "development")
    ks = view["kind_summary"]
    assert "agent" in ks and ks["agent"] >= 1, "el fragment aporta sus steps internos"
    assert "verificar" in " ".join(ks) or "tool" in ks
    assert "fragment" not in ks, "el fragmento compilado no es un kind del run"


def test_for_workflow_expande_fragments_del_flujo():
    """El fragment es compile-time only y el detalle construía desde el YAML
    crudo: development (preset core) mostraba el loop con UN hijo opaco en
    vez de su patrón test-first + verificación. El árbol expande el include."""
    pytest.importorskip("PySide6")
    from typing import cast

    from PySide6.QtWidgets import QApplication, QLabel

    from desktop.widgets.entity_info_dialog import EntityInfoDialog

    _qapp = cast(QApplication, QApplication.instance() or QApplication([]))
    dlg = EntityInfoDialog.for_workflow(None, "main", "development")
    assert dlg is not None
    texts = " | ".join(lbl.text() for lbl in dlg.findChildren(QLabel))
    assert "verificar_subtarea" in texts, "el body real del fragmento aparece"
    assert "subtarea" in texts
    dlg.close()
