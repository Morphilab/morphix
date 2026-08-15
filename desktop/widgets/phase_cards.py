"""PhaseCards — Diagrama derivado localmente de subtask_list (spec §3.4).

Reemplaza el signal diagram_update: la UI renderiza las tarjetas por fase
desde el último stats_update recibido. Cero drift con la lista de Subtareas.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTextBrowser

from orchestration.status import render_from_subtasks


class PhaseCards(QTextBrowser):
    """Visor de tarjetas de estado agrupadas por fase."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self.setHtml("<p style='color:#888; text-align:center'>Diagrama aquí</p>")

    def update_from_stats(self, data: dict):
        subtask_list = data.get("subtask_list") or []
        phase = data.get("phase")
        html = render_from_subtasks(subtask_list, phase=phase)
        self.setHtml(html)
