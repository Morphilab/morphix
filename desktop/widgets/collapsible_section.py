"""CollapsibleSection — grupo colapsable reutilizable (flecha + título + contenido)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from desktop.theme import ThemeManager

# Tinta del subrayado de sección (mockup v9: #45464D).
_UNDERLINE = "#45464D"


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
        self._toggle_btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        _c = ThemeManager.current().colors
        self._toggle_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"border-radius: 0px; "
            f"border-bottom: 2px solid {_UNDERLINE}; "
            f"color: {_c.text_secondary}; font-size: 10px; "
            f"font-weight: bold; text-align: left; padding: 6px 0 5px 0; }}"
            f"QPushButton:hover {{ color: {_c.accent_secondary}; "
            f"border-bottom-color: {_c.accent_secondary_dark}; }}"
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
        self._toggle_btn.adjustSize()

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
