"""Viewer standalone de archivos — md/pdf/html (read-only estricto).

Ejecución sin Morphix: `python viewer.py archivo.md [otro.pdf ...]`
Cero imports de Morphix, cero red. Morphix lo invoca como proceso externo.

Modos (pick_mode, función pura):
- .md   → QTextBrowser.setMarkdown (mismo motor que el chat)
- .html → QTextBrowser.setHtml + sanitize_html
- .pdf  → QPdfView (degrada a aviso si QtPdf no está en la rueda)
- resto → texto plano read-only (guard 1MB + heurística binaria NUL)

Seguridad por construcción: QTextBrowser nunca ejecuta JS; loadResource
bloquea recursos remotos; setOpenExternalLinks(False); solo archivos
locales pasados por argv.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MAX_TEXT_BYTES = 1_000_000  # guard 1MB para modos texto (patrón editor_tab)

# ── Funciones puras (testables sin Qt) ────────────────────────────────────

_SCRIPT_BLOCK_RE = re.compile(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)
_JS_SCHEME_RE = re.compile(r"javascript\s*:", re.IGNORECASE)
_ON_HANDLER_RE = re.compile(r'\son[a-z]+\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', re.IGNORECASE)
_FRAME_ESCAPE_RE = re.compile(r"⟪|⟫")

_PDF_EXTS = {".pdf"}
_MD_EXTS = {".md", ".markdown"}
_HTML_EXTS = {".html", ".htm"}


def pick_mode(path: Path) -> str:
    """Dispatch por extensión → 'markdown' | 'html' | 'pdf' | 'text' | 'binary'."""
    ext = path.suffix.lower()
    if ext in _PDF_EXTS:
        return "pdf"
    if ext in _MD_EXTS:
        return "markdown"
    if ext in _HTML_EXTS:
        return "html"
    return "text"


def looks_binary(data: bytes) -> bool:
    """Heurística barata: NUL en los primeros 8KB → binario (patrón editor_tab)."""
    return b"\x00" in data[:8192]


def sanitize_html(html: str) -> str:
    """Strip <script>, esquemas javascript: y handlers on*= (sin Qt).

    Neutraliza además delimitadores ⟪⟫ (anti-frame-escape, patrón
    desktop/sanitize.py ampliado). El contenido legítimo queda intacto.
    """
    html = _SCRIPT_BLOCK_RE.sub("[contenido bloqueado]", html)
    html = _JS_SCHEME_RE.sub("", html)
    html = _ON_HANDLER_RE.sub("", html)
    html = _FRAME_ESCAPE_RE.sub("", html)
    return html


def load_text_content(path: Path) -> tuple[str, str]:
    """Lee un archivo de texto → (contenido, modo_efectivo).

    Devuelve modo 'binary' si la heurística NUL dispara, y trunca a
    MAX_TEXT_BYTES con aviso si excede el guard.
    """
    data = path.read_bytes()
    if looks_binary(data):
        return "", "binary"
    if len(data) > MAX_TEXT_BYTES:
        return (
            data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace") + "\n\n[… truncado a 1MB …]"
        ), "text"
    return data.decode("utf-8", errors="replace"), "text"


# ── Qt (imports diferidos: las funciones puras no requieren display) ─────


def _make_text_browser(content: str, mode: str):
    """QTextBrowser read-only para md/html; nunca ejecuta JS ni carga remoto."""
    from PySide6.QtWidgets import QTextBrowser

    class _SafeTextBrowser(QTextBrowser):
        def loadResource(self, rtype, url):  # noqa: N802 — API Qt
            # Bloquea imágenes/CSS remotos: solo esquemas locales vacíos.
            if url.scheme() in ("http", "https", "ftp"):
                return ""
            return super().loadResource(rtype, url)

    browser = _SafeTextBrowser()
    browser.setOpenExternalLinks(False)
    browser.setOpenLinks(False)
    if mode == "markdown":
        browser.setMarkdown(content)
    else:
        browser.setHtml(sanitize_html(content))
    return browser


def _make_pdf_widget(path: Path):
    """QPdfView con layout real; degrada a aviso si QtPdf no está disponible."""
    try:
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPdfWidgets import QPdfView
    except ImportError:
        from PySide6.QtWidgets import QLabel

        return QLabel(
            "QtPdf no está disponible en esta instalación.\n"
            "El PDF no puede renderizarse; usa pdf_read para extraer texto."
        )
    doc = QPdfDocument()
    doc.load(str(path))
    view = QPdfView()
    view.setDocument(doc)
    view.setPageMode(QPdfView.PageMode.MultiPage)
    return view


def _make_plain_text(path: Path):
    from PySide6.QtWidgets import QPlainTextEdit

    content, mode = load_text_content(path)
    if mode == "binary":
        box = QPlainTextEdit()
        box.setPlainText(
            f"[Archivo binario — no visualizable como texto]\n{path.name}\n"
            f"{path.stat().st_size:,} bytes"
        )
        box.setReadOnly(True)
        return box
    box = QPlainTextEdit()
    box.setPlainText(content)
    box.setReadOnly(True)
    return box


def make_viewer_widget(path: Path):
    """Construye el widget correcto para `path` (Qt diferido)."""
    mode = pick_mode(path)
    if mode == "pdf":
        return _make_pdf_widget(path), mode
    if mode in ("markdown", "html"):
        try:
            content, eff = load_text_content(path)
        except OSError as e:
            return _make_plain_text(path), "text"
        if eff == "binary":
            return _make_plain_text(path), "text"
        return _make_text_browser(content, mode), mode
    return _make_plain_text(path), "text"


def run(paths_arg: list[str]) -> int:
    """Entry point Qt: valida archivos, abre ventana con una pestaña por archivo."""
    from PySide6.QtWidgets import QApplication, QTabWidget, QVBoxLayout, QWidget

    valid: list[Path] = []
    for raw in paths_arg:
        p = Path(raw).expanduser().resolve()
        if p.is_file():
            valid.append(p)
        else:
            print(f"viewer: archivo no encontrado: {raw}", file=sys.stderr)
    if not valid:
        print("uso: python viewer.py <archivo> [archivo2 ...]", file=sys.stderr)
        return 2

    app = QApplication(sys.argv)
    window = QWidget()
    window.setWindowTitle(f"Visor — {valid[0].name}" + ("…" if len(valid) > 1 else ""))
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)

    copy_targets: list = []
    if len(valid) == 1:
        widget, mode = make_viewer_widget(valid[0])
        layout.addWidget(widget)
        copy_targets.append((widget, mode))
    else:
        tabs = QTabWidget()
        for p in valid:
            widget, mode = make_viewer_widget(p)
            tabs.addTab(widget, p.name)
            copy_targets.append((widget, mode))
        layout.addWidget(tabs)

    # Toolbar mínima: Copiar todo (solo modos texto; en PDF la copia es por
    # selección nativa de QPdfView → botón deshabilitado).
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QHBoxLayout, QPushButton

    bar = QWidget()
    bar_layout = QHBoxLayout(bar)
    bar_layout.setContentsMargins(8, 4, 8, 4)
    copy_btn = QPushButton("Copiar todo")

    def _copy_all():
        texts = []
        for widget, mode in copy_targets:
            if mode == "pdf":
                continue
            plain = getattr(widget, "toPlainText", None)
            if callable(plain):
                texts.append(plain())
        QGuiApplication.clipboard().setText("\n\n".join(texts))

    copy_btn.clicked.connect(_copy_all)
    if all(mode == "pdf" for _w, mode in copy_targets):
        copy_btn.setEnabled(False)
        copy_btn.setToolTip("En PDF la copia es por selección (selecciona y Ctrl+C)")
    bar_layout.addWidget(copy_btn)
    bar_layout.addStretch()
    layout.addWidget(bar)

    window.resize(900, 700)
    window.show()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
