"""Tests para el fondo orbital estático (necesita QApplication offscreen)."""

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from desktop.theme import PAPEL  # noqa: E402
from desktop.widgets.orbital_background import OrbitalBackground  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def _process_pending_events() -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(0, loop.quit)
    loop.exec()


def test_attach_parents_and_sizes_to_container():
    _qapp()
    container = QWidget()
    container.resize(400, 300)
    bg = OrbitalBackground(theme=PAPEL)
    bg.attach(container)
    assert bg.parent() is container
    assert bg.geometry() == container.rect()


def test_attach_is_transparent_to_mouse():
    _qapp()
    from PySide6.QtCore import Qt

    container = QWidget()
    bg = OrbitalBackground()
    bg.attach(container)
    assert bg.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_paint_does_not_crash():
    _qapp()
    from PySide6.QtGui import QPixmap

    container = QWidget()
    bg = OrbitalBackground()
    bg.attach(container)
    bg.resize(120, 80)
    pixmap = QPixmap(120, 80)
    bg.render(pixmap)  # dispara paintEvent
    assert not pixmap.isNull()
    assert pixmap.toImage().pixelColor(60, 40).alpha() > 0


def test_attach_follows_container_resize():
    _qapp()
    container = QWidget()
    bg = OrbitalBackground()
    bg.attach(container)
    container.show()
    container.resize(500, 400)
    _process_pending_events()
    assert bg.geometry() == container.rect()


def test_attach_twice_is_idempotent():
    _qapp()
    from PySide6.QtCore import QEvent

    container = QWidget()
    container.resize(400, 300)
    bg = OrbitalBackground()
    bg.attach(container)
    bg.attach(container)
    assert bg.parent() is container
    assert bg.geometry() == container.rect()

    calls: list[int] = []
    orig = bg.eventFilter

    def counting(obj, event):
        if event.type() == QEvent.Type.Resize:
            calls.append(1)
        return orig(obj, event)

    bg.eventFilter = counting
    container.show()
    _process_pending_events()
    calls.clear()
    container.resize(500, 400)
    _process_pending_events()
    assert sum(calls) == 1
    assert bg.geometry() == container.rect()

    second = QWidget()
    second.resize(500, 400)
    bg.attach(second)
    assert bg.parent() is second
    assert bg.geometry() == second.rect()

    container.show()
    container.resize(700, 600)
    _process_pending_events()
    assert bg.geometry() == second.rect()


def test_render_frame_uses_bruma_tokens():
    _qapp()
    bg = OrbitalBackground(theme=PAPEL)
    assert bg.theme.colors.bg_to == PAPEL.colors.bg_to
    assert len(bg.theme.colors.glow_spots) == 0
    assert bg._draw_rules is True


def test_paint_rules_transparent_paints_flat_canvas():
    """Tema único papel: rule_color transparente → lienzo plano. El widget
    sigue dibujando los renglones, pero con pluma 100% transparente."""
    _qapp()
    from desktop.theme import PAPEL

    container = QWidget()
    container.resize(200, 200)
    bg = OrbitalBackground(theme=PAPEL)
    bg.attach(container)
    img = bg.grab().toImage()

    spacing = PAPEL.colors.rule_spacing_px
    y_rule = spacing * 3

    def _max_channel_delta(a, b) -> int:
        return max(
            abs(a.red() - b.red()),
            abs(a.green() - b.green()),
            abs(a.blue() - b.blue()),
        )

    xs = list(range(10, 200, 10))
    planos = sum(
        1
        for x in xs
        if _max_channel_delta(img.pixelColor(x, y_rule), img.pixelColor(x, y_rule - 4)) < 2
    )
    assert planos >= len(xs) * 0.9


def test_draw_rules_false_paints_flat_canvas():
    _qapp()
    container = QWidget()
    container.resize(200, 200)
    bg = OrbitalBackground(draw_rules=False)
    bg.attach(container)
    img = bg.grab().toImage()
    base = img.pixelColor(100, 100)
    for y in range(32, 200, 32):
        row = img.pixelColor(100, y)
        assert (
            abs(row.red() - base.red()) < 4
            and abs(row.green() - base.green()) < 4
            and abs(row.blue() - base.blue()) < 4
        ), f"fila {y} tiene regla con draw_rules=False"
