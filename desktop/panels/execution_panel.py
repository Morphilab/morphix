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

from desktop.theme import COLORS, StyleFactory, ThemeManager
from desktop.widgets.collapsible_section import CollapsibleSection
from desktop.widgets.stat_chips import StatChips

if TYPE_CHECKING:
    from desktop.maestro_tab import SessionPane

LIST_STYLE = (
    f"QListWidget {{ background: {COLORS['bg_surface']}; "
    f"border: 1px solid {COLORS['border_default']}; "
    f"border-radius: 9px; padding: 2px 0px; font-size: 12px; "
    f"color: {COLORS['text_secondary']}; }}"
    f"QListWidget::item {{ padding: 7px 13px; "
    f"border-bottom: 1px solid rgba(255, 255, 255, 30); }}"
)


def build_execution_panel(tab: SessionPane) -> QWidget:
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
        LIST_STYLE
        + f"QListWidget {{ font-family: {ThemeManager.current().typography.family_mono}; "
        f"font-size: 11px; }}"
    )
    tab._files_section.add_widget(tab._files_written_list)
    layout.addWidget(tab._files_section)

    # La leyenda de estado vacío ('Sin ejecución activa…') vive ahora en el
    # CHAT como nota dashed (tab._idle_note — chat_panel.py, opción A del
    # El chip ●/○ de la cabecera ACTIVIDAD conserva el
    # indicador en idle; el label del panel se eliminó por redundante.

    return panel
