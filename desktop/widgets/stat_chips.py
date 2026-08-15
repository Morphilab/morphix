"""StatChips — fila compacta de chips de estado (⏱ ⚡ 🧠 🚦 📂)."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

CHIP_DEFS = [
    ("elapsed_time", "⏱"),
    ("tokens_used", "⚡"),
    ("current_agent", "🧠"),
    ("status", "🚦"),
    ("phase", "📂"),
]

CHIP_STYLE = (
    "background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 8px;"
    " padding: 3px 8px; font-size: 11px; color: #E5E5E5;"
)


class StatChips(QWidget):
    """Chips derivados del contrato normalizado de stats."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._labels: dict[str, QLabel] = {}
        for key, icon in CHIP_DEFS:
            lbl = QLabel(f"{icon} —")
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
            lbl.setText(f"{next(icon for k, icon in CHIP_DEFS if k == key)} {text}")
            if key == "status":
                green = "completado" in text.lower()
                lbl.setStyleSheet(CHIP_STYLE + f" color: {'#22C55E' if green else '#F59E0B'};")

    def reset(self):
        """Vuelve los chips al estado inicial (—)."""
        for key, lbl in self._labels.items():
            icon = next(icon for k, icon in CHIP_DEFS if k == key)
            lbl.setText(f"{icon} —")
            lbl.setStyleSheet(CHIP_STYLE)
