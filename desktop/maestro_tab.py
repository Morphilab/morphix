"""Maestro Tab — chat, streaming, diagrama, agentes, y stats."""

import asyncio
import logging
import os
from datetime import UTC, datetime

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
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
from orchestration.context import WorkflowContext

logger = logging.getLogger(__name__)

from core.token_counter import get_encoding
from desktop.async_helpers import run_async
from desktop.theme import COLORS, StyleFactory


class MaestroTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._streaming_bubble = None
        self._streaming_text = ""
        self._typing_label = None
        self._history: list[dict] = []
        self._selected_agent: str | None = None
        self._force_agent: str | None = None
        self._workflow_running = False
        self._paused_session: Session | None = None
        self._scroll_pending = False
        self._current_project_root: str | None = None
        self._mode: str = "chat"
        self._conversation_id: int | None = None

        # Perf: differential-update caches to avoid redundant widget writes
        self._last_stats: dict[str, str] = {}
        self._last_progress: int = -1
        self._last_subtasks: list | None = None
        self._last_files: list | None = None
        self._last_diagram_html: str | None = None
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
        self.clear_btn: QPushButton
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
        self._detail_tabs: QTabWidget
        self._diagram_view: QTextBrowser
        self._status_log_view: QTextBrowser
        self.status_log: QTextBrowser  # backward-compat alias
        self._subtask_list: QListWidget
        self._files_written_list: QListWidget
        self._progress_bar: QProgressBar
        self.stat_labels: dict = {}
        self._current_pdf_text: str = ""

        self._build_ui()
        self._connect_maestro()

    def _build_ui(self):
        from desktop.panels import (
            build_chat_panel,
            build_detail_panel,
            build_execution_panel,
            build_top_bar,
        )
        from desktop.widgets.agent_panel import AgentPanel
        from desktop.widgets.bash_panel import BashPanel

        self.agent_panel = AgentPanel()
        self.bash_panel = BashPanel()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(build_top_bar(self))

        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setContentsMargins(6, 6, 6, 6)

        execution = build_execution_panel(self)
        execution.setMinimumWidth(200)
        columns.addWidget(execution)

        chat = build_chat_panel(self)
        chat.setMinimumWidth(300)
        columns.addWidget(chat)

        detail = build_detail_panel(self)
        detail.setMinimumWidth(280)
        columns.addWidget(detail)

        columns.setStretchFactor(0, 1)
        columns.setStretchFactor(1, 3)
        columns.setStretchFactor(2, 1)

        root.addWidget(columns, 1)

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

    def _build_stats_panel(self) -> QGroupBox:
        group = QGroupBox("Estado en tiempo real")
        group.setStyleSheet(StyleFactory.group_box())
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("—")
        self._progress_bar.setStyleSheet(StyleFactory.progress_bar())
        layout.addWidget(self._progress_bar)

        self.stat_labels = {}
        for key in ["subtasks_total", "elapsed_time", "tokens_used", "current_agent", "status"]:
            row = QHBoxLayout()
            label = QLabel(key.replace("_", " ").capitalize())
            label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 12px;")
            value = QLabel("—")
            value.setStyleSheet(
                f"color: {COLORS['text_primary']}; font-size: 12px; font-weight: bold;"
            )
            row.addWidget(label)
            row.addStretch()
            row.addWidget(value)
            layout.addLayout(row)
            self.stat_labels[key] = value
        return group

    def _connect_maestro(self):
        from desktop.events import get_signals

        signals = get_signals()
        signals.system_message.connect(self._on_system)
        signals.assistant_message.connect(self._on_assistant)
        signals.agent_message.connect(self._on_agent_message)
        signals.user_message.connect(self._on_user)
        signals.stream_chunk.connect(self._on_stream)
        signals.stats_update.connect(self._on_stats)
        signals.diagram_update.connect(self._on_diagram)
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

    def _on_workspace_switch(self, ws_name: str):
        """Refresh agent panel when workspace changes from dashboard."""
        self.ws_label.setText(ws_name)
        self._force_agent = None
        self._selected_agent = None
        self._set_mode(self._mode)  # Refresh agent panel for current mode

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

    def _on_assistant(self, msg: str):
        self._add_bubble(msg, "assistant")

    def _on_user(self, msg: str):
        self._add_bubble(msg, "user")

    def _on_agent_message(self, agent_name: str, label: str, text: str):
        self.agent_panel.add_response(agent_name, label, text)
        # Store with agent metadata for export, and formatted content for DB
        content = f"[{agent_name.capitalize()} - {label}]\n{text}"
        self._history.append(
            {"role": "agent", "agent": agent_name, "label": label, "content": content}
        )

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
        for key, label in self.stat_labels.items():
            if key not in data:
                continue
            value = str(data[key])
            if key == "subtasks_total":
                completed = data.get("subtasks_completed", 0)
                total = data[key]
                value = f"{completed} / {total}"
                if total > 0:
                    pct = int(completed / total * 100)
                    if pct != self._last_progress:
                        self._progress_bar.setValue(pct)
                        self._progress_bar.setFormat(f"{completed}/{total} subtareas")
                        self._last_progress = pct
            if self._last_stats.get(key) != value:
                label.setText(value)
                self._last_stats[key] = value
                if key == "status":
                    label.setStyleSheet(
                        "color: #22C55E; font-size: 12px; font-weight: bold;"
                        if "completado" in value.lower()
                        else "color: #F59E0B; font-size: 12px; font-weight: bold;"
                    )

        subtask_list = data.get("subtask_list")
        if subtask_list is not None and subtask_list != self._last_subtasks:
            self._last_subtasks = list(subtask_list)
            self._subtask_list.clear()
            for item in subtask_list:
                name = item.get("name", "")
                status = item.get("status", "pending")
                icon = {"completed": "✅", "running": "🔵", "failed": "❌", "pending": "⏳"}.get(
                    status, "⏳"
                )
                self._subtask_list.addItem(f"{icon}  {name}")

        files_written = data.get("files_written")
        if isinstance(files_written, list) and files_written and files_written != self._last_files:
            self._last_files = list(files_written)
            self._files_written_list.clear()
            for f in files_written:
                self._files_written_list.addItem(f"  {f}")

    def _on_diagram(self, html: str, graph=None):
        if html != self._last_diagram_html:
            self._diagram_view.setHtml(html)
            self._last_diagram_html = html

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
        while self.chat_layout.count() > 0:
            item = self.chat_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._history.clear()
        self._streaming_bubble = None
        self._streaming_text = ""
        self.chat_container.adjustSize()
        self.agent_panel.clear()
        self._subtask_list.clear()
        self._files_written_list.clear()
        self._last_stats.clear()
        self._last_progress = -1
        self._last_subtasks = None
        self._last_files = None
        self._last_diagram_html = None
        self._status_log_started = False
        self.status_log.setHtml(
            "<p style='color:#888; text-align:center'>Listo. Envía una consulta</p>"
        )
        self._on_system("Chat limpiado")

    def _new_conversation(self):
        self.clear_chat()
        self._conversation_id = None
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
            from tools.pdf_reader import PDFReader

            text = PDFReader.read_pdf(path)
            if text and not text.startswith("Error"):
                self._current_pdf_text = text
                self._on_system(
                    f"📄 PDF cargado ({len(text)} caracteres): {os.path.basename(path)}"
                )
            else:
                self._on_system(f"❌ {text}")
        except Exception as e:
            logger.debug(f"Error cargando PDF: {e}", exc_info=True)
            self._on_system(f"❌ Error cargando PDF: {e}")

    def _download_conversation(self):
        if not self._history:
            return
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
        internal = (
            "Eres Morphix",
            "Reglas anti-frustración",
            "Mantén siempre esta identidad",
            "Soy Morphix, un asistente experto",
        )

        try:
            filename = str(exports_dir / f"morphix_conversacion_nueva_{export_ts}.{fmt}")

            if fmt == "json":
                import json

                data = [
                    {
                        "role": m.get("role", "?"),
                        "content": m.get("content", ""),
                        "agent": m.get("agent"),
                        "label": m.get("label"),
                    }
                    for m in self._history
                    if not (
                        m.get("role") == "system"
                        and any(p in m.get("content", "") for p in internal)
                    )
                ]
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                self._on_system(f"✅ Exportado: **{filename}**")

            elif fmt == "md":
                with open(filename, "w", encoding="utf-8") as f:
                    f.write("# Conversación Morphix\n")
                    f.write(
                        f"**Fecha:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"
                    )
                    for msg in self._history:
                        role = msg.get("role", "?")
                        content = msg.get("content", "")
                        if role == "system" and any(p in content for p in internal):
                            continue
                        if role == "assistant":
                            f.write(f"**🤖 Maestro:**\n{content}\n\n---\n\n")
                        elif role == "user":
                            f.write(f"**👤 Usuario:**\n{content}\n\n---\n\n")
                        elif role == "agent":
                            agent = msg.get("agent", "agente")
                            label = msg.get("label", "")
                            f.write(f"**🧠 {agent.capitalize()} ({label}):**\n{content}\n\n---\n\n")
                        elif role == "tool":
                            f.write(f"**🔧 Herramienta:**\n{content}\n\n---\n\n")
                        else:
                            f.write(f"**⚙️ {role}:**\n{content}\n\n---\n\n")
                self._on_system(f"✅ Guardado: **{filename}**")

            elif fmt == "pdf":
                from reportlab.lib.pagesizes import letter
                from reportlab.lib.styles import getSampleStyleSheet
                from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

                data = [
                    m
                    for m in self._history
                    if not (
                        m.get("role") == "system"
                        and any(p in m.get("content", "") for p in internal)
                    )
                ]

                doc = SimpleDocTemplate(filename, pagesize=letter)
                styles = getSampleStyleSheet()
                story = []
                story.append(Paragraph("Conversación Morphix", styles["Title"]))
                story.append(Spacer(1, 12))
                for msg in data:
                    role = msg.get("role", "?")
                    content = msg.get("content", "")
                    label = {
                        "assistant": "🤖 Maestro",
                        "user": "👤 Usuario",
                        "agent": f"🧠 {msg.get('agent', 'agente').capitalize()}",
                        "tool": "🔧 Herramienta",
                    }.get(role, f"⚙️ {role}")
                    story.append(Paragraph(f"<b>{label}:</b> {content}", styles["Normal"]))
                    story.append(Spacer(1, 12))
                doc.build(story)

            elif fmt == "html":
                from html import escape

                try:
                    from pygments import highlight
                    from pygments.formatters import HtmlFormatter
                    from pygments.lexers import get_lexer_by_name, guess_lexer
                    from pygments.util import ClassNotFound

                    formatter = HtmlFormatter(style="default", noclasses=True)

                    def _hl_code(text: str) -> str:
                        import re

                        def _repl(m):
                            lang = m.group(1) or "python"
                            code = m.group(2)
                            try:
                                lexer = get_lexer_by_name(lang, stripall=True)
                            except ClassNotFound:
                                try:
                                    lexer = guess_lexer(code)
                                except ClassNotFound:
                                    lexer = get_lexer_by_name("text")
                            return highlight(code, lexer, formatter)

                        return re.sub(r"```(\w*)\n(.*?)```", _repl, text, flags=re.DOTALL)

                except ImportError:

                    def _hl_code(text: str) -> str:
                        return f"<pre><code>{escape(text)}</code></pre>"

                with open(filename, "w", encoding="utf-8") as f:
                    f.write('<!DOCTYPE html>\n<html lang="es">\n<head>\n')
                    f.write('<meta charset="utf-8">\n')
                    f.write("<title>Conversación Morphix</title>\n")
                    f.write("<style>")
                    f.write(
                        "body{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;"
                        "padding:20px;background:#fafafa;color:#222}"
                        "h1{color:#333;border-bottom:2px solid #ddd;padding-bottom:8px}"
                        ".msg{margin:12px 0;padding:12px;border-radius:6px;background:#fff;"
                        "box-shadow:0 1px 3px rgba(0,0,0,.1)}"
                        ".role{font-weight:bold;font-size:.9em;color:#555}"
                        ".content{margin-top:6px;line-height:1.5}"
                        "hr{border:0;border-top:1px solid #eee;margin:20px 0}"
                        ".highlight{background:#f4f4f4;border-radius:4px;padding:10px;"
                        "overflow-x:auto;font-size:.9em}"
                    )
                    f.write("</style>\n</head>\n<body>\n")
                    f.write("<h1>Conversación Morphix</h1>\n")
                    f.write(
                        f"<p><strong>Fecha:</strong> "
                        f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}</p>\n"
                        "<hr>\n"
                    )
                    for msg in self._history:
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        role_label = {
                            "assistant": "Maestro",
                            "user": "Usuario",
                            "agent": "Agente",
                            "tool": "Herramienta",
                        }.get(role, role.capitalize())
                        f.write(f'<div class="msg">\n<p class="role">{role_label}:</p>\n')
                        f.write(f'<div class="content">{_hl_code(content)}</div>\n')
                        f.write("</div>\n<hr>\n")
                    f.write("</body>\n</html>")

            self._on_system(f"✅ Exportado: **{filename}**")

        except Exception as e:
            logger.error(f"Error guardando conversación: {e}", exc_info=True)
            self._on_system(f"❌ Error al guardar: {e}")

    async def _export_via_repository(self, conv_id: int, fmt: str):
        """Export a saved conversation via the repository."""
        from core.path_resolver import paths
        from core.repositories.conversation_repository import ConversationRepository

        project_path = None
        if self._current_project_root:
            proj_dir = paths.memory_dir("main") / self._current_project_root
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

        from core.path_resolver import paths

        name, ok = QInputDialog.getText(self, "Nuevo proyecto", "Nombre del proyecto:", text="")
        if not ok or not name:
            return
        name = name.strip().lower().replace(" ", "_")
        if not name or not name.isidentifier():
            self._on_system("❌ Nombre inválido. Usa solo letras, números y _")
            return
        root = f"code_projects/{name}"
        proj_dir = paths.memory_dir("main") / root
        proj_dir.mkdir(parents=True, exist_ok=True)
        self._current_project_root = root
        self._update_project_display(name)
        self._refresh_project_list()
        self._on_system(f"✅ Proyecto '{name}' creado y activado.")
        self._preload_btn.setEnabled(True)
        self._preload_status.setText("")
        if self._mode == "chat":
            self._set_mode("orchestrate")
            self._on_system("⚙️ Modo cambiado a Orquestar automáticamente.")

    def _import_project(self):
        import shutil
        from pathlib import Path

        from core.path_resolver import paths

        src = QFileDialog.getExistingDirectory(self, "Seleccionar proyecto para importar")
        if not src:
            return

        src_path = Path(src)
        name = src_path.name.lower().replace(" ", "_")
        dst = paths.memory_dir("main") / "code_projects" / name

        if dst.exists():
            self._on_system(f"❌ Ya existe un proyecto llamado '{name}'")
            return

        try:
            self._on_system(f"📂 Copiando '{src_path.name}' → code_projects/{name}...")
            shutil.copytree(str(src_path), str(dst))
        except Exception as e:
            logger.warning("Unhandled exception in MaestroTab", exc_info=True)
            self._on_system(f"❌ Error copiando proyecto: {e}")
            return

        self._current_project_root = f"code_projects/{name}"
        self._update_project_display(name)
        self._refresh_project_list()
        self._preload_btn.setEnabled(True)
        self._preload_status.setText("")
        file_count = sum(1 for _ in dst.rglob("*") if _.is_file())
        self._on_system(f"✅ Proyecto '{name}' importado ({file_count} archivos)")

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

        indexer = CodebaseIndexer(workspace="main", project_root=self._current_project_root)

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
        root = f"code_projects/{name}"
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
        from core.path_resolver import paths

        base = paths.memory_dir("main") / "code_projects"
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
            self._show_typing()
            self._streaming_bubble = None
            self._streaming_text = ""
            session = self._paused_session
            self._paused_session = None
            run_async(self._resume_workflow(session, answer))
            return

        if self._workflow_running:
            return
        query = self.input_field.toPlainText().strip()
        if not query:
            return

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
            self._workflow_running = True
            self._add_bubble(query, "user")
            self.input_field.clear()
            self._show_typing()
            self._streaming_bubble = None
            self._streaming_text = ""
            run_async(self._run_direct_agent(query, agent))
            return

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
        from orchestration.workflows.orchestrator import WorkflowOrchestrator

        try:
            final = await WorkflowOrchestrator.run_full_workflow(session=session)
            ctx = session.context

            if final == "[PAUSED:clarification_needed]":
                question = ctx.last_clarification or "¿Podrías clarificar?"
                self._on_system(f"⏸️ Pausa: {question}")
                self._paused_session = session
                self._workflow_running = False
                self._hide_typing()
                self.input_field.setPlaceholderText(f"Responde: {question[:60]}...")
                return

            if ctx.project_root:
                self._current_project_root = ctx.project_root
            streaming_text = self._streaming_text

            had_streaming = self._streaming_bubble is not None
            had_content = bool(streaming_text.strip())

            self._streaming_bubble = None
            self._streaming_text = ""

            # Show final result: prefer streaming bubble (already visible),
            # fall back to explicit return value
            if had_streaming and had_content:
                self._history.append({"role": "assistant", "content": streaming_text.strip()})
            elif final:
                self._on_assistant(final)
            elif not had_content:
                self._on_system("⚠️ El workflow no produjo respuesta.")

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
                    agent_tool_entries = [
                        m for m in new_entries if m.get("role") in ("agent", "tool")
                    ]
                    if agent_tool_entries:
                        from core.repositories.conversation_repository import ConversationRepository

                        await ConversationRepository.add_messages(
                            self._conversation_id, agent_tool_entries
                        )
                except Exception:
                    logger.warning("Unhandled exception in MaestroTab", exc_info=True)
        except Exception as e:
            logger.error(f"Error en workflow: {e}", exc_info=True)
            self._on_system(f"❌ Error: {e}")
        finally:
            self._hide_typing()
            self._workflow_running = False

    async def _resume_workflow(self, session, answer: str):
        """Reanuda un workflow pausado tras recibir respuesta de clarificación."""
        from orchestration.workflows.orchestrator import WorkflowOrchestrator

        try:
            final = await WorkflowOrchestrator.resume_workflow(session=session, answer=answer)
            ctx = session.context

            if final == "[PAUSED:clarification_needed]":
                question = ctx.last_clarification or "¿Podrías clarificar?"
                self._on_system(f"⏸️ Pausa adicional: {question}")
                self._paused_session = session
                self._hide_typing()
                self.input_field.setPlaceholderText(f"Responde: {question[:60]}...")
                return

            streaming_text = self._streaming_text
            had_streaming = self._streaming_bubble is not None
            had_content = bool(streaming_text.strip())
            self._streaming_bubble = None
            self._streaming_text = ""

            if had_streaming and had_content:
                self._history.append({"role": "assistant", "content": streaming_text.strip()})
            elif final:
                self._on_assistant(final)

            if self._conversation_id is None:
                try:
                    from core.repositories.conversation_repository import ConversationRepository

                    recent = await ConversationRepository.list_all(limit=1)
                    if recent:
                        self._conversation_id = recent[0]["id"]
                except Exception:
                    logger.warning("Unhandled exception in MaestroTab", exc_info=True)
        except Exception as e:
            logger.error(f"Error resumiendo workflow: {e}", exc_info=True)
            self._on_system(f"❌ Error: {e}")
        finally:
            self._hide_typing()
            self._workflow_running = False
            self.input_field.setPlaceholderText("Escribe tu mensaje...")

    async def _run_direct_agent(self, query: str, agent: str | None = None):
        """Ejecuta conversación directa 1:1 con un agente (con function-calling nativo)."""
        agent = agent or self._force_agent or "conversacional"
        from desktop.events import build_workflow_events

        # Events so bash/system/stats reach the GUI also in chat mode.
        events = build_workflow_events()
        try:

            async def _stream(text: str):
                self._on_stream(text)

            current_history = list(self._history)

            # Get agent profile + tools with workflow template filtering
            from agents.registry import agents_registry as _reg
            from core.workflow_state import get_active_workflow
            from core.workspaces import get_global_workspaces
            from orchestration.loader import load_workflow_template
            from orchestration.loop import execute_agent_loop
            from tools.specs import expand_allowed_tools

            agent_profile = _reg.get_profile(agent)
            agent_tools = agent_profile.get("tools", []) if agent_profile else []
            workspace = get_global_workspaces().current

            # Filter tools against active workflow template if available
            effective_tools = None
            if agent_tools:
                expanded_tools = expand_allowed_tools(agent_tools) or []
                try:
                    template = load_workflow_template(
                        workspace_name=workspace, workflow_name=get_active_workflow()
                    )
                    workflow_allowed = (
                        template.get("tools", {}).get("allowed") if template else None
                    )
                    if workflow_allowed:
                        from tools.specs import (
                            tool_matches_allowlist,
                        )

                        allowed_list: list[str] = workflow_allowed  # type: ignore[assignment]
                        effective_tools = [
                            t for t in expanded_tools if tool_matches_allowlist(t, allowed_list)
                        ]
                except Exception:
                    logger.warning("Unhandled exception in MaestroTab", exc_info=True)
                if not effective_tools:
                    effective_tools = expanded_tools

                loop_result = await execute_agent_loop(
                    task=query,
                    agent_type=agent,
                    history=current_history,
                    allowed_tools=effective_tools,
                    workspace=workspace,
                    project_root=self._current_project_root,
                    on_stream_chunk=_stream,
                    events=events,
                )
                response = (
                    loop_result.get("result", str(loop_result))
                    if isinstance(loop_result, dict)
                    else str(loop_result)
                )
            else:
                # Agent has no tools — use text-only fallback
                from agents.service import AgentsService

                response = await AgentsService.execute_agent(
                    agent, query, current_history, on_stream_chunk=_stream
                )

            if self._streaming_bubble is not None:
                had_streaming = True
                had_content = bool(self._streaming_text.strip())
            else:
                had_streaming = False
                had_content = False

            self._streaming_bubble = None
            streaming_text = self._streaming_text
            self._streaming_text = ""

            if had_streaming and had_content:
                self._history.append({"role": "assistant", "content": streaming_text.strip()})
            elif response and response.strip():
                self._on_assistant(response)
            elif streaming_text.strip():
                if not self._history or self._history[-1].get("content") != streaming_text:
                    self._history.append({"role": "assistant", "content": streaming_text})
                self._on_assistant(streaming_text)
            elif not response or not response.strip():
                self._on_system(
                    f"⚠️ El agente no produjo respuesta. Estado: {loop_result.get('status', '?') if isinstance(loop_result, dict) else 'desconocido'}"
                )

            final_output = response or streaming_text
            if final_output:
                # Save conversation to database
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
            self._workflow_running = False
