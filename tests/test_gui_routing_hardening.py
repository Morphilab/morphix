# tests/test_gui_routing_hardening.py
"""Hardening del routing GUI multi-sesión.

Cobertura: workflow activo per-sesión, guards de ejecución y enrutado,
reserva de panes en carga y limpieza de estado entre sesiones.

Convenciones: QApplication offscreen compartida; se manipula el estado interno
de SessionPane/MaestroTab directamente (patrón test_multisession_container).
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
    """El diálogo de proyecto previo al lanzamiento jamás abre en tests
    (QDialog.exec() colgaría offscreen); el gate se testa aparte."""
    from desktop.maestro_tab import SessionPane

    monkeypatch.setattr(
        SessionPane, "_ask_project_for_workflow", lambda self, name: "proj_test", raising=False
    )
    yield


@pytest.fixture(autouse=True)
def _restore_max_sessions(monkeypatch):
    monkeypatch.setattr(settings, "maestro_max_sessions", 4)
    yield


def _set_running(pane, running: bool) -> None:
    with pane._workflow_running_lock:
        pane._workflow_running = running


async def _fake_load(self, conv_id):
    """Sustituto de SessionPane.load_conversation sin tocar BD (routing only)."""
    self._conversation_id = conv_id


# ══════════════════════════════════════════════════════════════════
# workflow activo per-sesión — un solo criterio de resolución
# ══════════════════════════════════════════════════════════════════


class TestResolveActiveWorkflow:
    def test_prefers_session_state_over_global(self, monkeypatch):
        from desktop.maestro_tab import SessionPane

        _qapp()
        monkeypatch.setattr("core.workflow_state.get_active_workflow", lambda: "development")
        pane = SessionPane()

        assert pane._resolve_active_workflow() == "development"  # fallback al global
        pane._active_workflow = "tdd"
        assert pane._resolve_active_workflow() == "tdd"  # per-sesión manda

    def test_set_mode_orchestrate_loads_session_workflow_template(self, monkeypatch):
        """_set_mode('orchestrate') NO debe filtrar agentes con la plantilla del
        workflow GLOBAL: el workflow activo per-sesión manda."""
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        pane._active_workflow = "tdd"

        seen: list[str] = []

        def _fake_load(workspace_name, workflow_name):
            seen.append(workflow_name)
            return {
                "description": "",
                "agents_allowed": [],
                "tools_allowed": [],
                "project_required": False,
                "skills": False,
                "dsl": False,
                "kind_summary": None,
                "raw": {"agents": {"allowed": None}, "tools": {}},
            }

        monkeypatch.setattr("desktop.services.workflow_view.load_workflow_view", _fake_load)
        monkeypatch.setattr("core.workflow_state.get_active_workflow", lambda: "development")

        pane._set_mode("orchestrate", silent=True)

        assert seen, "_set_mode debe cargar la plantilla del workflow activo"
        assert all(w == "tdd" for w in seen), seen


# ══════════════════════════════════════════════════════════════════
# guards de ejecución en acciones destructivas
# ══════════════════════════════════════════════════════════════════

_BUSY_MSG = "Ejecución en curso"


class TestRunGuards:
    def test_new_conversation_blocked_while_running(self, monkeypatch):
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        _set_running(pane, True)

        msgs: list[str] = []
        cleared: list[bool] = []
        monkeypatch.setattr(pane, "_on_system", lambda m: msgs.append(m))
        monkeypatch.setattr(pane, "clear_chat", lambda force=False: cleared.append(force))

        pane._new_conversation()

        assert cleared == []  # el chat NO se limpia
        assert any(_BUSY_MSG in m for m in msgs)

    def test_clear_chat_guarded_while_running_unless_forced(self, monkeypatch):
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        _set_running(pane, True)

        msgs: list[str] = []
        monkeypatch.setattr(pane, "_on_system", lambda m: msgs.append(m))

        pane._history.append({"role": "user", "content": "dato"})
        pane.clear_chat()  # sin force → guard
        assert pane._history == [{"role": "user", "content": "dato"}]
        assert any(_BUSY_MSG in m for m in msgs)

        pane.clear_chat(force=True)  # force explícito → limpia
        assert pane._history == []

    def test_set_mode_blocked_while_running(self, monkeypatch):
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        pane._set_mode("chat", silent=True)
        _set_running(pane, True)

        msgs: list[str] = []
        monkeypatch.setattr(pane, "_on_system", lambda m: msgs.append(m))

        pane._set_mode("orchestrate")  # intento de cambio con run activo

        assert pane._mode == "chat"  # no cambia
        assert any(_BUSY_MSG in m for m in msgs)

        # refresco del mismo modo (path de workspace switch) sigue permitido
        pane._set_mode("chat")
        assert pane._mode == "chat"

    def test_send_message_busy_shows_notice(self, monkeypatch):
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        pane._set_mode("chat", silent=True)
        _set_running(pane, True)
        pane.input_field.setPlainText("hola")

        msgs: list[str] = []
        monkeypatch.setattr(pane, "_on_system", lambda m: msgs.append(m))

        pane.send_message()

        assert any(_BUSY_MSG in m for m in msgs)
        assert pane._current_future is None  # no se lanzó ningún run
        assert pane.input_field.toPlainText() == "hola"  # el texto se conserva


# ══════════════════════════════════════════════════════════════════
# enrutadores retornan éxito — el Dashboard no destruye el prompt
# ══════════════════════════════════════════════════════════════════


class TestRouterReturnValues:
    def test_routers_return_false_when_all_busy(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        monkeypatch.setattr(settings, "maestro_max_sessions", 1)
        m = MaestroTab()
        _set_running(m._panes[0], True)

        assert m.launch_workflow("development") is False
        assert m.launch_agent("developer") is False
        assert m.set_pending_prompt("¿qué tal?") is False

    def test_routers_return_true_on_success(self):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        m = MaestroTab()

        assert m.launch_workflow("development") is True
        assert m.launch_agent("developer") is True
        assert m.set_pending_prompt("hola") is True


class TestLauncherFeedback:
    @staticmethod
    def _dashboard(monkeypatch, accepted: bool):

        from PySide6.QtWidgets import QLineEdit

        from desktop.dashboard_tab import DashboardTab

        d = DashboardTab.__new__(DashboardTab)  # sin __init__ (backend pesado)
        d.launch_field = QLineEdit()
        d.launch_field.setText("pregunta del usuario")

        class _FakeMaestro:
            def set_pending_prompt(self, text: str):
                return accepted

        monkeypatch.setattr(d, "_maestro", lambda: _FakeMaestro())
        navigated: list[str] = []
        monkeypatch.setattr(d, "_navigate", lambda route, context=None: navigated.append(route))
        boxes: list[tuple] = []
        monkeypatch.setattr(
            "PySide6.QtWidgets.QMessageBox.information",
            staticmethod(lambda *a, **k: boxes.append(a)),
        )
        return d, navigated, boxes

    def test_prompt_kept_and_feedback_when_no_free_session(self, monkeypatch):
        d, navigated, boxes = self._dashboard(monkeypatch, accepted=False)

        d._launch_prompt()

        assert d.launch_field.text() == "pregunta del usuario"  # NO se destruye
        assert navigated == []  # no navega a Maestro
        assert boxes, "debe mostrarse feedback de primer nivel"

    def test_prompt_cleared_and_navigates_on_success(self, monkeypatch):
        d, navigated, boxes = self._dashboard(monkeypatch, accepted=True)

        d._launch_prompt()

        assert d.launch_field.text() == ""
        assert navigated == ["maestro"]
        assert boxes == []


# ══════════════════════════════════════════════════════════════════
# pausa armada = pane ocupada + abandono explícito
# ══════════════════════════════════════════════════════════════════


class TestPauseOccupiesPane:
    @staticmethod
    def _arm_pause(pane) -> None:
        pane._paused_session = object()  # sesión pausada "armada"

    def test_has_pending_pause(self):
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        assert pane.has_pending_pause() is False
        self._arm_pause(pane)
        assert pane.has_pending_pause() is True

    def test_idle_pane_skips_paused(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        monkeypatch.setattr(settings, "maestro_max_sessions", 2)
        m = MaestroTab()
        self._arm_pause(m._panes[0])
        p2 = m.add_session()

        assert m._idle_pane() is p2

    def test_load_conversation_skips_paused_pane(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab, SessionPane

        _qapp()
        monkeypatch.setattr(settings, "maestro_max_sessions", 2)
        monkeypatch.setattr(SessionPane, "load_conversation", _fake_load)
        m = MaestroTab()
        self._arm_pause(m._panes[0])

        asyncio.run(m.load_conversation(5))

        assert m.session_count == 2  # NO reutilizó la pausada
        assert m._panes[0]._conversation_id is None
        assert m._panes[1]._conversation_id == 5

    def test_switch_project_does_not_absorb_paused_fresh_pane(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        monkeypatch.setattr(settings, "maestro_max_sessions", 2)
        m = MaestroTab()
        self._arm_pause(m._panes[0])  # pausada pero "fresca" (sin conv/proyecto)

        m.switch_project("nuevo")

        assert m.session_count == 2  # proyecto → pane NUEVA, no a la pausada
        assert m._panes[0]._current_project_root is None
        assert m._panes[1]._current_project_root == "code_projects/nuevo"

    def test_abandon_pause_clears_state_and_affordance(self, monkeypatch):
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        self._arm_pause(pane)
        pane._show_status_banner("⏸ ¿pregunta?", "warning")
        msgs: list[str] = []
        monkeypatch.setattr(pane, "_on_system", lambda m: msgs.append(m))

        pane._abandon_pause()

        assert pane._paused_session is None
        assert pane._abandon_pause_btn.isVisible() is False
        assert pane._status_banner.isVisible() is False
        assert pane.input_field.placeholderText() == "Escribe tu mensaje..."
        assert any("abandonada" in m.lower() for m in msgs)

    def test_new_conversation_disarms_pause(self, monkeypatch):
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        self._arm_pause(pane)

        pane._new_conversation()

        assert pane._paused_session is None

    def test_launch_workflow_disarms_pause_defensively(self, monkeypatch):
        """Defense-in-depth: si alguien llama launch_* directamente sobre una
        pane pausada, la pausa se desarma CON aviso (jamás queda armada para
        secuestrar el próximo mensaje como respuesta de clarificación)."""
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        self._arm_pause(pane)
        msgs: list[str] = []
        monkeypatch.setattr(pane, "_on_system", lambda m: msgs.append(m))

        pane.launch_workflow("development")

        assert pane._paused_session is None
        assert any("abandonada" in m.lower() for m in msgs)

    def test_resume_consumes_pause_and_hides_affordance(self):
        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        self._arm_pause(pane)
        pane._show_status_banner("⏸ ¿pregunta?", "warning")

        # simula la rama de resume de send_message (sin ejecutar run_async):
        pane._paused_session = None
        pane._hide_status_banner()
        pane._update_pause_affordance()

        assert pane._abandon_pause_btn.isVisible() is False

    def test_no_free_session_notice_mentions_pause(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        monkeypatch.setattr(settings, "maestro_max_sessions", 1)
        m = MaestroTab()
        _set_running(m._panes[0], True)

        msgs: list[str] = []
        monkeypatch.setattr(m._panes[0], "_on_system", lambda msg: msgs.append(msg))
        m._notify_no_free_session()

        assert any("pausa" in msg.lower() for msg in msgs)


# ══════════════════════════════════════════════════════════════════
# indicador ● de sesión ocupada cableado vía señal
# ══════════════════════════════════════════════════════════════════


class TestRunningIndicator:
    def test_tab_text_shows_running_dot(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        monkeypatch.setattr(settings, "maestro_max_sessions", 2)
        m = MaestroTab()
        m.launch_workflow("development")
        p2 = m._tabs.currentWidget()

        p2._set_workflow_running(True)
        assert m._tabs.tabText(1).endswith("●")

        p2._set_workflow_running(False)
        assert "●" not in m._tabs.tabText(1)

    def test_labels_stay_aligned_after_close(self, monkeypatch):
        """T5 colateral: _remove_session nunca podó _labels — el indicador
        habría desalineado los títulos tras cerrar una sesión."""
        from desktop.maestro_tab import MaestroTab

        _qapp()
        m = MaestroTab()
        m.launch_workflow("development")
        assert len(m._labels) == m.session_count == 2

        m._on_close_requested(0)  # la primera no está corriendo → se cierra

        assert len(m._labels) == m.session_count == 1
        m.launch_workflow("tdd")
        p2 = m._tabs.currentWidget()  # pane nueva → índice 1
        p2._set_workflow_running(True)
        assert m._tabs.tabText(1).endswith("●")
        assert "●" not in m._tabs.tabText(0)


# ══════════════════════════════════════════════════════════════════
# reserva de pane durante load_conversation + dedupe roster
# ══════════════════════════════════════════════════════════════════


class TestLoadReservation:
    @staticmethod
    def _slow_load_factory(started: asyncio.Event, release: asyncio.Event):
        """Fake: la PRIMERA carga se bloquea hasta ``release`` (simula await
        de BD); las siguientes completan de inmediato — evita deadlocks en RED."""

        def _factory():
            calls = {"n": 0}

            async def _load(self, conv_id):
                calls["n"] += 1
                if calls["n"] == 1:
                    started.set()
                    await release.wait()
                self._conversation_id = conv_id

            return _load

        return _factory()

    def test_same_conv_while_loading_focuses_not_duplicates(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab, SessionPane

        _qapp()
        monkeypatch.setattr(settings, "maestro_max_sessions", 4)
        started, release = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(
            SessionPane, "load_conversation", self._slow_load_factory(started, release)
        )
        m = MaestroTab()

        async def _scenario():
            t1 = asyncio.create_task(m.load_conversation(7))
            await asyncio.wait_for(started.wait(), 5)
            await asyncio.wait_for(m.load_conversation(7), 5)  # misma conv en curso
            assert m.session_count == 2, "no debe abrir una 3ª pane para la misma conv"
            release.set()
            await asyncio.wait_for(t1, 5)

        asyncio.run(_scenario())

        assert m._panes[1]._conversation_id == 7

    def test_busy_loading_pane_not_reused_at_max(self, monkeypatch):
        """Sin reserva en carga, la 2ª entrada reutilizaría la pane que está
        cargando y el provisional `_conversation_id` de la 1ª quedaría clobbered."""
        from desktop.maestro_tab import MaestroTab, SessionPane

        _qapp()
        monkeypatch.setattr(settings, "maestro_max_sessions", 1)
        started, release = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(
            SessionPane, "load_conversation", self._slow_load_factory(started, release)
        )
        m = MaestroTab()

        async def _scenario():
            t1 = asyncio.create_task(m.load_conversation(7))
            await asyncio.wait_for(started.wait(), 5)
            await asyncio.wait_for(m.load_conversation(8), 5)  # conv distinta mientras carga
            assert m._panes[0]._conversation_id == 7, "la pane en carga NO debe tocarse"
            release.set()
            await asyncio.wait_for(t1, 5)

        asyncio.run(_scenario())

        assert m.session_count == 1  # no reutilizó la pane en carga

    def test_provisional_id_survives_failed_load(self, monkeypatch):
        """Si la carga no encuentra mensajes (early-return del pane), la
        provisional queda asignada: el dedupe de la MISMA conv sigue
        funcionando y la pane queda claramente apuntando a esa conversación."""
        from desktop.maestro_tab import MaestroTab, SessionPane

        _qapp()

        async def _empty_load(self, conv_id):
            return  # simulación: sin mensajes → early-return sin asignar

        monkeypatch.setattr(SessionPane, "load_conversation", _empty_load)
        m = MaestroTab()
        asyncio.run(m.load_conversation(9))

        assert m.session_count == 2  # routing "nueva primero" (pane0 intacta)
        assert m._panes[1]._conversation_id == 9  # provisional sobrevive


class TestRosterOpenDedupe:
    @staticmethod
    def _roster_with_selection(monkeypatch):
        """Roster con fila seleccionada y `run_async` parcheado ANTES de
        construir (el __init__ ya llama run_async(refresh()) — sin loop real
        en pytest explotaría). Retorna también lo programado para inspección."""
        from types import SimpleNamespace

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidgetItem

        scheduled: list[tuple[object, list]] = []

        def _fake_run_async(coro):
            coro.close()
            cbs: list = []
            fut = SimpleNamespace(
                add_done_callback=cbs.append, exception=lambda: None, result=lambda: 33
            )
            scheduled.append((coro, cbs))
            return fut

        monkeypatch.setattr("desktop.bots_tab.run_async", _fake_run_async)

        from desktop.bots_tab import RosterPane

        r = RosterPane()
        row = {"slug": "ana", "display_name": "Ana", "canonical": None}
        item = QListWidgetItem("● @ana")
        item.setData(Qt.ItemDataRole.UserRole, row)
        r.list_widget.addItem(item)
        r.list_widget.setCurrentItem(item)

        emitted: list[int] = []
        monkeypatch.setattr(
            r, "open_requested", SimpleNamespace(emit=emitted.append), raising=False
        )
        return r, emitted, scheduled

    def test_no_second_open_while_first_in_flight(self, monkeypatch):
        from types import SimpleNamespace

        r, emitted, scheduled = self._roster_with_selection(monkeypatch)

        r._on_open_clicked()  # clic 1 → apertura EN CURSO
        assert r._opening is True
        r._on_open_clicked()  # clic 2 (doble-clic) → IGNORADO
        assert len(scheduled) == 2  # constructor refresh + 1ª apertura SOLAMENTE

        done_cb = scheduled[-1][1][0]
        done_cb(SimpleNamespace(exception=lambda: None, result=lambda: 33))
        assert emitted == [33]  # exactamente UNA emisión
        assert r._opening is False
        assert r.btn_open.isEnabled()


# ══════════════════════════════════════════════════════════════════
# recovery de pausas desde BD al cargar conversación
# ══════════════════════════════════════════════════════════════════


class TestPauseRecovery:
    @staticmethod
    def _pause_row(question: str = "¿Qué nombre tiene el módulo?") -> dict:
        return {
            "id": 1,
            "question": question,
            "options": None,
            "paused_state": '{"origin": "development", "query": "haz X", "subtasks": []}',
        }

    @staticmethod
    def _patch_repo(monkeypatch):
        """Mockea el repo para que corra el SessionPane.load_conversation REAL
        (el recovery vive dentro de él — parchear el método lo saltaría)."""
        from core.repositories import conversation_repository as cr

        async def _get_conversation(conv_id):
            return {"id": conv_id, "is_canonical": False}

        async def _get_messages(conv_id):
            return [{"role": "user", "content": "hola"}]

        monkeypatch.setattr(
            cr.ConversationRepository, "get_conversation", staticmethod(_get_conversation)
        )
        monkeypatch.setattr(cr.ConversationRepository, "get_messages", staticmethod(_get_messages))

    def test_load_conversation_rearms_unresolved_pause(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        self._patch_repo(monkeypatch)

        async def _gup(conv_id):
            return self._pause_row()

        monkeypatch.setattr(
            "orchestration.workflows.orchestrator.WorkflowOrchestrator.get_unresolved_pause",
            staticmethod(_gup),
        )
        m = MaestroTab()
        asyncio.run(m.load_conversation(7))

        pane = m._tabs.currentWidget()  # el container enruta a pane NUEVA
        asyncio.run(m.load_conversation(7))  # 2ª pasada ya enfoca la misma pane
        assert m._tabs.currentWidget() is pane
        assert pane.has_pending_pause() is True  # re-armada
        assert pane._paused_session.context.conversation_id == 7
        assert pane._paused_session.context.last_clarification.startswith("¿Qué nombre")
        assert pane._status_banner.isHidden() is False  # offscreen: sin ancestros visibles
        assert pane._abandon_pause_btn.isHidden() is False
        assert "sin resolver" in pane._status_log_view.toPlainText()

    async def test_rearmed_session_resumes_not_runs(self, monkeypatch):
        """El próximo send sobre una pausa recuperada llama `runner.resume`,
        NO `runner.run` (comportamiento idéntico a pausa en memoria)."""
        from desktop.maestro_tab import MaestroTab

        _qapp()
        self._patch_repo(monkeypatch)

        async def _gup(conv_id):
            return self._pause_row()

        monkeypatch.setattr(
            "orchestration.workflows.orchestrator.WorkflowOrchestrator.get_unresolved_pause",
            staticmethod(_gup),
        )
        m = MaestroTab()
        await m.load_conversation(7)
        pane = m._tabs.currentWidget()

        resumed: list = []
        ran: list = []

        async def _fake_resume(session, answer):
            resumed.append(answer)

        async def _fake_run(session):
            ran.append(session)

        monkeypatch.setattr(pane._runner, "resume", _fake_resume)
        monkeypatch.setattr(pane._runner, "run", _fake_run)

        pane.input_field.setPlainText("se llama módulo_x")
        pane.send_message()
        await asyncio.sleep(0.05)  # cede el loop: el resume programado se ejecuta

        assert resumed == ["se llama módulo_x"]
        assert ran == []

    def test_checkpoint_snapshots_never_rearm(self, monkeypatch):
        """El getter filtra snapshots [auto-checkpoint]: si solo existe ese
        snapshot, NO se re-arma pausa humana."""
        from desktop.maestro_tab import MaestroTab

        _qapp()
        self._patch_repo(monkeypatch)

        async def _gup(conv_id):
            return None

        monkeypatch.setattr(
            "orchestration.workflows.orchestrator.WorkflowOrchestrator.get_unresolved_pause",
            staticmethod(_gup),
        )
        m = MaestroTab()

        asyncio.run(m.load_conversation(8))

        assert m._tabs.currentWidget().has_pending_pause() is False

    def test_corrupt_paused_state_does_not_crash(self, monkeypatch):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        self._patch_repo(monkeypatch)

        async def _gup(conv_id):
            return {
                "id": 2,
                "question": "¿seguimos?",
                "options": None,
                "paused_state": "{json-roto",
            }

        monkeypatch.setattr(
            "orchestration.workflows.orchestrator.WorkflowOrchestrator.get_unresolved_pause",
            staticmethod(_gup),
        )
        m = MaestroTab()

        asyncio.run(m.load_conversation(11))

        pane = m._tabs.currentWidget()
        assert pane.has_pending_pause() is True  # se re-arma igual
        assert pane._paused_session.context.query == ""  # fallback vacío


# ══════════════════════════════════════════════════════════════════
# eliminar conversación invalida `_conversation_id` en panes
# ══════════════════════════════════════════════════════════════════


class TestConversationInvalidation:
    def test_forget_conversation_clears_only_matching_pane(self):
        from desktop.maestro_tab import MaestroTab

        _qapp()
        m = MaestroTab()
        m.add_session()
        m._panes[0]._conversation_id = 5
        m._panes[1]._conversation_id = 6

        m.forget_conversation(5)

        assert m._panes[0]._conversation_id is None
        assert m._panes[1]._conversation_id == 6

    def test_history_delete_emits_conversation_deleted(self, monkeypatch):
        from types import SimpleNamespace

        from desktop.history_tab import HistoryTab

        _qapp()  # sin QApplication el constructor de Qt aborta nativo

        def _fake_run_async(coro):
            coro.close()
            fut = SimpleNamespace(add_done_callback=lambda cb: None, exception=lambda: None)
            return fut

        monkeypatch.setattr("desktop.history_tab.run_async", _fake_run_async)

        from desktop.services import history_service

        deleted: list[int] = []

        async def _fake_delete(conv_id):
            deleted.append(conv_id)
            return True

        monkeypatch.setattr(
            history_service.HistoryService, "delete_conversation", staticmethod(_fake_delete)
        )

        tab = HistoryTab()
        emitted: list[int] = []
        monkeypatch.setattr(
            tab, "conversation_deleted", SimpleNamespace(emit=emitted.append), raising=False
        )
        tab._selected_id = 42

        asyncio.run(tab._delete())

        assert deleted == [42]
        assert emitted == [42]  # señal para invalidar panes
        assert tab._selected_id is None


# ══════════════════════════════════════════════════════════════════
# buscador de Historial — conecta la variante rica (fecha/tag/semántico)
# ══════════════════════════════════════════════════════════════════


class TestHistorySearch:
    async def test_service_with_query_uses_rich_loader(self, monkeypatch):
        from datetime import datetime
        from types import SimpleNamespace

        from desktop.services import history_service

        fake_conv = SimpleNamespace(
            id=7, title="resultado semántico", tags="chat", created_at=datetime(2026, 8, 30)
        )
        called: list[str] = []

        async def _rich(query):
            called.append(query)
            return [fake_conv]

        monkeypatch.setattr(
            history_service.HistoryService, "load_conversations", staticmethod(_rich)
        )

        res = await history_service.HistoryService.list_conversations("resultado")

        assert called == ["resultado"]
        assert res[0]["id"] == 7
        assert res[0]["title"] == "resultado semántico"

    async def test_service_without_query_uses_plain_list(self, monkeypatch):
        from desktop.services import history_service

        sentinel = [{"id": 1, "title": "t"}]

        async def _plain(limit=50, offset=0, **kw):
            return sentinel

        monkeypatch.setattr(
            history_service.ConversationRepository, "list_all", staticmethod(_plain)
        )

        assert await history_service.HistoryService.list_conversations() is sentinel

    def _history_tab(self, monkeypatch):
        from types import SimpleNamespace

        from desktop.history_tab import HistoryTab

        _qapp()  # sin QApplication el constructor de Qt aborta nativo

        def _fake_run_async(coro):
            coro.close()
            return SimpleNamespace(add_done_callback=lambda cb: None, exception=lambda: None)

        monkeypatch.setattr("desktop.history_tab.run_async", _fake_run_async)
        return HistoryTab()

    def test_search_field_queues_with_debounce(self, monkeypatch):
        tab = self._history_tab(monkeypatch)

        tab.search_field.setText("date:2026-08-30")

        assert tab._pending_search == "date:2026-08-30"
        assert tab._search_timer.isActive()  # debounce 300ms en vuelo

    def test_search_now_loads_with_query(self, monkeypatch):
        tab = self._history_tab(monkeypatch)
        loaded: list[str] = []

        async def _fake_load_list(query: str = ""):
            loaded.append(query)

        monkeypatch.setattr(tab, "_load_list", _fake_load_list)
        tab._pending_search = "tag:bots"

        asyncio.run(tab._search_now())

        assert loaded == ["tag:bots"]


# ══════════════════════════════════════════════════════════════════
# flush del stream al terminar (render inmediato del último tramo)
# ══════════════════════════════════════════════════════════════════


class TestFlushStreamWiring:
    def test_runner_on_assistant_flushes_streaming_bubble(self):
        from unittest.mock import MagicMock

        from desktop.maestro_tab import SessionPane

        _qapp()
        pane = SessionPane()
        bubble = MagicMock()
        pane._streaming_bubble = bubble
        pane._streaming_text = "texto parcial en vivo"

        asyncio.run(pane._runner_on_assistant(""))

        bubble.flush_stream.assert_called_once()  # render inmediato del final
        assert pane._streaming_bubble is None
