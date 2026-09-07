# tests/test_multisession_container.py
"""Multi-sesión: contenedor Maestro con sesiones a demanda (smoke, offscreen).

Verifica el ciclo de vida de las sub-pestañas: 1 sesión inicial con barra
oculta, añadir sesiones con `+`/launch, tope de sesiones con reutilización de
pane idle, y enrutamiento de entradas (workflow/agente/proyecto/lanzador).

Regla de enrutamiento: "nueva primero, nunca pisar una ocupada" —
Bots/Historial abren pane NUEVA cuando la conversación no está abierta, el
proyecto absorbe solo en una pane fresca, y al tope con todas ocupadas no se
pisa nada.
"""

import asyncio
import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.config import settings  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


@pytest.fixture(autouse=True)
def _skip_project_gate(monkeypatch):
    """El diálogo de proyecto previo al lanzamiento jamás abre en tests:
    los presets de producto exigen proyecto y el QDialog.exec() colgaría
    offscreen. Los tests del gate lo parchean explícitamente."""
    from desktop.maestro_tab import SessionPane

    monkeypatch.setattr(
        SessionPane, "_ask_project_for_workflow", lambda self, name: "proj_test", raising=False
    )
    yield


@pytest.fixture(autouse=True)
def _restore_max_sessions(monkeypatch):
    monkeypatch.setattr(settings, "maestro_max_sessions", 4)
    yield


async def _fake_load(self, conv_id):
    """Sustituto de SessionPane.load_conversation sin tocar BD (routing only)."""
    self._conversation_id = conv_id


def _set_running(pane, running: bool) -> None:
    with pane._workflow_running_lock:
        pane._workflow_running = running


def test_container_starts_with_one_session_and_hidden_tab_bar():
    from desktop.maestro_tab import MaestroTab

    _qapp()
    m = MaestroTab()
    assert m.session_count == 1
    assert m._tabs.tabBar().isHidden() is True


def test_launch_workflow_adds_session_and_shows_tab_bar():
    from desktop.maestro_tab import MaestroTab

    _qapp()
    m = MaestroTab()
    m.launch_workflow("development")
    assert m.session_count == 2
    assert m._tabs.tabBar().isHidden() is False


def test_launch_agent_adds_session():
    from desktop.maestro_tab import MaestroTab

    _qapp()
    m = MaestroTab()
    m.launch_agent("developer")
    assert m.session_count == 2


def test_max_sessions_reuses_idle_pane(monkeypatch):
    from desktop.maestro_tab import MaestroTab

    _qapp()
    monkeypatch.setattr(settings, "maestro_max_sessions", 2)
    m = MaestroTab()
    m.launch_workflow("development")  # 2ª sesión
    assert m.session_count == 2
    m.launch_agent("developer")  # al tope → reutiliza pane idle
    assert m.session_count == 2


def test_switch_project_applies_to_active_pane():
    from desktop.maestro_tab import MaestroTab

    _qapp()
    m = MaestroTab()
    m.switch_project("miapp")
    assert m._current_project_root == "code_projects/miapp"


def test_active_project_root_follows_active_pane():
    from desktop.maestro_tab import MaestroTab

    _qapp()
    m = MaestroTab()
    m.launch_workflow("development")
    pane = m._tabs.currentWidget()
    pane._current_project_root = "code_projects/otro"
    assert m._current_project_root == "code_projects/otro"


# ── Fix de enrutamiento: nueva primero, nunca pisar ocupadas ──


def test_load_conversation_opens_new_pane_when_not_open(monkeypatch):
    """Bots/Historial: conversación no abierta → pane NUEVA (no pisa la idle)."""
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    monkeypatch.setattr(SessionPane, "load_conversation", _fake_load)
    m = MaestroTab()
    m._panes[0]._conversation_id = 1  # Sesión 1 con SU conversación
    asyncio.run(m.load_conversation(2))

    assert m.session_count == 2
    assert m._panes[0]._conversation_id == 1  # intacta
    assert m._panes[1]._conversation_id == 2


def test_load_conversation_focuses_pane_already_open(monkeypatch):
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    monkeypatch.setattr(SessionPane, "load_conversation", _fake_load)
    m = MaestroTab()
    p1 = m._panes[0]
    p1._conversation_id = 7
    m.add_session()
    asyncio.run(m.load_conversation(7))

    assert m.session_count == 2
    assert m._tabs.currentWidget() is p1
    assert m._panes[1]._conversation_id is None


def test_load_conversation_at_max_reuses_idle_with_notice(monkeypatch):
    """Al tope: reutiliza la primera idle CON aviso en ella (regla acordada)."""
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    monkeypatch.setattr(SessionPane, "load_conversation", _fake_load)
    monkeypatch.setattr(settings, "maestro_max_sessions", 2)
    m = MaestroTab()
    p1 = m._panes[0]
    p1._conversation_id = 1
    p2 = m.add_session()
    p2._conversation_id = 2

    msgs: list[str] = []
    monkeypatch.setattr(p1, "_on_system", lambda msg: msgs.append(msg))
    asyncio.run(m.load_conversation(3))

    assert m.session_count == 2
    assert m._tabs.currentWidget() is p1
    assert p1._conversation_id == 3
    assert any("⚠️" in msg for msg in msgs)


def test_load_conversation_all_busy_does_not_overwrite(monkeypatch):
    """Tope + todas ocupadas: no se pisa NINGUNA sesión (solo aviso)."""
    from desktop.maestro_tab import MaestroTab, SessionPane

    _qapp()
    monkeypatch.setattr(SessionPane, "load_conversation", _fake_load)
    monkeypatch.setattr(settings, "maestro_max_sessions", 2)
    m = MaestroTab()
    p1 = m._panes[0]
    p1._conversation_id = 1
    p2 = m.add_session()
    p2._conversation_id = 2
    _set_running(p1, True)
    _set_running(p2, True)

    msgs: list[str] = []
    monkeypatch.setattr(p2, "_on_system", lambda msg: msgs.append(msg))
    asyncio.run(m.load_conversation(3))

    assert m.session_count == 2
    assert p1._conversation_id == 1
    assert p2._conversation_id == 2
    assert any("⚠️" in msg for msg in msgs)


def test_switch_project_second_click_opens_new_pane():
    """Proyecto sobre pane con contenido → pane NUEVA (mismo proyecto,
    otro workflow es posible abriendo otra sesión)."""
    from desktop.maestro_tab import MaestroTab

    _qapp()
    m = MaestroTab()
    m.switch_project("alpha")  # pane fresca absorbe
    m.switch_project("beta")  # activa ya tiene proyecto → pane nueva

    assert m.session_count == 2
    assert m._panes[0]._current_project_root == "code_projects/alpha"
    assert m._panes[1]._current_project_root == "code_projects/beta"


def test_switch_project_at_max_reuses_idle(monkeypatch):
    from desktop.maestro_tab import MaestroTab

    _qapp()
    monkeypatch.setattr(settings, "maestro_max_sessions", 2)
    m = MaestroTab()
    m.switch_project("alpha")
    m.switch_project("beta")
    m.switch_project("gamma")  # tope → primera idle reutilizada

    assert m.session_count == 2
    assert m._panes[0]._current_project_root == "code_projects/gamma"
    assert m._panes[1]._current_project_root == "code_projects/beta"


def test_switch_project_all_busy_no_change(monkeypatch):
    from desktop.maestro_tab import MaestroTab

    _qapp()
    monkeypatch.setattr(settings, "maestro_max_sessions", 2)
    m = MaestroTab()
    m.switch_project("alpha")
    m.switch_project("beta")
    for pane in m._panes:
        _set_running(pane, True)

    msgs: list[str] = []
    monkeypatch.setattr(m._panes[1], "_on_system", lambda msg: msgs.append(msg))
    m.switch_project("gamma")

    assert m.session_count == 2
    assert m._panes[0]._current_project_root == "code_projects/alpha"
    assert m._panes[1]._current_project_root == "code_projects/beta"
    assert any("⚠️" in msg for msg in msgs)


def test_launch_workflow_all_busy_does_not_touch_busy_pane(monkeypatch):
    from desktop.maestro_tab import MaestroTab

    _qapp()
    monkeypatch.setattr(settings, "maestro_max_sessions", 2)
    m = MaestroTab()
    m.switch_project("alpha")
    m.switch_project("beta")
    for pane in m._panes:
        _set_running(pane, True)

    m.launch_workflow("tdd")

    assert m.session_count == 2
    assert all(pane._active_workflow is None for pane in m._panes)


def test_new_or_idle_returns_none_when_all_busy(monkeypatch):
    from desktop.maestro_tab import MaestroTab

    _qapp()
    monkeypatch.setattr(settings, "maestro_max_sessions", 2)
    m = MaestroTab()
    m.add_session()
    for pane in m._panes:
        _set_running(pane, True)

    assert m._new_or_idle() is None
