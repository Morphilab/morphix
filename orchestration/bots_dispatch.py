"""Ejecución de turnos de bot que el orquestador delega: el chat canónico
(despacho + pausa + finalize), el resume de pausas de bot y la anotación
identification-only de menciones. El orquestador DETECTA la ruta
(``canonical_owner_of`` / ``paused_state.origin``); este módulo la EJECUTA.

Los consumidores llaman a los helpers de pausa vía atributo de módulo
(``pauses.save_paused_session``) — un solo punto de parche para ambas rutas.
La infraestructura de bot pesada (``bots_runner``, ``orchestration.loop``,
``core.bots``) se importa a nivel de función: mantiene el módulo liviano y
los patch targets de origen estables.
"""

import logging
import time

from orchestration import pauses
from orchestration.context import PAUSED_MARKER, WorkflowContext, WorkflowEvents, emit_system
from orchestration.finalizer import finalize_workflow

logger = logging.getLogger(__name__)


async def bots_roster_safe() -> list[dict]:
    """Roster de bots si existe; [] ante cualquier fallo (middleware suave)."""
    try:
        from core.bots import BotsService

        return await BotsService.list_bots(include_disabled=False)
    except Exception:
        return []


async def annotate_query_mentions(query: str, ctx: WorkflowContext) -> str:
    """Las menciones del usuario solo IDENTIFICAN contexto — jamás auto-envían
    ni reenvían texto verbatim. Middleware suave: cualquier fallo deja la
    query intacta (la anotación es mejora informativa, no prerequisito)."""
    try:
        roster = await bots_roster_safe()
        if roster:
            from core import bots_protocol

            annotated = bots_protocol.annotate_user_mentions(str(query), roster)
            if annotated != query:
                ctx.query = annotated
                return annotated
    except Exception:
        pass
    return query


async def dispatch_canonical_turn(
    *,
    owner: dict,
    query: str,
    ctx: WorkflowContext,
    events: WorkflowEvents,
    start_time: float,
    persist: bool,
) -> str:
    """Turno de chat canónico: SIEMPRE corre por bots_runner con la identidad
    del bot, INDEPENDIENTE del workflow activo (cargar la plantilla activa
    mataba turnos de bot cuando el nombre del workflow no existía).

    ``persist=True`` es el chat canónico del usuario en la GUI — finaliza con
    scorecard; los transportes machine-local persisten por su cuenta via
    ``persist_turn_exchange`` y llegan aquí con ``persist=False``.
    """
    conv_id = ctx.conversation_id
    if conv_id is None:
        # El despachador solo llega aquí tras canonical_owner_of(conv_id) —
        # un None es error de cableado, no condición runtime.
        raise ValueError("dispatch_canonical_turn requiere ctx.conversation_id")
    from orchestration.bots_runner import run_bot_turn

    await emit_system(events, f"🤖 Conversando con identidad de @{owner['slug']}.")
    final = await run_bot_turn(
        str(owner["slug"]),
        query,
        int(conv_id),
        transport="canonical",
        events=events,
    )
    if isinstance(final, dict) and final.get("status") == "clarification_needed":
        # Bot con can_pause=true en su chat canónico — pausa con origin='bot';
        # el resume (resume_workflow) continúa por esa rama.
        ctx.last_clarification = str(final.get("clarification_question", ""))
        try:
            await pauses.save_paused_session(
                conv_id=conv_id,
                query=query,
                question=ctx.last_clarification,
                options=list(final.get("clarification_options") or []),
                paused_state={
                    "origin": "bot",
                    "bot_slug": final.get("bot_slug"),
                    "paused_loop_state": final.get("paused_loop_state", {}),
                    "allowed_tools": [],
                },
            )
        except Exception as e:
            await pauses.warn_pause_not_persisted(events, "bot", e)
        return PAUSED_MARKER
    if final is None:
        msg = "❌ El bot no produjo respuesta (revisa su plantilla/modelo)."
        await emit_system(events, msg)
        return msg
    final_text = str(final)
    if persist:
        from tools.orchestrator import get_llm_token_usage

        real_tokens = get_llm_token_usage()
        scorecard = {
            "subtasks": 1,
            "completadas": 1,
            "recuperadas": 0,
            "fallidas": 0,
            "tokens": real_tokens or len(final) // 4,
            "tiempo": f"{round(time.monotonic() - start_time, 2)}s",
            "calidad": "Alta",
            "tipo_tarea": "bot_canonical_chat",
            "complejidad": "simple",
        }
        await finalize_workflow(
            query=query,
            final_output=final_text,
            conversation_history=ctx.conversation_history,
            conversation_id=conv_id,
            scorecard=scorecard,
            subtasks_list=["Turno de bot"],
            task_analysis={
                "requires_full_orchestration": False,
                "primary_type": "conversacional",
            },
            G=None,
            events=events,
        )
    return final_text


async def resume_bot_turn(
    *,
    conv_id: int | None,
    events,
    start_time: float,
    paused_data: dict,
    question: str,
    answer: str,
    workspace: str,
) -> str:
    """Resume de un turno de bot pausado.

    Reconstruye el estado del agent loop, re-inyecta la identidad del bot y
    persiste el intercambio en el chat canónico.
    """
    from llm.tool_calls import sanitize_messages_for_ollama
    from orchestration.loop import execute_agent_loop
    from orchestration.utils import apply_undercover

    bot_slug = str(paused_data.get("bot_slug") or "")
    from core.bots import BotsService

    bot_def = await BotsService.get_bot(bot_slug) if bot_slug else None
    if not bot_def or not bot_def.get("enabled"):
        msg = f"❌ El bot '{bot_slug}' ya no existe o está deshabilitado."
        await emit_system(events, msg)
        return msg

    loop_state = paused_data.get("paused_loop_state", {})
    messages = loop_state.get("messages", [])
    messages = sanitize_messages_for_ollama(messages)
    messages.append({"role": "user", "content": f"[Respuesta a: {question}] {answer}"})

    await emit_system(events, f"🤖 @{bot_slug} continúa con tu respuesta…")

    loop_result = await execute_agent_loop(
        task=loop_state.get("task", ""),
        agent_type="conversacional",
        history=messages,
        allowed_tools=paused_data.get("allowed_tools") or list(bot_def.get("tool_names") or []),
        workspace=workspace,
        on_stream_chunk=events.on_stream_chunk if events else None,
        events=events,
        skip_task_message=True,
        skills_enabled=True,
        bot_context={
            **bot_def,
            "conversation_id": int(conv_id) if conv_id else 0,
            "workspace": workspace,
            "dm_enabled": True,
        },
        model_override=bot_def.get("model") or None,
    )

    result_text = (
        str(loop_result.get("result") or "").strip() if isinstance(loop_result, dict) else ""
    )
    final = await apply_undercover(result_text)

    if not final:
        await emit_system(events, "❌ El bot no produjo respuesta tras la clarificación.")
        return final or ""

    real_tokens = loop_result.get("tokens_used", 0) if isinstance(loop_result, dict) else 0
    scorecard = {
        "subtasks": 1,
        "completadas": 1,
        "recuperadas": 1,
        "fallidas": 0,
        "tokens": int(real_tokens) if isinstance(real_tokens, (int, float)) else len(final) // 4,
        "tiempo": f"{round(time.monotonic() - start_time, 2)}s",
        "calidad": "Alta",
        "tipo_tarea": "bot_canonical_resume",
        "complejidad": "simple",
    }
    await finalize_workflow(
        query=paused_data.get("query", question),
        final_output=final,
        conversation_history=[],
        conversation_id=conv_id,
        scorecard=scorecard,
        subtasks_list=["Resume de bot"],
        task_analysis={"requires_full_orchestration": False, "primary_type": "conversacional"},
        G=None,
        events=events,
    )
    return final
