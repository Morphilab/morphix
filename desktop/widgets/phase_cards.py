"""PhaseCards — Diagrama derivado localmente de subtask_list (spec §3.4).

Reemplaza el signal diagram_update: la UI renderiza las tarjetas por fase
desde el último stats_update recibido. Cero drift con la lista de Subtareas.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTextBrowser

from desktop.theme import ThemeManager
from orchestration.status import render_from_subtasks


def _empty_html() -> str:
    return (
        "<p style='color:"
        f"{ThemeManager.current().colors.text_dim}; text-align:center'>Diagrama aquí</p>"
    )


class PhaseCards(QTextBrowser):
    """Visor de tarjetas de estado agrupadas por fase."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self._last_html: str = self._normalize(_empty_html())
        self.setHtml(_empty_html())

    def reset(self) -> None:
        """Vuelve al estado vacío (run nuevo / chat limpio).

        Sin esto el diagrama seguía mostrando las tarjetas del run anterior
        — un run que no emite plan (chat directo) nunca lo reemplazaba.
        """
        self._last_html = self._normalize(_empty_html())
        self.setHtml(_empty_html())

    def update_from_stats(self, data: dict):
        # stats parciales (agent-loop "Agent iteration N/M")
        # llegan SIN subtask_list o con lista vacía — `or []` borraba el
        # diagrama a "Workflow vacío" hasta el próximo boundary (parpadeo).
        # Sin lista nueva conocida: conservar el último render.
        subtask_list = data.get("subtask_list")
        if not subtask_list:
            return
        phase = data.get("phase")
        c = ThemeManager.current().colors
        colors = {
            "completed": c.status_completed,
            "running": c.status_running,
            "failed": c.status_failed,
            "pending": c.status_pending,
            "recovered": c.status_recovered,
            "card_bg": c.bg_raised,
            "body_bg": "transparent",
            "text": c.text_primary,
            "dim": c.text_dim,
            "accent": c.accent_secondary,
        }
        html = render_from_subtasks(subtask_list, phase=phase, colors=colors)
        # re-render solo con contenido distinto: setHtml resetea el scroll y
        # cada boundary del run re-emitía la misma lista.
        normalized = self._normalize(html)
        if normalized == self._last_html:
            return
        self._last_html = normalized
        # el contenido SÍ cambió: conservar la posición de lectura (un run
        # largo actualiza el diagrama en cada ronda y saltaba al principio).
        scroll = self.verticalScrollBar().value()
        self.setHtml(html)
        self.verticalScrollBar().setValue(scroll)

    @staticmethod
    def _normalize(html: str) -> str:
        return " ".join(str(html or "").split())
