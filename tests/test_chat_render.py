# tests/test_chat_render.py — orden y limpieza del panel de chat
"""El chat de un run multi-agente debe leerse como narrativa.

Cubre tres invariantes del render:
- recarga: orden user → actividad (agent/tool) → asistente por turno (la BD
  guarda el final ANTES que los mensajes de agente; el orden cronológico
  mostraba la conclusión antes que su evidencia);
- bloques agent: el header "[Agente - paso]" del contenido persistido se
  promueve a etiqueta de rol, y los bloques de actividad nacen EXPANDIDOS;
- runs multi-agente: el stream plano se suprime (N agentes no interleave su
  texto en una burbuja anónima) y el final agregado SIEMPRE se muestra.
"""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6.QtWidgets")


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ── orden narrativo (pure) ──────────────────────────────────────────


def _msg(role, content):
    return {"role": role, "content": content, "timestamp": None}


def test_orden_narrativo_reordena_final_tras_la_actividad():
    from desktop.maestro_tab import SessionPane

    # orden persistido real: final ANTES que los agentes (mismo milisegundo)
    persisted = [
        _msg("user", "consulta"),
        _msg("assistant", "RESUMEN FINAL"),
        _msg("agent", "[Developer - ejecutar#2/subtarea]\nout2"),
        _msg("agent", "[Developer - ejecutar#1/subtarea]\nout1"),
    ]
    ordered = SessionPane._narrative_order(persisted)
    assert [m["role"] for m in ordered] == ["user", "agent", "agent", "assistant"]
    assert ordered[-1]["content"] == "RESUMEN FINAL"
    # la actividad conserva su orden relativo (estable)
    assert "out2" in ordered[1]["content"] and "out1" in ordered[2]["content"]


def test_orden_narrativo_chats_simples_y_preambulo_intactos():
    from desktop.maestro_tab import SessionPane

    plain = [
        _msg("user", "hola"),
        _msg("assistant", "qué tal"),
        _msg("user", "chau"),
        _msg("assistant", "hasta luego"),
    ]
    assert SessionPane._narrative_order(plain) == plain

    # kickoff assistant-first de los chats canónicos de bots: el preámbulo
    # previo al primer user no se toca
    bot_chat = [
        _msg("assistant", "Soy Base. ¿En qué te ayudo?"),
        _msg("user", "[Rutina] hola"),
        _msg("assistant", "respuesta"),
    ]
    assert SessionPane._narrative_order(bot_chat) == bot_chat


def test_orden_narrativo_multiturno():
    from desktop.maestro_tab import SessionPane

    two_runs = [
        _msg("user", "q1"),
        _msg("assistant", "final1"),
        _msg("agent", "[D - a1]\nx"),
        _msg("user", "q2"),
        _msg("assistant", "final2"),
        _msg("agent", "[D - a2]\ny"),
    ]
    ordered = SessionPane._narrative_order(two_runs)
    assert [m["role"] for m in ordered] == [
        "user",
        "agent",
        "assistant",
        "user",
        "agent",
        "assistant",
    ]
    assert ordered[2]["content"] == "final1"
    assert ordered[5]["content"] == "final2"


# ── render de bloques ───────────────────────────────────────────────


def test_chat_block_promueve_header_de_agente():
    from PySide6.QtWidgets import QLabel

    from desktop.widgets.chat_bubble import ChatBlock

    _qapp()
    block = ChatBlock("[Developer - ejecutar#1/subtarea]\nEsto es el cuerpo", "agent")
    labels = [lbl.text() for lbl in block.findChildren(QLabel)]
    assert any("Developer - ejecutar#1/subtarea" in t for t in labels)
    # el contenido ya no muestra el prefijo crudo entre corchetes
    assert block._browser.toPlainText().strip().startswith("Esto es el cuerpo")

    # un mensaje agent SIN prefijo no explota
    sin_prefijo = ChatBlock("texto plano", "agent")
    assert sin_prefijo._browser.toPlainText().strip() == "texto plano"


def test_bloque_de_actividad_nace_expandido():
    from desktop.widgets.debate_section import DebateSection

    _qapp()
    section = DebateSection()
    section.append_chunk("developer", "primer tramo", "ejecutar#1/subtarea")
    block = section._blocks["developer||ejecutar#1/subtarea"]
    assert block.content.isVisibleTo(section)


# ── supresión del stream plano en runs multi-agente ─────────────────


def test_stream_plano_suprimido_y_final_siempre_visible():
    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    try:
        pane._on_agent_stream("developer", "ejecutar#1/subtarea", "chunk agente")
        pane._on_stream("chunk plano")  # dual: NO debe abrir burbuja plana

        assert pane._suppress_flat_stream is True
        assert pane._streaming_bubble is None
        assert not pane.debate_section.is_empty()

        asyncio.run(pane._runner_on_assistant("FINAL AGREGADO"))
        assert pane._history[-1] == {"role": "assistant", "content": "FINAL AGREGADO"}
        assert pane._streaming_bubble is None
        assert pane._suppress_flat_stream is False
    finally:
        pane.deleteLater()


def test_stream_plano_sigue_vivo_en_chat_directo():
    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    try:
        pane._on_stream("respuesta directa en vivo")
        assert pane._streaming_bubble is not None

        asyncio.run(pane._runner_on_assistant("respuesta directa en vivo"))
        # chat directo: la burbuja de stream ES la respuesta; no se duplica
        assert pane._history[-1] == {
            "role": "assistant",
            "content": "respuesta directa en vivo",
        }
        assert pane._streaming_bubble is None
    finally:
        pane.deleteLater()


# ── control del run: ⏹ por sesión y 🔴 global ───────────────────────


def test_stop_btn_visible_durante_todo_el_run():
    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    try:
        pane._set_workflow_running(True)
        assert pane._stop_btn.isVisibleTo(pane)
        # el streaming ya no esconde el botón (antes desaparecía con el
        # primer token y frenar un run largo exigía cerrar la app)
        pane._on_stream("token")
        assert pane._stop_btn.isVisibleTo(pane)
        pane._set_workflow_running(False)
        assert not pane._stop_btn.isVisibleTo(pane)
    finally:
        pane.deleteLater()


def test_paro_de_emergencia_global_cancela_todas_las_sesiones():
    import concurrent.futures as cf

    from desktop.maestro_tab import MaestroTab

    _qapp()
    tabs = MaestroTab()
    tabs.add_session()
    try:
        futures = []
        for pane in tabs._panes:
            fut = cf.Future()
            pane._current_future = fut
            pane._set_workflow_running(True)
            futures.append(fut)

        assert tabs._panic_btn.isVisibleTo(tabs)
        tabs._emergency_stop()
        assert all(f.cancelled() for f in futures)

        # el finally de cada corrutina cancelada libera su flag (aquí se
        # simula: el cf.Future no tiene corrutina detrás)
        for pane in tabs._panes:
            pane._set_workflow_running(False)
        assert not tabs._panic_btn.isVisibleTo(tabs)
    finally:
        for pane in tabs._panes:
            pane.deleteLater()
        tabs.deleteLater()


def test_persist_cancelled_no_duplica_el_user(monkeypatch):
    """⏹: el user viaja SOLO en user_message — el historial aporta solo la
    respuesta parcial (antes el mismo mensaje quedaba persistido dos veces)."""
    from types import SimpleNamespace

    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    try:
        pane._history = [
            {"role": "user", "content": "consulta"},
            {"role": "assistant", "content": "respuesta parcial"},
        ]
        captured: dict = {}

        class _FakeRepo:
            @staticmethod
            async def save(**kwargs):
                captured.update(kwargs)

        import core.repositories.conversation_repository as repo_mod
        import orchestration.finalizer as fin_mod

        monkeypatch.setattr(repo_mod, "ConversationRepository", _FakeRepo)
        monkeypatch.setattr(fin_mod, "get_finalized_conversation_id", lambda: None)

        session = SimpleNamespace(context=SimpleNamespace(query="consulta"))
        asyncio.run(pane._persist_cancelled_run(session))

        assert captured["user_message"] == "consulta"
        assert [m["role"] for m in captured["conversation_history"]] == ["assistant"]
    finally:
        pane.deleteLater()


# ── pasos sin streaming: el mensaje de agente ES la vista ──────────
# Un paso sin tools no streamea (el loop solo streamea con function-calling
# nativo) y sin este render su única señal queda invisible: collaborative
# projectless (allowlist 100% FS dropeada) mostraría SOLO la síntesis final.


def test_agent_message_sin_streaming_renderiza_bloque_en_vivo():
    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    try:
        pane._on_agent_message("developer", "rondas#1 ▸ opinion_developer", "mi opinión del debate")
        # el bloque existe con el texto completo — vista en vivo sin chunks
        block = pane.debate_section._blocks["developer||rondas#1 ▸ opinion_developer"]
        assert block.browser.toPlainText().strip() == "mi opinión del debate"
        assert "✅" in block.toggle.text()  # estado ready al cerrar el paso
        # multi-agente: la burbuja plana queda suprimida desde el mensaje
        pane._on_stream("chunk plano")
        assert pane._suppress_flat_stream is True
        assert pane._streaming_bubble is None
    finally:
        pane.deleteLater()


def test_agent_message_no_duplica_bloque_que_ya_streameo():
    from desktop.maestro_tab import SessionPane

    _qapp()
    pane = SessionPane()
    try:
        pane._on_agent_stream("developer", "ejecutar#1/subtarea", "texto streameado")
        pane._on_agent_message("developer", "ejecutar#1/subtarea", "texto streameado FINAL")
        block = pane.debate_section._blocks["developer||ejecutar#1/subtarea"]
        # una sola vez: el mensaje de fin de paso no re-anexa sobre el stream
        assert block.browser.toPlainText() == "texto streameado"
    finally:
        pane.deleteLater()


def test_agent_message_completa_bloque_streameado_vacio():
    # edge de streaming muerto: el chunk vacío creó el bloque sin texto —
    # el mensaje de fin de paso lo completa (append_message no lo salta)
    from desktop.widgets.debate_section import DebateSection

    _qapp()
    section = DebateSection()
    section.append_chunk("developer", "", "paso")
    section.append_message("developer", "salida final", "paso")
    block = section._blocks["developer||paso"]
    assert block.browser.toPlainText().strip() == "salida final"
