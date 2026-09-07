# orchestration/bots_runner.py — turno de bot desacoplado de workflows
"""Punto ÚNICO de ejecución de turnos de bot.

Un bot es un agente con capacidades PROPIAS (plantilla YAML): soul_md, model,
temperature, tool_names, skill_allowlist, can_pause, dm_enabled. NUNCA pasa
por el orquestador de workflows ni carga plantillas de workflow — la clase
de bug 'No existe plantilla de workflow default' muere estructuralmente aquí.

Transportes soportados (todos delegan en run_bot_turn):
- bots_wake.dispatch_row      → DM entrante (pending_turns)
- bots_groups_drive._member_reply → respuesta de sala (Group: <roomId>)
- bots_clock.execute_prompt   → rutina deliver='history' (conv '⏰ <nombre>')
- guard canónico de WorkflowOrchestrator → chat canónico desde la GUI

La persistencia sigue siendo responsabilidad del CALLER (persist=False era el
contrato original): wake persiste via persist_turn_exchange (entrega terminal,
con borrado de fila), salas/rutinas persisten su intercambio propio.
"""

import logging
import time

from core.workspaces import get_global_workspaces
from orchestration.context import Session, WorkflowContext, WorkflowEvents
from orchestration.emitter import WorkflowEmitter
from orchestration.loop import execute_agent_loop
from orchestration.utils import apply_undercover

logger = logging.getLogger(__name__)

# Transportes sin humano al otro lado: jamás pausan (la pausa moriría en
# dead-letter). can_pause del template SOLO abre la puerta en chat canónico.
_NO_PAUSE_TRANSPORTS = {"dm", "room", "routine"}

# Continuidad del chat eterno: últimos N mensajes persistidos de la conversación.
# El snapshot de memoria (bots_memory) sigue aportando lo curado por encima.
HISTORY_MAX_MESSAGES = 40


async def _load_history(conv_id: int) -> list[dict]:
    """Historial del chat canónico/sala/rutina para continuidad del turno."""
    try:
        from core.repositories.conversation_repository import ConversationRepository

        msgs = await ConversationRepository.get_messages(int(conv_id))
        return msgs[-HISTORY_MAX_MESSAGES:]
    except Exception as e:
        logger.warning("historial de bot indisponible conv=%s: %s", conv_id, e)
        return []


async def run_bot_turn(
    slug: str,
    query: str,
    conv_id: int,
    *,
    transport: str = "canonical",
    dm_enabled: bool | None = None,
    extra_query: str = "",
    events: WorkflowEvents | None = None,
) -> str | dict | None:
    """Ejecuta UN turno del bot en SU conversación con SU identidad.

    Args:
        slug: id del bot (template = identidad/capacidades).
        query: turno del usuario (ya enmarcado según el transporte — ver
            bots_wake._ctx_query / _room_prompt).
        conv_id: conversación destino (canónica 'Bot Chat', 'Group: <id>'
            o '⏰ <rutina>').
        transport: dm | room | routine | canonical — gobierna la matriz de
            pausas y el protocolo DM.
        dm_enabled: override explícito (salas pasan False); None ⇒ template.
        extra_query: directiva CONFIABLE del transporte anexada tras el turno
            (p.ej. _dm_turn_directive del wake).
        events: bus del caller (None ⇒ silencioso).

    Returns:
        str: texto final del bot (limpio, undercover aplicado).
        dict: {"status": "clarification_needed", ...} si el bot (canónico,
            can_pause=true) pide clarificación — el caller persiste la pausa.
        None: sin respuesta del modelo (caller decide la nota).
    """
    from core.bot_templates import load_bot_template
    from core.bots import BotsService

    bot_def = await BotsService.get_bot(slug)
    if not bot_def or not bot_def.get("enabled"):
        raise ValueError(f"bot '{slug}' inexistente o deshabilitado")

    # Plantilla = identidad/capacidades. Si falta el YAML (legado pre-sync),
    # el turno NO se pierde: fallback conservador a la fila DB (proyección)
    # con can_pause=False y dm_enabled según transporte.
    try:
        tpl = load_bot_template(get_global_workspaces().current, slug)
    except FileNotFoundError:
        logger.warning(
            "bot @%s sin plantilla YAML — fallback conservador a fila DB "
            "(can_pause=False; el switch exporta huérfanas al próximo ciclo)",
            slug,
        )
        tpl = {
            "can_pause": False,
            "dm_enabled": transport not in {"room", "routine"},
            "agent": "conversacional",
        }

    pause_allowed = tpl["can_pause"] and transport == "canonical"
    if not pause_allowed:
        from orchestration.context import clarification_denied

        _flag = clarification_denied.set(True)
    else:
        _flag = None

    # Defense-in-depth: room/routine JAMÁS tienen DM aunque el caller falle;
    # el override explícito solo aplica en dm/canonical.
    if transport in {"room", "routine"}:
        eff_dm = False
    elif dm_enabled is not None:
        eff_dm = bool(dm_enabled)
    else:
        eff_dm = tpl["dm_enabled"]

    bot_ctx = {
        **bot_def,
        "conversation_id": int(conv_id),
        "workspace": get_global_workspaces().current,
        "dm_enabled": eff_dm,
    }

    history = await _load_history(int(conv_id))
    task = f"{query}{extra_query}"
    start = time.monotonic()

    emitter = WorkflowEmitter(None)
    await emitter.emit(
        status=f"Bot @{slug}",
        current_agent=f"@{slug}",
        phase="Respondiendo",
        subtask_list=[{"name": f"Turno @{slug}", "status": "running"}],
    )

    try:
        loop_result = await execute_agent_loop(
            task=task,
            agent_type=tpl["agent"],
            history=history,
            allowed_tools=list(bot_def.get("tool_names") or []),
            workspace=get_global_workspaces().current,
            on_stream_chunk=events.on_stream_chunk if events else None,
            events=events,
            skills_enabled=True,
            bot_context=bot_ctx,
            model_override=bot_def.get("model") or None,
        )
    finally:
        if _flag is not None:
            from orchestration.context import clarification_denied

            clarification_denied.reset(_flag)

    if not isinstance(loop_result, dict):
        logger.error("turno de @%s retornó no-dict: %r", slug, type(loop_result))
        return None

    status = str(loop_result.get("status") or "")
    if status == "clarification_needed":
        # Solo alcanzable en chat canónico con can_pause=true (los demás
        # transportes corren con clarification_denied). Se propaga como dict:
        # el caller (el guard de chat canónico) persiste la PausedSession
        # origin='bot'.
        if transport != "canonical":  # pragma: no cover — defensa en profundidad
            logger.warning(
                "bot @%s pidió clarificación en transporte '%s' — denegada",
                slug,
                transport,
            )
            return None
        logger.info("bot @%s pide clarificación — pausa origin='bot'", slug)
        return {
            "status": "clarification_needed",
            "bot_slug": slug,
            "clarification_question": loop_result.get("clarification_question", ""),
            "clarification_options": loop_result.get("clarification_options", []),
            "paused_loop_state": loop_result.get("paused_loop_state", {}),
        }

    result_text = loop_result.get("result", "")
    final = str(result_text).strip() if result_text else ""
    final = await apply_undercover(final)

    # tokens_used/elapsed_time los mide el propio WorkflowEmitter
    # (antes se pasaban explícitos y el emitter logueaba warning por turno).
    await emitter.emit(
        status="Completado",
        subtasks_completed=1 if final else 0,
        current_agent=f"@{slug}",
        phase="Completado",
    )

    if not final:
        logger.warning("turno de @%s sin respuesta del modelo (%s)", slug, status or "vacío")
        return None
    logger.info("turno de @%s completado (%s, %.1fs)", slug, transport, time.monotonic() - start)
    return final


def make_bot_session(query: str, conv_id: int) -> Session:
    """Session mínima para callers que aún necesitan un Session explícito."""
    from core.workspaces import get_global_workspaces

    ctx = WorkflowContext(
        query=query,
        mode="chat",
        workspace=get_global_workspaces().current,
        conversation_id=int(conv_id),
    )
    return Session(context=ctx, events=WorkflowEvents())


__all__ = ["run_bot_turn", "make_bot_session", "_NO_PAUSE_TRANSPORTS"]
