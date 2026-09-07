# Los chats canónicos de bot van
# por bots_runner (punto ÚNICO de turnos de bot); el guard de
# _dispatch_route los despacha ANTES de cualquier otra ruta.

"""La rama LLM directa (agentes sin tools) no aplica identidad del bot ni
inyecta send_to_bot — con `tools: []` en conversacional.yaml, todo bot
recibiría identidad genérica Morphix (eco "Soy Morphix, tu asistente
experto en razonamiento…") e intercom imposible (pending_turns=0 con el
modelo respondiendo el saludo como texto).

El guard de _dispatch_route despacha TODO chat canónico por bots_runner
antes de las demás rutas. El invariante "bot → loop con identidad" vive
en test_bots_runner (e2e) y en
test_workflow_activo_no_secuestra_el_chat_del_bot."""

import threading
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_send_message_routes_bot_canonical_chat_through_workflow():
    """En modo 'chat', un chat canónico de bot NO debe
    usar la ruta de chat directo (_run_direct_agent no aplica bot_context ni
    ofrece send_to_bot → el DM entre bots muere en silencio). _uses_direct_agent_route
    debe devolver False para que send_message caiga por la ruta de workflow
    (donde se inyecta identidad + send_to_bot)."""
    import pytest

    pytest.importorskip("PySide6.QtWidgets")
    from desktop.maestro_tab import SessionPane

    # Sin __init__: basta con los dos atributos que lee el método.
    tab = SessionPane.__new__(SessionPane)

    tab._mode = "chat"
    tab._in_bot_canonical = False
    assert tab._uses_direct_agent_route() is True, "chat humano → ruta directa"

    tab._in_bot_canonical = True
    assert (
        tab._uses_direct_agent_route() is False
    ), "chat canónico de bot → NO ruta directa (debe ir por workflow con identidad)"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_bot_canonical_cleared_on_mode_switch_and_agent_select():
    """Tras abrir el chat de un bot, el usuario debe poder
    volver a usar un agente directo o un workflow. Cambiar de modo o elegir un
    agente explícito deben SALIR del modo bot (limpiar _in_bot_canonical); si no,
    el flag queda pegado y todo se enruta como modo bot."""
    import pytest

    pytest.importorskip("PySide6.QtWidgets")

    from desktop.maestro_tab import SessionPane

    class _StubMaestro(SessionPane):
        def __init__(self):
            # Sin super().__init__: evita UI/BD pesados; stub de widgets.
            self._in_bot_canonical = False
            self._mode = "chat"
            self._force_agent = None
            self._selected_agent = None
            self._paused_session = None  # estado de pausa de SessionPane
            self._workflow_running = False  # guard de _set_mode
            self._workflow_running_lock = threading.Lock()
            self._chat_toggle = MagicMock()
            self._orchestrate_toggle = MagicMock()
            self._workflow_combo = MagicMock()
            self._agent_combo = MagicMock()
            self._toggle_style_active = ""
            self._toggle_style_inactive = ""
            self._agent_label = MagicMock()  # _set_mode lo usa
            self._active_workflow = None

        def _update_agent_detail(self):
            pass

        def _update_info_button(self):
            pass

        def _abandon_pause(self):  # launch_agent lo invoca
            pass

        def _on_system(self, *a, **k):
            pass

        def _populate_agents(self, *a, **k):
            pass

        def _populate_workflow_combo(self, *a, **k):
            pass

        def _refresh_detail_tabs_for_workflow(self, *a, **k):
            pass

    m = _StubMaestro()

    # Está dentro del chat canónico de un bot (modo chat, Auto).
    m._in_bot_canonical = True
    m._mode = "chat"
    m._force_agent = None
    assert m._uses_direct_agent_route() is False, "chat del bot → modo bot"

    # Elige un agente directo → sale del modo bot.
    m._select_agent("developer")
    assert m._in_bot_canonical is False, "elegir agente explícito sale del modo bot"
    assert m._uses_direct_agent_route() is True, "ahora ruta de agente directo"

    # Vuelve al chat del bot y cambia de modo → sale del modo bot.
    m._in_bot_canonical = True
    m._set_mode("orchestrate")
    assert m._in_bot_canonical is False, "cambiar de modo sale del modo bot"


@pytest.mark.asyncio
async def test_launch_agent_from_dashboard_exits_bot_mode():
    """Tras abrir el chat de un bot, el usuario abre un
    agente desde el Dashboard. launch_agent debe salir del modo bot (limpiar
    _in_bot_canonical y la conversación) AUNQUE no haya cambio de modo, y
    empezar una conversación nueva del agente."""
    import pytest

    pytest.importorskip("PySide6.QtWidgets")

    from desktop.maestro_tab import SessionPane

    class _StubMaestro2(SessionPane):
        def __init__(self):
            self._in_bot_canonical = False
            self._mode = "chat"
            self._force_agent = None
            self._selected_agent = None
            self._conversation_id = None
            self._paused_session = None  # estado de pausa de SessionPane
            self._workflow_running = False  # guard de _set_mode
            self._workflow_running_lock = threading.Lock()
            self._chat_toggle = MagicMock()
            self._orchestrate_toggle = MagicMock()
            self._workflow_combo = MagicMock()
            self._agent_combo = MagicMock()
            self._toggle_style_active = ""
            self._toggle_style_inactive = ""
            self._agent_label = MagicMock()  # _set_mode lo usa
            self._active_workflow = None

        def _update_agent_detail(self):
            pass

        def _update_info_button(self):
            pass

        def _abandon_pause(self):  # launch_agent lo invoca
            pass

        def _on_system(self, *a, **k):
            pass

        def _populate_agents(self, *a, **k):
            pass

        def _populate_workflow_combo(self, *a, **k):
            pass

        def _refresh_detail_tabs_for_workflow(self, *a, **k):
            pass

    m = _StubMaestro2()
    # Está dentro del chat canónico del bot alfa.
    m._in_bot_canonical = True
    m._mode = "chat"
    m._conversation_id = 1  # conv del bot

    m.launch_agent("Developer")

    assert m._in_bot_canonical is False, "launch_agent debe salir del modo bot"
    assert m._conversation_id is None, "launch_agent inicia conversación nueva"
    assert m._force_agent == "developer"
    assert m._uses_direct_agent_route() is True, "ahora usa ruta de agente directo"
