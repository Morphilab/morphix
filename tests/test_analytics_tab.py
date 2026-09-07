"""Tests para el ciclo de vida de refresco de AnalyticsTab (a consumo).

Convención: QApplication offscreen compartida (patrón test_gui_routing_hardening).
El timer nace DETENIDO: nada corre hasta que el usuario pulsa ▶.
"""

from typing import cast
from unittest.mock import patch

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QHideEvent, QShowEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.analytics_tab import AnalyticsTab  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


@pytest.fixture()
def tab():
    """AnalyticsTab con run_async parcheado (no hay loop asyncio en tests)."""
    _qapp()
    with patch("desktop.analytics_tab.run_async") as ra:
        t = AnalyticsTab()
        yield t, ra
        t._timer.stop()


def test_initial_state_stopped(tab):
    t, _ = tab
    assert not t._timer.isActive()
    assert t.toggle_btn.text() == "▶ Actualizar"
    assert "detenido" in t.status_label.text()
    # Sin datos al construir: labels en "—"
    assert t.metric_labels["uptime"].text() == "—"


def test_toggle_starts_refresh(tab):
    t, ra = tab
    t._toggle_refresh()
    assert t._timer.isActive()
    assert t.toggle_btn.text() == "⏹ Detener"
    assert "en vivo" in t.status_label.text()
    # Primera lectura inmediata (no espera el primer tick de 5s)
    assert ra.call_count == 1


def test_toggle_stops_refresh(tab):
    t, _ = tab
    t._toggle_refresh()  # arranca
    t._toggle_refresh()  # detiene
    assert not t._timer.isActive()
    assert t.toggle_btn.text() == "▶ Actualizar"
    assert "detenido" in t.status_label.text()


def test_double_start_is_idempotent(tab):
    t, ra = tab
    t._start_refresh()
    t._start_refresh()  # guard isActive: no segunda corrutina ni reinicio
    assert ra.call_count == 1
    assert t._timer.isActive()


def test_hide_event_stops_timer(tab):
    t, _ = tab
    t._start_refresh()
    assert t._timer.isActive()
    t.hideEvent(QHideEvent())
    assert not t._timer.isActive()
    assert "detenido" in t.status_label.text()
    assert t.toggle_btn.text() == "▶ Actualizar"


def test_hide_event_when_stopped_is_safe(tab):
    t, _ = tab
    t.hideEvent(QHideEvent())  # sin run activo: no rompe
    assert not t._timer.isActive()


def test_show_event_does_not_restart(tab):
    """Decisión de diseño: re-entrar al tab NO re-arranca el refresco."""
    t, _ = tab
    t._start_refresh()
    t.hideEvent(QHideEvent())
    t.showEvent(QShowEvent())
    assert not t._timer.isActive()
    assert "detenido" in t.status_label.text()
