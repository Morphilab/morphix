"""CodeEditor — QPlainTextEdit con gutter de números de línea y resaltado Pygments.

Componente de edición:
- Gutter con números de línea (línea actual resaltada, colores del theme).
- Resaltado de sintaxis vía Pygments (import perezoso; degrada a no-op).
- Archivos > HIGHLIGHT_LIMIT se muestran sin resaltado (rendimiento).
- Tracking de estado "sucio" y path asociado por documento.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from desktop.theme import StyleFactory, ThemeManager

logger = logging.getLogger(__name__)

HIGHLIGHT_LIMIT = 200_000  # bytes: por encima → texto plano + números (sin resaltado)

_EXT_LEXERS: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".sh": "bash",
    ".bash": "bash",
    ".sql": "sql",
    ".css": "css",
    ".html": "html",
    ".js": "javascript",
    ".ts": "typescript",
}

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name
    from pygments.token import Token

    _PYGMENTS_OK = True
except ImportError:  # pragma: no cover — pygments es dependencia transitiva
    _PYGMENTS_OK = False


def mono_font() -> QFont:
    """Fuente mono de la primera familia del theme (fallback monospace genérica)."""
    first = ThemeManager.current().typography.family_mono.split(",")[0].strip().strip('"')
    font = QFont()
    font.setFamilies([first, "monospace"])
    return font


def _qcolor(css: str) -> QColor:
    """Convierte un token de color del theme (#hex o rgba()) a QColor."""
    if css.startswith("rgba("):
        parts = [p.strip() for p in css.removeprefix("rgba(").removesuffix(")").split(",")]
        r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
        a = int(float(parts[3]) * 255) if "." in parts[3] else int(parts[3])
        return QColor(r, g, b, a)
    return QColor(css)


def _build_formats() -> dict[Any, QTextCharFormat]:
    """Mapea tokens Pygments → QTextCharFormat con la paleta desaturada del theme."""
    if not _PYGMENTS_OK:
        return {}
    c = ThemeManager.current().colors

    def fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setForeground(QColor(color))
        if bold:
            f.setFontWeight(QFont.Weight.Bold)
        if italic:
            f.setFontItalic(True)
        return f

    return {
        Token.Keyword: fmt(c.accent_primary, bold=True),
        Token.String: fmt(c.success),
        Token.Comment: fmt(c.text_dim, italic=True),
        Token.Number: fmt(c.warning),
        Token.Name.Function: fmt(c.info, bold=True),
        Token.Name.Class: fmt(c.accent_light, bold=True),
        Token.Name.Decorator: fmt(c.status_recovered),
        Token.Name.Builtin: fmt(c.info),
        Token.Operator: fmt(c.text_secondary),
    }


class PygmentsHighlighter(QSyntaxHighlighter):
    """Resaltado por bloques vía Pygments; no-op si Pygments no está disponible."""

    def __init__(self, document: Any) -> None:
        super().__init__(document)
        self._lexer_name: str | None = None
        self._formats = _build_formats() if _PYGMENTS_OK else {}

    def set_lexer_name(self, name: str | None) -> None:
        self._lexer_name = name
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        if not _PYGMENTS_OK or self._lexer_name is None or not text:
            return
        try:
            lexer = get_lexer_by_name(self._lexer_name)
        except Exception:
            logger.debug("lexer pygments no encontrado: %s", self._lexer_name)
            self._lexer_name = None
            return
        pos = 0
        for tok_type, value in lex(text, lexer):
            for parent, fmt in self._formats.items():
                if tok_type in parent:  # pygments: subtipos contenidos en el padre
                    self.setFormat(pos, len(value), fmt)
                    break
            pos += len(value)


class _LineNumberArea(QWidget):
    """Gutter izquierdo del CodeEditor; delega el pintado al editor dueño."""

    def __init__(self, editor: CodeEditor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event: Any) -> None:
        self._editor.lineNumberAreaPaintEvent(event)


class CodeEditor(QPlainTextEdit):
    """Editor de texto plano con gutter de números de línea y resaltado."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(StyleFactory.text_editor())
        self.setFont(mono_font())
        self._dirty = False
        self._path: Path | None = None
        self.highlighter = PygmentsHighlighter(self.document())

        self.line_number_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self.textChanged.connect(self._mark_dirty)
        self._update_line_number_area_width(0)
        self._highlight_current_line()

    # ── Documento ──────────────────────────────────────────────────

    def set_path(self, path: Path) -> None:
        self._path = Path(path)

    def path(self) -> Path | None:
        return self._path

    def is_dirty(self) -> bool:
        return self._dirty

    def mark_saved(self) -> None:
        self._dirty = False

    def _mark_dirty(self) -> None:
        self._dirty = True

    def set_lexer_for_path(self, path: Path) -> None:
        """Selecciona lexer por extensión; desactiva resaltado si el archivo es grande."""
        try:
            size = Path(path).stat().st_size
        except OSError:
            logger.debug("no se pudo stat() para límite de resaltado: %s", path)
            size = 0
        if size > HIGHLIGHT_LIMIT:
            self.highlighter.setDocument(None)
            return
        self.highlighter.setDocument(self.document())
        self.highlighter.set_lexer_name(_EXT_LEXERS.get(Path(path).suffix.lower()))

    # ── Gutter ─────────────────────────────────────────────────────

    def lineNumberAreaWidth(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        char_w = max(1, self.fontMetrics().horizontalAdvance("9"))
        return 10 + digits * char_w + 6

    def lineNumberAreaPaintEvent(self, event: Any) -> None:
        c = ThemeManager.current().colors
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor(c.bg_code))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        current = self.textCursor().blockNumber()
        offset = self.contentOffset()
        top = round(self.blockBoundingGeometry(block).translated(offset).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                pen_color = c.text_primary if block_number == current else c.text_dim
                painter.setPen(QColor(pen_color))
                painter.setFont(self.font())
                painter.drawText(
                    0,
                    top,
                    self.line_number_area.width() - 4,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number,
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def _update_line_number_area_width(self, _new_block_count: int) -> None:
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height())
        )

    # ── Línea actual ───────────────────────────────────────────────

    def _highlight_current_line(self) -> None:
        c = ThemeManager.current().colors
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(_qcolor(c.bg_raised))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
        self.line_number_area.update()
