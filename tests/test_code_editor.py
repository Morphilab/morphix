# tests/test_code_editor.py — CodeEditor: gutter + resaltado Pygments (editor fiable)
"""Contratos del componente de edición:

- CodeEditor añade gutter de números de línea y fuente mono del theme.
- PygmentsHighlighter colorea tokens (keyword/string) y degrada a no-op
  si Pygments no está disponible.
- Archivos > HIGHLIGHT_LIMIT desactivan el resaltado (rendimiento).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def _qapp():
    return QApplication.instance() or QApplication([])


def test_editor_has_line_number_area():
    _qapp()
    from desktop.code_editor import CodeEditor

    editor = CodeEditor()
    assert editor.line_number_area is not None
    # el gutter reserva ancho para al menos 2 dígitos
    assert editor.lineNumberAreaWidth() >= 12


def test_mono_font_resolves_theme_family():
    _qapp()
    from desktop.code_editor import mono_font
    from desktop.theme import ThemeManager

    f = mono_font()
    fam = f.families()
    assert fam, "mono_font debe resolver una familia"
    first = ThemeManager.current().typography.family_mono.split(",")[0].strip().strip('"')
    assert fam[0] == first or first in fam


def test_editor_uses_theme_text_editor_style():
    _qapp()
    from desktop.code_editor import CodeEditor
    from desktop.theme import StyleFactory

    editor = CodeEditor()
    assert editor.styleSheet() == StyleFactory.text_editor()


def test_highlighter_colors_keywords_and_strings(tmp_path):
    _qapp()
    from desktop.code_editor import CodeEditor
    from desktop.theme import ThemeManager

    c = ThemeManager.current().colors
    editor = CodeEditor()
    p = tmp_path / "x.py"
    p.write_text("def foo():\n    return 1\n", encoding="utf-8")
    editor.set_lexer_for_path(p)
    editor.setPlainText('def foo():\n    s = "hola"\n    return 1\n')
    editor.highlighter.rehighlight()
    _qapp().processEvents()

    # bloque 0: 'def foo():' → 'def' coloreado como keyword (accent_primary)
    fmts = editor.document().firstBlock().layout().formats()
    assert fmts, "el bloque debe tener rangos de formato"
    first = fmts[0]
    assert (first.start, first.length) == (
        0,
        3,
    ), f"'def' debe cubrir [0,3): {first.start},{first.length}"
    assert (
        first.format.foreground().color().name() == QColor(c.accent_primary).name()
    ), "'def' debe colorearse como keyword"

    # bloque 1: ' s = "hola"' → el string coloreado como success
    block1 = editor.document().firstBlock().next()
    fmts1 = block1.layout().formats()
    assert any(
        f.format.foreground().color().name() == QColor(c.success).name() for f in fmts1
    ), '"hola" debe colorearse como string'


def test_highlighter_degrades_without_pygments(monkeypatch):
    _qapp()
    import desktop.code_editor as ce

    monkeypatch.setattr(ce, "_PYGMENTS_OK", False)
    editor = ce.CodeEditor()
    editor.setPlainText("def foo():\n    return 1\n")
    editor.highlighter.rehighlight()
    _qapp().processEvents()

    fmts = editor.document().firstBlock().layout().formats()
    assert not fmts, "sin pygments el resaltado debe ser no-op (sin rangos de formato)"


def test_large_file_disables_highlight(tmp_path, monkeypatch):
    _qapp()
    import desktop.code_editor as ce

    monkeypatch.setattr(ce, "HIGHLIGHT_LIMIT", 16)
    editor = ce.CodeEditor()

    big = tmp_path / "big.py"
    big.write_text("x = 1  # " + "y" * 100, encoding="utf-8")
    editor.set_lexer_for_path(big)
    assert editor.highlighter.document() is None, "archivo grande: resaltado desactivado"

    small = tmp_path / "small.py"
    small.write_text("x = 1\n", encoding="utf-8")
    editor.set_lexer_for_path(small)
    assert (
        editor.highlighter.document() is editor.document()
    ), "archivo pequeño: resaltado activo de nuevo"


def test_path_roundtrip(tmp_path):
    _qapp()
    from desktop.code_editor import CodeEditor

    editor = CodeEditor()
    p = tmp_path / "a.py"
    editor.set_path(p)
    assert editor.path() == p


def test_dirty_tracking():
    _qapp()
    from desktop.code_editor import CodeEditor

    editor = CodeEditor()
    assert not editor.is_dirty()
    editor.setPlainText("hola")
    assert editor.is_dirty()
    editor.mark_saved()
    assert not editor.is_dirty()


def test_theme_editor_tabs_qss_exists():
    _qapp()
    from desktop.theme import StyleFactory

    qss = StyleFactory.editor_tabs()
    assert "QTabWidget::pane" in qss
    assert "QTabBar::tab" in qss
