"""Workflow context and events — decoupled from any UI framework.

WorkflowContext y WorkflowEvents viven en orchestration/ para que tanto la CLI
como la GUI PySide6 puedan usarlos sin dependencias mutuas.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WorkflowContext:
    """Contexto inmutable del workflow — sin objetos UI."""

    query: str
    mode: str = "chat"
    conversation_history: list[dict] = field(default_factory=list)
    current_pdf_text: str = ""
    workspace: str = "main"
    project_root: str | None = None
    active_workflow: str = "default"
    force_agent: str | None = None
    allowed_tools: list[str] | None = None
    settings: Any = None
    agents_registry: Any = None
    enc: Any = None
    conversation_id: int | None = None
    is_follow_up: bool = False
    cancelled: bool = False
    last_clarification: str = ""
    blackboard: Any = None


@dataclass
class WorkflowEvents:
    """Callbacks que el orchestrator dispara. La UI los implementa.

    Todos son async, opcionales (None = se ignora el evento).
    Ningún objeto UI aquí — solo callbacks tipados.
    """

    on_system_message: Callable[[str], Awaitable[None]] | None = None
    on_assistant_message: Callable[[str], Awaitable[None]] | None = None
    on_user_message: Callable[[str], Awaitable[None]] | None = None
    on_stream_chunk: Callable[[str], Awaitable[None]] | None = None
    on_diagram_update: Callable[[str, Any], Awaitable[None]] | None = None
    on_stats_update: Callable[[dict], Awaitable[None]] | None = None
    on_ui_refresh: Callable[[], Awaitable[None]] | None = None
    on_approval_required: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None
    on_agent_message: Callable[[str, str, str], Awaitable[None]] | None = None
    on_agent_stream: Callable[[str, str, str], Awaitable[None]] | None = None
    on_agent_status: Callable[[str, str], Awaitable[None]] | None = None


@dataclass
class Session:
    """Agrupa contexto y eventos para simplificar firmas de funciones.

    Reemplaza el patrón (ctx, events) disperso por toda la cadena de orquestación.
    """

    context: WorkflowContext
    events: WorkflowEvents

    def cancel(self) -> None:
        """Mark the workflow as cancelled."""
        self.context.cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self.context.cancelled


async def _emit(callback, *args):
    """Fire a callback if not None, catching and logging exceptions."""
    if callback is not None:
        try:
            await callback(*args)
        except Exception:
            logger.debug("Error en callback de evento UI", exc_info=True)


async def emit_system(events: WorkflowEvents, message: str) -> None:
    await _emit(events.on_system_message, message)


async def emit_assistant(events: WorkflowEvents, message: str) -> None:
    await _emit(events.on_assistant_message, message)


async def emit_user(events: WorkflowEvents, message: str) -> None:
    await _emit(events.on_user_message, message)


async def emit_stream_chunk(events: WorkflowEvents, chunk: str) -> None:
    await _emit(events.on_stream_chunk, chunk)


async def emit_diagram(events: WorkflowEvents, mermaid_code: str, graph: Any = None) -> None:
    await _emit(events.on_diagram_update, mermaid_code, graph)


async def emit_stats(events: WorkflowEvents, stats: dict) -> None:
    await _emit(events.on_stats_update, stats)


async def emit_refresh(events: WorkflowEvents) -> None:
    await _emit(events.on_ui_refresh)


async def emit_agent(events: WorkflowEvents, agent_name: str, label: str, text: str) -> None:
    await _emit(events.on_agent_message, agent_name, label, text)


async def emit_agent_stream(
    events: WorkflowEvents, agent_name: str, label: str, chunk: str
) -> None:
    await _emit(events.on_agent_stream, agent_name, label, chunk)


async def emit_agent_status(events: WorkflowEvents, agent_name: str, status: str) -> None:
    await _emit(events.on_agent_status, agent_name, status)
