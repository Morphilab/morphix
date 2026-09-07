"""Persistencia de pausas humanas (PausedSession) compartida por las dos
rutas que pueden pausar: el chat canónico de bots (bots_dispatch) y el motor
DSL (orchestrator). Un fallo de BD al persistir jamás se traga en silencio —
la señal es loud en la UI y el contrato de retorno (PAUSED_MARKER) no cambia.
"""

import json
import logging

from core.database import get_async_session
from core.models import PausedSession
from orchestration.context import WorkflowEvents, emit_system

logger = logging.getLogger(__name__)


async def warn_pause_not_persisted(events: WorkflowEvents, origin: str, exc: Exception) -> None:
    """Una pausa no persistida es una pausa perdida: sin fila PausedSession,
    responder la pregunta no reanuda nada. Señal loud (evento de sistema
    visible + log de error) sin cambiar el contrato de retorno."""
    logger.error("pausa %s no persistida (DB error)", origin, exc_info=exc)
    await emit_system(
        events,
        f"⚠️ No se pudo persistir la pausa ({origin}) en la BD: {str(exc)[:120]}. "
        "Si respondes esta pregunta el flujo NO se reanudará — detén la "
        "ejecución o reintenta cuando la BD responda.",
    )


async def save_paused_session(
    conv_id: int | None,
    query: str,
    question: str,
    options: list[str],
    paused_state: dict,
) -> None:
    """Persist a paused workflow session to DB for later resume."""
    async with get_async_session() as db_session:
        paused = PausedSession(
            conversation_id=conv_id,
            clarification_question=question,
            clarification_options=json.dumps(options) if options else None,
            paused_state=json.dumps({**paused_state, "query": query}),
        )
        db_session.add(paused)
