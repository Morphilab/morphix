"""StatChips — fila compacta de chips de estado con puntos indicadores."""

from __future__ import annotations

from html import escape as _escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from desktop.theme import StyleFactory, ThemeManager
from orchestration.status import is_terminal_status

CHIP_KEYS = ["elapsed_time", "tokens_used", "current_agent", "status", "phase"]

# Punto neutro: el gris más tenue del tema (antes hex fijo #55575F).
_DOT_NEUTRAL = ThemeManager.current().colors.text_muted

CHIP_STYLE = StyleFactory.chip()

_RUNNING_WORDS = ("ejecut", "running", "curso", "execut")
_FAILED_WORDS = ("fallid", "failed", "error", "detenid", "cancel")


def _dot(color: str) -> str:
    return f"<span style='color:{color}'>&#9679;</span>"


def _status_color(text: str) -> str:
    """Color del punto según el status REAL (verde/rojo/azul/ámbar)."""
    lower = text.lower()
    colors = ThemeManager.current().colors
    if any(w in lower for w in _FAILED_WORDS):
        return colors.status_failed
    if "completad" in lower:
        return colors.success
    if any(w in lower for w in _RUNNING_WORDS):
        return colors.status_running
    return colors.warning


class StatChips(QWidget):
    """Chips derivados del contrato normalizado de stats (dot + valor)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._labels: dict[str, QLabel] = {}
        # valor plano del chip de estado: mark_finished decide sobre él, no
        # sobre el HTML (parsear el markup era frágil).
        self._status_text: str = ""
        for key in CHIP_KEYS:
            lbl = QLabel(f"{_dot(_DOT_NEUTRAL)} —")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet(CHIP_STYLE)
            lbl.setToolTip(key.replace("_", " "))
            layout.addWidget(lbl)
            self._labels[key] = lbl
        layout.addStretch()

    def _paint(self, key: str, text: str, dot_color: str | None = None, bold: bool = True):
        if dot_color is None:
            dot_color = _status_color(text) if key == "status" else _DOT_NEUTRAL
        body = f"<b>{_escape(text)}</b>" if bold else _escape(text)
        self._labels[key].setText(f"{_dot(dot_color)}&nbsp;&nbsp;{body}")
        if key == "status":
            self._status_text = text

    def update_from_stats(self, data: dict):
        for key in self._labels:
            value = data.get(key)
            if value is None:
                continue
            text = str(value)
            if key == "tokens_used":
                try:
                    text = f"{int(value):,}"
                except (TypeError, ValueError):
                    text = str(value)
            elif key == "current_agent" and str(value) in ("—", "None"):
                text = "—"
            self._paint(key, text)

    def set_status(self, text: str) -> None:
        """Fija el chip de estado sin pasar por el contrato de stats."""
        self._paint("status", str(text))

    def set_phase(self, text: str) -> None:
        """Fija el chip de fase (fase derivada del plan si el stats no la trae)."""
        if text:
            self._paint("phase", str(text))

    def mark_finished(self) -> None:
        """Cierra el chip si el run terminó sin un stats terminal.

        Un chip "Ejecutando…" con el dot ○ es una contradicción: el run ya no
        corre (excepción/cancelación sin cierre), así que el estado se declara
        finalizado en vez de dejar la mentira del último parcial.
        """
        if not self._status_text or is_terminal_status(self._status_text):
            return
        self.set_status("Finalizado")

    def reset(self):
        """Vuelve los chips al estado inicial (—)."""
        self._status_text = ""
        for lbl in self._labels.values():
            lbl.setText(f"{_dot(_DOT_NEUTRAL)} —")
