# tests/test_preload_gui.py — cableado del preload en la pane (offscreen)
"""El QAction de pre-carga nace deshabilitado y se habilita AL SELECCIONAR
proyecto (también para un proyecto fresco); sin force (incremental) y sin
bloquear el input del chat. El handler de progreso muestra/oculta la barra
según la fase."""

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


@pytest.fixture
def pane():
    _qapp()
    from desktop.maestro_tab import MaestroTab

    m = MaestroTab()
    p = m._panes[-1]
    yield p
    m.deleteLater()


def test_preload_nace_deshabilitado_y_se_habilita_al_elegir_proyecto(pane):
    assert pane._preload_action.isEnabled() is False
    pane._switch_project("mi_proyecto")
    assert pane._current_project_root == "code_projects/mi_proyecto"
    assert pane._preload_action.isEnabled() is True


def test_preload_se_apaga_al_limpiar_proyecto(pane):
    pane._switch_project("mi_proyecto")
    pane._clear_project_selection()
    assert pane._preload_action.isEnabled() is False


def test_preload_no_bloquea_el_input_del_chat(pane, monkeypatch):
    """El preload YA NO secuestra la sesión: el campo de texto queda usable
    mientras indexa (el propio botón se deshabilita durante su run)."""
    from unittest.mock import MagicMock

    import desktop.maestro_tab as mt

    coros: list = []

    def fake_run_async(coro):
        coros.append(coro)
        return MagicMock()  # soporta add_done_callback de otros callers

    monkeypatch.setattr(mt, "run_async", fake_run_async)
    pane._switch_project("mi_proyecto")
    pane._preload_project()
    assert pane.input_field.isEnabled() is True  # antes: False durante el indexado
    assert pane._preload_action.isEnabled() is False  # el botón sí, mientras corre
    assert len(coros) >= 1  # disparó el preload (+ refresh de rama del switch)
    for c in coros:
        c.close()  # los no-op no corren: cerrarlos limpia el warning


@pytest.mark.asyncio
async def test_do_preload_es_incremental_sin_force(pane, monkeypatch):
    """_do_preload YA NO fuerza re-encode total: sin force=True el
    hash-cache decide qué indexar (paridad con la vía automática)."""

    class _FakeIndexer:
        def __init__(self, workspace=None, project_root=None):
            self.project_root = project_root

        def index_project(self, progress_callback=None, **kw):
            assert "force" not in kw, f"force no debe viajar: {kw}"
            if progress_callback:
                progress_callback({"phase": "indexing", "pct": 10})
            return 3

    monkeypatch.setattr("core.codebase_indexer.CodebaseIndexer", _FakeIndexer)
    pane._switch_project("mi_proyecto")
    await pane._do_preload()
    assert "✅ 3 chunks en FAISS" in pane._preload_status.text()


def test_on_indexing_progress_muestra_y_cierra_por_fase(pane):
    pane._on_indexing_progress({"phase": "indexing", "pct": 42, "files_scanned": 5})
    assert pane._preload_progress.isHidden() is False
    assert pane._preload_progress.value() == 42

    pane._on_indexing_progress({"phase": "done"})
    assert pane._preload_progress.isHidden() is True
    assert "Índice listo" in pane._preload_status.text()

    pane._on_indexing_progress({"phase": "aborted"})
    assert pane._preload_progress.isHidden() is True
    assert "interrumpido" in pane._preload_status.text()


def test_progreso_de_via_automatica_visible_en_la_pane(pane):
    """La vía automática (workflow) emite por el bus de señales PROPIO de la
    pane (build_workflow_events lleva _own_signals) — la MISMA barra muestra
    el indexado que antes era 5 min de silencio."""
    pane._own_signals.indexing_progress.emit(
        {"phase": "indexing", "pct": 30, "current_file": "src/x.py", "files_scanned": 9}
    )
    assert pane._preload_progress.isHidden() is False
    assert "src/x.py" in pane._preload_status.text()
