# mypy: ignore-errors
"""Execution panel — secciones colapsables: Ejecución / Subtareas / Archivos (spec §4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QListWidget,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import StyleFactory
from desktop.widgets.collapsible_section import CollapsibleSection
from desktop.widgets.stat_chips import StatChips

if TYPE_CHECKING:
    from desktop.maestro_tab import MaestroTab

LIST_STYLE = (
    "QListWidget { background: #0F0F0F; border: 1px solid #2A2A2A; "
    "border-radius: 8px; padding: 4px; font-size: 11px; color: #A0A0A0; }"
    "QListWidget::item { padding: 3px 6px; }"
)


def build_execution_panel(tab: MaestroTab) -> QWidget:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(6)

    # ── Ejecución (siempre visible) ──
    run_section = CollapsibleSection("Ejecución")
    tab._progress_bar = QProgressBar()
    tab._progress_bar.setRange(0, 100)
    tab._progress_bar.setValue(0)
    tab._progress_bar.setFormat("—")
    tab._progress_bar.setStyleSheet(StyleFactory.progress_bar())
    run_section.add_widget(tab._progress_bar)
    tab.stat_chips = StatChips()
    run_section.add_widget(tab.stat_chips)
    layout.addWidget(run_section)

    # ── Subtareas (auto-colapsada si no hay pasos) ──
    tab._subtask_section = CollapsibleSection("Subtareas", collapsed=True)
    tab._subtask_list = QListWidget()
    tab._subtask_list.setStyleSheet(LIST_STYLE)
    tab._subtask_section.add_widget(tab._subtask_list)
    layout.addWidget(tab._subtask_section, 1)

    # ── Archivos creados ──
    tab._files_section = CollapsibleSection("Archivos creados", collapsed=True)
    tab._files_written_list = QListWidget()
    tab._files_written_list.setStyleSheet(
        LIST_STYLE + "QListWidget { color: #22C55E; font-size: 10px; }"
    )
    tab._files_section.add_widget(tab._files_written_list)
    layout.addWidget(tab._files_section)

    return panel
