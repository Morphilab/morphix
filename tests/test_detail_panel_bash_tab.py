# tests/test_detail_panel_bash_tab.py
"""El tab Bash SIEMPRE visible.

Ocultarlo según allowlist hace que la pestaña 'desaparezca' entre
workflows. Se muestra deshabilitada con tooltip cuando bash_manager no
está permitido (misma info, sin sorpresa).
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QLabel,  # noqa: E402
    QTabWidget,
    QWidget,
)

from desktop.panels.detail_panel import update_tabs_for_workflow  # noqa: E402


class _FakeTab(QWidget):
    def __init__(self):
        super().__init__()
        self._detail_tabs = QTabWidget()
        self.bash_panel = QWidget()
        self._detail_tabs.addTab(self.bash_panel, "Bash")
        self._detail_tabs.addTab(QLabel("diag"), "Diagrama")


def _qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app


def test_bash_visible_y_habilitada_con_allowlist():
    _qapp()
    tab = _FakeTab()
    update_tabs_for_workflow(tab, ["file_manager", "bash_manager", "code_search"])
    idx = tab._detail_tabs.indexOf(tab.bash_panel)
    assert tab._detail_tabs.isTabVisible(idx)
    assert tab._detail_tabs.isTabEnabled(idx)


def test_bash_visible_deshabilitada_sin_bash_en_allowlist():
    _qapp()
    tab = _FakeTab()
    update_tabs_for_workflow(tab, ["file_manager", "code_search"])
    idx = tab._detail_tabs.indexOf(tab.bash_panel)
    assert tab._detail_tabs.isTabVisible(idx), "el tab nunca debe desaparecer"
    assert not tab._detail_tabs.isTabEnabled(idx)
    assert "bash_manager" in (tab._detail_tabs.tabToolTip(idx) or "")


def test_bash_habilitada_sin_info():
    _qapp()
    tab = _FakeTab()
    update_tabs_for_workflow(tab, None)
    idx = tab._detail_tabs.indexOf(tab.bash_panel)
    assert tab._detail_tabs.isTabEnabled(idx)


def test_bash_agente_forzado_sin_bash_deshabilitada():
    _qapp()
    tab = _FakeTab()
    update_tabs_for_workflow(tab, ["bash_manager"], agent_tools=["memory_inspector"])
    idx = tab._detail_tabs.indexOf(tab.bash_panel)
    assert tab._detail_tabs.isTabVisible(idx)
    assert not tab._detail_tabs.isTabEnabled(idx)
