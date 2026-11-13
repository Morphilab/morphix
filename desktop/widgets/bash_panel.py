"""Bash Panel — visor de salida de comandos shell."""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget

from desktop.theme import COLORS

# Tope del buffer acumulado (mismo límite documentado del panel: 5000).
MAX_CHARS = 5000


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
        self._buffer = ""

    def append_output(self, text: str) -> None:
        """Añade la salida de UN comando conservando las anteriores en orden.

        Antes cada comando reemplazaba el panel: con varios bash en un run
        solo sobrevivía el último y la salida previa se perdía.
        """
        chunk = str(text or "")
        if not chunk.strip():
            return
        self._buffer = f"{self._buffer}\n{chunk}" if self._buffer else chunk
        self._buffer = self._buffer[-MAX_CHARS:]
        self._render()

    def set_output(self, text: str) -> None:
        """Reemplaza el contenido del panel (compatibilidad)."""
        self._buffer = str(text or "")[-MAX_CHARS:]
        self._render()

    def clear(self) -> None:
        """Vacía el panel al inicio de un run nuevo."""
        self._buffer = ""
        self._render()

    def _render(self) -> None:
        self.output.setPlainText(self._buffer)
        # Scroll al final
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.output.setTextCursor(cursor)
