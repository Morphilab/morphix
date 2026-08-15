"""CollapsibleSection — grupo colapsable reutilizable (flecha + título + contenido)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QPushButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """Sección colapsable con estado inicial configurable.

    Se usa en el panel de actividad (Ejecución / Subtareas / Archivos).
    """

    def __init__(self, title: str, parent=None, collapsed: bool = False):
        super().__init__(parent)
        self._collapsed = collapsed
        self._title = title

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(2)

        self._toggle_btn = QPushButton()
        self._toggle_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #E5E5E5;"
            " font-size: 12px; font-weight: bold; text-align: left; padding: 4px 2px; }"
            "QPushButton:hover { color: #22C55E; }"
        )
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.clicked.connect(self.toggle)
        main.addWidget(self._toggle_btn)

        self._body = QFrame()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(2, 2, 2, 2)
        self._body_layout.setSpacing(4)
        main.addWidget(self._body)

        self._update_arrow()

    def _update_arrow(self):
        arrow = "▶" if self._collapsed else "▼"
        self._toggle_btn.setText(f"{arrow}  {self._title}")
        self._body.setVisible(not self._collapsed)

    def toggle(self):
        self._collapsed = not self._collapsed
        self._update_arrow()

    def set_collapsed(self, collapsed: bool):
        self._collapsed = collapsed
        self._update_arrow()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_title(self, title: str):
        self._title = title
        self._update_arrow()

    def add_widget(self, widget: QWidget):
        self._body_layout.addWidget(widget)
