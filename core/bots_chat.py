# core/bots_chat.py — el chat canónico eterno (contrato 08 §2)
"""Apertura y acuñación del chat canónico por NOMBRE.

Contratos:
- Abrir = lookup por índice único parcial (window-free, incluye
  hidden). Un error de BD es FAIL-CLOSED: se informa y JAMÁS se interpreta
  como "no existe".
- Crear = adopt-before-mint (re-lookup dentro de la sección crítica) +
  nace ``is_hidden=True`` + título exacto "Bot Chat" + kickoff encolado.
- Canónicos SIEMPRE ocultos; sweep reconciliador idempotente.
- Preview == click: ambos leen ESTA resolución server-side.

Identidad: conversación ``(bot_id, title='Bot Chat')`` — jamás un puntero
session-id persistido.
"""

import asyncio
import logging

from sqlalchemy import select, update

from core.bots import BotError
from core.database import get_async_session
from core.models import BOT_CHAT_TITLE, Bot, Conversation, Message

logger = logging.getLogger(__name__)

# Single-flight in-process: una sola acuñación concurrente por slug.
_mint_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


def _lock_for(slug: str) -> asyncio.Lock:
    return _mint_locks.setdefault(slug, asyncio.Lock())


async def _canonical_row(session, bot_id: int) -> Conversation | None:
    """Lookup EXACTO por índice único — sin ventana temporal ni orden."""
    stmt = select(Conversation).where(
        Conversation.bot_id == bot_id,  # type: ignore[arg-type]
        Conversation.is_canonical.is_(True),  # type: ignore[attr-defined]
    )
    res = await session.execute(stmt)
    return res.scalars().first()


async def resolve_canonical(bot_slug: str) -> dict | None:
    """Registro actual del chat eterno (o None si nunca existió).

    FAIL-CLOSED: cualquier error de infraestructura se traduce en un
    error visible con acción clara — nunca en None silencioso.
    """
    try:
        async with get_async_session() as session:
            bot = (
                await session.execute(
                    select(Bot).where(Bot.slug == bot_slug)  # type: ignore[arg-type]
                )
            ).scalar_one_or_none()
            if bot is None:
                raise BotError(f"no existe el bot '{bot_slug}' en este workspace")
            row = await _canonical_row(session, bot.id)
            if row is None:
                return None
            return {
                "conversation_id": row.id,
                "title": row.title,
                "is_hidden": row.is_hidden,
                "capability_epoch": row.capability_epoch,
            }
    except BotError:
        raise
    except Exception as e:
        logger.error("resolve_canonical falló para '%s': %s", bot_slug, e)
        raise BotError(
            f"no se pudo verificar el registro de '{bot_slug}' — reintenta; "
            "la apertura NO acuña bajo error"
        ) from e


async def ensure_open(bot_slug: str, *, intro: str | None = None) -> dict:
    """Devuelve la fila canónica; si no existe, la acuña UNA vez.

    - Adopt-before-mint: dentro del lock se re-ejecuta el lookup exacto.
    - Nace hidden y con kickoff encolado como primer turno del bot.
    """
    lock = _lock_for(bot_slug)
    existing = await resolve_canonical(bot_slug)
    if existing is not None:
        return existing
    async with lock:
        existing = await resolve_canonical(bot_slug)
        if existing is not None:
            return existing  # adoptada (carrera o preexistente)

        try:
            async with get_async_session() as session:
                bot = (
                    await session.execute(
                        select(Bot).where(Bot.slug == bot_slug)  # type: ignore[arg-type]
                    )
                ).scalar_one_or_none()
                if bot is None:
                    raise BotError(f"no existe el bot '{bot_slug}'")

                conv = Conversation(
                    title=BOT_CHAT_TITLE,
                    bot_id=bot.id,
                    is_canonical=True,
                    is_hidden=True,
                )
                session.add(conv)
                await session.flush()

                msg = Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=intro or f"Soy {bot.display_name}. ¿En qué te ayudo?",
                )
                session.add(msg)
                conversation_id = conv.id
            logger.info("Chat canónico acuñado para '%s' (conv %s)", bot_slug, conversation_id)
        except BotError:
            raise
        except Exception as e:
            logger.error("acuñación falló para '%s': %s", bot_slug, e)
            raise BotError(
                f"no se pudo verificar/acuñar el registro de '{bot_slug}' — reintenta"
            ) from e
        return {
            "conversation_id": conversation_id,
            "title": BOT_CHAT_TITLE,
            "is_hidden": True,
            "capability_epoch": None,
        }


async def canonical_owner_of(conversation_id: int) -> dict | None:
    """Devuelve {'slug','conversation_id'} si la conversación ES un chat
    canónico de bot habilitado; None para cualquier otra (incluidas Group:/hidden).

    Errores de BD → None con log (consulta AUXILIAR: el fail-closed duro aplica
    en la ruta de apertura, no aquí)."""
    try:
        async with get_async_session() as session:
            row = await session.get(Conversation, conversation_id)
            if row is None or not row.is_canonical or row.bot_id is None:
                return None
            bot = await session.get(Bot, row.bot_id)
            if bot is None or not bot.enabled:
                return None
            return {"slug": bot.slug, "conversation_id": row.id}
    except Exception as e:
        logger.warning("canonical_owner_of falló conv=%s: %s", conversation_id, e)
        return None


# ── Sweep reconciliador ──────────────────────────────────────────────────────


async def sweep_hidden_bot_chats() -> int:
    """Reconoce todo lo que DEBE estar oculto y lo oculta. Idempotente.

    Reglas (por títulos, jamás toca titulados de usuario):
    - Canónicos 'Bot Chat': siempre ``is_hidden=True`` (aunque algo los
      desocultara por bug o migración legada).
    - Sesiones miembro de grupos del bot: prefijo ``Group:`` asociadas a un
      ``bot_id``.
    Devuelve cuántas filas reconcilió.
    """
    try:
        async with get_async_session() as session:
            res = await session.execute(
                update(Conversation)
                .where(
                    Conversation.bot_id.is_not(None),  # type: ignore[union-attr]
                    Conversation.is_hidden.is_(False),  # type: ignore[attr-defined]
                    (  # type: ignore[union-attr]
                        Conversation.title.in_(["Bot Chat"])  # type: ignore[attr-defined]
                        | Conversation.title.like("Group: %")  # type: ignore[attr-defined]
                    ),
                )
                .values(is_hidden=True)
            )
            changed = getattr(res, "rowcount", 0) or 0
        if changed:
            logger.info("sweep ocultó %d conversación(es) de bots", changed)
        return int(changed)
    except Exception as e:
        logger.warning("sweep_hidden_bot_chats no pudo ejecutarse: %s", e)
        return 0


async def repair_duplicate_canonicals() -> int:
    """Defensa espejo de TestSessionTitleIndexRepair: demueve duplicados
    conservando el más reciente como canónico (los demás quedan ocultos)."""
    try:
        async with get_async_session() as session:
            rows = (
                (
                    await session.execute(
                        select(Conversation).where(Conversation.is_canonical.is_(True))  # type: ignore[attr-defined]
                    )
                )
                .scalars()
                .all()
            )
            newest_by_bot: dict[int, Conversation] = {}
            for r in sorted(rows, key=lambda c: c.id or 0):
                if r.bot_id is not None:
                    newest_by_bot[r.bot_id] = r
            demoted = 0
            for r in rows:
                if r.bot_id is not None and newest_by_bot.get(r.bot_id) is not r:
                    r.is_canonical = False
                    r.is_hidden = True
                    session.add(r)
                    demoted += 1
            if demoted:
                logger.warning("reparación: %d duplicados canónicos demovidos", demoted)
            return demoted
    except Exception as e:
        logger.warning("repair_duplicate_canonicals no pudo ejecutarse: %s", e)
        return 0
