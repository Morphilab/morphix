"""Event bridge — señales Qt ↔ WorkflowEvents (core).

Thread-safe: las señales Qt pueden emitirse desde cualquier hilo.
Los slots se ejecutan en el hilo principal de Qt automáticamente.

Non-blocking approval: uses asyncio.Event + signal bridge so the async
workflow can await user input without freezing the event loop.
"""

import asyncio
import logging
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox

from orchestration.context import WorkflowEvents

logger = logging.getLogger(__name__)

# Session-level "Always Allow" tracking for tool approvals
_always_allowed: set[str] = set()

# Pending approval requests: request_id → asyncio.Event
_approval_events: dict[str, asyncio.Event] = {}
# Loop propietario de cada request (registrado al crear la aprobación en el
# loop thread) — necesario para programar el wakeup con call_soon_threadsafe
# cuando la respuesta llega desde el hilo de Qt.
_approval_loops: dict[str, asyncio.AbstractEventLoop] = {}
_approval_results: dict[str, bool] = {}
_approval_counter = 0

# El slot de Qt (_handle_approval_response) corre en el hilo principal de Qt,
# mientras _approval espera el asyncio.Event en el loop. Protege los dicts
# compartidos contra mutaciones concurrentes.
_approval_lock = threading.Lock()

# Safety net: if the user does not answer the approval dialog within this
# window, the request is denied automatically instead of hanging the workflow
# indefinitely (previously a missing response blocked the whole workflow).
_APPROVAL_TIMEOUT = 60.0  # seconds


def reset_approval_state() -> None:
    """Clear session approval memory (e.g. on workspace switch)."""
    with _approval_lock:
        _always_allowed.clear()
        _approval_events.clear()
        _approval_loops.clear()
        _approval_results.clear()


def _format_params(params: dict) -> str:
    """Format tool parameters for display in the approval dialog."""
    lines = []
    for key, value in params.items():
        val_str = str(value)
        if len(val_str) > 120:
            val_str = val_str[:117] + "..."
        lines.append(f"  {key}: {val_str}")
    return "\n".join(lines) if lines else "(none)"


def _handle_approval_response(request_id: str, tool_name: str, approved: bool, allow_all: bool):
    """Resolve the pending approval event (called from Qt slot).

    Puede invocarse desde el hilo principal de Qt (slot del diálogo), que es
    distinto del hilo del event loop. asyncio.Event no es thread-safe, así
    que el wakeup se programa en el loop propietario vía
    ``call_soon_threadsafe`` y los dicts compartidos se mutan bajo lock.
    """
    with _approval_lock:
        if allow_all:
            _always_allowed.add(tool_name)
            approved = True
        event = _approval_events.pop(request_id, None)
        if event is None:
            # Already resolved (e.g. timed out) — ignore the late response.
            return
        _approval_results[request_id] = approved
        loop = _approval_loops.pop(request_id, None)

    if loop is None or loop.is_closed() or not loop.is_running():
        event.set()
        return
    loop.call_soon_threadsafe(event.set)


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
    offline_changed = Signal(bool)
    workspace_changed = Signal(str)
    project_changed = Signal(str)  # project_root activo ("" = sin proyecto)
    indexing_progress = Signal(dict)  # {phase, current_file, files_scanned, pct}
    approval_requested = Signal(str, str, str)  # request_id, tool_name, params_text
    open_file_requested = Signal(str)  # path (relativo al proyecto activo)


_signals = None


def _get_signals() -> DesktopSignals:
    """Lazy init — evita crear QObject antes de QApplication."""
    global _signals
    if _signals is None:
        _signals = DesktopSignals()
        _signals.approval_requested.connect(_on_approval_requested)
        # Al cambiar de workspace se limpia el estado de aprobaciones
        # ('Always Allow' no debe perdura entre workspaces).
        _signals.workspace_changed.connect(lambda _ws: reset_approval_state())
    return _signals


def _on_approval_requested(request_id: str, tool_name: str, params_text: str):
    """Qt slot: shows non-blocking approval dialog and resolves the asyncio.Event."""
    msg = (
        f"Allow execution of:\n\n"
        f"Tool: {tool_name}\n"
        f"Parameters:\n{params_text}\n\n"
        f"This tool can modify files or execute commands."
    )
    dialog = QMessageBox()
    dialog.setWindowTitle("Approve Tool Execution")
    dialog.setText(msg)
    dialog.setStandardButtons(
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.YesToAll
        | QMessageBox.StandardButton.No
    )
    dialog.setDefaultButton(QMessageBox.StandardButton.No)

    def _on_finished(result):
        allow_all = result == QMessageBox.StandardButton.YesToAll
        approved = result in (
            QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.YesToAll,
        )
        _handle_approval_response(request_id, tool_name, approved, allow_all)

    dialog.finished.connect(_on_finished)
    dialog.open()


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

    async def _approval(tool_name: str, params: dict) -> bool:
        with _approval_lock:
            if tool_name in _always_allowed:
                return True

            global _approval_counter
            _approval_counter += 1
            request_id = f"req_{_approval_counter}"

            event = asyncio.Event()
            _approval_events[request_id] = event
            _approval_loops[request_id] = asyncio.get_running_loop()

        params_text = _format_params(params)
        _get_signals().approval_requested.emit(request_id, tool_name, params_text)

        try:
            await asyncio.wait_for(event.wait(), timeout=_APPROVAL_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "⏳ Aprobación para '%s' sin respuesta en %ss — denegada automáticamente",
                tool_name,
                _APPROVAL_TIMEOUT,
            )
            with _approval_lock:
                _approval_events.pop(request_id, None)
                _approval_loops.pop(request_id, None)
                _approval_results.pop(request_id, None)
            return False
        with _approval_lock:
            return _approval_results.pop(request_id, False)

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
        on_ui_refresh=_noop,
        on_approval_required=_approval,
    )


def get_signals() -> DesktopSignals:
    """Retorna la instancia global de señales para conectar slots."""
    return _get_signals()
