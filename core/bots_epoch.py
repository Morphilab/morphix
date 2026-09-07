# core/bots_epoch.py — capability epoch del chat canónico
"""Fingerprint de capacidades del bot → rebuild ÚNICO del system prompt.

Contrato de caching ("prompt caching sagrado"): el system message del chat eterno es
byte-estable salvo UN rebuild por cambio real de capacidades. La marca vive en
``conversation.capability_epoch`` (SHA-256[:12]) y se verifica al ENTRAR al
turno. Si el cálculo falla (probe roto), fail-closed: NO refrescar.
"""

import hashlib
import json
import logging

from sqlalchemy import select

from core.bots import BotError
from core.database import get_async_session
from core.models import Bot, Conversation

logger = logging.getLogger(__name__)


def compute_fingerprint(bot: dict, roster: list[dict] | None = None) -> str:
    """SHA-256[:12] determinista de las capacidades que afectan el prompt.

    Ejes: provider, model, temperature, tool_names, skill_allowlist,
    soul_sha256 y roster+roles (los cambios de TEAMS afectan la sección de
    protocolo inter-bot, incluida por contrato).
    """
    payload = {
        "provider": bot.get("provider"),
        "model": bot.get("model"),
        "temperature": bot.get("temperature"),
        "tools": sorted(list(bot.get("tool_names") or [])),
        "skills": sorted(list(bot.get("skill_allowlist") or [])),
        "soul_sha": hashlib.sha256((bot.get("soul_md") or "").encode()).hexdigest()[:12],
        "roster": sorted({"@r" for r in (roster or [])}),  # placeholder replaced below
        "roster_slugs": sorted(r["slug"] for r in (roster or []) if r.get("slug")),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


async def current_stored_epoch(conv_id: int) -> str | None:
    async with get_async_session() as session:
        row = await session.get(Conversation, conv_id)
        return row.capability_epoch if row else None


async def stamp_epoch(conv_id: int, fingerprint: str) -> None:
    async with get_async_session() as session:
        row = await session.get(Conversation, conv_id)
        if row is not None:
            row.capability_epoch = fingerprint
            session.add(row)


async def refresh_if_stale(slug: str) -> dict:
    """Verifica el epoch del canónico y lo actualiza EXACTAMENTE una vez.

    Returns:
        {"conversation_id", "epoch", "rebuild"} — ``rebuild`` True solo cuando
        el fingerprint cambió respecto al almacenado (o era el primer turno).

    Fail-closed: cualquier error de cómputo/BD deja ``rebuild=False``
    con el epoch almacenado intacto — jamás invalida caché por accidente.
    """
    try:

        bot = None
        canonical = None
        async with get_async_session() as session:
            bot_row = (
                await session.execute(select(Bot).where(Bot.slug == slug))  # type: ignore[arg-type]
            ).scalar_one_or_none()
            if bot_row is None:
                raise BotError(f"no existe el bot '{slug}'")
            canon_stmt = select(Conversation).where(
                Conversation.bot_id == bot_row.id,  # type: ignore[arg-type]
                Conversation.is_canonical.is_(True),  # type: ignore[attr-defined]
            )
            canonical = (await session.execute(canon_stmt)).scalars().first()

        # Roster vivo para el eje de protocolo inter-bot.
        # SIN try propio — un fallo aquí debe propagar al handler
        # fail-closed exterior. Tragar la excepción con roster=[] computaba
        # un fingerprint sin el eje de protocolo ≠ almacenado → rebuild
        # espurio del prompt cacheado (invalidación prohibida).
        from core.bots import BotsService

        roster = await BotsService.list_bots(include_disabled=True)

        fingerprint = compute_fingerprint(
            {
                "provider": bot_row.provider,
                "model": bot_row.model,
                "temperature": bot_row.temperature,
                "tool_names": bot_row.tool_names,
                "skill_allowlist": bot_row.skill_allowlist,
                "soul_md": bot_row.soul_md,
            },
            roster,
        )
        stored = canonical.capability_epoch if canonical is not None else None
        needs_rebuild = bool(canonical is not None and stored != fingerprint)

        if needs_rebuild and canonical is not None:
            await stamp_epoch(canonical.id, fingerprint)
            logger.info("capability epoch refrescado para '%s' (%s→%s)", slug, stored, fingerprint)
        return {
            "conversation_id": canonical.id if canonical else None,
            "epoch": fingerprint,
            "rebuild": needs_rebuild,
        }
    except Exception as e:
        logger.warning("epoch probe falló para '%s' (fail-closed): %s", slug, e)
        return {"conversation_id": None, "epoch": "", "rebuild": False}
