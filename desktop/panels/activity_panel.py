# mypy: ignore-errors
"""Activity panel unificado — Ejecución/Subtareas/Archivos + tabs (spec §4).

Columna derecha del Maestro (2 columnas): secciones colapsables arriba,
tabs Diagrama/Log/Bash debajo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout, QWidget

from desktop.panels.detail_panel import build_detail_panel
from desktop.panels.execution_panel import build_execution_panel

if TYPE_CHECKING:
    from desktop.maestro_tab import MaestroTab


def build_activity_panel(tab: MaestroTab) -> QWidget:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    layout.addWidget(build_execution_panel(tab))
    layout.addWidget(build_detail_panel(tab), 1)

    return panel
