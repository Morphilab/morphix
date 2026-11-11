"""Tests de MainWindow — selector global de workspace en el pie del sidebar.

Verifica el comportamiento de _create_workspace:
- honra el veto del switch guard (no emite workspace_changed si el switch
  retorna False).
- try/finally restaura _ws_new_btn ante retorno False y ante excepción.
- el botón "+" usa icono SVG (texto vacío) en vez de unicode.
"""

import asyncio
import os
import time
from typing import cast
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.main_window import MainWindow  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


class _FakeWorkspaces:
    """Stub de get_global_workspaces(): sin BD, sin switch real."""

    def __init__(self, current: str = "main", schemas: list[str] | None = None):
        self.current = current
        self._schemas = schemas or ["main", "other"]

    async def list_workspaces(self) -> list[str]:
        return list(self._schemas)


async def _drain(predicate, *, timeout: float = 2.0) -> None:
    """Drena las corrutinas encoladas por run_async hasta que se cumpla predicate."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condición no alcanzada (drain timeout)")
        await asyncio.sleep(0)


def test_ws_new_button_uses_svg_icon():
    _qapp()
    mw = MainWindow()
    assert mw._ws_new_btn.text() == ""
    assert not mw._ws_new_btn.icon().isNull()


@pytest.mark.asyncio
async def test_create_workspace_honors_veto_and_restores_button():
    _qapp()
    mw = MainWindow()

    async def _fake_switch(name: str) -> bool:
        return False

    with (
        patch("core.workspaces.get_global_workspaces", return_value=_FakeWorkspaces()),
        patch("core.workspaces.switch_workspace_handler", new=_fake_switch),
        patch("desktop.events.get_signals") as gs,
        patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("main", True)),
        patch("PySide6.QtWidgets.QMessageBox.information") as info_box,
    ):
        sig = MagicMock()
        gs.return_value = sig
        mw._create_workspace()
        # Drena _create + el _refresh_workspaces() del finally
        await _drain(lambda: mw._ws_new_btn.isEnabled() and mw._ws_combo.count() == 2)

    sig.workspace_changed.emit.assert_not_called()
    info_box.assert_called_once()
    assert mw._ws_new_btn.isEnabled()


@pytest.mark.asyncio
async def test_create_workspace_restores_button_on_exception():
    _qapp()
    mw = MainWindow()

    async def _fake_switch(name: str) -> bool:
        raise RuntimeError("boom")

    with (
        patch("core.workspaces.get_global_workspaces", return_value=_FakeWorkspaces()),
        patch("core.workspaces.switch_workspace_handler", new=_fake_switch),
        patch("desktop.events.get_signals") as gs,
        patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("main", True)),
    ):
        sig = MagicMock()
        gs.return_value = sig
        mw._create_workspace()
        await _drain(lambda: mw._ws_new_btn.isEnabled() and mw._ws_combo.count() == 2)

    sig.workspace_changed.emit.assert_not_called()
    assert mw._ws_new_btn.isEnabled()
