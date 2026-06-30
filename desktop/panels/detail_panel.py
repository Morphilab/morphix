# mypy: ignore-errors
"""Detail panel — inner tabs: Agents, Diagram, Log, Bash."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QTabWidget,
    QTextBrowser,
)

from desktop.theme import StyleFactory

if TYPE_CHECKING:
    from desktop.maestro_tab import MaestroTab


def build_detail_panel(tab: MaestroTab) -> QTabWidget:
    # --- Detail panel: static tabs (Agents / Diagram / Log / Bash) ---
    tabs = QTabWidget()
    tabs.setStyleSheet(StyleFactory.detail_tabs())
    tab._detail_tabs = tabs
    log_style = StyleFactory.text_browser_log()

    tabs.addTab(tab.agent_panel, "Agentes")

    tab._diagram_view = QTextBrowser()
    tab._diagram_view.setReadOnly(True)
    tab._diagram_view.setStyleSheet(log_style)
    tab._diagram_view.setHtml("<p style='color:#888; text-align:center'>Diagrama DAG aquí</p>")
    tabs.addTab(tab._diagram_view, "Diagrama")

    tab._status_log_view = QTextBrowser()
    tab._status_log_view.setReadOnly(True)
    tab._status_log_view.setStyleSheet(log_style)
    tab._status_log_view.document().setMaximumBlockCount(400)
    tab._status_log_view.setHtml(
        "<p style='color:#888; text-align:center'>Listo. Envía una consulta</p>"
    )
    tab.status_log = tab._status_log_view  # backward-compat alias
    tabs.addTab(tab._status_log_view, "Log")

    tabs.addTab(tab.bash_panel, "Bash")
    return tabs
