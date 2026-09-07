"""
Workflow Finalizer — conversation persistence, export, and structured profile extraction.
"""

import contextvars
import json
import logging

from core.config import settings
from core.database import get_async_session
from core.memory.manager import memory as memory_manager
from core.models import Conversation, Workflow
from core.repositories.conversation_repository import ConversationRepository
from core.utils import clean_llm_response

logger = logging.getLogger(__name__)

_MAX_SUMMARY_CHARS = 4000

# el conv_id finalizado se registra en un ContextVar por-run,
# de modo que la GUI lee SU conversación sin la race de `list_all(limit=1)`.
_finalized_conv_id_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "finalized_conversation_id", default=None
)


def get_finalized_conversation_id() -> int | None:
    """conv_id de la conversación persistida por el workflow del task actual."""
    return _finalized_conv_id_ctx.get()


def _truncate_safe_summary(final_output: str) -> str:
    """Recorta el resumen a _MAX_SUMMARY_CHARS solo si el original fue cortado.

    Un output de exactamente 4000 chars es íntegro (no fue truncado por el
    slice) y no debe recortarse de más; solo los outputs >4000 se recortan
    al último punto de frase si existe uno razonable.
    """
    safe_summary = final_output[:_MAX_SUMMARY_CHARS].strip()
    if len(final_output) > _MAX_SUMMARY_CHARS and not safe_summary.endswith((".", "!", "?")):
        last_period = safe_summary.rfind(".")
        if last_period > 1000:
            safe_summary = safe_summary[: last_period + 1]
    return safe_summary


async def _extract_personal_facts(final_output: str, query: str) -> dict:
    prompt = f"""Extrae SOLO información personal del usuario del siguiente texto.
Responde ÚNICAMENTE con un JSON válido. Si no hay datos nuevos, devuelve {{}}.

Texto:
{query}
{final_output[:2000]}

Ejemplo de respuesta:
{{
  "name": "Moisés",
  "city": "McAllen",
  "dog": "Max",
  "favorite_food": "tacos al pastor",
  "age": 32,
  "favorite_color": "azul marino"
}}

Responde solo el JSON:"""

    try:
        from llm import models

        response = await models.call(
            messages=[{"role": "user", "content": prompt}],
            role="fast",
            temperature=0.0,
        )
        raw = clean_llm_response(response)

        # Parser unificado de JSON (mismo que decomposer/loop — no duplicar
        # la extracción de llaves balanceadas a mano).
        from llm import parse_json_from_llm

        facts = parse_json_from_llm(raw, default=None)

        if not isinstance(facts, dict):
            return {}
        # Filter out null/empty values
        facts = {k: v for k, v in facts.items() if v is not None and v != ""}
        # El gate trivial es CONTEXTUAL y vive en update_user_profile: con
        # perfil vacío, un hecho solo-nombre es el dato legítimo del usuario;
        # con perfil poblado, se descarta (anti-stale).
        return facts
    except Exception as e:
        logger.warning(f"⚠️ No se pudo extraer perfil estructurado: {e}")
        return {}


import networkx as nx


async def finalize_workflow(
    query: str,
    final_output: str,
    conversation_history: list,
    scorecard: dict,
    subtasks_list: list,
    task_analysis: dict,
    G: nx.DiGraph | None,
    events,
    project_root: str | None = None,
    workspace: str | None = None,
    files_written: list[str] | None = None,
    conversation_id: int | None = None,
    status: str = "completed",
):
    if workspace is None:
        workspace = settings.active_workspace
    conv_id = None

    # 1. Save conversation + user message + assistant response
    try:
        user_message = (
            next(
                (
                    msg.get("content", "")
                    for msg in reversed(conversation_history)
                    if msg.get("role") == "user"
                ),
                query,
            )
            or query
        )

        # Build the messages to persist: user message + history + assistant response
        messages_to_save = list(conversation_history) if conversation_history else []
        if final_output and final_output.strip():
            messages_to_save.append({"role": "assistant", "content": final_output.strip()})

        conv_id = await ConversationRepository.save(
            title=query[:100],
            user_message=user_message or query,
            tags="maestro",
            workflow_id=None,
            conversation_history=messages_to_save,
            conversation_id=conversation_id,
        )
        if conv_id is not None:
            _finalized_conv_id_ctx.set(conv_id)
        logger.info(
            f"Conversation {conv_id} saved"
            + (f" (resumed from {conversation_id})" if conversation_id else " (new)")
        )
    except Exception as e:
        logger.error(f"Error guardando conversación: {e}")

    # 2. Save workflow and associate to conversation (ASYNC)
    try:
        async with get_async_session() as session:
            wf = Workflow(
                # persistir el status REAL (cancelled/partial/failed/completed)
                query=query,
                subtasks=json.dumps(subtasks_list),
                scorecard=json.dumps(scorecard),
                status=status,
            )
            session.add(wf)
            await session.flush()

            if conv_id:
                conv = await session.get(Conversation, conv_id)
                if conv:
                    conv.workflow_id = wf.id
                    session.add(conv)

            # auto commit on context manager exit
            logger.info(f"✅ Workflow {wf.id} asociado a conversación {conv_id}")
    except Exception as e:
        logger.error(f"Error guardando workflow: {e}")

    # 3. Perfil estructurado (hechos limpios)
    try:
        facts = await _extract_personal_facts(final_output, query)
        if facts:
            await memory_manager.update_user_profile(facts)
            logger.info(f"📝 Perfil estructurado actualizado con {len(facts)} hechos nuevos")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo actualizar perfil estructurado: {e}")

    # 4. Save complete summary of the last response
    try:
        safe_summary = _truncate_safe_summary(final_output)

        await memory_manager.write("last_task_summary", safe_summary, validated=True)
        logger.info(f"📝 last_task_summary guardado ({len(safe_summary)} caracteres)")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo guardar last_task_summary: {e}")

    # 5. Smart git commit — ELIMINADO: el auto-commit implícito
    # committeaba estados intermedios sin valor semántico y chocaba con el
    # gate de aprobación (approval_unavailable). La decisión de committear es
    # del agente (tool git_manager a petición) o del workflow (commit_after).

    # 6. Record metrics
    try:
        from core.metrics import metrics as m

        tokens = scorecard.get("tokens", 0)
        subtasks = scorecard.get("subtasks", 0)
        m.record_workflow_completed(tokens=tokens, tool_calls=subtasks)
    except Exception:
        logger.warning("Error registrando métricas de workflow", exc_info=True)

    return conv_id
