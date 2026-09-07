# orchestration/bots_wake.py — despertar idle-first del inbox
"""Consume ``pending_turns`` y ejecuta el turno destino dentro de SU chat
eterno, NUNCA interrumpiendo un turno en curso (single-flight por bot) y
JAMÁS spliceeando mensajes sintéticos (la entrada es un turno user-role
real entregado al orquestador).

Transporte v1: fila atribuida → turno real → respuesta persistida aquí.
Multi-proceso seguro vía claim SKIP LOCKED (un solo consumidor por fila).
"""

import asyncio
import contextlib
import logging
import re

import sqlalchemy as sa

from core import bots_chat, bots_messaging
from core.bots_gate import dm_sends_this_turn
from core.bots_messaging import hop_scope
from core.constants import wrap_untrusted
from core.database import get_async_session
from core.feature_flags import kairos
from orchestration.context import WorkflowEvents

logger = logging.getLogger(__name__)

_running_locks: dict[int, asyncio.Lock] = {}

# Anti ping-pong del espejo: respuestas de cortesía sin contenido no viajan
# (protocolo de bots_protocol.section_text: '(pass)' / 'sin respuesta necesaria')
_PASS_RE = re.compile(r"\(\s*pass\s*\)|sin respuesta necesaria", re.IGNORECASE)


def _lock_for(bot_id: int) -> asyncio.Lock:
    return _running_locks.setdefault(bot_id, asyncio.Lock())


def _ctx_query(body: str, *, source: str = "dm") -> str:
    """El cuerpo de un DM entrante (salida LLM de otro bot, entrega de
    rutina o nota humana) es DATO, no instrucción. Entra SIEMPRE encuadrado en
    los marcadores ⟪DATOS-NO-CONFIABLES⟫ con neutralización anti-frame-escape.

    Source-aware: las entregas de RUTINA (source='routine',
    from_handle='system') son el prompt programado por el USUARIO — config
    confiable, no salida de un peer. Sin el marco: en pruebas en vivo moises
    comentó "vaya envoltorio más raro" al recibir su propio prompt envuelto.
    La persistencia sí conserva el marco por seguridad de historial."""
    if source == "routine":
        return bots_messaging.compact_body(body)
    return wrap_untrusted(bots_messaging.compact_body(body))


def _dm_turn_directive(source: str, from_handle: str) -> str:
    """Round-trip A: instrucción CONFIABLE fuera del bloque no-confiable.

    La evidencia de 2 corridas es que el
    protocolo genérico del system prompt no basta: el bot lee el turno como un
    mensaje y responde en texto final, que muere en su chat eterno. El turno
    mismo debe decirle CÓMO responder. Es texto de sistema (confiable) anexado
    DESPUÉS del wrapper — nunca dentro (el body es dato)."""
    if source != "dm" or not from_handle.strip():
        return ""
    return (
        "\n\n[Instrucción del sistema] Este turno es un DM entrante de "
        f"@{from_handle.strip()}: tu texto final NO le llega automáticamente. "
        "Si merece respuesta, envíala con la herramienta send_to_bot "
        f"(target='{from_handle.strip()}'). Si no exige respuesta, responde '(pass)'."
    )


async def resolve_target_conversation(bot_slug: str) -> dict | None:
    """Acceso legible para llamadores/monitoreo; fail-closed vive en core."""
    return await bots_chat.resolve_canonical(bot_slug)


async def _release_claim(row_id) -> None:
    """Re-encolado sin conteo de fallos (bot ocupado — no es un error)."""
    try:
        async with get_async_session() as s:
            await s.execute(
                sa.text("UPDATE pending_turns SET claimed_at = NULL WHERE id = :i"),
                {"i": row_id},
            )
    except Exception as e:
        logger.warning("no pude liberar claim %s: %s", row_id, e)


async def fail_row(row_id) -> None:
    """Despacho fallido real ⇒ attempts++ y claim liberado.

    Con attempts >= DEAD_LETTER_ATTEMPTS la fila deja de reclamarse
    (dead-letter auditable en vez de retry infinito)."""
    try:
        async with get_async_session() as s:
            await s.execute(
                sa.text(
                    "UPDATE pending_turns "
                    "SET claimed_at = NULL, attempts = attempts + 1 WHERE id = :i"
                ),
                {"i": row_id},
            )
    except Exception as e:
        logger.warning("no pude marcar fallo %s: %s", row_id, e)


async def _heartbeat(row_id: int) -> None:
    """Mantiene vivo el claim mientras el turno se ejecuta.

    Un turno full-orchestration (300s x subtareas + reintentos) puede superar
    STALE_CLAIM_MINUTES; sin heartbeat, un segundo proceso re-claimaria y
    despacharia la misma fila en paralelo (doble LLM, doble persistencia)."""
    interval = max(1.0, bots_messaging.STALE_CLAIM_MINUTES * 60 / 2)
    while True:
        await asyncio.sleep(interval)
        try:
            await bots_messaging.touch_claim(row_id)
        except Exception as e:
            logger.warning("heartbeat %s falló: %s", row_id, e)


async def _mirror_silent_reply(slug: str, row: dict, assistant: str | None) -> bool:
    """Round-trip B: espejo runtime de la respuesta a un DM entrante.

    El protocolo promete 'su respuesta llegará como mensaje entrante', pero eso
    exige que el destino llame send_to_bot — y 2/2 corridas observadas fallaron
    (respuesta en texto final que muere en el chat eterno del receptor). Si el
    turno NO envió ningún DM (contador de bots_gate) y hay texto final con
    contenido, se encola como DM de vuelta al remitente.

    Anti-bucle: corre DENTRO de hop_scope — cada espejo consume un salto del
    TTL (hops=0 ⇒ DmLimitError y la cadena muere); '(pass)' no viaja; rutinas
    y notas sin remitente no se espejan. Best-effort: un fallo NO invalida la
    entrega ya persistida (devuelve False y loguea)."""
    if row.get("source") != "dm":
        return False
    sender = str(row.get("from_handle") or "").strip()
    if not sender or not isinstance(assistant, str) or not assistant.strip():
        return False
    if dm_sends_this_turn() > 0:
        return False  # el bot ya respondió por tool — no duplicar
    reply = assistant.strip()
    if _PASS_RE.search(reply[:200]):
        return False  # cortesía anti ping-pong del protocolo
    try:
        with hop_scope(int(row.get("hops") or 0)):
            ack = await bots_messaging.send_dm(slug, sender, reply)
        logger.info(
            "reply espejado %s→%s (pos=%s, hops=%s)",
            slug,
            sender,
            ack.get("position"),
            int(row.get("hops") or 0) - 1,
        )
        return True
    except Exception as e:
        logger.warning("espejo de reply %s→%s falló: %s", slug, sender, e)
        return False


async def dispatch_row(row: dict) -> bool:
    """Ejecuta UNA fila reclamada como turno real del bot destino."""
    bot_id = int(row["bot_id"])
    lock = _lock_for(bot_id)
    if lock.locked():
        # idle-only: no interrumpe; devuelve la fila a la cola ordenada
        logger.info("bot %d ocupado — fila %d re-encolada", bot_id, row["id"])
        await _release_claim(row["id"])
        return False

    async with lock:
        async with get_async_session() as s:
            bot_row = (
                await s.execute(
                    sa.text("SELECT slug, enabled FROM bots WHERE id = :b"), {"b": bot_id}
                )
            ).first()
        if bot_row is None:
            # sin fail_row la fila cicla en claims inútiles para siempre
            logger.warning("fila %s sin bot vivo (%s)", row.get("id"), bot_id)
            await fail_row(row.get("id"))
            return False
        if not bot_row.enabled:
            # un bot apagado no consume su inbox (no es un fallo del
            # mensaje — se re-encola sin attempts++ para cuando se re-active)
            logger.info("bot %d deshabilitado — fila %s re-encolada", bot_id, row.get("id"))
            await _release_claim(row["id"])
            return False
        slug = str(bot_row.slug)

        canonical = await bots_chat.resolve_canonical(slug)
        if canonical is None:
            # acuñar bajo demanda: un turno nunca se pierde
            canonical = await bots_chat.ensure_open(slug)
        conv_id = int(canonical["conversation_id"])

        # El turno corre por bots_runner con la identidad
        # del bot — SIN plantillas de workflow.
        from orchestration.bots_runner import run_bot_turn

        source = str(row.get("source") or "")

        # ── Las RUTINAS son turnos canónicos ──
        # transport="routine" (NO "dm"): sin protocolo DM (send_to_bot
        # violaría la deny-list de rutinas), sin TTL de cadena y con
        # persistencia canónica SIN wrapper ⟪DATOS-NO-CONFIABLES⟫ — el
        # historial del chat canónico queda limpio.
        if source == "routine":
            hb = asyncio.create_task(_heartbeat(int(row["id"])))
            try:
                final = await run_bot_turn(
                    slug,
                    str(row["body"]),  # limpio: un prompt de rutina es confiable
                    conv_id,
                    transport="routine",  # dm_enabled=False, sin pausas, sin protocolo DM
                    events=WorkflowEvents(),
                )
            finally:
                hb.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await hb

            body = str(row["body"])
            assistant = final if (isinstance(final, str) and final.strip()) else None
            note = None if assistant else "[entrega sin respuesta del modelo]"
            persisted = await bots_messaging.persist_canonical_exchange(
                conv_id, body=body, assistant=assistant, note=note
            )
            if persisted:
                await bots_messaging.complete_row(int(row["id"]))
            else:
                logger.error("persistencia falló para fila %s — re-intento", row.get("id"))
                await fail_row(int(row["id"]))
                return False
            logger.info("rutina entregada a @%s (conv #%d, transporte routine)", slug, conv_id)
            return True

        # TTL de cadena: los send_dm encadenados DE este turno decrecen aquí;
        # heartbeat en paralelo mantiene el claim vivo
        hb = asyncio.create_task(_heartbeat(int(row["id"])))
        try:
            with hop_scope(int(row.get("hops") or 0)):
                final = await run_bot_turn(
                    slug,
                    _ctx_query(str(row["body"]), source=source),
                    conv_id,
                    transport="dm",
                    extra_query=_dm_turn_directive(source, str(row.get("from_handle") or "")),
                    events=WorkflowEvents(),
                )
        finally:
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb

        body = str(row["body"])
        if isinstance(final, str) and final.strip():
            note = None
            assistant = final
        else:
            assistant = None
            note = "[entrega sin respuesta del modelo]"
        persisted = await bots_messaging.persist_turn_exchange(
            conv_id, body=body, assistant=assistant, note=note
        )
        if persisted:
            # entrega terminal — la fila sale de la cola; sin esto el
            # claim stale la re-despachaba cada ~10min indefinidamente
            await bots_messaging.complete_row(int(row["id"]))
            # Round-trip B: si el bot respondió solo con texto final, la respuesta
            # viaja por espejo (best-effort; ya después de la entrega persistida)
            await _mirror_silent_reply(slug, row, assistant)
        else:
            # sin persistencia no hay entrega — la fila sobrevive para
            # re-intento (attempts++; el dead-letter acota a N intentos). El
            # re-despacho re-ejecuta el turno, pero conserva la posibilidad
            # de persistir el intercambio.
            logger.error("persistencia falló para fila %s — re-intento", row.get("id"))
            await fail_row(int(row["id"]))
            return False

        logger.info("turno entregado a @%s (conv #%d, hops=%d)", slug, conv_id, row.get("hops"))
        return True


async def drain_tick() -> int:
    """Un tick del despertador: reclama FIFO y despacha lo posible."""
    try:
        rows = await bots_messaging.claim_pending(limit=10)
    except Exception as e:
        logger.warning("claim_pending falló: %s", e)
        return 0
    delivered = 0
    for row in rows:
        try:
            if await dispatch_row(row):
                delivered += 1
        except Exception as e:
            logger.error("despacho de fila %s falló: %s", row.get("id"), e)
            await fail_row(row.get("id"))  # dead-letter tras N fallos
    return delivered


async def bots_wake_loop(interval_s: float | None = None) -> None:
    """Daemon machine-local: SOLO drena dentro de este workspace."""
    if interval_s is None:
        interval_s = float(kairos.get("bots.wake_interval_seconds") or 5.0)
    logger.info("bots wake loop ON (%.1fs)", interval_s)
    while True:
        try:
            await drain_tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("wake tick error (continúa): %s", e)
        await asyncio.sleep(interval_s)
