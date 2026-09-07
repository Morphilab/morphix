# core/bots.py — Bot Mode: CRUD de bots con identidad persistente (contrato 08)
"""CRUD, validación y CAS de ui_meta para el subsistema Bot Mode.

- Un bot es una fila en ``bots`` dentro del schema de SU workspace
  ("islas por diseño"); cero tablas globales de sesiones.
- ``ui_meta`` server-authoritative con revisión optimista (CAS),
  tope de 64 KiB, y registro histórico que SOBREVIVE al borrado del bot.

Esta capa vive en ``core``: sin dependencias de UI ni de orquestación.
"""

import logging
import re

from sqlalchemy import delete as sqlalchemy_delete
from sqlalchemy import select

from core.database import get_async_session
from core.models import BOT_SLUG_PATTERN, Bot, BotMetaHistory

logger = logging.getLogger(__name__)

SLUG_RE = re.compile(BOT_SLUG_PATTERN)

BOT_UI_META_MAX_BYTES = 64 * 1024


class BotError(ValueError):
    """Error de dominio del subsistema bots (mensaje accionable)."""


def validate_slug(slug: str) -> str:
    """Normaliza y valida un slug de bot (^\\[a-z0-9\\]\\[a-z0-9_-\\]{0,63}$)."""
    if not isinstance(slug, str):
        raise BotError("slug debe ser texto")
    s = slug.strip()
    if not SLUG_RE.match(s):
        raise BotError(
            "slug inválido: usa 1-64 caracteres de [a-z0-9], empezando por "
            "letra o número (guión/guion_bajo permitidos después)"
        )
    return s


def _ensure_ui_meta_size(ui_meta: dict) -> None:
    import json

    raw = len(json.dumps(ui_meta, ensure_ascii=False).encode("utf-8"))
    if raw > BOT_UI_META_MAX_BYTES:
        raise BotError(f"ui_meta excede {BOT_UI_META_MAX_BYTES // 1024} KiB ({raw} bytes)")


def _bot_dict(b: Bot) -> dict:
    return {
        "id": b.id,
        "slug": b.slug,
        "display_name": b.display_name,
        "description": b.description,
        "soul_md": b.soul_md,
        "provider": b.provider,
        "model": b.model,
        "temperature": b.temperature,
        "tool_names": list(b.tool_names or []),
        "skill_allowlist": list(b.skill_allowlist or []),
        "memory_prefix": b.memory_prefix,
        "ui_meta": dict(b.ui_meta or {}),
        "ui_meta_rev": b.ui_meta_rev,
        "enabled": b.enabled,
    }


class BotsService:
    """Operaciones asíncronas sobre la tabla ``bots`` (schema activo)."""

    @staticmethod
    async def create_bot(
        slug: str,
        display_name: str | None = None,
        description: str = "",
        soul_md: str = "",
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        tool_names: list[str] | None = None,
        skill_allowlist: list[str] | None = None,
        enabled: bool = True,
    ) -> dict:
        """Crea un bot. Rinde error accionable si el slug ya existe."""
        s = validate_slug(slug)
        name = (display_name or s)[:64]
        async with get_async_session() as session:
            existing = (
                await session.execute(select(Bot).where(Bot.slug == s))  # type: ignore[arg-type]
            ).scalar_one_or_none()
            if existing is not None:
                raise BotError(f"ya existe un bot '{s}' en este workspace")
            bot = Bot(
                slug=s,
                display_name=name,
                description=description,
                soul_md=soul_md,
                provider=provider,
                model=model,
                temperature=temperature,
                tool_names=list(tool_names or []),
                skill_allowlist=list(skill_allowlist or []),
                memory_prefix=f"bots/{s}",
                ui_meta={},
                ui_meta_rev=0,
                enabled=enabled,
            )
            session.add(bot)
            await session.flush()
            await session.refresh(bot)
            out = _bot_dict(bot)
        logger.info("Bot creado: %s", s)
        return out

    @staticmethod
    async def get_bot(slug: str) -> dict | None:
        s = validate_slug(slug)
        async with get_async_session() as session:
            bot = (
                await session.execute(select(Bot).where(Bot.slug == s))  # type: ignore[arg-type]
            ).scalar_one_or_none()
            return _bot_dict(bot) if bot else None

    @staticmethod
    async def list_bots(include_disabled: bool = True) -> list[dict]:
        """Roster del workspace ordenado por slug."""
        async with get_async_session() as session:
            stmt = select(Bot).order_by(Bot.slug)  # type: ignore[arg-type]
            if not include_disabled:
                stmt = stmt.where(Bot.enabled.is_(True))  # type: ignore[attr-defined,union-attr]
            rows = (await session.execute(stmt)).scalars().all()
            return [_bot_dict(b) for b in rows]

    @staticmethod
    async def update_bot(
        slug: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        soul_md: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        tool_names: list[str] | None = None,
        skill_allowlist: list[str] | None = None,
        enabled: bool | None = None,
    ) -> dict:
        """Actualiza campos editables; los cambios de capacidades disparan
        capability epoch en capas superiores — aquí solo persistencia."""
        s = validate_slug(slug)
        async with get_async_session() as session:
            bot = (
                await session.execute(select(Bot).where(Bot.slug == s))  # type: ignore[arg-type]
            ).scalar_one_or_none()
            if bot is None:
                raise BotError(f"no existe el bot '{s}'")
            if display_name is not None:
                bot.display_name = display_name[:64]
            if description is not None:
                bot.description = description
            if soul_md is not None:
                bot.soul_md = soul_md
            if provider is not None:
                bot.provider = provider
            if model is not None:
                bot.model = model
            if temperature is not None:
                bot.temperature = temperature
            if tool_names is not None:
                bot.tool_names = list(tool_names)
            if skill_allowlist is not None:
                bot.skill_allowlist = list(skill_allowlist)
            if enabled is not None:
                bot.enabled = enabled
            session.add(bot)
            await session.flush()
            await session.refresh(bot)
            out = _bot_dict(bot)
        logger.info("Bot actualizado: %s", s)
        return out

    @staticmethod
    async def set_ui_meta(slug: str, ui_meta: dict, expected_rev: int) -> dict:
        """CAS de ui_meta: rechaza revisiones obsoletas con error claro.

        Toda revisión exitosa queda registrada en ``bot_meta_history`` y
        sobrevive al borrado posterior del bot.
        """
        s = validate_slug(slug)
        _ensure_ui_meta_size(ui_meta)
        async with get_async_session() as session:
            bot = (
                await session.execute(select(Bot).where(Bot.slug == s))  # type: ignore[arg-type]
            ).scalar_one_or_none()
            if bot is None:
                raise BotError(f"no existe el bot '{s}'")
            if bot.ui_meta_rev != expected_rev:
                raise BotError(
                    f"ui_meta_rev desfasado: esperaba {expected_rev}, "
                    f"actual {bot.ui_meta_rev} — recarga y reintenta"
                )
            new_rev = bot.ui_meta_rev + 1
            session.add(
                BotMetaHistory(bot_slug=s, rev=new_rev, ui_meta=dict(ui_meta), tombstone=False)
            )
            bot.ui_meta = dict(ui_meta)
            bot.ui_meta_rev = new_rev
            session.add(bot)
            await session.flush()
            await session.refresh(bot)
            out = _bot_dict(bot)
        logger.info("ui_meta %s actualizado a rev %d", s, new_rev)
        return out

    @staticmethod
    async def meta_history(slug: str) -> list[dict]:
        """Revisiones históricas (incluye tombstones post-borrado)."""
        s = validate_slug(slug)
        async with get_async_session() as session:
            rows = (
                (
                    await session.execute(
                        select(BotMetaHistory)
                        .where(BotMetaHistory.bot_slug == s)  # type: ignore[arg-type]
                        .order_by(BotMetaHistory.rev)  # type: ignore[arg-type]
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "bot_slug": r.bot_slug,
                    "rev": r.rev,
                    "ui_meta": dict(r.ui_meta or {}),
                    "tombstone": r.tombstone,
                    "recorded_at": r.recorded_at,
                }
                for r in rows
            ]

    @staticmethod
    async def delete_bot(slug: str) -> bool:
        """Elimina el bot; el CASCADE limpia su chat canónico, rutinas e inbox.

        La última revisión de ui_meta se marca como tombstone antes de borrar:
        las revisiones sobreviven al borrado.
        """
        s = validate_slug(slug)
        async with get_async_session() as session:
            bot = (
                await session.execute(select(Bot).where(Bot.slug == s))  # type: ignore[arg-type]
            ).scalar_one_or_none()
            if bot is None:
                return False
            session.add(
                BotMetaHistory(
                    bot_slug=s,
                    rev=bot.ui_meta_rev,
                    ui_meta=dict(bot.ui_meta or {}),
                    tombstone=True,
                )
            )
            await session.delete(bot)  # FK ON DELETE CASCADE
            logger.info("Bot eliminado (con tombstone en historial): %s", s)
            return True

    @staticmethod
    async def purge_history(slug: str) -> int:
        """Borra el historial ui_meta de un slug (acción explícita del usuario;
        nunca automática — C10 garantiza supervivencia por defecto)."""
        s = validate_slug(slug)
        async with get_async_session() as session:
            result = await session.execute(
                sqlalchemy_delete(BotMetaHistory).where(BotMetaHistory.bot_slug == s)  # type: ignore[arg-type]
            )
            deleted = getattr(result, "rowcount", 0) or 0
        logger.info("Historial ui_meta purgado para '%s': %d filas", s, deleted)
        return int(deleted)
