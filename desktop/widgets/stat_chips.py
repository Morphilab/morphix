"""StatChips — fila compacta de chips de estado con puntos indicadores."""

from __future__ import annotations

from html import escape as _escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from desktop.theme import StyleFactory, ThemeManager

CHIP_KEYS = ["elapsed_time", "tokens_used", "current_agent", "status", "phase"]

_DOT_NEUTRAL = "#55575F"

CHIP_STYLE = StyleFactory.chip()


def _dot(color: str) -> str:
    return f"<span style='color:{color}'>&#9679;</span>"


class StatChips(QWidget):
    """Chips derivados del contrato normalizado de stats (dot + valor)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._labels: dict[str, QLabel] = {}
        for key in CHIP_KEYS:
            lbl = QLabel(f"{_dot(_DOT_NEUTRAL)} —")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet(CHIP_STYLE)
            lbl.setToolTip(key.replace("_", " "))
            layout.addWidget(lbl)
            self._labels[key] = lbl
        layout.addStretch()

    def update_from_stats(self, data: dict):
        for key, lbl in self._labels.items():
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
            dot_color = _DOT_NEUTRAL
            if key == "status":
                lower = text.lower()
                _c = ThemeManager.current().colors
                if "completad" in lower:
                    dot_color = _c.success
                    extra = f"<b>{_escape(text)}</b>"
                elif any(w in lower for w in ("ejecut", "running", "curso", "execut")):
                    dot_color = _c.status_running
                    extra = _escape(text)
                else:
                    dot_color = _c.warning
                    extra = _escape(text)
            elif key in ("elapsed_time", "tokens_used"):
                extra = f"<b>{_escape(text)}</b>"
            else:
                extra = f"<b>{_escape(text)}</b>"
            lbl.setText(f"{_dot(dot_color)}&nbsp;&nbsp;{extra}")

    def reset(self):
        """Vuelve los chips al estado inicial (—)."""
        for lbl in self._labels.values():
            lbl.setText(f"{_dot(_DOT_NEUTRAL)} —")
