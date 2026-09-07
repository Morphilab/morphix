# core/bots_groups.py — salas grupales round-robin
"""Grupos: log ordenado client-side aquí (``group_messages.seq``
único por sala), caps duros, sesiones miembro ``Group: <roomId>`` SIEMPRE
ocultas y menciones deterministas.

Caps v1 clonados: ≤3 rondas seriales · ≤10 msgs por sesión grupal · ≤6
miembros · turno 24h · silencio 180s asienta '(pass)' · cap total 20 min.
El DRIVE de rondas vive en orchestration/bots_groups_drive.py (no invertir
dependencias); esta capa guarda estado y reglas.
"""

import logging
import secrets
import string

import sqlalchemy as sa
from sqlalchemy import func, select, text

from core.bots import BotError
from core.database import get_async_session
from core.models import Conversation, GroupMessage, GroupRoom

logger = logging.getLogger(__name__)

MAX_MEMBERS = 6
MAX_ROUNDS = 3
MAX_TOTAL_MSGS = 10
TURN_WINDOW_S = 24 * 3600
SILENCE_TIMEOUT_S = 180
SESSION_CAP_S = 20 * 60


class GroupError(BotError):
    pass


def new_room_id() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return (
        f"r{int(datetime_now().timestamp())}-{ ''.join(secrets.choice(alphabet) for _ in range(6))}"
    )


def datetime_now():
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=None)


# ── CRUD de salas ──────────────────────────────────────────────────────────


async def create_room(name: str, owner_slug: str, members: list[str]) -> dict:
    """Crea la sala; owner debe pertenecer a members (≤6). Renombrar conserva
    roomId; disband borra sala pero las sesiones miembro sobreviven."""
    if not name.strip():
        raise GroupError("nombre de sala requerido")
    clean = [m.strip().lstrip("@").lower() for m in members if m and m.strip()]
    owner = owner_slug.strip().lstrip("@").lower()
    if len(set(clean)) != len(clean):
        raise GroupError("miembros duplicados")
    if MAX_MEMBERS < 2 or len(clean) > MAX_MEMBERS or len(clean) < 2:
        raise GroupError(f"se requieren entre 2 y {MAX_MEMBERS} miembros distintos")
    if owner not in clean:
        raise GroupError("el owner debe ser uno de los miembros")

    async with get_async_session() as session:
        rows = (
            (await session.execute(sa.text("SELECT slug FROM bots WHERE enabled"))).scalars().all()
        )
        existing = {r for r in rows}
        missing = [m for m in clean if m not in existing]
        if missing:
            raise GroupError(f"bots inexistentes/inactivos: {', '.join(missing)}")

        room = GroupRoom(id=new_room_id(), name=name[:128], owner_bot_slug=owner, members=clean)
        session.add(room)
        await session.flush()
        await session.refresh(room)
        out = {
            "id": room.id,
            "name": room.name,
            "owner_bot_slug": room.owner_bot_slug,
            "members": list(room.members),
        }
    logger.info("grupo creado %s (%s) — owner @%s", out["id"], name, owner)
    return out


async def rename_room(room_id: str, new_name: str) -> bool:
    if not (new_name or "").strip():
        raise GroupError("nombre vacío")
    async with get_async_session() as session:
        res = await session.execute(
            sa.text("UPDATE group_rooms SET name=:n WHERE id=:i"),
            {"n": new_name.strip()[:128], "i": room_id},
        )
        return bool(getattr(res, "rowcount", 0))


async def disband_room(room_id: str) -> bool:
    """Borra sala + mensajes; conversaciones 'Group: <id>' de los bots
    permanecen intactas (historial personal de cada bot)."""
    async with get_async_session() as session:
        res = await session.execute(sa.text("DELETE FROM group_rooms WHERE id=:i"), {"i": room_id})
        return bool(getattr(res, "rowcount", 0))


async def get_room(room_id: str) -> dict | None:
    async with get_async_session() as s:
        row = (
            (
                await s.execute(
                    text("SELECT id,name,owner_bot_slug,members FROM group_rooms WHERE id=:i"),
                    {"i": room_id},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None


async def list_rooms() -> list[dict]:
    async with get_async_session() as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT id,name,owner_bot_slug,members FROM group_rooms ORDER BY created_at DESC"
                    )
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]


# ── Log ordenado ───────────────────────────────────────────────────────────


async def post_message(room_id: str, author: str, content: str) -> int:
    room = await get_room(room_id)
    if room is None:
        raise GroupError(f"sala inexistente '{room_id}'")
    if not content.strip():
        raise GroupError("mensaje vacío")
    async with get_async_session() as session:
        seq = (
            await session.execute(
                select(func.coalesce(func.max(GroupMessage.seq), 0)).where(  # type: ignore[call-overload]
                    GroupMessage.room_id == room_id  # type: ignore[arg-type]
                )
            )
        ).scalar() or 0
        msg = GroupMessage(
            room_id=room_id, author=author[:64], content=content[:16000], seq=int(seq) + 1
        )
        session.add(msg)
        await session.flush()
        out_seq = int(msg.seq)
    return out_seq


async def messages_of(room_id: str, limit: int = 100) -> list[dict]:
    async with get_async_session() as session:
        rows = (
            (
                await session.execute(
                    select(GroupMessage)
                    .where(GroupMessage.room_id == room_id)  # type: ignore[arg-type]
                    .order_by(GroupMessage.seq)  # type: ignore[arg-type]
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [{"author": m.author, "content": m.content, "seq": m.seq} for m in rows]


def parse_mentions(content: str, candidates: list[str]) -> list[str]:
    """Menciones deterministas POR ORDEN DE APARICIÓN en el texto.

    Duplicados colapsan a la primera aparición; desconocidos se ignoran.
    """
    import re

    if not content:
        return []
    known = {c.lower(): c for c in candidates if c}
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"@([a-z0-9][a-z0-9_-]{0,63})", content, re.IGNORECASE):
        key = m.group(1).lower()
        if key in known and key not in seen:
            seen.add(key)
            out.append(known[key])
    return out


async def ensure_member_session(bot_slug: str, bot_id: int, room_id: str) -> int:
    """Conversación oculta 'Group: <roomId>' DENTRO del schema del bot."""
    title = f"Group: {room_id}"
    async with get_async_session() as session:
        from sqlalchemy import and_

        stmt = select(Conversation).where(
            and_(
                Conversation.bot_id == bot_id,  # type: ignore[arg-type]
                Conversation.title == title,  # type: ignore[arg-type]
                Conversation.is_hidden.is_(True),  # type: ignore[attr-defined,arg-type]
            )
        )
        row = (await session.execute(stmt)).scalars().first()
        if row:
            return int(row.id)
        conv = Conversation(title=title, bot_id=bot_id, is_canonical=False, is_hidden=True)
        session.add(conv)
        await session.flush()
        cid = int(conv.id or 0)
    logger.info("sesión miembro creada para @%s en %s (conv #%d)", bot_slug, room_id, cid)
    return cid


async def member_sessions_state() -> dict[str, list[str]]:
    """Mapa roomId → lista de slugs con sesión abierta (para stranded-harvest/UI)."""
    async with get_async_session() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT c.bot_id, b.slug AS slug, c.title FROM conversation c "
                    "JOIN bots b ON b.id=c.bot_id WHERE c.title LIKE 'Group: %' "
                    "AND c.is_hidden"
                )
            )
        ).all()
        mapping: dict[str, list[str]] = {}
        for row in rows:
            title = str(row[2])
            slug = str(row[1])
            rid = title.split("Group: ", 1)[1]
            mapping.setdefault(rid, []).append(slug)
    return mapping


async def room_member_transcripts(room_id: str) -> dict[str, list[dict]]:
    """Transcripción de la sesión 'Group: <roomId>' de cada miembro.

    La memoria por sala de cada bot es invisible (conversaciones
    ocultas) — el visor de la GUI usa esto para mostrar 'qué piensa' cada
    bot en la sala. Mapea slug → lista de {role, content}."""
    async with get_async_session() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT b.slug AS slug, c.id AS conv_id FROM conversation c "
                    "JOIN bots b ON b.id=c.bot_id WHERE c.title=:t AND c.is_hidden"
                ),
                {"t": f"Group: {room_id}"},
            )
        ).all()
        out: dict[str, list[dict]] = {}
        for slug, conv_id in rows:
            msgs = (
                (
                    await session.execute(
                        sa.text(
                            "SELECT role, content FROM message WHERE conversation_id=:c "
                            "ORDER BY id"
                        ),
                        {"c": int(conv_id)},
                    )
                )
                .mappings()
                .all()
            )
            out[str(slug)] = [{"role": m["role"], "content": m["content"]} for m in msgs]
    return out
