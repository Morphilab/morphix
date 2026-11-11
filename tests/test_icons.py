"""Tests de desktop/icons.py — QIcon desde SVG (offscreen)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from typing import cast  # noqa: E402

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.icons import AVAILABLE_ICONS, get_icon  # noqa: E402


def _qapp():
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_all_sidebar_icons_available():
    assert {
        "dashboard",
        "maestro",
        "historial",
        "editor",
        "config",
        "analytics",
    } <= set(AVAILABLE_ICONS)


def test_get_icon_returns_non_empty():
    _qapp()
    icon = get_icon("maestro")
    assert isinstance(icon, QIcon)
    assert not icon.isNull()
    assert icon.availableSizes()


def test_get_icon_is_cached():
    _qapp()
    assert get_icon("dashboard") is get_icon("dashboard")


def test_unknown_icon_falls_back():
    _qapp()
    assert not get_icon("no-existe").isNull()


@pytest.mark.parametrize("name", sorted(AVAILABLE_ICONS))
def test_every_icon_renders_ink(name):
    _qapp()
    img = get_icon(name).pixmap(16, 16).toImage()
    assert any(img.pixelColor(x, y).alpha() > 0 for x in range(16) for y in range(16)), name
