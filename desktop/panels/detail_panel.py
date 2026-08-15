# mypy: ignore-errors
"""Detail panel — tabs: Diagrama / Log / Bash (Bash oculto según allowlist, spec §4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QTabWidget, QTextBrowser

from desktop.theme import StyleFactory
from desktop.widgets.phase_cards import PhaseCards

if TYPE_CHECKING:
    from desktop.maestro_tab import MaestroTab


def build_detail_panel(tab: MaestroTab) -> QTabWidget:
    tabs = QTabWidget()
    tabs.setStyleSheet(StyleFactory.detail_tabs())
    tab._detail_tabs = tabs
    log_style = StyleFactory.text_browser_log()

    tab._diagram_view = PhaseCards()
    tab._diagram_view.setStyleSheet(log_style)
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


def update_tabs_for_workflow(
    tab: MaestroTab,
    workflow_allowed_tools: list[str] | None = None,
    agent_tools: list[str] | None = None,
):
    """Oculta/muestra el tab Bash según allowlist (spec §4.4).

    Precedencia: perfil del agente forzado (chat directo) → allowlist del
    workflow → sin información: mostrar siempre.
    """
    from tools.specs import tool_matches_allowlist

    if agent_tools is not None:
        has_bash = "bash_manager" in agent_tools
    elif workflow_allowed_tools:
        has_bash = tool_matches_allowlist("bash_manager", workflow_allowed_tools)
    else:
        has_bash = True
    bash_idx = tab._detail_tabs.indexOf(tab.bash_panel)
    if bash_idx < 0:
        return
    tab._detail_tabs.setTabVisible(bash_idx, has_bash)
