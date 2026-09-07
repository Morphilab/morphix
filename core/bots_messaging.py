# core/bots_messaging.py — cola de turnos entre bots
"""Inbox real del bot: tabla ``pending_turns`` como única cola.

Semántica de la cola:
- fire-and-forget: el envío acusa recibo INMEDIATO ({status:"sent"});
  la respuesta del destino llegará después como turno entrante.
- atribución server-side: el cuerpo se construye aquí con prefijo
  "Message from 🤖 <display> (@<slug>): ..." — el emisor no puede falsificarlo.
- cap de caracteres 16k y validaciones con roster accionable.
- TTL anti-bucle: ``hops`` desciende por cada re-envío
  en cadena dentro de un despacho; a 0 la cadena muere con error claro.

Solo almacena y aplica reglas: el DESPACHO (ejecutar el turno destino) vive
en orchestration/bots_wake.py para no invertir dependencias.
"""

import logging
import re
from contextvars import ContextVar

from sqlalchemy import func, select, text

from core.bots import BotError
from core.bots_registry import UnknownTargetError, resolve_target
from core.constants import wrap_untrusted
from core.database import get_async_session
from core.models import Message, PendingTurn

logger = logging.getLogger(__name__)

MAX_DM_CHARS = 16000
DEFAULT_HOPS = 2

_current_hops: ContextVar[int | None] = ContextVar("bots_current_hops", default=None)

_PEERS_HINT = "targets cross-machine ('<peer>/<agente>') están fuera de alcance v1"


class DmLimitError(BotError):
    """Mensaje por encima del cap o cadena agotada por TTL."""


def attribution_prefix(display_name: str, slug: str) -> str:
    return f"Message from 🤖 {display_name} (@{slug}): "


async def pending_count(slug: str) -> int:

    async with get_async_session() as session:
        bot = (
            await session.execute(text("SELECT id FROM bots WHERE slug = :s"), {"s": slug})
        ).scalar()
        if bot is None:
            raise BotError(f"no existe el bot '{slug}'")
        n = (
            await session.execute(
                select(func.count())
                .select_from(PendingTurn)
                .where(  # type: ignore[call-overload]
                    PendingTurn.bot_id == bot,  # type: ignore[arg-type]
                    PendingTurn.claimed_at.is_(None),  # type: ignore[attr-defined,union-attr]
                )
            )
        ).scalar()
        return int(n or 0)


async def send_dm(sender_slug: str, target: str, message: str) -> dict:
    """Valida, atribuye y ENCOLA un DM. Jamás bloquea al emisor.

    Returns:
        {"status": "sent", "to": "<slug>", "position": n} tras el INSERT.

    Raises:
        BotError / UnknownTargetError: target inválido, self-block, roster vacío…
        DmLimitError: >MAX_DM_CHARS o cadena con TTL agotado.
    """
    if not isinstance(message, str) or not message.strip():
        raise BotError("message vacío — escribe algo útil antes de enviar")

    body_text = message.strip()
    if len(body_text) > MAX_DM_CHARS:
        raise DmLimitError(
            f"message supera el cap de {MAX_DM_CHARS} caracteres "
            f"({len(body_text)}): divide tu mensaje"
        )

    clean_target = (target or "").strip().lstrip("@")
    dest = await resolve_target(clean_target, sender_slug=sender_slug)

    # Checks puros (sin BD) ANTES de abrir sesión: un desenlace esperado y
    # accionable (peers / TTL) no debe cruzar el context manager de sesión,
    # que lo registraría como ERROR falso (core/database.py) — la evidencia
    # del run 04:53: un TTL agotado se logueó como "Error en
    # sesión asíncrona" cuando en realidad el modelo lo recuperó bien.
    if _PEERS_HINT in clean_target or "/" in clean_target:
        raise UnknownTargetError(_PEERS_HINT)

    # Hops (TTL anti-bucle): dentro de un despacho el presupuesto decrece
    # POR CADA re-envío (set dinámico); a 0 la cadena muere con error claro.
    carried = _current_hops.get()
    if carried is not None:
        if carried <= 0:
            logger.warning(
                "cadena DM descartada por TTL agotado (%s→%s)", sender_slug, dest["slug"]
            )
            raise DmLimitError("la cadena de mensajes alcanzó su límite de saltos (TTL)")
        hops = carried - 1
        _current_hops.set(hops)  # decay visible para los siguientes sends
    else:
        hops = DEFAULT_HOPS

    # Resolución del emisor SOLO para el prefijo (ya validado por caller)
    async with get_async_session() as session:
        sender_row = (
            await session.execute(
                text("SELECT id, display_name FROM bots WHERE slug = :s"),
                {"s": sender_slug},
            )
        ).first()
        dest_id = (
            await session.execute(text("SELECT id FROM bots WHERE slug = :s"), {"s": dest["slug"]})
        ).scalar()

        row = PendingTurn(
            bot_id=int(dest_id),
            source="dm",
            from_handle=f"{sender_slug}",
            body=attribution_prefix(sender_row.display_name, sender_slug) + body_text,
            hops=hops,
        )
        session.add(row)
        await session.flush()
        position = int(row.id or 0)

    logger.info("DM encolado %s→%s (pos=%d, hops=%d)", sender_slug, dest["slug"], position, hops)
    return {"status": "sent", "to": dest["slug"], "position": position}


# ── Claim atómico FIFO (una sola toma por fila, multi-proceso safe) ────────

_CLAIM_SQL = """
UPDATE pending_turns SET claimed_at = NOW()
WHERE id IN (
    SELECT id FROM pending_turns
    WHERE (
        claimed_at IS NULL
        OR claimed_at < NOW() - {stale_interval} * INTERVAL '1 minute'
    )
    AND attempts < {max_attempts}
    AND bot_id NOT IN (SELECT id FROM bots WHERE enabled = FALSE)
    {extra_filter}
    ORDER BY id
    LIMIT :n
    FOR UPDATE SKIP LOCKED
)
RETURNING id, bot_id, source, from_handle, body, hops, created_at;
"""

# tras N despachos fallidos la fila queda en dead-letter silenciosa
# (auditable vía attempts>=N); evita retry infinito de filas venenosas.
DEAD_LETTER_ATTEMPTS = 5
# un proceso muerto a mitad de despacho deja claims huérfanos:
# pasados STALE_CLAIM_MINUTES la fila vuelve a ser reclamable.
STALE_CLAIM_MINUTES = 10


async def claim_pending(limit: int = 10, *, bot_id: int | None = None) -> list[dict]:
    extra = ""
    params: dict = {"n": limit}
    if bot_id is not None:
        extra = "AND bot_id = :b"
        params["b"] = bot_id
    # solo replace — .format() explota con KeyError ante los
    # placeholders {stale_interval}/{max_attempts} del template.
    sql = (
        _CLAIM_SQL.replace("{extra_filter}", extra)
        .replace("{max_attempts}", str(DEAD_LETTER_ATTEMPTS))
        .replace("{stale_interval}", str(STALE_CLAIM_MINUTES))
    )
    async with get_async_session() as session:
        res = await session.execute(text(sql), params)
        rows = res.mappings().all()
        return [dict(r) for r in rows]


async def touch_claim(row_id: int) -> None:
    """Heartbeat del despacho — refresca ``claimed_at`` para que un
    turno largo (full-orchestration, 300s x subtareas) no sea re-claimado por
    otro proceso al vencer la ventana stale."""
    async with get_async_session() as session:
        await session.execute(
            text("UPDATE pending_turns SET claimed_at = NOW() WHERE id = :i"), {"i": row_id}
        )


async def complete_row(row_id: int) -> None:
    """Entrega exitosa ⇒ estado terminal.

    Sin esto, la fila con ``claimed_at`` vencido volvia a calificar en el
    claim stale cada ~10 min: re-despacho eterno del mismo turno (coste LLM
    no acotado + respuestas duplicadas). El dead-letter (attempts>=N) sigue
    cubriendo los fallos; una fila eliminada no puede resucitar."""
    async with get_async_session() as session:
        await session.execute(text("DELETE FROM pending_turns WHERE id = :i"), {"i": row_id})


def hop_scope(hops: int | None):
    """Contexto de despacho: los send_dm encadenados usan ESTE presupuesto."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        token = _current_hops.set(hops)
        try:
            yield
        finally:
            _current_hops.reset(token)

    return _ctx()


_DMLIKE_RE = re.compile(r"\s+")


def compact_body(body: str, limit: int = MAX_DM_CHARS) -> str:
    """Normaliza espacios del cuerpo entregado como turno usuario."""
    return _DMLIKE_RE.sub(" ", (body or "").strip())[:limit]


async def persist_turn_exchange(
    conv_id: int, *, body: str, assistant: str | None, note: str | None = None
) -> bool:
    """Persiste el intercambio en el chat eterno (usuario atribuido +
    respuesta del bot, o nota explicativa si el modelo no respondió).

    El body (salida LLM de otro bot, entrega de rutina o nota) es
    DATO no confiable — se persiste DENTRO de los marcadores para que la
    continuidad de historial no lo re-inyecte limpio al contexto del LLM
    (el turno fresco ya va envuelto vía bots_wake._ctx_query).

    Returns:
        True si el intercambio quedó persistido. False ante fallo —
        el caller NO debe considerar entregado el turno (la fila
        debe sobrevivir para re-intento con dead-letter acotando)."""
    try:
        from datetime import UTC, datetime

        now = datetime.now(UTC).replace(tzinfo=None)
        async with get_async_session() as session:
            session.add(
                Message(
                    conversation_id=conv_id,
                    role="user",
                    content=wrap_untrusted(str(body)[:7800])[:8000],
                    timestamp=now,
                )
            )
            payload = assistant if (assistant and str(assistant).strip()) else note
            if payload:
                session.add(
                    Message(
                        conversation_id=conv_id,
                        role="assistant",
                        content=str(payload)[:8000],
                        timestamp=now,
                    )
                )
            logger.info("intercambio persistido conv=%d", conv_id)
            return True
    except Exception as e:
        logger.error("persistencia del intercambio falló conv=%s: %s", conv_id, e)
        return False


async def persist_canonical_exchange(
    conv_id: int, *, body: str, assistant: str | None, note: str | None = None
) -> bool:
    """Persiste un turno CANÓNICO del bot (usuario-sistema + respuesta) SIN el
    marco no-confiable.

    RC-Botmode: las entregas de rutina despiertan turnos cuyo body
    es prompt del sistema ('[Rutina] …') — persistirlo con el wrapper INT-M3 de
    DMs lo marcaba como dato hostil en el historial eterno y contaminaba todo
    contexto futuro del bot. El turno fresco ya va limpio (source-aware); aquí
    el historial también. Mismo contrato de retorno que persist_turn_exchange
    (False ante fallo ⇒ la fila sobrevive para re-intento)."""
    try:
        from datetime import UTC, datetime

        now = datetime.now(UTC).replace(tzinfo=None)
        async with get_async_session() as session:
            session.add(
                Message(
                    conversation_id=conv_id,
                    role="user",
                    content=str(body)[:8000],
                    timestamp=now,
                )
            )
            payload = assistant if (assistant and str(assistant).strip()) else note
            if payload:
                session.add(
                    Message(
                        conversation_id=conv_id,
                        role="assistant",
                        content=str(payload)[:8000],
                        timestamp=now,
                    )
                )
            logger.info("intercambio canónico persistido conv=%d", conv_id)
            return True
    except Exception as e:
        logger.error("persistencia del intercambio canónico falló conv=%s: %s", conv_id, e)
        return False
