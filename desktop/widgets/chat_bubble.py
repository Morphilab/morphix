"""Chat Block — full-width dense message blocks (no bubbles, no copy button)."""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QTextOption
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.sanitize import sanitize_rich_text
from desktop.theme import COLORS, ThemeManager

# Role display config (snapshot del tema activo al importar)
_ROLE_CONFIG = {
    "user": ("Tú", COLORS["text_secondary"]),
    "assistant": ("Morphix", ThemeManager.current().colors.accent_secondary),
    "system": ("", COLORS["text_dim"]),
}

# Tinte de la regla punteada inferior (~28% sobre fondos oscuros).
_RULE_COLOR = QColor(235, 235, 245, 72)


class ChatBlock(QWidget):
    """Full-width message block with role header and markdown content."""

    def __init__(self, text: str, role: str = "assistant", parent=None):
        super().__init__(parent)
        self._text = text
        # hora LOCAL (antes UTC — 6h de desfase con el reloj del sistema)
        self._timestamp = datetime.now().astimezone().strftime("%H:%M")
        self._role = role

        role_name, role_color = _ROLE_CONFIG.get(role, ("", COLORS["text_dim"]))
        _c = ThemeManager.current().colors
        _ty = ThemeManager.current().typography

        # -- Header row: role label + timestamp ---
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 2)
        header.setSpacing(6)

        if role_name:
            role_weight = "bold" if role == "assistant" else "normal"
            role_label = QLabel(role_name)
            role_label.setStyleSheet(
                f"font-family: {_ty.family_ui}; "
                f"color: {role_color}; font-size: 11px; "
                f"font-weight: {role_weight};"
            )
            header.addWidget(role_label)

        ts = QLabel(self._timestamp)
        ts.setStyleSheet(
            f"color: {COLORS['text_timestamp']}; font-size: 10px; "
            f"font-family: {_ty.family_mono};"
        )
        header.addWidget(ts)
        header.addStretch()

        # -- Content: QTextBrowser (markdown, no bubble styling) ---
        content = QTextBrowser()
        content.setOpenExternalLinks(True)
        # R6/viewer: links view-file://<ruta> abren el visor
        # standalone en vez de navegar. El resto de links sigue nativo.
        content.anchorClicked.connect(self._on_anchor_clicked)
        content.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        content.setMarkdown(sanitize_rich_text(text))
        content.document().setDocumentMargin(4)
        content.setStyleSheet(
            # .11: cuerpo del chat 14→15px — legibilidad pedida por el usuario
            # Regla de 2 tonos: bloques de código = superficie
            # (bg_solid); el mono + borde los distinguen, sin tercer tono.
            f"QTextBrowser {{ background: transparent; color: {COLORS['text_primary']}; "
            f"border: none; font-size: 15px; }}"
            f"QTextBrowser a {{ color: {_c.accent_light}; }}"
            f"QTextBrowser code {{ background: {COLORS['bg_surface']}; "
            f"padding: 2px 5px; border-radius: 4px; "
            f"color: {_c.accent_highlight}; }}"
            f"QTextBrowser pre {{ background: {COLORS['bg_surface']}; "
            f"padding: 10px 12px; border-radius: 8px; "
            f"color: {_c.accent_highlight}; }}"
        )
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._browser = content

        # Streaming debounce state
        self._pending_text: str | None = None
        self._stream_timer: QTimer | None = None

        # -- Assembly ---
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 4)
        col.setSpacing(2)
        col.addLayout(header)
        col.addWidget(content)

        # Fit height to content after layout
        QTimer.singleShot(0, self._update_text_width)

    def _update_text_width(self):
        browser = self._browser
        if browser:
            w = browser.viewport().width()
            if w > 50:
                browser.document().setTextWidth(max(w - 16, 100))
            doc_h = int(browser.document().size().height() + 12)
            browser.setFixedHeight(max(doc_h, 22))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_text_width()

    def paintEvent(self, event):  # noqa: N802
        """Regla punteada inferior entre mensajes (estilo manuscrito)."""
        super().paintEvent(event)
        painter = QPainter(self)
        pen = QPen(_RULE_COLOR, 1, Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3, 5])
        painter.setPen(pen)
        y = self.height() - 1
        painter.drawLine(6, y, max(self.width() - 6, 8), y)
        painter.end()

    # ── Streaming API (same signature as ChatBubble) ──

    def update_text(self, text: str):
        """Debounced streaming update — coalesces tokens every ~70ms."""
        self._text = text
        self._pending_text = text
        if self._stream_timer is None:
            self._stream_timer = QTimer(self)
            self._stream_timer.setSingleShot(True)
            self._stream_timer.timeout.connect(self._flush_stream)
        if not self._stream_timer.isActive():
            self._stream_timer.start(70)

    def _flush_stream(self):
        if self._pending_text is None or self._browser is None:
            return
        self._browser.setMarkdown(sanitize_rich_text(self._pending_text))
        self._pending_text = None
        self._update_text_width()

    def flush_stream(self):
        """Render pending text immediately (e.g. at stream end)."""
        if self._stream_timer is not None and self._stream_timer.isActive():
            self._stream_timer.stop()
        self._flush_stream()

    def _on_anchor_clicked(self, url) -> None:
        """Links view-file://<ruta-relativa> → visor standalone."""
        if url.scheme() != "view-file":
            return
        from desktop.events import get_signals

        get_signals().view_file_requested.emit(url.toString().removeprefix("view-file://"))
