# mypy: ignore-errors
"""Activity panel unificado — Ejecución/Subtareas/Archivos + tabs (spec §4).

Columna derecha del Maestro (2 columnas): secciones colapsables arriba,
tabs Diagrama/Log/Bash debajo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from desktop.panels.detail_panel import build_detail_panel
from desktop.panels.execution_panel import build_execution_panel
from desktop.theme import COLORS

if TYPE_CHECKING:
    from desktop.maestro_tab import SessionPane


def build_activity_panel(tab: SessionPane) -> QWidget:
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)

    # ── Cabecera ACTIVIDAD + chip de estado ──
    header = QWidget()
    hlay = QHBoxLayout(header)
    hlay.setContentsMargins(8, 6, 8, 0)
    title = QLabel("ACTIVIDAD")
    title.setStyleSheet(
        f"font-size: 9px; letter-spacing: 1px; color: {COLORS['text_dim']}; font-weight: 600;"
    )
    tab._activity_dot = QLabel("○")
    tab._activity_dot.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px;")
    tab._activity_dot.setToolTip("Sin ejecución activa")
    hlay.addWidget(title)
    hlay.addStretch()
    hlay.addWidget(tab._activity_dot)
    layout.addWidget(header)

    layout.addWidget(build_execution_panel(tab))
    layout.addWidget(build_detail_panel(tab), 1)

    return panel
