# tests/test_multisession_events.py
"""Multi-sesión: build_workflow_events enruta a señales por-pane.

Cada SessionPane pasa su propio objeto de señales; los eventos de sesión
(stream/system/stats/agent) deben llegar SOLO a ese pane, no al bus global
que mezclaría los eventos de todas las sesiones. La aprobación sigue en el
bus global (el diálogo es único para toda la app).
"""

import asyncio
import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_build_workflow_events_routes_session_events_to_given_signals():
    """Los eventos de sesión van al pane indicado, no al singleton global."""
    from desktop import events as ev

    _qapp()
    ev._signals = None  # reinicia el singleton para un bus global limpio

    pane = ev.DesktopSignals()
    got_pane: list[str] = []
    got_global: list[str] = []
    pane.stream_chunk.connect(lambda t: got_pane.append(t))
    ev._get_signals().stream_chunk.connect(lambda t: got_global.append(t))

    wf = ev.build_workflow_events(signals=pane)
    asyncio.run(wf.on_stream_chunk("hola-pane"))

    assert got_pane == ["hola-pane"]
    assert got_global == []


def test_build_workflow_events_defaults_to_global_signals():
    """Sin argumento (backward-compat), los eventos van al bus global."""
    from desktop import events as ev

    _qapp()
    ev._signals = None

    got: list[str] = []
    ev._get_signals().stream_chunk.connect(lambda t: got.append(t))

    wf = ev.build_workflow_events()
    asyncio.run(wf.on_stream_chunk("hola-global"))

    assert got == ["hola-global"]


def test_stats_event_routes_to_given_signals():
    """stats_update también se enruta por-pane."""
    from desktop import events as ev

    _qapp()
    ev._signals = None

    pane = ev.DesktopSignals()
    got_pane: list[dict] = []
    pane.stats_update.connect(lambda d: got_pane.append(d))

    wf = ev.build_workflow_events(signals=pane)
    asyncio.run(wf.on_stats_update({"status": "running"}))

    assert got_pane == [{"status": "running"}]
