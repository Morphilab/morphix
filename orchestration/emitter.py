"""WorkflowEmitter — contrato normalizado de stats para la UI.

Todos los workflows emiten su estado a través de este emisor, que garantiza
el contrato completo (spec §3): campos siempre presentes, elapsed_time y
tokens_used medidos automáticamente, files_written siempre lista, y
subtask_list con default derivado de la consulta.

La frontera con la UI sigue siendo dict (Qt Signal(dict)); _WorkflowState
solo valida internamente.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from tools.orchestrator import get_llm_token_usage

if TYPE_CHECKING:
    from orchestration.context import Session

logger = logging.getLogger(__name__)

_FIELDS: dict[str, type] = {
    "status": str,
    "current_agent": str,
    "subtask_list": list,
    "subtasks_total": int,
    "subtasks_completed": int,
    "files_written": list,
    "phase": str,
    "iterations": int,
    "actions_taken": int,
}


@dataclass
class _WorkflowState:
    """Estado interno validado — todos los campos siempre presentes."""

    status: str = "idle"
    current_agent: str | None = None
    subtask_list: list[dict] = field(default_factory=list)
    subtasks_total: int = 0
    subtasks_completed: int = 0
    files_written: list[str] = field(default_factory=list)
    tokens_used: int = 0
    elapsed_time: str = "0s"
    phase: str | None = None
    iterations: int = 0
    actions_taken: int = 0


class WorkflowEmitter:
    """Emisor de estado ligado a un Session.

    Vive en el Session (spec §3.1): sobrevive a la pausa de clarificación,
    por lo que elapsed_time/tokens acumulan desde el inicio real del workflow.
    """

    def __init__(self, session: Session | None) -> None:
        self._session = session
        self._start = time.monotonic()
        self._state = _WorkflowState()
        self._query = session.context.query if session is not None else ""

    async def emit(self, **updates: Any) -> None:
        """Aplica actualizaciones parciales y emite el estado completo."""
        for key, value in updates.items():
            if key not in _FIELDS:
                raise TypeError(f"Campo desconocido en emit: {key}")
            # phase admite None como reset explícito (emits finales);
            # el resto de campos usa None-skip para updates parciales.
            if value is not None or key == "phase":
                setattr(self._state, key, value)

        self._state.elapsed_time = f"{round(time.monotonic() - self._start, 1)}s"
        self._state.tokens_used = get_llm_token_usage()

        if not self._state.subtask_list:
            self._state.subtask_list = [{"name": self._query[:60], "status": "pending"}]

        if self._session is None:
            return  # sin session (invocaciones legacy/directas): no hay frontera Qt

        try:
            from orchestration.context import emit_stats

            await emit_stats(self._session.events, asdict(self._state))
        except Exception:
            logger.warning("WorkflowEmitter: fallo emitiendo stats", exc_info=True)
