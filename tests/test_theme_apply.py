"""Tests para ThemeManager.apply_to_app (necesita QApplication offscreen)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from typing import cast  # noqa: E402

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.theme import PAPEL, Theme, ThemeManager  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_current_returns_theme():
    assert isinstance(ThemeManager.current(), Theme)


def test_apply_to_app_sets_palette_and_stylesheet():
    app = _qapp()
    ThemeManager.apply_to_app(app)
    palette = app.palette()
    assert (
        palette.window().color().name() == QColor(ThemeManager.current().colors.bg_deepest).name()
    )
    assert ThemeManager.current().colors.accent_primary in app.styleSheet()
    assert "QToolTip" in app.styleSheet()


def test_apply_with_custom_theme(monkeypatch):
    monkeypatch.setattr(ThemeManager, "_current", PAPEL)
    app = _qapp()
    custom = Theme(name="custom")
    ThemeManager.apply_to_app(app, theme=custom)
    assert ThemeManager.current() is custom
