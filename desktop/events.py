"""Event bridge — señales Qt ↔ WorkflowEvents (core).

Thread-safe: las señales Qt pueden emitirse desde cualquier hilo.
Los slots se ejecutan en el hilo principal de Qt automáticamente.
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from orchestration.context import WorkflowEvents

# Session-level "Always Allow" tracking for tool approvals
_always_allowed: set[str] = set()


def reset_approval_state() -> None:
    """Clear session approval memory (e.g. on workspace switch)."""
    _always_allowed.clear()


def _format_params(params: dict) -> str:
    """Format tool parameters for display in the approval dialog."""
    lines = []
    for key, value in params.items():
        val_str = str(value)
        if len(val_str) > 120:
            val_str = val_str[:117] + "..."
        lines.append(f"  {key}: {val_str}")
    return "\n".join(lines) if lines else "(none)"


class DesktopSignals(QObject):
    """Señales Qt emitidas durante la ejecución de un workflow."""

    stream_chunk = Signal(str)
    system_message = Signal(str)
    assistant_message = Signal(str)
    user_message = Signal(str)
    agent_message = Signal(str, str, str)  # agent_name, label, text (deprecated, use agent_stream)
    agent_stream = Signal(str, str, str)  # agent_name, label, chunk_text
    agent_status = Signal(str, str)  # agent_name, status
    stats_update = Signal(dict)
    diagram_update = Signal(str, object)
    offline_changed = Signal(bool)
    workspace_changed = Signal(str)
    project_changed = Signal(str)  # project_root activo ("" = sin proyecto)
    indexing_progress = Signal(dict)  # {phase, current_file, files_scanned, pct}


_signals = None


def _get_signals() -> DesktopSignals:
    """Lazy init — evita crear QObject antes de QApplication."""
    global _signals
    if _signals is None:
        _signals = DesktopSignals()
    return _signals


def build_workflow_events() -> WorkflowEvents:
    """Construye WorkflowEvents conectados a señales Qt."""
    from orchestration.context import WorkflowEvents

    async def _stream(text: str) -> None:
        _get_signals().stream_chunk.emit(text)

    async def _system(text: str) -> None:
        _get_signals().system_message.emit(text)

    async def _assistant(text: str) -> None:
        _get_signals().assistant_message.emit(text)

    async def _user(text: str) -> None:
        _get_signals().user_message.emit(text)

    async def _agent(agent_name: str, label: str, text: str) -> None:
        _get_signals().agent_message.emit(agent_name, label, text)

    async def _agent_stream(agent_name: str, label: str, chunk: str) -> None:
        _get_signals().agent_stream.emit(agent_name, label, chunk)

    async def _agent_status(agent_name: str, status: str) -> None:
        _get_signals().agent_status.emit(agent_name, status)

    async def _stats(data: dict) -> None:
        _get_signals().stats_update.emit(data)

    async def _diagram(code: str, graph=None) -> None:
        _get_signals().diagram_update.emit(code, graph)

    async def _approval(tool_name: str, params: dict) -> bool:
        if tool_name in _always_allowed:
            return True

        params_text = _format_params(params)
        msg = (
            f"Allow execution of:\n\n"
            f"Tool: {tool_name}\n"
            f"Parameters:\n{params_text}\n\n"
            f"This tool can modify files or execute commands."
        )
        reply = QMessageBox.question(
            None,
            "Approve Tool Execution",
            msg,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.YesToAll
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.YesToAll:
            _always_allowed.add(tool_name)
            return True
        return reply == QMessageBox.StandardButton.Yes

    async def _noop() -> None:
        pass

    return WorkflowEvents(
        on_stream_chunk=_stream,
        on_system_message=_system,
        on_assistant_message=_assistant,
        on_user_message=_user,
        on_agent_message=_agent,
        on_agent_stream=_agent_stream,
        on_agent_status=_agent_status,
        on_stats_update=_stats,
        on_diagram_update=_diagram,
        on_ui_refresh=_noop,
        on_approval_required=_approval,
    )


def get_signals() -> DesktopSignals:
    """Retorna la instancia global de señales para conectar slots."""
    return _get_signals()
