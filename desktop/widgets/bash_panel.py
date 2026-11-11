"""Bash Panel — visor de salida de comandos shell."""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget

from desktop.theme import COLORS


class BashPanel(QWidget):
    """Panel de salida bash con fuente monoespaciada."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        title = QLabel("Salida Bash")
        title.setStyleSheet(f"font-size: 11px; color: {COLORS['text_secondary']};")

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("monospace", 10))
        self.output.setStyleSheet(
            f"QTextEdit {{ background: {COLORS['bg_bash']}; color: {COLORS['success']}; "
            f"border: 1px solid {COLORS['border_default']}; "
            f"border-radius: 4px; padding: 4px; }}"
        )
        self.output.setPlaceholderText("(sin comandos ejecutados aún)")

        layout.addWidget(title)
        layout.addWidget(self.output)

    def set_output(self, text: str) -> None:
        """Actualiza la salida del panel."""
        self.output.setPlainText(text[-5000:])
        # Scroll al final
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.output.setTextCursor(cursor)
