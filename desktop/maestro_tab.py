"""Maestro Tab — chat, streaming, diagrama, agentes, y stats."""

import asyncio
import logging
import os
import threading
from datetime import UTC, datetime

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agents.registry import agents_registry
from core.config import settings
from core.constants import PROJECTS_DIR_NAME
from desktop.services.project_service import active_workspace
from orchestration.context import WorkflowContext

logger = logging.getLogger(__name__)

from core.token_counter import get_encoding
from desktop.async_helpers import run_async
from desktop.services.workflow_runner import WorkflowRunner
from desktop.widgets.collapsible_section import CollapsibleSection
from desktop.widgets.phase_cards import PhaseCards
from desktop.widgets.stat_chips import StatChips


class MaestroTab(QWidget):
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
        self._paused_session: Session | None = None
        self._scroll_pending = False
        self._current_project_root: str | None = None
        self._mode: str = "chat"
        self._conversation_id: int | None = None

        # Perf: differential-update caches to avoid redundant widget writes
        self._last_progress: int = -1
        self._last_subtasks: list | None = None
        self._last_files: list | None = None
        self._status_log_started: bool = False

        # Widgets set by panel builders (declared for mypy)
        self._toggle_style_active: str = ""
        self._toggle_style_inactive: str = ""
        self.mode_label: QLabel
        self.ws_label: QLabel
        self._chat_toggle: QPushButton
        self._orchestrate_toggle: QPushButton
        self._project_label: QLabel
        self._project_combo: QComboBox
        self._new_proj_btn: QPushButton
        self._import_proj_btn: QPushButton
        self._agent_combo: QComboBox
        self._preload_btn: QPushButton
        self._preload_status: QLabel
        self._preload_progress: QProgressBar
        self.offline_btn: QPushButton
        self.download_btn: QPushButton
        self.download_format: QComboBox
        self._new_conv_btn: QPushButton
        self.chat_scroll: QScrollArea
        self.chat_container: QWidget
        self.chat_layout: QVBoxLayout
        self.input_field: QTextEdit
        self.pdf_path_field: QLineEdit
        self.pdf_load_btn: QPushButton
        self.send_btn: QPushButton
        self._status_banner: QLabel
        self._workflow_label: QLabel
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
        self._current_pdf_text: str = ""

        self._build_ui()
        self._connect_maestro()

        self._runner = WorkflowRunner()
        self._runner.on_system = self._runner_on_system
        self._runner.on_assistant = self._runner_on_assistant
        self._runner.on_pause = lambda s, q: self._on_runner_pause(s, q)
        self._runner.streaming_check = self._has_streaming

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

        self._files_written_list.itemDoubleClicked.connect(self._open_file_in_editor)

    def _open_file_in_editor(self, item):
        """Doble clic en 'Archivos creados' → abre el archivo en editor_tab."""
        from desktop.events import get_signals

        name = item.text().strip()
        if name:
            get_signals().open_file_requested.emit(name)

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
                self.chat_container.setFixedWidth(w)
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
        # In chat mode: activate agent for direct conversation
        if self._mode == "chat":
            self._force_agent = name
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

    def _connect_maestro(self):
        from desktop.events import get_signals

        signals = get_signals()
        signals.system_message.connect(self._on_system)
        signals.assistant_message.connect(self._on_assistant)
        signals.agent_message.connect(self._on_agent_message)
        signals.agent_stream.connect(self._on_agent_stream)
        signals.agent_status.connect(self._on_agent_status)
        signals.user_message.connect(self._on_user)
        signals.stream_chunk.connect(self._on_stream)
        signals.stats_update.connect(self._on_stats)
        signals.workspace_changed.connect(self._on_workspace_switch)
        signals.offline_changed.connect(lambda offline: self._refresh_offline_indicator())
        signals.indexing_progress.connect(self._on_indexing_progress)

    # ── Public methods for Dashboard ──

    def launch_workflow(self, workflow_name: str):
        """Called from Dashboard when a workflow card is clicked."""
        from core.workflow_state import set_active_workflow
        from core.workspaces import get_global_workspaces
        from orchestration.loader import load_workflow_template

        set_active_workflow(workflow_name)
        ws = get_global_workspaces().current
        template = load_workflow_template(workspace_name=ws, workflow_name=workflow_name)
        self._force_agent = None
        self._selected_agent = None
        self._set_mode("orchestrate", silent=True)
        self._orchestrate_toggle.setEnabled(True)

        desc = template.get("description", "") if template else ""
        self._on_system(f"Workflow activated: **{workflow_name}**\n{desc}")

    def _update_workflow_label(self):
        from core.workflow_state import get_active_workflow

        self._workflow_label.setText(f"workflow: {get_active_workflow()}")

    def _on_workspace_switch(self, ws_name: str):
        """Refresh agent panel when workspace changes from dashboard."""
        self.ws_label.setText(ws_name)
        self._force_agent = None
        self._selected_agent = None
        self._set_mode(self._mode)  # Refresh agent panel for current mode
        self._refresh_detail_tabs_for_workflow()

    def _refresh_detail_tabs_for_workflow(self):
        from core.workflow_state import get_active_workflow
        from core.workspaces import get_global_workspaces
        from desktop.panels.detail_panel import update_tabs_for_workflow
        from orchestration.loader import load_workflow_template

        # Chat con agente forzado: visibilidad de Bash dictada por el perfil del agente
        agent_tools: list[str] | None = None
        if self._force_agent:
            profile = agents_registry.get_profile(self._force_agent)
            if profile and profile.get("tools"):
                from tools.specs import expand_allowed_tools

                agent_tools = expand_allowed_tools(profile.get("tools", [])) or []
            else:
                agent_tools = None

        try:
            template = load_workflow_template(
                workspace_name=get_global_workspaces().current,
                workflow_name=get_active_workflow(),
            )
        except Exception:
            template = None

        allowed = template.get("tools", {}).get("allowed") if template else None
        update_tabs_for_workflow(self, allowed, agent_tools=agent_tools)

    def launch_agent(self, agent_name: str):
        """Llamado desde el Dashboard al hacer clic en una card de agente."""
        normalized = agent_name.lower()
        self._force_agent = normalized
        self._selected_agent = normalized
        self._set_mode("chat", silent=True)
        self._orchestrate_toggle.setEnabled(False)

        self._on_system(f"Conversación directa con: **{agent_name}**")

    # ── Qt signal callbacks ──

    def _on_system(self, msg: str):
        if "[bash_manager]" in msg:
            self.bash_panel.set_output(msg[-3000:])
        self._append_status(msg, "#888888")
        if msg.startswith("❌"):
            self._show_status_banner(msg, "error")

    def _show_status_banner(self, text: str, kind: str = "info"):
        colors = {"info": "#3B82F6", "error": "#EF4444", "warning": "#F59E0B"}
        self._status_banner.setText(text)
        self._status_banner.setStyleSheet(
            f"QLabel {{ background: {colors.get(kind, colors['info'])}; color: white;"
            f" border-radius: 6px; padding: 6px 10px; font-size: 12px; }}"
        )
        self._status_banner.setVisible(True)

    def _hide_status_banner(self):
        self._status_banner.setVisible(False)

    def _on_assistant(self, msg: str):
        self._add_bubble(msg, "assistant")

    async def _runner_on_system(self, msg: str):
        self._append_status(msg, "#888888")
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
        self._streaming_bubble = None
        self._streaming_text = ""

    async def _on_runner_pause(self, session, question: str):
        self._paused_session = session
        self.input_field.setPlaceholderText(f"Responde: {question[:60]}...")
        self._show_status_banner(f"⏸️ {question}", "warning")
        with self._workflow_running_lock:
            self._workflow_running = False
        self._hide_typing()

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
        self.debate_section.append_chunk(agent_name, chunk)

    def _on_agent_status(self, agent_name: str, status: str):
        self.debate_section.set_status(agent_name, status)

    def _add_debate_section(self):
        """Insert the debate section into the chat layout before the stretch."""
        idx = self.chat_layout.count() - 1  # before stretch
        self.chat_layout.insertWidget(idx, self.debate_section)
        self.debate_section.show()
        self.chat_container.adjustSize()
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _append_status(self, msg: str, color: str = "#888888"):
        """Append a line to the status log (O(1) — no full-document reparse)."""
        timestamp = datetime.now(UTC).strftime("%H:%M:%S")
        entry = (
            f"<span style='color:{color}; font-size:12px;'>"
            f"<span style='color:#555'>{timestamp}</span>  {msg}</span>"
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

    def _on_stats(self, data: dict):
        self.stat_chips.update_from_stats(data)

        # Barra de progreso
        total = data.get("subtasks_total", 0)
        completed = data.get("subtasks_completed", 0)
        if total and total > 0:
            pct = int(completed / total * 100)
            if pct != self._last_progress:
                self._progress_bar.setValue(pct)
                self._progress_bar.setFormat(f"{completed}/{total} subtareas")
                self._last_progress = pct

        # Subtareas: expandir sección si hay pasos reales (>1 ítem)
        subtask_list = data.get("subtask_list")
        if subtask_list is not None and subtask_list != self._last_subtasks:
            self._last_subtasks = list(subtask_list)
            if len(subtask_list) > 1:
                self._subtask_section.set_collapsed(False)
            self._subtask_list.clear()
            for item in subtask_list:
                name = item.get("name", "")
                status = item.get("status", "pending")
                icon = {"completed": "✅", "running": "🔵", "failed": "❌", "pending": "⏳"}.get(
                    status, "⏳"
                )
                self._subtask_list.addItem(f"{icon}  {name}")

        # Archivos: expandir sección si hay archivos
        files_written = data.get("files_written")
        if isinstance(files_written, list) and files_written and files_written != self._last_files:
            self._last_files = list(files_written)
            self._files_section.set_collapsed(False)
            self._files_written_list.clear()
            for f in files_written:
                self._files_written_list.addItem(f"  {f}")

        # Diagrama derivado localmente (reemplaza _on_diagram)
        self._diagram_view.update_from_stats(data)

    # ── Chat ──

    def _add_bubble(self, text: str, role: str):
        from desktop.widgets.chat_bubble import ChatBlock

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
            self._typing_label = QLabel("Generando")
            self._typing_label.setStyleSheet("color: #A0A0A0; font-style: italic; padding: 8px;")
            self.chat_layout.addWidget(self._typing_label)
            self.chat_container.adjustSize()
            self._typing_dots = 0
        if hasattr(self, "_typing_timer") and self._typing_timer is not None:
            self._typing_timer.stop()
        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._animate_typing)
        self._typing_timer.start(400)

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

    def clear_chat(self):
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
        # Clear remaining chat widgets
        while self.chat_layout.count() > 0:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._history.clear()
        self._streaming_bubble = None
        self._streaming_text = ""
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
            "<p style='color:#888; text-align:center'>Listo. Envía una consulta</p>"
        )
        self._on_system("Chat limpiado")

    def _new_conversation(self):
        self.clear_chat()
        self._conversation_id = None
        self._current_project_root = None
        self._project_label.setText("Proyecto: —")
        self._project_label.setStyleSheet("color: #A0A0A0; font-size: 10px; padding: 2px 4px;")
        self._project_combo.blockSignals(True)
        self._project_combo.setCurrentIndex(0)
        self._project_combo.blockSignals(False)
        from desktop.events import get_signals

        get_signals().project_changed.emit("")
        self._on_system("✨ Nueva conversación iniciada")

    async def load_conversation(self, conv_id: int):
        """Load all messages from a conversation and prepare to continue it."""
        from core.repositories.conversation_repository import ConversationRepository

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
        except Exception as e:
            logger.error(f"Error loading conversation #{conv_id}: {e}", exc_info=True)
            self._on_system(f"❌ Error al cargar conversación #{conv_id}: {e}")

    def _scroll_to_bottom(self):
        sb = self.chat_scroll.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _throttled_scroll(self):
        self._scroll_pending = False
        self._scroll_to_bottom()

    # ── Actions ──

    def _toggle_offline(self):
        from desktop.services.config_service import ConfigService

        ConfigService.toggle_offline_mode()
        self._refresh_offline_indicator()
        from desktop.events import get_signals

        get_signals().offline_changed.emit(settings.offline_mode)

    def _refresh_offline_indicator(self):
        """Actualiza los indicadores locales de modo offline."""
        is_off = settings.offline_mode
        self.offline_btn.setText("Desactivar Offline" if is_off else "Activar Offline")
        self.mode_label.setText("Offline" if is_off else "Online")
        self.mode_label.setStyleSheet(
            f"color: {'#F59E0B' if is_off else '#22C55E'}; font-size: 11px; font-weight: bold;"
        )

    def _load_pdf(self):
        path = self.pdf_path_field.text().strip()
        if not path:
            return
        try:
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

    def _download_conversation(self):
        if not self._history:
            return
        with self._workflow_running_lock:
            if self._workflow_running:
                self._on_system("⚠️ Espera a que termine el workflow antes de exportar.")
                return

        fmt = self.download_format.currentText()
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

    def _set_mode(self, mode: str, silent: bool = False):
        previous_mode = self._mode
        self._mode = mode
        if mode != previous_mode:
            self._conversation_id = None  # reset on mode switch
        if mode == "chat":
            self._chat_toggle.setStyleSheet(self._toggle_style_active)
            self._orchestrate_toggle.setStyleSheet(self._toggle_style_inactive)
        else:
            self._chat_toggle.setStyleSheet(self._toggle_style_inactive)
            self._orchestrate_toggle.setStyleSheet(self._toggle_style_active)

        # Panel de agentes: dictado por _force_agent o por el modo
        if self._force_agent:
            self._populate_agents([self._force_agent])
        elif mode == "orchestrate":
            from core.workflow_state import get_active_workflow
            from core.workspaces import get_global_workspaces
            from orchestration.loader import load_workflow_template

            ws = get_global_workspaces().current
            template = load_workflow_template(
                workspace_name=ws, workflow_name=get_active_workflow()
            )
            allowed = template.get("agents", {}).get("allowed") if template else None
            self._populate_agents(allowed)
        else:
            self._populate_agents(None)

        self._update_agent_detail()
        self._update_workflow_label()
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

    def _create_project(self):
        from PySide6.QtWidgets import QInputDialog

        from desktop.services.project_service import (
            create_project,
            normalize_project_name,
        )

        name, ok = QInputDialog.getText(self, "Nuevo proyecto", "Nombre del proyecto:", text="")
        if not ok or not name:
            return
        name = normalize_project_name(name)
        if not name:
            self._on_system("❌ Nombre inválido. Usa solo letras, números y _")
            return
        ok, root = create_project(name)
        if not ok:
            self._on_system(f"❌ Error creando proyecto: {root}")
            return
        self._current_project_root = root
        self._conversation_id = None
        self._update_project_display(name)
        self._refresh_project_list()
        self._on_system(f"✅ Proyecto '{name}' creado y activado.")
        self._preload_btn.setEnabled(True)
        self._preload_status.setText("")
        if self._mode == "chat":
            self._set_mode("orchestrate")
            self._on_system("⚙️ Modo cambiado a Orquestar automáticamente.")

    def _import_project(self):
        from pathlib import Path

        from desktop.services.project_service import normalize_project_name, project_dir

        src = QFileDialog.getExistingDirectory(self, "Seleccionar proyecto para importar")
        if not src:
            return

        src_path = Path(src)
        name = normalize_project_name(src_path.name)
        if not name:
            self._on_system("❌ Nombre de proyecto inválido. Usa solo letras, números y _")
            return
        dst = project_dir(name)

        if dst.exists():
            self._on_system(f"❌ Ya existe un proyecto llamado '{name}'")
            return

        self._on_system(f"📂 Copiando '{src_path.name}' → {PROJECTS_DIR_NAME}/{name}...")

        from PySide6.QtCore import QThread
        from PySide6.QtCore import Signal as QSignal

        from desktop.services.project_service import import_project

        class _CopyWorker(QThread):
            done = QSignal(bool, str)

            def __init__(self, src, name):
                super().__init__()
                self._src = src
                self._name = name

            def run(self):
                ok, message = import_project(self._src, self._name)
                self.done.emit(ok, message)

        self._copy_worker = _CopyWorker(str(src_path), name)
        self._copy_worker.done.connect(self._on_import_done)
        self._copy_worker.start()

    def _on_import_done(self, success, error):
        if success:
            self._on_system(f"✅ Proyecto importado: {error}")
            self._refresh_project_list()
        else:
            logger.warning("Unhandled exception in MaestroTab", exc_info=True)
            self._on_system(f"❌ Error copiando proyecto: {error}")
        self._preload_btn.setEnabled(True)
        self._preload_status.setText("")

    def _preload_project(self):
        if not self._current_project_root:
            self._on_system("❌ Selecciona un proyecto primero")
            return
        self._preload_btn.setEnabled(False)
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

        self._preload_btn.setEnabled(True)
        self.input_field.setEnabled(True)
        self._preload_progress.setVisible(False)
        self._preload_status.setText(f"✅ {chunks} chunks en FAISS")

    def _on_indexing_progress(self, data: dict):
        pct = data.get("pct", 0)
        self._preload_progress.setValue(pct)
        self._preload_status.setText(
            f"⏳ {data.get('current_file', '')} ({data.get('files_scanned', 0)} archivos)"
        )

    def _on_project_combo_changed(self, _index):
        name = self._project_combo.currentData()
        if name:
            self._switch_project(name)
        elif self._current_project_root is not None:
            self._current_project_root = None
            self._project_label.setText("Proyecto: —")
            self._project_label.setStyleSheet("color: #A0A0A0; font-size: 10px; padding: 2px 4px;")
            from desktop.events import get_signals

            get_signals().project_changed.emit("")

    def _switch_project(self, name: str):
        if not name:
            return
        root = f"{PROJECTS_DIR_NAME}/{name}"
        self._current_project_root = root
        self._update_project_display(name)
        self._on_system(f"✅ Cambiado a proyecto '{name}'.")
        self._preload_btn.setEnabled(True)
        self._preload_status.setText("")

    def _update_project_display(self, name: str):
        self._project_label.setText(f"Proyecto: {name}")
        self._project_label.setStyleSheet("color: #22C55E; font-size: 10px; padding: 2px 4px;")
        idx = self._project_combo.findData(name)
        if idx >= 0:
            self._project_combo.blockSignals(True)
            self._project_combo.setCurrentIndex(idx)
            self._project_combo.blockSignals(False)
        from desktop.events import get_signals

        get_signals().project_changed.emit(self._current_project_root or "")

    def _refresh_project_list(self):
        """Escanea code_projects/ y llena el dropdown de proyectos."""
        from desktop.services.project_service import projects_base

        base = projects_base()
        self._project_combo.blockSignals(True)
        self._project_combo.clear()
        self._project_combo.addItem("— sin proyecto —", None)
        if base.exists():
            for d in sorted(base.iterdir()):
                if d.is_dir() and not d.name.startswith("."):
                    self._project_combo.addItem(d.name, d.name)
        # Restore selection to the current project
        if self._current_project_root:
            current_name = (
                self._current_project_root.split("/")[-1]
                if "/" in self._current_project_root
                else self._current_project_root
            )
            idx = self._project_combo.findData(current_name)
            if idx >= 0:
                self._project_combo.setCurrentIndex(idx)
        self._project_combo.blockSignals(False)

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
            run_async(self._resume_workflow(session, answer))
            return

        with self._workflow_running_lock:
            if self._workflow_running:
                return
        query = self.input_field.toPlainText().strip()
        if not query:
            return
        self._hide_status_banner()

        # Guard: Orquestar requiere proyecto (excepto workflows que no lo necesitan)
        if self._mode == "orchestrate" and not self._current_project_root:
            from core.workflow_state import get_active_workflow
            from core.workspaces import get_global_workspaces
            from orchestration.loader import load_workflow_template

            template = load_workflow_template(
                workspace_name=get_global_workspaces().current,
                workflow_name=get_active_workflow(),
            )
            if template.get("type") != "collaborative":
                self._on_system(
                    "❌ Modo Orquestar requiere un proyecto. Crea uno con el botón ➕ Nuevo proyecto."
                )
                self.input_field.clear()
                return

        # Chat mode: always direct conversation with an agent
        if self._mode == "chat":
            agent = self._force_agent or "conversacional"
            with self._workflow_running_lock:
                self._workflow_running = True
            self._add_bubble(query, "user")
            self.input_field.clear()
            self._show_typing()
            self._streaming_bubble = None
            self._streaming_text = ""
            run_async(self._run_direct_agent(query, agent))
            return

        with self._workflow_running_lock:
            self._workflow_running = True
        self._add_bubble(query, "user")
        self.input_field.clear()
        self._show_typing()
        self._streaming_bubble = None
        self._streaming_text = ""

        enc = get_encoding()

        from core.workflow_state import get_active_workflow
        from core.workspaces import get_global_workspaces
        from orchestration.context import Session

        ctx = WorkflowContext(
            query=query,
            mode=self._mode,
            conversation_history=list(self._history),
            current_pdf_text=self._current_pdf_text,
            workspace=get_global_workspaces().current,
            project_root=self._current_project_root,
            active_workflow=get_active_workflow(),
            force_agent=self._force_agent,
            settings=settings,
            agents_registry=agents_registry,
            enc=enc,
            conversation_id=self._conversation_id,
            is_follow_up=self._conversation_id is not None,
        )

        from desktop.events import build_workflow_events

        events = build_workflow_events()
        session = Session(context=ctx, events=events)

        run_async(self._run_workflow(session))

    async def _run_workflow(self, session):
        try:
            await self._runner.run(session)
            await self._after_workflow(session)
        finally:
            self._hide_typing()
            with self._workflow_running_lock:
                self._workflow_running = False

    async def _after_workflow(self, session):
        """Persistencia post-workflow: project_root, conversation_id y mensajes agent/tool."""
        ctx = session.context

        if ctx.project_root:
            self._current_project_root = ctx.project_root

        # Track conversation_id for follow-up messages in same session
        if self._conversation_id is None:
            try:
                from core.repositories.conversation_repository import ConversationRepository

                recent = await ConversationRepository.list_all(limit=1)
                if recent:
                    self._conversation_id = recent[0]["id"]
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
            with self._workflow_running_lock:
                self._workflow_running = False
            self.input_field.setPlaceholderText("Escribe tu mensaje...")

    async def _run_direct_agent(self, query: str, agent: str | None = None):
        """Ejecuta conversación directa 1:1 con un agente (con function-calling nativo)."""
        agent = agent or self._force_agent or "conversacional"
        from core.workspaces import get_global_workspaces
        from desktop.events import build_workflow_events
        from orchestration.context import Session, WorkflowContext

        # Events so bash/system/stats reach the GUI also in chat mode.
        events = build_workflow_events()
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
            with self._workflow_running_lock:
                self._workflow_running = False
