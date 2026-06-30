"""Chat Block — full-width dense message blocks (no bubbles, no copy button)."""

from datetime import UTC, datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextOption
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.theme import COLORS

# Role display config
_ROLE_CONFIG = {
    "user": ("You", COLORS["accent"]),
    "assistant": ("Morphix", COLORS["success"]),
    "system": ("", COLORS["text_dim"]),
}


class ChatBlock(QWidget):
    """Full-width message block with role header and markdown content."""

    def __init__(self, text: str, role: str = "assistant", parent=None):
        super().__init__(parent)
        self._text = text
        self._timestamp = datetime.now(UTC).strftime("%H:%M")
        self._role = role

        role_name, role_color = _ROLE_CONFIG.get(role, ("", COLORS["text_dim"]))

        # --- Header row: role label + timestamp ---
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 2)
        header.setSpacing(6)

        if role_name:
            role_label = QLabel(role_name)
            role_label.setFont(self._header_font())
            role_label.setStyleSheet(f"color: {role_color}; font-weight: bold; font-size: 10px;")
            header.addWidget(role_label)

        ts = QLabel(self._timestamp)
        ts.setStyleSheet(f"color: {COLORS['text_timestamp']}; font-size: 9px;")
        header.addWidget(ts)
        header.addStretch()

        # --- Content: QTextBrowser (markdown, no bubble styling) ---
        content = QTextBrowser()
        content.setOpenExternalLinks(True)
        content.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        content.setMarkdown(text)
        content.document().setDocumentMargin(4)
        content.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {COLORS['text_primary']}; "
            f"border: none; font-size: 14px; }}"
            f"QTextBrowser a {{ color: {COLORS['accent_light']}; }}"
            f"QTextBrowser code {{ background: {COLORS['bg_code']}; "
            f"padding: 2px 4px; border-radius: 4px; }}"
            f"QTextBrowser pre {{ background: {COLORS['bg_code']}; "
            f"padding: 8px; border-radius: 6px; }}"
        )
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._browser = content

        # Streaming debounce state
        self._pending_text: str | None = None
        self._stream_timer: QTimer | None = None

        # --- Assembly ---
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 4)
        col.setSpacing(2)
        col.addLayout(header)
        col.addWidget(content)

        # Fit height to content after layout
        QTimer.singleShot(0, self._update_text_width)

    @staticmethod
    def _header_font() -> QFont:
        f = QFont()
        f.setBold(True)
        f.setPointSize(9)
        return f

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
        self._browser.setMarkdown(self._pending_text)
        self._pending_text = None
        self._update_text_width()

    def flush_stream(self):
        """Render pending text immediately (e.g. at stream end)."""
        if self._stream_timer is not None and self._stream_timer.isActive():
            self._stream_timer.stop()
        self._flush_stream()
