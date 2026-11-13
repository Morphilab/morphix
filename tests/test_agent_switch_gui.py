# tests/test_agent_switch_gui.py — cambio de agente y modo sin atajos (offscreen)
"""El selector de agentes lista TODOS los registrados (el activo queda
seleccionado) y el toggle Orquestar nunca queda gris tras usar una card del
Dashboard: forzar un agente no debe filtrar el combo a uno solo, elegir
"Auto" debe restaurar la lista y launch_agent no debe deshabilitar el único
camino de vuelta a Orquestar (el combo de workflows está oculto en modo chat)."""

import os
from types import SimpleNamespace
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


_ROSTER = {"analista": {}, "developer": {}, "moderador": {}, "conversacional": {}}


@pytest.fixture
def pane(monkeypatch):
    _qapp()
    import desktop.maestro_tab as mt
    from desktop.maestro_tab import MaestroTab

    # el registry de agentes no se carga en pytest (solo al arranque de la
    # app): fake con el roster de 4
    monkeypatch.setattr(
        mt,
        "agents_registry",
        SimpleNamespace(
            list_agents=lambda: dict(_ROSTER),
            get_profile=lambda name: {"system_prompt": "p", "tools": []},
        ),
    )
    m = MaestroTab()
    p = m._panes[-1]
    yield p
    m.deleteLater()


def _items(pane):
    combo = pane._agent_combo
    return [combo.itemData(i) for i in range(combo.count())]


def test_combo_lista_todo_el_roster_con_agente_forzado(pane):
    """Card del Dashboard (launch_agent) con analista: el combo SIGUE
    mostrando los 4 agentes — antes quedaba en [Auto, analista]."""
    pane.launch_agent("analista")
    assert pane._force_agent == "analista"
    items = _items(pane)
    assert set(_ROSTER) <= {i for i in items if i}
    assert pane._agent_combo.currentData() == "analista"  # el activo seleccionado


def test_set_mode_chat_con_forzado_no_filtra_el_combo(pane):
    """⟳ y re-entrar a chat con agente forzado: la lista completa sigue
    (el filtro era el laberinto del cambio de agente)."""
    pane._set_mode("chat", silent=True)
    pane._select_agent("developer")
    assert pane._force_agent == "developer"

    pane._set_mode("chat", silent=True)  # lo que hace ⟳
    items = _items(pane)
    assert set(_ROSTER) <= {i for i in items if i}
    assert pane._agent_combo.currentData() == "developer"


def test_auto_restaura_la_seleccion_libre(pane):
    """Elegir 'Auto' deja la pane sin agente forzado y la lista sigue
    completa — cambiar de agente es un paso, no una secuencia."""
    pane._set_mode("chat", silent=True)
    combo = pane._agent_combo
    combo.setCurrentIndex(combo.findData("moderador"))  # camino real: la señal dispara
    assert pane._force_agent == "moderador"

    auto_idx = next(i for i in range(combo.count()) if combo.itemData(i) is None)
    combo.setCurrentIndex(auto_idx)

    assert pane._force_agent is None
    assert pane._selected_agent is None
    assert set(_ROSTER) <= {i for i in _items(pane) if i}


def test_toggle_orquestar_sigue_habilitado_tras_launch_agent(pane):
    """El toggle de modo nunca queda gris: launch_workflow era el ÚNICO que
    lo rehabilitaba y el combo de workflows está oculto en modo chat."""
    pane.launch_agent("analista")
    assert pane._orchestrate_toggle.isEnabled() is True
    # y el switch de modo funciona desde la pane
    pane._set_mode("orchestrate", silent=True)
    assert pane._mode == "orchestrate"
