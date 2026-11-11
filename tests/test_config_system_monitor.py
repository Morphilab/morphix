# tests/test_config_system_monitor.py
"""Backlog 'polling eterno de config_tab' → patrón a-consumo (analytics).

El monitor de Sistema nace DETENIDO; solo corre por decisión del usuario
(▶/⏹), con primera lectura inmediata, hideEvent auto-stop y showEvent no-op.
"""

from typing import cast

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.config_tab import ConfigTab  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


@pytest.fixture()
def tab():
    _qapp()
    t = ConfigTab()
    # ir a la sub-pestaña Sistema para construirla
    yield t
    t._monitor_timer.stop()


def test_monitor_nace_detenido(tab):
    assert not tab._monitor_timer.isActive()
    assert tab.toggle_btn.text() == "▶ Actualizar"
    assert "detenido" in tab.status_label.text()


def test_start_arranca_con_lectura_inmediata(tab, monkeypatch):
    import datetime as dt

    reads = []

    def fake_read():
        reads.append(1)
        tab._last_monitor = dt.datetime.now()

    monkeypatch.setattr(tab, "_read_system_stats", fake_read)
    tab._start_monitor()
    assert tab._monitor_timer.isActive()
    assert tab.toggle_btn.text() == "⏹ Detener"
    assert "en vivo" in tab.status_label.text()
    assert reads == [1]  # primera lectura inmediata
    tab._stop_monitor()


def test_stop_y_guard_doble_click(tab, monkeypatch):
    import datetime as dt

    reads = []

    def fake_read():
        reads.append(1)
        tab._last_monitor = dt.datetime.now()

    monkeypatch.setattr(tab, "_read_system_stats", fake_read)
    tab._start_monitor()
    tab._start_monitor()  # guard: no re-programa ni duplica lectura
    assert reads == [1]
    tab._stop_monitor()
    assert not tab._monitor_timer.isActive()
    assert tab.toggle_btn.text() == "▶ Actualizar"
    assert "detenido" in tab.status_label.text()
    assert ":" in tab.status_label.text()  # conserva hora de última lectura


def test_hideevent_detiene_y_show_no_rearranca(tab, monkeypatch):
    from PySide6.QtGui import QHideEvent, QShowEvent

    monkeypatch.setattr(tab, "_read_system_stats", lambda: None)
    tab._start_monitor()
    tab.hideEvent(QHideEvent())
    assert not tab._monitor_timer.isActive()
    tab.showEvent(QShowEvent())
    assert not tab._monitor_timer.isActive()  # no-op deliberado
