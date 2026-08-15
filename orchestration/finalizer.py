"""
Workflow Finalizer — conversation persistence, export, and structured profile extraction.
"""

import json
import logging

from core.config import settings
from core.database import get_async_session
from core.memory.manager import memory as memory_manager
from core.models import Conversation, Workflow
from core.repositories.conversation_repository import ConversationRepository
from core.utils import clean_llm_response
from orchestration.diagram import update_live_diagram

logger = logging.getLogger(__name__)

_MAX_SUMMARY_CHARS = 4000


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
        # Rompe el bucle de contaminación: un perfil trivial (solo name,
        # p. ej. "ChatGPT" proveniente de una conversación ya contaminada)
        # no debe re-escribir user_profile (evidencia 2026-08-14, prueba 2).
        from core.utils import is_trivial_profile

        if is_trivial_profile(facts):
            return {}
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
                query=query,
                subtasks=json.dumps(subtasks_list),
                scorecard=json.dumps(scorecard),
                status="completed",
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

        await memory_manager.write("user_profile_last_update", safe_summary, validated=True)
        logger.info(f"📝 user_profile_last_update guardado ({len(safe_summary)} caracteres)")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo guardar user_profile_last_update: {e}")

    # 5. Smart git commit
    if project_root and files_written:
        try:
            from core.git_operations import smart_auto_commit

            await smart_auto_commit(
                workspace=workspace,
                project_root=project_root,
                task_description=query,
                files_written=files_written,
            )
        except Exception as e:
            logger.warning("⚠️ No se pudo hacer commit automático: %s", e)

    # 6. Record metrics
    try:
        from core.metrics import metrics as m

        tokens = scorecard.get("tokens", 0)
        subtasks = scorecard.get("subtasks", 0)
        m.record_workflow_completed(tokens=tokens, tool_calls=subtasks)
    except Exception:
        logger.warning("Error registrando métricas de workflow", exc_info=True)

    await update_live_diagram(G, events)
    return conv_id
