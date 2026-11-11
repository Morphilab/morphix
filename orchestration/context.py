"""Workflow context and events — decoupled from any UI framework.

WorkflowContext y WorkflowEvents viven en orchestration/ para que tanto la CLI
como la GUI PySide6 puedan usarlos sin dependencias mutuas.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestration.emitter import WorkflowEmitter

logger = logging.getLogger(__name__)

# Marker canónico de pausa-por-clarificación. Única fuente: todos los
# workflows retornan ESTA constante; el GUI la detecta por valor.
PAUSED_MARKER = "[PAUSED:clarification_needed]"

# Con la deny-list activa (rutinas programadas), ask_clarification NO pausa
# el workflow — se deniega con un tool_result accionable porque no hay humano
# en el ciclo que reanude la pausa. La activa bots_clock.execute_prompt.
from contextvars import ContextVar

clarification_denied: ContextVar[bool] = ContextVar("clarification_denied", default=False)

# plan-mode: el agente entra con plan_mode y sale con exit_plan_mode; el loop
# lo lee para denegar escrituras y avisar en el system prompt.
plan_mode_active: ContextVar[bool] = ContextVar("plan_mode_active", default=False)

# allowlist de skills del bot activo (None = sin restricción). load_skill la
# consulta: el listado filtrado no es la frontera si la tool acepta cualquier nombre.
skill_allowlist_active: ContextVar[list[str] | None] = ContextVar(
    "skill_allowlist_active", default=None
)

# marcadores de datos no confiables — definidos en core.constants
# (módulo neutro, evita ciclo llm→orchestration) y re-exportados aquí
# como superficie pública para consumidores de orchestration.
from core.constants import (  # noqa: F401
    UNTRUSTED_CLOSE,
    UNTRUSTED_OPEN,
    UNTRUSTED_RULE,
)


@dataclass
class WorkflowContext:
    """Contexto inmutable del workflow — sin objetos UI."""

    query: str
    mode: str = "chat"
    conversation_history: list[dict] = field(default_factory=list)
    workspace: str = "main"
    project_root: str | None = None
    active_workflow: str = "default"
    force_agent: str | None = None
    allowed_tools: list[str] | None = None
    agents_registry: Any = None
    enc: Any = None
    conversation_id: int | None = None
    is_follow_up: bool = False
    cancelled: bool = False
    last_clarification: str = ""
    skills_enabled: bool = False  # skills procedimentales (doc 1 Tarea 4)


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
    on_stats_update: Callable[[dict], Awaitable[None]] | None = None
    on_approval_required: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None
    on_agent_message: Callable[[str, str, str], Awaitable[None]] | None = None
    on_agent_stream: Callable[[str, str, str], Awaitable[None]] | None = None
    on_agent_status: Callable[[str, str], Awaitable[None]] | None = None
    # Progreso del indexado de codebase (vía automática): {phase: indexing|
    # done|aborted, files_scanned, pct, ...}. La UI lo puentea a su señal de
    # indexado: sin él el indexado automático es invisible para el usuario.
    on_indexing_progress: Callable[[dict], Awaitable[None]] | None = None


@dataclass
class Session:
    """Agrupa contexto y eventos para simplificar firmas de funciones.

    Reemplaza el patrón (ctx, events) disperso por toda la cadena de orquestación.
    """

    context: WorkflowContext
    events: WorkflowEvents
    emitter: "WorkflowEmitter | None" = None

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
            logger.warning("UI callback failed", exc_info=True)


async def emit_system(events: WorkflowEvents, message: str) -> None:
    await _emit(events.on_system_message, message)


async def emit_stats(events: WorkflowEvents, stats: dict) -> None:
    await _emit(events.on_stats_update, stats)


async def emit_indexing_progress(events: WorkflowEvents, data: dict) -> None:
    await _emit(events.on_indexing_progress, data)


async def emit_agent_stream(
    events: WorkflowEvents, agent_name: str, label: str, chunk: str
) -> None:
    await _emit(events.on_agent_stream, agent_name, label, chunk)


async def emit_agent_status(events: WorkflowEvents, agent_name: str, status: str) -> None:
    await _emit(events.on_agent_status, agent_name, status)
