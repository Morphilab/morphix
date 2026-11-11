# tests/test_project_gate.py — gate de proyecto previo al lanzamiento (offscreen)
"""Al lanzar un preset que exige proyecto (project.required) desde Dashboard,
la pane pide selección/creación ANTES de activar nada. Cancelar no muta la
pane y el contenedor reporta False (feedback en el Dashboard)."""

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_gate_asks_and_applies_project_before_launch(monkeypatch):
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    asked: list[str] = []

    def fake_ask(self, workflow_name: str):
        asked.append(workflow_name)
        self._switch_project("proj_elegido")
        return "proj_elegido"

    monkeypatch.setattr(SessionPane, "_ask_project_for_workflow", fake_ask, raising=False)
    m = MaestroTab()
    assert m.launch_workflow("tdd") is True
    assert asked == ["tdd"]
    pane = m._panes[-1]
    assert pane._current_project_root == "code_projects/proj_elegido"
    assert pane._active_workflow == "tdd"


def test_gate_cancel_returns_false_and_leaves_pane_untouched(monkeypatch):
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    monkeypatch.setattr(
        SessionPane, "_ask_project_for_workflow", lambda self, name: None, raising=False
    )
    m = MaestroTab()
    sessions_before = m.session_count
    assert m.launch_workflow("tdd") is False
    pane = m._panes[-1]
    assert pane._active_workflow is None
    assert pane._current_project_root is None
    assert m.session_count >= sessions_before


def test_gate_not_triggered_for_preset_without_project(monkeypatch):
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    calls: list[str] = []

    def fake_ask(self, workflow_name: str):
        calls.append(workflow_name)
        return None

    monkeypatch.setattr(SessionPane, "_ask_project_for_workflow", fake_ask, raising=False)
    m = MaestroTab()
    # collaborative es el preset sin project.required
    assert m.launch_workflow("collaborative") is True
    assert calls == []


def test_launch_workflow_returns_false_when_session_pane_launches_false(monkeypatch):
    """Contrato del contenedor: un False de la pane (cancelación) NO cambia de
    pestaña y se propaga al Dashboard."""
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    monkeypatch.setattr(
        SessionPane, "_ask_project_for_workflow", lambda self, name: None, raising=False
    )
    m = MaestroTab()
    assert m.launch_workflow("development") is False
