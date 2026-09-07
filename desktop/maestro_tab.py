"""Maestro Tab — chat, streaming, diagrama, agentes, y stats."""

import asyncio
import logging
import os
import threading
from datetime import UTC, datetime

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from agents.registry import agents_registry
from core.config import settings
from core.constants import PROJECTS_DIR_NAME
from desktop.panels.top_bar import project_button_style
from desktop.services.project_service import active_workspace
from desktop.theme import COLORS, StyleFactory, ThemeManager
from orchestration.context import WorkflowContext

logger = logging.getLogger(__name__)

from core.token_counter import get_encoding
from desktop.async_helpers import run_async
from desktop.services.workflow_runner import WorkflowRunner
from desktop.widgets.collapsible_section import CollapsibleSection
from desktop.widgets.phase_cards import PhaseCards
from desktop.widgets.stat_chips import StatChips

_SUBTASK_GLYPHS = {"completed": "✓", "running": "●", "failed": "✕", "pending": "○"}
CHAT_MAX_WIDTH = 760
# Prefijos (case-insensitive, anclados AL INICIO del status) del contrato real
# de stats que indican que el workflow YA NO está en ejecución. Los emits
# terminales empiezan con la palabra ("Completado", "Fallido", "completed"...)
# mientras que los intermedios solo la contienen en medio ("Ronda 2/3
# completada", "Aggregating (2 done, 1 failed)", "DAG level completed").
_ACTIVITY_TERMINAL_PREFIXES = (
    "completad",  # Completado/Completada (final español; OJO: diverge de "completed")
    "complete",  # completed (final inglés: pipeline y agent loop)
    "fallid",  # Fallido/Fallida (ruta TDD fallida)
    "cancel",  # Cancelado/cancelled
    "paus",  # Pausa/paused (gate/checkpoint de pipeline)
    "fail",  # failed (stage/gate de pipeline)
    "error",  # Error ... (defensivo; sin emits actuales)
    "idle",  # default del WorkflowEmitter
)


class SessionPane(QWidget):
    # cambio de estado de ejecución notificable al contenedor (la señal Qt
    # marshallea cross-thread si el loop asyncio corre fuera del hilo GUI).
    running_changed = Signal()
    refresh_requested = Signal()

    _workflow_running: bool

    def is_workflow_running(self) -> bool:
        """Estado consultable por el switch_guard del workspace."""
        with self._workflow_running_lock:
            return self._workflow_running

    def _set_workflow_running(self, value: bool) -> None:
        """Único punto de escritura de `_workflow_running` (post-init).

        Notifica al contenedor para que los indicadores de sub-pestaña
        ("Sesión N ●") se actualicen en cada transición.
        """
        with self._workflow_running_lock:
            changed = self._workflow_running != value
            self._workflow_running = value
        if changed:
            if value:
                self._budget_warned = False  # nuevo run → banner re-armado
            self.running_changed.emit()

    def has_pending_pause(self) -> bool:
        """True con clarificación pendiente — la pane NO está libre para
        entradas nuevas (el próximo mensaje sería consumido como respuesta de
        la pausa vieja)."""
        return self._paused_session is not None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._streaming_bubble = None
        self._streaming_text = ""
        self._typing_label = None
        self._history: list[dict] = []
        self._selected_agent: str | None = None
        self._force_agent: str | None = None
        self._workflow_running_lock = threading.Lock()
        self._workflow_running = False

        # vetar cambios de workspace mientras haya un workflow en curso.
        # Cada pane registra SU guard (multi-observador); el switch se veta si
        # CUALQUIER pane corre.
        self._guard_token: int | None = None
        try:
            from core.workspaces import get_global_workspaces

            self._guard_token = get_global_workspaces().add_switch_guard(
                lambda _name: not self.is_workflow_running()
            )
        except Exception:  # pragma: no cover — arranque sin BD no debe romper GUI
            pass
        self._paused_session: Session | None = None
        self._scroll_pending = False
        # pane reservada mientras un load_conversation está en curso —
        # evita que otra entrada la reutilice y clobbered el conv_id provisional.
        self.busy_loading: bool = False
        self._current_project_root: str | None = None
        self._pending_prompt: str | None = None
        self._mode: str = "chat"
        self._conversation_id: int | None = None
        self._in_bot_canonical: bool = False

        # señales propias del pane (eventos de sesión) y
        # workflow activo por-sesión (en vez del global workflow_state).
        self._own_signals = None
        self._active_workflow: str | None = None
        self._current_future = None
        self._stop_btn: QPushButton

        # Perf: differential-update caches to avoid redundant widget writes
        self._last_progress: int = -1
        self._last_subtasks: list | None = None
        self._last_files: list | None = None
        self._status_log_started: bool = False
        self._budget_warned: bool = False  # banner de presupuesto una vez por run

        # Widgets set by panel builders (declared for mypy)
        self._chat_toggle: QPushButton
        self._orchestrate_toggle: QPushButton
        self._workflow_combo: QComboBox
        self._agent_combo: QComboBox
        self._project_btn: QToolButton
        self._project_menu: QMenu
        self._preload_action: QAction
        self._change_project_action: QAction
        self._preload_status: QLabel
        self._preload_progress: QProgressBar
        self._new_conv_btn: QPushButton
        self._export_btn: QToolButton
        self.chat_scroll: QScrollArea
        self.chat_container: QWidget
        self.chat_layout: QVBoxLayout
        self.input_field: QTextEdit
        self.pdf_path_field: QLineEdit
        self._attach_btn: QPushButton
        self.send_btn: QPushButton
        self._status_banner: QLabel
        self._abandon_pause_btn: QPushButton
        self._detail_tabs: QTabWidget
        self._diagram_view: PhaseCards
        self._status_log_view: QTextBrowser
        self.status_log: QTextBrowser  # backward-compat alias
        self._subtask_list: QListWidget
        self._files_written_list: QListWidget
        self._subtask_section: CollapsibleSection
        self._files_section: CollapsibleSection
        self.stat_chips: StatChips
        self._progress_bar: QProgressBar
        self._activity_dot: QLabel
        self._idle_note: QLabel
        self._agent_label: QLabel
        self._info_btn: QPushButton
        self._branch_btn: QToolButton
        self._bot_canonical_slug: str | None = None
        self._current_pdf_text: str = ""

        self._build_ui()
        self._set_activity_state(False)
        self._connect_maestro()

        self._runner = WorkflowRunner()
        self._runner.on_system = self._runner_on_system
        self._runner.on_assistant = self._runner_on_assistant
        self._runner.on_pause = lambda s, q: self._on_runner_pause(s, q)
        self._runner.streaming_check = self._has_streaming
        # Rutas sin emit terminal (coordinated) o excepción: el fin del run
        # garantiza el reset del chip aunque ningún stats lo haga.
        self._runner.on_finish = lambda: self._set_activity_state(False)
        # ⏹ persiste la conversación parcial (guarda lo que haya en UI).
        self._runner.on_cancel_persist = self._persist_cancelled_run

    async def _persist_cancelled_run(self, session) -> None:
        """⏹: persiste la conversación parcial para que aparezca en Historial.

        Antes del fix, cancelar con CancelledError saltaba la persistencia
        entera (el run detenida no dejaba conversación ni mensajes).
        """
        try:
            from core.repositories.conversation_repository import ConversationRepository
            from orchestration.finalizer import get_finalized_conversation_id

            conv_id = self._conversation_id or get_finalized_conversation_id()
            title = (session.context.query or "Ejecución detenida")[:100]
            user_msg = session.context.query or "(detenida)"
            messages_to_save = [m for m in self._history if m.get("role") in ("user", "assistant")]
            await ConversationRepository.save(
                title=f"⏹ {title}",
                user_message=user_msg,
                tags="detenida",
                workflow_id=None,
                conversation_history=messages_to_save,
                conversation_id=conv_id,
            )
            if self._conversation_id is None:
                self._conversation_id = conv_id
        except Exception:
            logger.warning("No se pudo persistir la conversación detenida", exc_info=True)

    def _build_ui(self):
        from desktop.panels import (
            build_activity_panel,
            build_chat_panel,
            build_top_bar,
        )
        from desktop.widgets.bash_panel import BashPanel
        from desktop.widgets.debate_section import DebateSection

        self.debate_section = DebateSection()
        self.bash_panel = BashPanel()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(build_top_bar(self))

        # spec §4: 2 columnas — chat (flex 3) + panel de actividad unificado (1.2)
        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setContentsMargins(6, 6, 6, 6)
        columns.setChildrenCollapsible(True)

        chat = build_chat_panel(self)
        chat.setMinimumWidth(300)
        columns.addWidget(chat)

        activity = build_activity_panel(self)
        activity.setMinimumWidth(280)
        columns.addWidget(activity)

        columns.setStretchFactor(0, 30)
        columns.setStretchFactor(1, 12)

        root.addWidget(columns, 1)

        self._files_written_list.itemDoubleClicked.connect(self._open_file_in_viewer)

    def _open_file_in_viewer(self, item):
        """Doble clic en 'Archivos creados' → visor standalone."""
        from desktop.events import get_signals

        name = item.text().strip()
        if name:
            get_signals().view_file_requested.emit(name)

    def eventFilter(self, obj, event):
        """Ctrl+Enter para enviar desde el QTextEdit multilínea."""
        if obj is self.input_field and event.type() == QEvent.Type.KeyPress:
            if (
                event.key() == Qt.Key.Key_Return
                and event.modifiers() == Qt.KeyboardModifier.ControlModifier
            ):
                self.send_message()
                return True
        elif obj is self.chat_scroll.viewport() and event.type() == QEvent.Type.Resize:
            w = obj.width()
            if w > 0:
                self.chat_container.setFixedWidth(min(w, CHAT_MAX_WIDTH))
        return super().eventFilter(obj, event)

    def _populate_agents(self, allowed: list[str] | None):
        """Fill the agent selector combo, optionally filtered by an allowlist."""
        combo = self._agent_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("🤖 Auto", None)
        registered = agents_registry.list_agents()
        for name in sorted(registered.keys()):
            if allowed is not None and name not in allowed:
                continue
            combo.addItem(name.capitalize(), name)
        target = self._force_agent or self._selected_agent
        idx = combo.findData(target) if target else 0
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        self._update_agent_detail()

    def _on_agent_combo_changed(self, _index: int):
        name = self._agent_combo.currentData()
        if name:
            self._select_agent(name)
        else:
            self._force_agent = None
            self._selected_agent = None
            self._update_agent_detail()

    def _select_agent(self, name: str):
        self._selected_agent = name
        self._update_agent_detail()
        self._update_info_button()
        # In chat mode: activate agent for direct conversation
        if self._mode == "chat":
            self._force_agent = name
            # Bot Mode: elegir un agente explícito sale del modo bot
            # del chat canónico — el usuario quiere hablar con ESE agente, no con el bot.
            self._in_bot_canonical = False
            self._on_system(f"Conversación directa con: **{name.capitalize()}**")

    def _update_agent_detail(self):
        """Show the selected agent's profile as the combo tooltip."""
        if not self._selected_agent:
            self._agent_combo.setToolTip("Selecciona un agente (o Auto)")
            return
        profile = agents_registry.get_profile(self._selected_agent)
        if profile:
            prompt = profile.get("system_prompt", "Sin prompt")[:200]
            tools = profile.get("tools", [])
            self._agent_combo.setToolTip(
                f"{prompt}...\nHerramientas: {', '.join(tools) if tools else 'Ninguna'}"
            )
        else:
            self._agent_combo.setToolTip("Sin perfil definido")

    def _resolve_active_workflow(self) -> str:
        """Workflow activo de ESTA sesión; fallback al global (único criterio).

        Todos los consumidores (combo, detalle, guard de proyecto, envío y
        _set_mode) resuelven con esto — el estado per-sesión manda sobre el
        workflow_state global, que puede quedar desfasado entre sesiones.
        """
        from core.workflow_state import get_active_workflow

        return self._active_workflow or get_active_workflow()

    def _populate_workflow_combo(self):
        from core.workspaces import get_global_workspaces
        from orchestration.loader import list_workflows

        ws = get_global_workspaces().current
        workflows = list_workflows(ws)
        combo = self._workflow_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(workflows)
        current = self._resolve_active_workflow()
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentIndex(0)
            first = combo.currentText()
            if first:
                self._active_workflow = first
        combo.blockSignals(False)
        combo.setVisible(self._mode == "orchestrate")

    def _on_workflow_picked(self, name: str):
        if not name:
            return
        self._active_workflow = name
        self._refresh_detail_tabs_for_workflow()
        self._on_system(f"⚙️ Workflow activo: **{name}**")

    def _request_change_project(self):
        parent = self.window()
        if parent and hasattr(parent, "tabs") and hasattr(parent, "_sidebar"):
            for i in range(parent.tabs.count()):
                if parent.tabs.tabText(i) == "Dashboard":
                    parent.tabs.setCurrentIndex(i)
                    parent._sidebar.setCurrentRow(0)
                    return

    def _own_events_signals(self):
        """Bus de señales propio del pane (eventos de sesión).

        Cada SessionPane crea SU DesktopSignals y `build_workflow_events`
        emite ahí — así los eventos de dos sesiones no se entrelazan en el bus
        global. workspace_changed sigue global (refresco transversal).
        """
        if self._own_signals is None:
            from desktop.events import DesktopSignals

            self._own_signals = DesktopSignals()
        return self._own_signals

    def _connect_maestro(self):
        from desktop.events import get_signals

        signals = self._own_events_signals()
        signals.system_message.connect(self._on_system)
        signals.assistant_message.connect(self._on_assistant)
        signals.agent_message.connect(self._on_agent_message)
        signals.agent_stream.connect(self._on_agent_stream)
        signals.agent_status.connect(self._on_agent_status)
        signals.user_message.connect(self._on_user)
        signals.stream_chunk.connect(self._on_stream)
        signals.stats_update.connect(self._on_stats)
        signals.indexing_progress.connect(self._on_indexing_progress)
        # workspace_changed es global (refresco transversal de agentes/panel).
        get_signals().workspace_changed.connect(self._on_workspace_switch)

    # ── Public methods for Dashboard ──

    def set_pending_prompt(self, text: str) -> None:
        """Pre-carga un prompt (lanzador del Dashboard) para consumirlo al mostrarse."""
        self._pending_prompt = text

    def showEvent(self, event) -> None:  # noqa: N802 — API Qt
        super().showEvent(event)
        if self._pending_prompt:
            self.input_field.setPlainText(self._pending_prompt)
            cursor = self.input_field.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.input_field.setTextCursor(cursor)
            self.input_field.setFocus()
            self._pending_prompt = None

    def launch_workflow(self, workflow_name: str) -> bool:
        """Called from Dashboard when a workflow card is clicked.

        False = el usuario canceló el pedido de proyecto (nada cambió en
        la pane); el llamador decide cómo avisar.
        """
        # si esta pane tenía una pausa armada, se
        # desarma CON aviso — el workflow nuevo jamás hereda la clarificación.
        self._abandon_pause()
        from core.workspaces import get_global_workspaces
        from desktop.services.workflow_view import load_workflow_view

        ws = get_global_workspaces().current
        view = load_workflow_view(ws, workflow_name)
        if view is None:
            logger.warning("Plantilla '%s' no se pudo cargar", workflow_name)

        # Gate temprano: si el preset exige proyecto y esta pane no tiene
        # ninguno, se pide ANTES de activar nada — el error runtime llegó
        # tarde (tras montar el run) y el usuario lo vivía como fallo.
        if view and view.get("project_required") and not self._current_project_root:
            chosen = self._ask_project_for_workflow(workflow_name)
            if not chosen:
                return False

        self._active_workflow = workflow_name
        self._force_agent = None
        self._selected_agent = None
        self._in_bot_canonical = False
        self._conversation_id = None
        self._set_mode("orchestrate", silent=True)
        self._orchestrate_toggle.setEnabled(True)
        # El título del panel de actividad describe el tipo de run: solo el
        # debate multi-agente merece el header de debate.
        self.debate_section.set_title(
            "💬 Debate entre agentes"
            if workflow_name == "collaborative"
            else "🤖 Actividad de agentes"
        )

        desc = (view or {}).get("description", "")
        self._on_system(f"Workflow activated: **{workflow_name}**\n{desc}")
        return True

    def _ask_project_for_workflow(self, workflow_name: str) -> str | None:
        """Diálogo de selección/creación de proyecto previo al lanzamiento.

        Retorna el nombre elegido o None si el usuario canceló."""

        from PySide6.QtWidgets import (
            QDialog,
            QHBoxLayout,
            QInputDialog,
            QLabel,
            QListWidget,
            QPushButton,
            QVBoxLayout,
        )

        from core.workspaces import get_global_workspaces
        from desktop.services.project_service import (
            create_project,
            normalize_project_name,
            projects_base,
        )

        def _project_names() -> list[str]:
            base = projects_base(get_global_workspaces().current)
            if not base.exists():
                return []
            return sorted(
                p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")
            )

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Proyecto para '{workflow_name}'")
        dlg.setModal(True)
        layout = QVBoxLayout(dlg)
        layout.addWidget(
            QLabel(
                f"El workflow **{workflow_name}** requiere un proyecto seleccionado.\n"
                "Elige uno o crea uno nuevo:"
            )
        )
        listw = QListWidget(dlg)
        for name in _project_names():
            listw.addItem(name)
        layout.addWidget(listw)

        ok_btn = QPushButton("Usar proyecto")
        ok_btn.setEnabled(False)
        cancel_btn = QPushButton("Cancelar")
        new_btn = QPushButton("Crear nuevo…")

        def _refresh_list(select: str | None = None) -> None:
            listw.clear()
            for name in _project_names():
                listw.addItem(name)
            if select:
                for item in listw.findItems(select, Qt.MatchFlag.MatchExactly):
                    listw.setCurrentItem(item)
                    break

        def _on_new() -> None:
            raw, accepted = QInputDialog.getText(dlg, "Nuevo proyecto", "Nombre del proyecto:")
            if not accepted:
                return
            name = normalize_project_name(raw)
            if not name:
                return
            created, msg = create_project(name)
            if created:
                _refresh_list(name)
            else:
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.warning(dlg, "Nuevo proyecto", msg)

        def _sync_ok() -> None:
            ok_btn.setEnabled(listw.currentItem() is not None)

        listw.currentItemChanged.connect(lambda *_: _sync_ok())
        listw.itemDoubleClicked.connect(lambda *_: dlg.accept())
        ok_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        new_btn.clicked.connect(_on_new)

        btns = QHBoxLayout()
        btns.addWidget(new_btn)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        layout.addLayout(btns)

        listw.setStyleSheet("QListWidget { min-height: 160px; min-width: 280px; }")
        if listw.count():
            listw.setCurrentRow(0)
            _sync_ok()

        if dlg.exec() != QDialog.DialogCode.Accepted or listw.currentItem() is None:
            return None
        chosen = listw.currentItem().text()
        self._switch_project(chosen)
        return chosen

    def _on_workspace_switch(self, ws_name: str):
        """Refresh agent panel when workspace changes from dashboard."""
        self._force_agent = None
        self._selected_agent = None
        self._set_mode(self._mode)  # Refresh agent panel for current mode
        self._refresh_detail_tabs_for_workflow()

    def _refresh_all(self) -> None:
        """⟳: recarga combos, detalle y los chats abiertos sin reabrir la app.

        Ignorado con ejecución en curso (los combos alimentan runs — cambiarlos
        a mitad de ejecución sería inconsistente; recargar un chat en vivo
        destruiría el streaming).
        """
        if self.is_workflow_running():
            self._on_system("⚠️ Ejecución en curso — el refresco se ignora.")
            return
        self._set_mode(self._mode, silent=True)
        self._on_system("⟳ Workflows, agentes y detalle recargados.")
        self.refresh_requested.emit()

    def _refresh_detail_tabs_for_workflow(self):
        from core.workspaces import get_global_workspaces
        from desktop.panels.detail_panel import update_tabs_for_workflow
        from desktop.services.workflow_view import load_workflow_view

        # Chat con agente forzado: visibilidad de Bash dictada por el perfil del agente
        agent_tools: list[str] | None = None
        if self._force_agent:
            profile = agents_registry.get_profile(self._force_agent)
            if profile and profile.get("tools"):
                from tools.specs import expand_allowed_tools

                agent_tools = expand_allowed_tools(profile.get("tools", [])) or []
            else:
                agent_tools = None

        view = load_workflow_view(get_global_workspaces().current, self._resolve_active_workflow())
        allowed = view.get("tools_allowed") if view else None
        update_tabs_for_workflow(self, allowed, agent_tools=agent_tools)

    def launch_agent(self, agent_name: str):
        """Llamado desde el Dashboard al hacer clic en una card de agente."""
        self._abandon_pause()  # defense-in-depth (ver launch_workflow)
        normalized = agent_name.lower()
        self._force_agent = normalized
        self._selected_agent = normalized
        self._in_bot_canonical = False
        self._conversation_id = None
        self._set_mode("chat", silent=True)
        self._orchestrate_toggle.setEnabled(False)

        self._on_system(f"Conversación directa con: **{agent_name}**")

    # ── Qt signal callbacks ──

    def _on_system(self, msg: str):
        if "[bash_manager]" in msg:
            self.bash_panel.set_output(msg[-3000:])
        self._append_status(msg)
        if msg.startswith("❌"):
            self._show_status_banner(msg, "error")

    def _show_status_banner(self, text: str, kind: str = "info"):
        self._status_banner.setText(text)
        self._status_banner.setStyleSheet(StyleFactory.status_banner(kind))
        self._status_banner.setVisible(True)

    def _hide_status_banner(self):
        self._status_banner.setVisible(False)

    def _on_assistant(self, msg: str):
        self._add_bubble(msg, "assistant")

    async def _runner_on_system(self, msg: str):
        self._append_status(msg)
        if msg.startswith("❌"):
            self._show_status_banner(msg, "error")

    def _has_streaming(self) -> bool:
        return bool(self._streaming_text.strip()) or self._streaming_bubble is not None

    async def _runner_on_assistant(self, msg: str):
        streaming_text = self._streaming_text
        had_streaming = self._streaming_bubble is not None
        had_content = bool(streaming_text.strip())

        if had_streaming and had_content:
            self._history.append({"role": "assistant", "content": streaming_text.strip()})
        elif msg and msg.strip():
            self._on_assistant(msg)
        # render inmediato del último tramo — sin esto el timer debounced
        # tardaba hasta ~70ms en pintar el final del stream.
        if self._streaming_bubble is not None:
            self._streaming_bubble.flush_stream()
        self._streaming_bubble = None
        self._streaming_text = ""

    async def _on_runner_pause(self, session, question: str):
        self._paused_session = session
        self.input_field.setPlaceholderText(f"Responde: {question[:60]}...")
        self._show_status_banner(f"⏸️ {question}", "warning")
        self._update_pause_affordance()
        self._set_workflow_running(False)
        self._set_activity_state(False)
        self._hide_typing()

    def _update_pause_affordance(self) -> None:
        """El botón 'Abandonar pausa' existe SOLO mientras hay pausa armada."""
        btn = getattr(self, "_abandon_pause_btn", None)
        if btn is not None:
            btn.setVisible(self._paused_session is not None)

    def _abandon_pause(self) -> None:
        """Descarta la pausa de clarificación de forma explícita.

        La fila PausedSession persiste en BD (solo se limpia el estado local):
        recargar la conversación la recupera (recovery).
        """
        if self._paused_session is None:
            return
        self._paused_session = None
        self._hide_status_banner()
        self._update_pause_affordance()
        self.input_field.setPlaceholderText("Escribe tu mensaje...")
        self._on_system(
            "⏸→ Pausa abandonada — la fila queda en BD; recargar la " "conversación la recupera."
        )

    def _on_user(self, msg: str):
        self._add_bubble(msg, "user")

    def _on_agent_message(self, agent_name: str, label: str, text: str):
        # Store with agent metadata for export, and formatted content for DB
        content = f"[{agent_name.capitalize()} - {label}]\n{text}"
        self._history.append(
            {"role": "agent", "agent": agent_name, "label": label, "content": content}
        )

    def _on_agent_stream(self, agent_name: str, label: str, chunk: str):
        # Ensure debate section is visible in the chat
        if not self.debate_section.isVisible():
            self._add_debate_section()
        self.debate_section.append_chunk(agent_name, chunk, label)

    def _on_agent_status(self, agent_name: str, status: str):
        self.debate_section.set_status(agent_name, status)

    def _add_debate_section(self):
        """Insert the debate section into the chat layout before the stretch."""
        self._idle_note.setVisible(False)
        idx = self.chat_layout.count() - 1  # before stretch
        self.chat_layout.insertWidget(idx, self.debate_section)
        self.debate_section.show()
        self.chat_container.adjustSize()
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _append_status(self, msg: str, color: str = COLORS["text_dim"]):
        """Append a line to the status log (O(1) — no full-document reparse)."""
        # hora LOCAL — la GUI debe coincidir con el reloj del sistema
        timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
        entry = (
            f"<span style='color:{color}; font-size:12px;'>"
            f"<span style='color:{COLORS['text_timestamp']}'>{timestamp}</span>  {msg}</span>"
        )
        if not self._status_log_started:
            self.status_log.clear()
            self._status_log_started = True
        self.status_log.append(entry)

    def _on_stream(self, text: str):
        if self._streaming_bubble is None:
            self._hide_typing()
            self._streaming_text = ""
            self._streaming_bubble = self._add_bubble("", "assistant")
        self._streaming_text += text
        self._streaming_bubble.update_text(self._streaming_text)
        if not self._scroll_pending:
            self._scroll_pending = True
            QTimer.singleShot(100, self._throttled_scroll)

    @staticmethod
    def _is_activity_running(data: dict) -> bool:
        """Detección de workflow vivo por prefijo terminal del status real."""
        status = str(data.get("status") or "").strip().lower()
        if status:
            return not status.startswith(_ACTIVITY_TERMINAL_PREFIXES)
        return bool(data.get("subtasks_total"))

    def _set_activity_state(self, running: bool) -> None:
        """Chip ●/○ de la cabecera ACTIVIDAD + visibilidad del estado vacío."""
        color = ThemeManager.current().colors.status_running if running else COLORS["text_dim"]
        self._activity_dot.setText("●" if running else "○")
        self._activity_dot.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._activity_dot.setToolTip(
            "Workflow en ejecución" if running else "Sin ejecución activa"
        )

    def _on_stats(self, data: dict):
        # emits en vuelo tras ⏹/fin (create_task del runtime
        # DSL) no deben re-encender indicadores ni chips después del cierre.
        with self._workflow_running_lock:
            if not self._workflow_running:
                return
        self._set_activity_state(self._is_activity_running(data))

        self.stat_chips.update_from_stats(data)

        # presupuesto de tokens visible — en la sesión
        # se agotó en TODOS los runs (80-223k) sin que la GUI
        # dijera nada; al cruzarse, las tools quedan bloqueadas y los
        # subtareas degradan en silencio.
        self._maybe_warn_token_budget(data)

        # Barra de progreso
        total = data.get("subtasks_total", 0)
        completed = data.get("subtasks_completed", 0)
        if total and total > 0:
            pct = int(completed / total * 100)
            if pct != self._last_progress:
                self._progress_bar.setValue(pct)
                self._progress_bar.setFormat(f"{completed}/{total} subtareas")
                self._last_progress = pct

        # Subtareas: expandir la sección en cuanto haya pasos reales
        # (TDD/DSL emiten 1 solo path — sin esto la sección queda colapsada)
        subtask_list = data.get("subtask_list")
        if subtask_list is not None and subtask_list != self._last_subtasks:
            self._last_subtasks = list(subtask_list)
            if subtask_list:
                self._subtask_section.set_collapsed(False)
            self._subtask_list.clear()
            for item in subtask_list:
                name = item.get("name", "")
                status = item.get("status", "pending")
                icon = _SUBTASK_GLYPHS.get(status, "○")
                self._subtask_list.addItem(f"{icon}  {name}")

        # Archivos: expandir sección si hay archivos
        files_written = data.get("files_written")
        if isinstance(files_written, list) and files_written and files_written != self._last_files:
            self._last_files = list(files_written)
            self._files_section.set_collapsed(False)
            self._files_written_list.clear()
            for f in files_written:
                self._files_written_list.addItem(f)

        # Diagrama derivado localmente (reemplaza _on_diagram)
        self._diagram_view.update_from_stats(data)

    # ── Chat ──

    def _maybe_warn_token_budget(self, data: dict):
        """Banner ⚠️ al cruzar el 80% del presupuesto (una vez por run)."""
        if self._budget_warned:
            return
        try:
            used = int(data.get("tokens_used") or 0)
        except (TypeError, ValueError):
            return
        if used <= 0:
            return
        try:
            from core.config import settings

            budget = int(settings.tool_max_tokens_per_workflow)
            if not bool(settings.tool_enable_token_budget):
                return
        except Exception:
            return
        if budget > 0 and used >= int(budget * 0.8):
            self._budget_warned = True
            pct = min(100, int(used / budget * 100))
            self._show_status_banner(
                f"⚠️ Presupuesto de tokens al {pct}% ({used:,}/{budget:,}). "
                "Al agotarse las tools quedan bloqueadas.",
                "warning",
            )

    def _add_bubble(self, text: str, role: str):
        from desktop.widgets.chat_bubble import ChatBlock

        # La nota de sesión vacía vive solo hasta que aparece el primer contenido.
        self._idle_note.setVisible(False)
        bubble = ChatBlock(text, role)
        self.chat_layout.addWidget(bubble)
        self.chat_container.adjustSize()
        QTimer.singleShot(50, self._scroll_to_bottom)
        if role == "system" and self._is_internal_message(text):
            return None
        if text.strip() or role == "system":
            self._history.append({"role": role, "content": text})
        return bubble

    @staticmethod
    def _is_internal_message(text: str) -> bool:
        internal = (
            "[bash_manager]",
            "Eres Morphix",
            "Reglas anti-frustración",
            "Mantén siempre esta identidad",
            "Soy Morphix, un asistente experto",
        )
        return any(p in text for p in internal)

    def _show_typing(self):
        if self._typing_label is None:
            self._idle_note.setVisible(False)
            self._typing_label = QLabel("Generando")
            self._typing_label.setStyleSheet(
                f"color: {COLORS['text_secondary']}; font-style: italic; padding: 8px;"
            )
            self.chat_layout.addWidget(self._typing_label)
            self.chat_container.adjustSize()
            self._typing_dots = 0
        if hasattr(self, "_typing_timer") and self._typing_timer is not None:
            self._typing_timer.stop()
        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._animate_typing)
        self._typing_timer.start(400)
        if hasattr(self, "_stop_btn") and self._stop_btn is not None:
            self._stop_btn.setVisible(True)

    def _animate_typing(self):
        if self._typing_label is None:
            return
        self._typing_dots = (self._typing_dots + 1) % 4
        self._typing_label.setText("Generando" + "." * self._typing_dots)

    def _hide_typing(self):
        if self._typing_label is not None:
            if self._typing_timer:
                self._typing_timer.stop()
            self.chat_layout.removeWidget(self._typing_label)
            self._typing_label.deleteLater()
            self._typing_label = None
            self.chat_container.adjustSize()
        if hasattr(self, "_stop_btn") and self._stop_btn is not None:
            self._stop_btn.setVisible(False)

    def _stop_workflow(self):
        """Detiene el workflow en curso de ESTA sesión (cancelación de task)."""
        future = self._current_future
        if future is not None and not future.done():
            self._on_system("⏹ Deteniendo ejecución…")
            future.cancel()
        self._current_future = None

    def clear_chat(self, force: bool = False):
        """Limpia el chat de la sesión.

        Sin ``force`` rechaza limpiar mientras hay un workflow en ejecución
        (destruiría streaming/history en vivo y rompería la persistencia del
        run en ``_after_workflow``). ``force=True`` es para callers que ya
        validaron el estado.
        """
        with self._workflow_running_lock:
            if self._workflow_running and not force:
                self._on_system("⚠️ Ejecución en curso — detén (⏹) o espera.")
                return
        # limpiar el chat desarma cualquier pausa armada (acción explícita
        # sobre esta pane — el banner oculto no debe dejar estado ciego).
        had_pause = self._paused_session is not None
        self._paused_session = None
        self._update_pause_affordance()
        self._hide_typing()
        self._hide_status_banner()
        # Remove debate section from layout without deleting the widget
        debate_idx = None
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            if item and item.widget() is self.debate_section:
                debate_idx = i
                break
        if debate_idx is not None:
            self.chat_layout.takeAt(debate_idx)
            self.debate_section.hide()
        # Clear remaining chat widgets (la nota de sesión vacía se conserva
        # como widget — se re-inserta y muestra al quedar el chat vacío).
        while self.chat_layout.count() > 0:
            item = self.chat_layout.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None and w is not self._idle_note:
                w.deleteLater()
        self._idle_note.setVisible(True)
        self.chat_layout.insertWidget(0, self._idle_note)
        self._history.clear()
        self._streaming_bubble = None
        self._streaming_text = ""
        self._bot_canonical_slug = None
        self._update_info_button()
        self.chat_container.adjustSize()
        self.debate_section.clear()
        self._subtask_list.clear()
        self._files_written_list.clear()
        self._last_progress = -1
        self._last_subtasks = None
        self._last_files = None
        self._status_log_started = False
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("—")
        self.stat_chips.reset()
        self._subtask_section.set_collapsed(True)
        self._files_section.set_collapsed(True)
        self.status_log.setHtml(
            f"<p style='color:{COLORS['text_dim']}; text-align:center'>Listo. Envía una consulta</p>"
        )
        if had_pause:
            self._on_system("⏸→ Pausa abandonada junto con el chat.")
        self._on_system("Chat limpiado")

    def _new_conversation(self):
        # con un workflow en ejecución NO se limpia nada — se destruiría
        # el streaming en vivo y la persistencia del run en curso.
        with self._workflow_running_lock:
            if self._workflow_running:
                self._on_system("⚠️ Ejecución en curso — detén (⏹) o espera.")
                return
        # Bot Mode: dentro del chat eterno del bot, 'nueva conversación'
        # está PROHIBIDA — ofrecer compactar es la única vía.
        from desktop.services.bots_service import new_conversation_guard

        allowed, guard_msg = new_conversation_guard(bool(getattr(self, "_in_bot_canonical", False)))
        if not allowed:
            self._on_system(f"🔒 {guard_msg}")
            return
        self.clear_chat(force=True)
        self._conversation_id = None
        self._in_bot_canonical = False
        self._current_project_root = None
        self._project_btn.setText("▤ — sin proyecto")
        self._project_btn.setStyleSheet(project_button_style(False))
        from desktop.events import get_signals

        get_signals().project_changed.emit("")
        self._on_system("✨ Nueva conversación iniciada")

    async def load_conversation(self, conv_id: int):
        """Load all messages from a conversation and prepare to continue it."""
        from core.repositories.conversation_repository import ConversationRepository

        # Bot Mode: conocer si entramos a un chat eterno del bot.
        try:
            _meta = await ConversationRepository.get_conversation(conv_id)
            self._in_bot_canonical = bool((_meta or {}).get("is_canonical"))
        except Exception:
            self._in_bot_canonical = False
        # ⓘ Bot: slug del bot propietario para la ventana de info.
        self._bot_canonical_slug = None
        if self._in_bot_canonical:
            try:
                from core.bots_chat import canonical_owner_of

                owner = await canonical_owner_of(conv_id)
                self._bot_canonical_slug = (owner or {}).get("slug")
            except Exception:
                self._bot_canonical_slug = None
        self._update_info_button()

        try:
            messages = await ConversationRepository.get_messages(conv_id)
            if not messages:
                self._on_system(f"⚠️ Conversación #{conv_id} no tiene mensajes")
                return

            self.clear_chat()
            for m in messages:
                role = m["role"]
                content = m["content"]
                if role in ("user", "assistant", "system", "agent", "tool"):
                    self._add_bubble(content, role)

            self._conversation_id = conv_id
            self._on_system(f"📖 Conversación #{conv_id} cargada ({len(messages)} mensajes)")

            # recovery — pausa de clarificación sin resolver en BD se
            # re-arma aquí: tras cerrar la pane o reiniciar la app, la pausa
            # sigue respondible (antes solo era posible en la misma sesión).
            if self._paused_session is None:
                try:
                    from core.database import bound_schema
                    from core.workspaces import get_global_workspaces
                    from orchestration.workflows.orchestrator import WorkflowOrchestrator

                    async with bound_schema(get_global_workspaces().current):
                        paused_row = await WorkflowOrchestrator.get_unresolved_pause(conv_id)
                except Exception:
                    logger.warning("Búsqueda de pausa sin resolver falló", exc_info=True)
                    paused_row = None
                if paused_row is not None:
                    self._rearm_paused_session(conv_id, paused_row)
        except Exception as e:
            logger.error(f"Error loading conversation #{conv_id}: {e}", exc_info=True)
            self._on_system(f"❌ Error al cargar conversación #{conv_id}: {e}")

    def _rearm_paused_session(self, conv_id: int, paused_row: dict) -> None:
        """Reconstruye el Session mínimo que `resume_workflow` exige.

        Cada ruta de resume (development/coordinated/tdd/pipeline) lee su
        estado del `paused_state` persistido; del ctx solo necesita
        conversation_id/workspace/cancelled/query-fallback — suficiente con la
        reconstrucción mínima verificada en T7.0.
        """
        import json as _json

        from core.workspaces import get_global_workspaces
        from desktop.events import build_workflow_events
        from orchestration.context import Session, WorkflowContext

        raw = paused_row.get("paused_state") or ""
        try:
            paused_data = _json.loads(raw) if isinstance(raw, str) and raw else {}
        except Exception:
            logger.warning("paused_state corrupto en recovery", exc_info=True)
            paused_data = {}
        question = str(paused_row.get("question") or "¿Podrías clarificar?")

        ctx = WorkflowContext(
            query=str(paused_data.get("query", "")),
            mode="orchestrate",
            workspace=get_global_workspaces().current,
            conversation_id=conv_id,
            is_follow_up=True,
        )
        ctx.last_clarification = question
        session = Session(
            context=ctx, events=build_workflow_events(signals=self._own_events_signals())
        )
        self._paused_session = session
        self.input_field.setPlaceholderText(f"Responde: {question[:60]}...")
        self._show_status_banner(f"⏸️ Pausa activa: {question}", "warning")
        self._update_pause_affordance()
        self._on_system(
            "⏸️ Esta conversación tiene una pausa de clarificación sin resolver "
            "— responde o abandónala."
        )

    def _scroll_to_bottom(self):
        sb = self.chat_scroll.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _throttled_scroll(self):
        self._scroll_pending = False
        self._scroll_to_bottom()

    # ── Actions ──

    def _attach_pdf(self):
        """📎 Adjuntar PDF vía diálogo de archivo y cargarlo en background."""
        path, _ = QFileDialog.getOpenFileName(self, "Adjuntar PDF", "", "PDF (*.pdf)")
        if not path:
            return
        self.pdf_path_field.setText(path)
        try:
            # Carrera QThread: si el worker previo sigue vivo, Qt lo destruiría
            # en ejecución al reasignar (qFatal). Esperarlo antes de reemplazar.
            old = getattr(self, "_pdf_worker", None)
            if old is not None and old.isRunning():
                old.wait()

            from PySide6.QtCore import QThread
            from PySide6.QtCore import Signal as QSignal

            from tools.pdf_reader import PDFReader

            class _PdfWorker(QThread):
                done = QSignal(str, str)

                def __init__(self, pdf_path):
                    super().__init__()
                    self._path = pdf_path

                def run(self):
                    try:
                        text = PDFReader.read_pdf(self._path)
                        self.done.emit(text, "")
                    except Exception as e:
                        self.done.emit("", str(e))

            self._pdf_worker = _PdfWorker(path)
            self._pdf_worker.done.connect(self._on_pdf_loaded)
            self._pdf_worker.start()
            self._on_system(f"📄 Cargando PDF: {os.path.basename(path)}...")
        except Exception as e:
            logger.debug(f"Error cargando PDF: {e}", exc_info=True)
            self._on_system(f"❌ Error cargando PDF: {e}")

    def _on_pdf_loaded(self, text, error):
        if error:
            self._on_system(f"❌ Error cargando PDF: {error}")
            return
        if text and not text.startswith("Error"):
            self._current_pdf_text = text
            path = self.pdf_path_field.text().strip()
            self._on_system(f"📄 PDF cargado ({len(text)} caracteres): {os.path.basename(path)}")
        else:
            self._on_system(f"❌ {text}")

    def _download_conversation(self, fmt: str | None = None):
        if not self._history:
            return
        with self._workflow_running_lock:
            if self._workflow_running:
                self._on_system("⚠️ Espera a que termine el workflow antes de exportar.")
                return

        fmt = fmt or "md"
        from core.path_resolver import paths

        exports_dir = paths.exports_dir()
        exports_dir.mkdir(parents=True, exist_ok=True)

        # If we have a conversation_id, delegate to repository
        if self._conversation_id is not None:
            run_async(self._export_via_repository(self._conversation_id, fmt))
            return

        # No conversation_id — write from in-memory history
        export_ts = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")

        try:
            filename = str(exports_dir / f"morphix_conversacion_nueva_{export_ts}.{fmt}")
            run_async(self._write_history_export(fmt, filename))
        except Exception as e:
            logger.error(f"Error guardando conversación: {e}", exc_info=True)
            self._on_system(f"❌ Error al guardar: {e}")

    async def _write_history_export(self, fmt: str, filename: str):
        """Escribe el export desde el history en memoria vía conversation_export."""
        from desktop.services.conversation_export import export_history_to_file

        try:
            saved = await export_history_to_file(self._history, filename, fmt)
            self._on_system(f"✅ Exportado: **{saved}**")
        except Exception as e:
            logger.error(f"Error guardando conversación: {e}", exc_info=True)
            self._on_system(f"❌ Error al guardar: {e}")

    async def _export_via_repository(self, conv_id: int, fmt: str):
        """Export a saved conversation via the repository."""
        from core.path_resolver import paths
        from core.repositories.conversation_repository import ConversationRepository

        project_path = None
        if self._current_project_root:
            proj_dir = paths.memory_dir(active_workspace()) / self._current_project_root
            if proj_dir.exists():
                project_path = str(proj_dir)
        filename = await ConversationRepository.export(conv_id, fmt, project_path=project_path)
        if filename:
            self._on_system(f"✅ Exportado: **{filename}**")
        else:
            self._on_system(f"❌ Error al exportar conversación #{conv_id}")

    def _apply_mode_styles(self, chat_active: bool | None = None) -> None:
        """Segmentado Chat/Orquestar.

        Un bloque: sin bordes intermedios ni caja contenedora; el activo
        lleva fondo claro y el inactivo gris, radio solo en el extremo
        exterior de cada mitad (posición fija: Chat=izq, Orquestar=der).
        Sin argumento deriva del modo actual.
        """
        if chat_active is None:
            chat_active = getattr(self, "_mode", "chat") == "chat"

        def _seg(active: bool, side: str) -> str:
            radius = (
                "border-top-left-radius: 6px; border-bottom-left-radius: 6px;"
                if side == "left"
                else "border-top-right-radius: 6px; border-bottom-right-radius: 6px;"
            )
            if active:
                return (
                    f"QPushButton {{ background: {COLORS['text_primary']}; "
                    f"color: {COLORS['bg_deepest']}; border: none; "
                    f"padding: 3px 12px; font-size: 11px; font-weight: bold; {radius} }}"
                )
            return (
                f"QPushButton {{ background: {COLORS['bg_surface']}; "
                f"color: {COLORS['text_dim']}; border: none; "
                f"padding: 3px 12px; font-size: 11px; {radius} }}"
            )

        self._chat_toggle.setStyleSheet(_seg(chat_active, "left"))
        self._orchestrate_toggle.setStyleSheet(_seg(not chat_active, "right"))

    def _update_info_button(self) -> None:
        """Texto del botón ⓘ según contexto: Bot > Workflow (orquestar) > Agente."""
        if not hasattr(self, "_info_btn"):
            return
        if self._in_bot_canonical and self._bot_canonical_slug:
            label = "ⓘ Bot"
        elif self._mode == "orchestrate":
            label = "ⓘ Workflow"
        else:
            label = "ⓘ Agente"
        self._info_btn.setText(label)

    def _open_info_dialog(self) -> None:
        """Abre la ventana de info de la entidad activa (no-modal)."""
        from core.workspaces import get_global_workspaces

        ws = get_global_workspaces().current
        parent = self.window()
        if self._in_bot_canonical and self._bot_canonical_slug:
            run_async(self._show_bot_info(self._bot_canonical_slug, parent))
            return
        if self._mode == "orchestrate":
            self._show_workflow_info(ws, parent)
            return
        self._show_agent_info(parent)

    def _show_workflow_info(self, ws: str, parent) -> None:
        from desktop.widgets.entity_info_dialog import EntityInfoDialog

        name = self._resolve_active_workflow()
        dlg = EntityInfoDialog.for_workflow(parent, ws, name)
        if dlg is None:
            self._on_system(f"⚠️ No hay info del workflow '{name}'")
            return
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _show_agent_info(self, parent) -> None:
        from desktop.widgets.entity_info_dialog import EntityInfoDialog

        name = self._force_agent or self._selected_agent or "conversacional"
        profile = agents_registry.get_profile(name)
        if not profile:
            self._on_system(f"⚠️ Sin perfil del agente '{name}'")
            return
        dlg = EntityInfoDialog.for_agent(parent, name, profile)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    async def _show_bot_info(self, slug: str, parent) -> None:
        from core.bots import BotsService
        from desktop.widgets.entity_info_dialog import EntityInfoDialog

        bot = await BotsService.get_bot(slug)
        if not bot:
            self._on_system(f"⚠️ Sin info del bot '{slug}'")
            return
        dlg = EntityInfoDialog.for_bot(parent, bot)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _set_mode(self, mode: str, silent: bool = False):
        # cambiar de modo a mitad de ejecución resetea _conversation_id y
        # desincroniza el run en curso — bloqueado (el refresco del MISMO modo
        # desde workspace switch sigue permitido).
        with self._workflow_running_lock:
            if self._workflow_running and mode != self._mode:
                self._on_system("⚠️ Ejecución en curso — detén (⏹) o espera.")
                return
        previous_mode = self._mode
        self._mode = mode
        if mode != previous_mode:
            self._conversation_id = None  # reset on mode switch
            self._bot_canonical_slug = None
            # Bot Mode: cambiar de modo es una decisión explícita de
            # contexto — salir del chat canónico de un bot. Sin esto, _in_bot_canonical
            # quedaba pegado y cualquier agente/workflow se enrutaba como modo bot
            # (síntoma: "no puedo usar un agente directo ni un workflow").
            # Re-abrir el chat del bot lo restaura vía load_conversation.
            self._in_bot_canonical = False
        if mode == "chat":
            self._apply_mode_styles(chat_active=True)
        else:
            self._apply_mode_styles(chat_active=False)

        if hasattr(self, "_workflow_combo"):
            self._populate_workflow_combo()

        # Paridad de modos (en Orquestar el selector de
        # agente NO tiene efecto (el workflow reparte por subtarea) → oculto.
        # La info del equipo vive en ⓘ Workflow → EntityInfoDialog.
        chat_ui_visible = mode == "chat"
        self._agent_label.setVisible(chat_ui_visible)
        self._agent_combo.setVisible(chat_ui_visible)

        # Panel de agentes: dictado por _force_agent o por el modo
        if mode == "chat":
            if self._force_agent:
                self._populate_agents([self._force_agent])
            else:
                self._populate_agents(None)

        self._update_agent_detail()
        self._update_info_button()
        self._refresh_detail_tabs_for_workflow()

        # Show message when entering chat mode
        if mode == "chat" and not silent:
            agent = self._force_agent or "conversacional"
            if self._force_agent:
                self._on_system(f"Conversación directa con: **{agent.capitalize()}**")
            else:
                self._on_system(
                    f"Conversación directa con: **{agent.capitalize()}** "
                    "(por defecto — selecciona un agente)"
                )

        # Reset agent forcing + show message when entering orchestrate mode
        if mode == "orchestrate" and not silent:
            self._force_agent = None
            self._selected_agent = None
            self._on_system(
                "⚙️ Modo Orquestar activado — el sistema elegirá el mejor agente por tarea"
            )

    def _preload_project(self):
        if not self._current_project_root:
            self._on_system("❌ Selecciona un proyecto primero")
            return
        self._preload_action.setEnabled(False)
        self.input_field.setEnabled(False)
        self._preload_progress.setVisible(True)
        self._preload_progress.setValue(0)
        self._preload_status.setText("⏳ Indexando...")
        run_async(self._do_preload())

    async def _do_preload(self):
        from asyncio import CancelledError

        from core.codebase_indexer import CodebaseIndexer
        from desktop.events import get_signals

        indexer = CodebaseIndexer(
            workspace=settings.active_workspace, project_root=self._current_project_root
        )

        def _on_progress(data: dict):
            try:
                get_signals().indexing_progress.emit(data)
            except Exception:
                logger.warning("Unhandled exception in MaestroTab", exc_info=True)

        try:
            chunks = await asyncio.to_thread(
                indexer.index_project, force=True, progress_callback=_on_progress
            )
        except CancelledError:
            return  # app cerrada durante indexing, ignorar

        self._preload_action.setEnabled(True)
        self.input_field.setEnabled(True)
        self._preload_progress.setVisible(False)
        self._preload_status.setText(f"✅ {chunks} chunks en FAISS")

    def _on_indexing_progress(self, data: dict):
        pct = data.get("pct", 0)
        self._preload_progress.setValue(pct)
        self._preload_status.setText(
            f"⏳ {data.get('current_file', '')} ({data.get('files_scanned', 0)} archivos)"
        )

    def _switch_project(self, name: str):
        if not name:
            return
        root = f"{PROJECTS_DIR_NAME}/{name}"
        self._current_project_root = root
        self._update_project_display(name)
        self._on_system(f"✅ Cambiado a proyecto '{name}'.")
        self._preload_status.setText("")
        self._refresh_branch_btn()

    def _update_project_display(self, name: str):
        self._project_btn.setText(f"▤ {name}")
        self._project_btn.setStyleSheet(project_button_style(True))
        from desktop.events import get_signals

        get_signals().project_changed.emit(self._current_project_root or "")
        self._refresh_branch_btn()

    # ── Selector de rama git (v1: locales, dirty bloqueado, sin crear) ──

    def _refresh_branch_btn(self) -> None:
        """Pobla/oculta el botón ⑂ según el proyecto activo."""
        from core.config import settings
        from desktop.services import git_service

        async def _go() -> dict | None:
            return await git_service.list_branches(
                settings.active_workspace, self._current_project_root
            )

        def _done(fut) -> None:
            try:
                exc = fut.exception()
            except Exception:  # future cancelado
                return
            if exc is not None:
                logger.warning("list_branches falló: %s", exc)
                self._branch_btn.setVisible(False)
                return
            self._render_branch_menu(fut.result())

        run_async(_go()).add_done_callback(_done)

    def _render_branch_menu(self, data: dict | None) -> None:
        from desktop.panels.top_bar import project_button_style

        if not data or not data.get("current"):
            self._branch_btn.setVisible(False)
            return
        current = str(data["current"])
        local = [str(b) for b in data.get("local") or []]
        self._branch_btn.setText(f"⑂ {current}")
        self._branch_btn.setStyleSheet(project_button_style(True))
        menu = QMenu(self._branch_btn)
        if len(local) > 1:
            for branch in local:
                label = f"✓  {branch}" if branch == current else f"    {branch}"
                act = menu.addAction(label)
                if branch != current:
                    act.triggered.connect(
                        lambda checked=False, b=branch: self._on_branch_clicked(b)
                    )
                else:
                    act.setEnabled(False)
        menu.addSeparator()
        info = menu.addAction("Rama local del proyecto (v1: solo lectura)")
        info.setEnabled(False)
        # El menú es un snapshot: una rama creada fuera de la app no aparece
        # hasta refrescar. Acción explícita de re-consulta.
        refresh_act = menu.addAction("⟳  Refrescar ramas")
        refresh_act.triggered.connect(self._refresh_branch_btn)
        self._branch_btn.setMenu(menu)
        self._branch_btn.setVisible(True)

    def _on_branch_clicked(self, branch: str) -> None:
        from core.config import settings
        from desktop.services import git_service

        async def _go() -> tuple[bool, str]:
            return await git_service.checkout_branch(
                settings.active_workspace, self._current_project_root, branch
            )

        def _done(fut) -> None:
            try:
                exc = fut.exception()
            except Exception:  # future cancelado
                return
            if exc is not None:
                logger.warning("checkout de rama falló: %s", exc)
                self._on_system(f"❌ Checkout falló: {exc}")
                return
            ok, msg = fut.result()
            self._on_system(msg)
            if ok:
                self._branch_btn.setText(f"⑂ {branch}")
                self._refresh_branch_btn()

        run_async(_go()).add_done_callback(_done)

    def _uses_direct_agent_route(self) -> bool:
        """True solo en modo 'chat' y fuera de un chat canónico de bot.

        Bot Mode: un chat canónico de bot NUNCA usa la ruta de
        chat directo — debe pasar por la ruta de workflow donde se aplica la
        identidad del bot y el schema send_to_bot. En ese caso
        esta función devuelve False y send_message cae por la rama de workflow.
        """
        return self._mode == "chat" and not self._in_bot_canonical

    def send_message(self):
        if self._paused_session is not None:
            answer = self.input_field.toPlainText().strip()
            if not answer:
                return
            self._add_bubble(answer, "user")
            self.input_field.clear()
            self._hide_status_banner()
            self._show_typing()
            self._streaming_bubble = None
            self._streaming_text = ""
            session = self._paused_session
            self._paused_session = None
            self._update_pause_affordance()
            self._current_future = run_async(self._resume_workflow(session, answer))
            return

        with self._workflow_running_lock:
            if self._workflow_running:
                # feedback visible — antes el envío se ignoraba en silencio
                # y parecía que el mensaje se perdía.
                self._on_system("⏳ Ejecución en curso — espera o detén (⏹).")
                return
        query = self.input_field.toPlainText().strip()
        if not query:
            return
        self._hide_status_banner()

        # Guard: Orquestar requiere proyecto (excepto workflows que no lo necesitan)
        if self._mode == "orchestrate" and not self._current_project_root:
            from core.workspaces import get_global_workspaces
            from desktop.services.workflow_view import load_workflow_view

            view = load_workflow_view(
                get_global_workspaces().current, self._resolve_active_workflow()
            )
            project_required = bool(view and view.get("project_required"))
            # legacy: collaborative histórico no exige proyecto (paridad de
            # comportamiento pre-DSL); DSL: decide project.required del doc
            is_legacy_collaborative = bool(
                view and not view.get("dsl") and view["raw"].get("type") == "collaborative"
            )
            if project_required and not is_legacy_collaborative:
                self._on_system(
                    "❌ Modo Orquestar requiere un proyecto. Selecciónalo desde el Dashboard → Proyectos."
                )
                self.input_field.clear()
                return

        # Chat mode: always direct conversation with an agent.
        # EXCEPCIÓN Bot Mode: un chat canónico de bot NUNCA usa la
        # ruta de chat directo (_run_direct_agent no aplica bot_context ni ofrece
        # send_to_bot → el DM entre bots muere en silencio). Debe caer por la
        # ruta de workflow (abajo) donde _dispatch_route / _run_simple_conversation
        # inyectan la identidad del bot y el schema send_to_bot.
        if self._uses_direct_agent_route():
            agent = self._force_agent or "conversacional"
            self._set_workflow_running(True)
            self._add_bubble(query, "user")
            self.input_field.clear()
            self._show_typing()
            self._streaming_bubble = None
            self._streaming_text = ""
            self._current_future = run_async(self._run_direct_agent(query, agent))
            return

        self._set_workflow_running(True)
        self._add_bubble(query, "user")
        self.input_field.clear()
        self._show_typing()
        self._streaming_bubble = None
        self._streaming_text = ""

        enc = get_encoding()

        from core.workspaces import get_global_workspaces
        from orchestration.context import Session

        ctx = WorkflowContext(
            query=query,
            mode=self._mode,
            conversation_history=list(self._history),
            current_pdf_text=self._current_pdf_text,
            workspace=get_global_workspaces().current,
            project_root=self._current_project_root,
            active_workflow=self._resolve_active_workflow(),
            force_agent=self._force_agent,
            settings=settings,
            agents_registry=agents_registry,
            enc=enc,
            conversation_id=self._conversation_id,
            is_follow_up=self._conversation_id is not None,
        )

        from desktop.events import build_workflow_events

        events = build_workflow_events(signals=self._own_events_signals())
        session = Session(context=ctx, events=events)

        self._current_future = run_async(self._run_workflow(session))

    async def _run_workflow(self, session):
        try:
            await self._runner.run(session)
            await self._after_workflow(session)
        finally:
            self._hide_typing()
            self._set_workflow_running(False)

    async def _after_workflow(self, session):
        """Persistencia post-workflow: project_root, conversation_id y mensajes agent/tool."""
        ctx = session.context

        if ctx.project_root:
            self._current_project_root = ctx.project_root

        # Track conversation_id for follow-up messages in same session
        # leemos el conv_id real propagado por finalize_workflow
        # (ContextVar por-run) en vez de "la última conversación del workspace"
        # (race que cruza conversaciones entre sesiones concurrentes).
        if self._conversation_id is None:
            try:
                from orchestration.finalizer import get_finalized_conversation_id

                self._conversation_id = get_finalized_conversation_id()
            except Exception:
                logger.warning("Unhandled exception in MaestroTab", exc_info=True)

        # Persist agent/tool messages to DB (these arrive during workflow
        # execution via emit_agent and are in self._history but NOT in
        # the conversation_history snapshot passed to finalize_workflow).
        if self._conversation_id is not None:
            try:
                # Find agent/tool entries added to history during workflow
                snapshot_len = len(ctx.conversation_history)
                new_entries = self._history[snapshot_len:]
                agent_tool_entries = [m for m in new_entries if m.get("role") in ("agent", "tool")]
                if agent_tool_entries:
                    from core.repositories.conversation_repository import ConversationRepository

                    await ConversationRepository.add_messages(
                        self._conversation_id, agent_tool_entries
                    )
            except Exception:
                logger.warning("Unhandled exception in MaestroTab", exc_info=True)

    async def _resume_workflow(self, session, answer: str):
        """Reanuda un workflow pausado tras recibir respuesta de clarificación."""
        try:
            await self._runner.resume(session, answer)
            await self._after_workflow(session)
        finally:
            self._hide_typing()
            self._set_workflow_running(False)
            self.input_field.setPlaceholderText("Escribe tu mensaje...")

    async def _run_direct_agent(self, query: str, agent: str | None = None):
        """Ejecuta conversación directa 1:1 con un agente (con function-calling nativo)."""
        agent = agent or self._force_agent or "conversacional"
        from core.workspaces import get_global_workspaces
        from desktop.events import build_workflow_events
        from orchestration.context import Session, WorkflowContext

        # Events so bash/system/stats reach the GUI also in chat mode.
        events = build_workflow_events(signals=self._own_events_signals())
        current_history = list(self._history)
        session = Session(
            context=WorkflowContext(
                query=query,
                mode="chat",
                workspace=get_global_workspaces().current,
                conversation_history=current_history,
                project_root=self._current_project_root,
            ),
            events=events,
        )
        try:
            response = await self._runner.run_direct_agent(session, query, agent)

            # Persist conversation + perfil (comportamiento original conservado)
            final_output = (response or "").strip()
            if final_output:
                try:
                    from core.repositories.conversation_repository import ConversationRepository

                    messages_to_save = list(current_history)
                    messages_to_save.append({"role": "assistant", "content": final_output.strip()})

                    conv_id = await ConversationRepository.save(
                        title=query[:100],
                        user_message=query,
                        tags="chat",
                        workflow_id=None,
                        conversation_history=messages_to_save,
                        conversation_id=self._conversation_id,
                    )
                    if self._conversation_id is None:
                        self._conversation_id = conv_id
                    logger.info(f"Chat guardado: conversation_id={conv_id}")
                except Exception as e:
                    logger.warning(f"Error saving chat conversation: {e}")

                try:
                    from core.memory.manager import memory as memory_manager
                    from orchestration.finalizer import (
                        _extract_personal_facts,
                    )

                    facts = await _extract_personal_facts(final_output, query)
                    if facts:
                        await memory_manager.update_user_profile(facts)
                        logger.info(f"Perfil actualizado: {list(facts.keys())}")
                except Exception:
                    logger.warning("Unhandled exception in MaestroTab", exc_info=True)
        except Exception as e:
            logger.error(f"Error en agente directo: {e}", exc_info=True)
            self._on_system(f"❌ Error: {e}")
        finally:
            self._hide_typing()
            self._set_workflow_running(False)


class MaestroTab(QWidget):
    """Contenedor de sesiones Maestro (sub-pestañas a demanda).

    Multi-sesión: N paneles SessionPane en un QTabWidget. La barra de
    pestañas se oculta con 1 sola sesión. Enruta las entradas externas
    (Dashboard cards, Historial, Bots, proyecto, lanzador) según las reglas de
    enrutamiento acordadas — nunca sobrescribe una sesión ocupada.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._panes: list[SessionPane] = []
        self._labels: list[str] = []
        self._next_session_num = 1

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.tabCloseRequested.connect(self._on_close_requested)
        self._tabs.currentChanged.connect(self._on_current_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._tabs)

        self._tabs.tabBar().setVisible(False)
        self.add_session()
        self._sync_tab_bar()

    # ── Sesiones ──

    @property
    def session_count(self) -> int:
        return len(self._panes)

    def _max_sessions(self) -> int:
        try:
            return max(1, int(getattr(settings, "maestro_max_sessions", 4)))
        except (TypeError, ValueError):
            return 4

    def _sync_tab_bar(self) -> None:
        self._tabs.tabBar().setVisible(len(self._panes) > 1)

    def _active_pane(self) -> SessionPane | None:
        w = self._tabs.currentWidget()
        return w if isinstance(w, SessionPane) else None

    def _idle_pane(self) -> SessionPane | None:
        for pane in self._panes:
            # una pane con pausa armada NO está libre — una entrada nueva
            # ahí sería consumida como respuesta de la clarificación pendiente.
            # tampoco una pane con load_conversation en curso.
            if (
                not pane.is_workflow_running()
                and not pane.has_pending_pause()
                and not pane.busy_loading
            ):
                return pane
        return None

    def _reload_open_conversations(self) -> int:
        """Re-carga desde BD el chat de cada pane con conversación abierta.

        Los turnos entregados externamente (wake de bots, rutinas) persisten
        sin que la GUI emita nada — antes solo aparecían al cerrar y reabrir
        la pestaña. Devuelve cuántas panes se recargaron."""
        from desktop.async_helpers import run_async

        n = 0
        for pane in self._panes:
            conv_id = pane._conversation_id
            if (
                conv_id is None
                or pane.is_workflow_running()
                or pane.has_pending_pause()
                or pane.busy_loading
            ):
                continue
            pane.busy_loading = True
            try:
                run_async(pane.load_conversation(conv_id))
                n += 1
            finally:
                pane.busy_loading = False
        return n

    def add_session(self) -> SessionPane:
        if len(self._panes) >= self._max_sessions():
            existing = self._idle_pane() or self._active_pane()
            if existing is not None:
                self._tabs.setCurrentWidget(existing)
                return existing
        pane = SessionPane()
        label = f"Sesión {self._next_session_num}"
        self._next_session_num += 1
        self._panes.append(pane)
        self._labels.append(label)
        # cada transición running→idle del pane refresca los indicadores
        # de sub-pestaña (señal Qt — marshallea cross-thread si hace falta).
        pane.running_changed.connect(self._update_indicators)
        pane.refresh_requested.connect(self._reload_open_conversations)
        self._tabs.addTab(pane, label)
        self._tabs.setCurrentWidget(pane)
        self._sync_tab_bar()
        return pane

    def _on_close_requested(self, index: int) -> None:
        pane = self._tabs.widget(index)
        if isinstance(pane, SessionPane) and pane.is_workflow_running():
            pane._on_system("⚠️ Sesión ocupada — detén la ejecución antes de cerrar.")
            return
        if len(self._panes) <= 1:
            return
        self._remove_session(index)

    def _remove_session(self, index: int) -> None:
        pane = self._tabs.widget(index)
        self._tabs.removeTab(index)
        if pane in self._panes:
            self._panes.remove(pane)
        # colateral: _labels debe seguir alineado con _panes para que
        # _update_indicators escriba el título correcto por índice.
        if index < len(self._labels):
            self._labels.pop(index)
        if getattr(pane, "_guard_token", None) is not None:
            try:
                from core.workspaces import get_global_workspaces

                get_global_workspaces().remove_switch_guard(pane._guard_token)
            except Exception:  # pragma: no cover
                pass
        pane.deleteLater()
        self._sync_tab_bar()
        self._update_indicators()

    def _on_current_changed(self, _index: int) -> None:
        self._sync_tab_bar()
        active = self._active_pane()
        if active is not None:
            from desktop.events import get_signals

            get_signals().project_changed.emit(active._current_project_root or "")

    def _update_indicators(self) -> None:
        for i, pane in enumerate(self._panes):
            suffix = " ●" if pane.is_workflow_running() else ""
            self._tabs.setTabText(i, f"{self._labels[i]}{suffix}")

    def forget_conversation(self, conv_id: int) -> None:
        """Una conversación eliminada desde Historial deja de ser
        válida — las panes que la tenían cargada vuelven a conversación nueva
        (si no, el próximo save fallaría silenciosamente en el repo)."""
        for pane in self._panes:
            if pane._conversation_id == conv_id:
                pane._conversation_id = None
                pane._on_system(
                    "🗑 Conversación eliminada — el chat continuará como conversación nueva."
                )

    # ── Enrutamiento de entradas ──

    def _new_or_idle(self) -> SessionPane | None:
        """Pane para una entrada nueva: NUEVA si hay capacidad; al tope,
        primera idle CON aviso; ``None`` si todas están ocupadas (el llamador
        avisa y no pisa nada — jamás se toca una sesión en ejecución)."""
        if len(self._panes) < self._max_sessions():
            return self.add_session()
        idle = self._idle_pane()
        if idle is not None:
            self._tabs.setCurrentWidget(idle)
            idle._on_system("⚠️ Tope de sesiones: se reutiliza esta sesión libre.")
            return idle
        return None

    def _notify_no_free_session(self) -> None:
        active = self._active_pane()
        if active is not None:
            active._on_system(
                "⚠️ Todas las sesiones están ocupadas — detén, responde la pausa "
                "o abandónala antes de continuar."
            )

    def launch_workflow(self, workflow_name: str) -> bool:
        """Enruta una card de workflow. False = ninguna sesión disponible o
        el usuario canceló el pedido de proyecto (el llamador decide cómo
        avisar; nunca se pisa una ocupada)."""
        pane = self._new_or_idle()
        if pane is None:
            self._notify_no_free_session()
            return False
        if pane.launch_workflow(workflow_name) is False:
            return False
        self._tabs.setCurrentWidget(pane)
        return True

    def launch_agent(self, agent_name: str) -> bool:
        pane = self._new_or_idle()
        if pane is None:
            self._notify_no_free_session()
            return False
        pane.launch_agent(agent_name)
        self._tabs.setCurrentWidget(pane)
        return True

    def set_pending_prompt(self, text: str) -> bool:
        """Pregunta directa del lanzador → pane nueva/idle (nunca una ocupada).

        Retorna False si no hay sesión disponible — el texto NUNCA se
        descarta silenciosamente.
        """
        pane = self._new_or_idle()
        if pane is None:
            self._notify_no_free_session()
            return False
        pane.set_pending_prompt(text)
        self._tabs.setCurrentWidget(pane)
        return True

    def switch_project(self, name: str, reset_conversation: bool = False) -> None:
        """Selección de proyecto → "vacía absorbe, sino nueva".

        Si la pane activa está FRESCA (sin conversación, sin proyecto y sin
        workflow corriendo) el proyecto se le aplica; si tiene contenido se
        abre una pane nueva dedicada. Al tope, primera idle con aviso; todas
        ocupadas → aviso sin pisar nada.
        """
        if not name:
            return
        active = self._active_pane()
        pane: SessionPane | None
        if (
            active is not None
            and not active.is_workflow_running()
            and not active.has_pending_pause()
            and not active.busy_loading
            and active._conversation_id is None
            and active._current_project_root is None
        ):
            pane = active
        else:
            pane = self._new_or_idle()
            if pane is None:
                self._notify_no_free_session()
                return
        if reset_conversation:
            pane._conversation_id = None
        pane._switch_project(name)
        self._tabs.setCurrentWidget(pane)

    def _switch_project(self, name: str) -> None:
        """Backward-compat alias (dashboard)."""
        self.switch_project(name)

    async def load_conversation(self, conv_id: int) -> None:
        """Abre una conversación (Bots/Historial): foco a la pane que ya la
        tiene; sino pane NUEVA si hay capacidad; al tope, primera idle con
        aviso; todas ocupadas → aviso sin pisar nada.

        Durante el await la pane queda RESERVADA (``busy_loading``)
        y con el conv_id provisional asignado — dedupe de dobles clics y sin
        reutilización por otras entradas concurrentes.
        """
        for open_pane in self._panes:
            if open_pane._conversation_id == conv_id:
                self._tabs.setCurrentWidget(open_pane)
                return
        pane = self._new_or_idle()
        if pane is None:
            self._notify_no_free_session()
            return
        pane.busy_loading = True
        pane._conversation_id = conv_id  # provisional: dedupe inmediato
        try:
            await pane.load_conversation(conv_id)
        finally:
            pane.busy_loading = False
        self._tabs.setCurrentWidget(pane)

    @property
    def _current_project_root(self) -> str | None:
        active = self._active_pane()
        return active._current_project_root if active is not None else None

    def is_workflow_running(self) -> bool:
        return any(pane.is_workflow_running() for pane in self._panes)
