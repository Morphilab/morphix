# tests/test_bots_chat.py
"""Apertura/acuñación del chat canónico + ocultación reconciliada."""

import secrets

import pytest

from core import bots_chat
from core.bots import BotError, BotsService
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)
from core.models import BOT_CHAT_TITLE, Conversation
from core.repositories.conversation_repository import ConversationRepository
from desktop.services import bots_service

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


async def _fresh() -> str:
    sch = f"bots_chat_{secrets.token_hex(4)}"
    await create_schema(sch)
    async with bound_schema(sch):
        await create_tables_in_schema(sch)
    return sch


@_pg
@pytest.mark.asyncio
async def test_mint_is_hidden_with_title_and_kickoff_message():
    """Nace hidden, título exacto 'Bot Chat', con mensaje de kickoff."""
    from sqlmodel import select as _sel  # noqa: F401 — claridad de imports

    sch = await _fresh()
    try:
        async with bound_schema(sch):
            await BotsService.create_bot("sage", display_name="Sage")
            row = await bots_chat.ensure_open("sage")

            async with get_async_session() as s:
                conv = await s.get(Conversation, row["conversation_id"])
                assert conv.title == BOT_CHAT_TITLE
                assert conv.is_canonical is True
                assert conv.is_hidden is True
                msgs = (await s.execute(_msg_sel(conv.id))).scalars().all()
            assert len(msgs) == 1 and "Sage" in msgs[0].content and msgs[0].role == "assistant"

            # idempotente: segunda apertura adopta la MISMA fila
            again = await bots_chat.ensure_open("sage")
            assert again["conversation_id"] == row["conversation_id"]
    finally:
        await drop_schema(sch)


def _msg_sel(conv_id: int):
    from sqlalchemy import select

    from core.models import Message

    return select(Message).where(Message.conversation_id == conv_id)


@_pg
@pytest.mark.asyncio
async def test_concurrent_ensure_open_mints_exactly_one():
    """Carrera real de dos ensure_open ⇒ una sola fila canónica."""
    sch = await _fresh()
    try:
        async with bound_schema(sch):
            await BotsService.create_bot("race")
            a, b = await __import__("asyncio").gather(
                bots_chat.ensure_open("race"), bots_chat.ensure_open("race")
            )
            assert a["conversation_id"] == b["conversation_id"]
            async with get_async_session() as s:
                from sqlalchemy import func, select

                n = (
                    await s.execute(
                        select(func.count())
                        .select_from(Conversation)
                        .where(Conversation.is_canonical.is_(True))
                    )
                ).scalar()
            assert n == 1
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_resolve_fail_closed_on_db_error(monkeypatch):
    """Error de BD → error visible; JAMÁS None ni acuñación silenciosa."""
    sch = await _fresh()
    try:
        async with bound_schema(sch):
            await BotsService.create_bot("edge")

            from core.database import get_async_session as real_gas

            class _BoomSessionCtx:
                async def __aenter__(self):
                    raise RuntimeError("db down")

                async def __aexit__(self, *a):
                    return False

            monkeypatch.setattr(bots_chat, "get_async_session", lambda: _BoomSessionCtx())

            with pytest.raises(BotError, match="no se pudo verificar"):
                await bots_chat.resolve_canonical("edge")
            with pytest.raises(BotError, match="no se pudo verificar|no existe el bot"):
                await bots_chat.ensure_open("edge")

            # sin filas creadas durante el fallo
            monkeypatch.setattr(bots_chat, "get_async_session", real_gas)
            async with get_async_session() as s:
                from sqlalchemy import func, select

                n = (await s.execute(select(func.count()).select_from(Conversation))).scalar()
            assert n == 0
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_sweep_rehides_and_repo_filters_hidden():
    """Desocultar un canónico por accidente ⇒ sweep lo re-oculta;
    list_all/count_all excluyen hidden por defecto."""
    sch = await _fresh()
    try:
        async with bound_schema(sch):
            await BotsService.create_bot("hid")
            row = await bots_chat.ensure_open("hid")

            # el usuario crea un chat normal
            user_conv_id = ConversationRepository.save  # referencia no-ejecutada
            conv_user = Conversation(title="mis notas")
            async with get_async_session() as s:
                s.add(conv_user)
                await s.flush()
                uid = conv_user.id
                # bug simulado: alguien desoculta el canónico
                canon = await s.get(Conversation, row["conversation_id"])
                canon.is_hidden = False
                await s.flush()

            changed = await bots_chat.sweep_hidden_bot_chats()
            assert changed == 1

            visible = await ConversationRepository.list_all(limit=50)
            ids = {c["id"] for c in visible}
            assert uid in ids and row["conversation_id"] not in ids
            total_all = await ConversationRepository.count_all(exclude_hidden=False)
            total_vis = await ConversationRepository.count_all()
            assert total_all - total_vis >= 1
    finally:
        await drop_schema(sch)


def test_guard_new_in_canonical():
    ok, msg = bots_service.new_conversation_guard(False)
    assert ok and msg is None
    blocked, msg2 = bots_service.new_conversation_guard(True)
    assert not blocked and "eterno" in (msg2 or "")


def test_derive_clone_slug():
    assert bots_service.derive_clone_slug("bot", set()) == "bot-2"
    assert bots_service.derive_clone_slug("bot", {"bot-2"}) == "bot-3"
