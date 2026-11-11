# tests/test_bots_identity.py — la identidad por NOMBRE es un índice
"""Contrato de identidad Bot Mode (contrato 08 §1):

- Índice único parcial ``uq_conversation_bot_chat`` en el metadata (siempre,
  incluidos schemas provisionados con create_all — gotcha de índices a15xvi).
- Contra PostgreSQL real: dos INSERT canónicos para el mismo bot ⇒ IntegrityError.
- Paridad legacy: ``create_tables_in_schema`` añade columnas + índice a schemas
  provisionados antes del delta b01.
"""

import asyncio
import os
import secrets
import sys
from pathlib import Path

import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy import text

from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _url() -> str | None:
    return os.environ.get("DATABASE_URL")


# ── Unidades sin BD ────────────────────────────────────────────────────────


def test_metadata_carries_partial_unique_index():
    """El índice vive en __table_args__ del modelo: create_all lo reproduce."""
    from sqlmodel import SQLModel

    from core.models import UQ_BOT_CHAT_INDEX

    conv = next(t for t in SQLModel.metadata.sorted_tables if t.name == "conversation")
    assert UQ_BOT_CHAT_INDEX.name in {ix.name for ix in conv.indexes}, (
        "uq_conversation_bot_chat DEBE estar en __table_args__ "
        "(los schemas workspace no reciben índices de migraciones)"
    )


def test_canonical_title_is_exact_constant():
    from core.models import BOT_CHAT_TITLE

    assert BOT_CHAT_TITLE == "Bot Chat"


def test_migration_chain_reachable_from_baseline():
    """p04 → … → b01: la línea completa se recorre desde ScriptDirectory."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    walk = [r.revision for r in script.walk_revisions()]
    assert "b01_bot_mode" in walk and "p04_baseline" in walk


# ── E2E contra PostgreSQL real ─────────────────────────────────────────────


@pytest.mark.skipif(not _url(), reason="requiere DATABASE_URL (PG real)")
@pytest.mark.asyncio
async def test_two_canonical_inserts_violate_unique_partial_index():
    """(bot_id WHERE is_canonical) es registro de ≤1 fila — PG lo impone."""
    from core.models import BOT_CHAT_TITLE, Bot, Conversation

    suffix = secrets.token_hex(4)
    sch = f"bots_ident_{suffix}"
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)

            # caso 1: bot + su chat canónico eterno (fila única permitida)
            async with get_async_session() as s1:
                bot = Bot(slug="alpha", display_name="Alpha", memory_prefix="bots/alpha")
                s1.add(bot)
                await s1.flush()
                s1.add(
                    Conversation(
                        title=BOT_CHAT_TITLE, bot_id=bot.id, is_canonical=True, is_hidden=True
                    )
                )
                await s1.flush()
                bot_id = bot.id

            # caso 2: segunda conversación canónica para el MISMO bot ⇒ violación.
            # La sesión falla y __aexit__ hace rollback del intento completo.
            async with get_async_session() as s2:
                s2.add(Conversation(title="otra", bot_id=bot_id, is_canonical=True))
                with pytest.raises(sa_exc.IntegrityError):
                    await s2.flush()
                await s2.rollback()  # descarta el intento antes del close

            # caso 3: tras el rollback solo queda la original; chats de usuario no
            # tocan el índice parcial.
            async with get_async_session() as s3:
                n = (
                    await s3.execute(
                        text("SELECT COUNT(*) FROM conversation WHERE is_canonical IS TRUE")
                    )
                ).scalar()
                assert n == 1
                s3.add(Conversation(title="chat usuario"))
                await s3.flush()
    finally:
        await drop_schema(sch)


@pytest.mark.skipif(not _url(), reason="requiere DATABASE_URL (PG real)")
@pytest.mark.asyncio
async def test_legacy_workspace_parity_adds_bot_mode_columns_and_index():
    """Schema legado (migrado SOLO hasta h8a): paridad posterior añade columnas
    de bot-mode + índice parcial vía create_tables_in_schema."""

    suffix = secrets.token_hex(4)
    sch = f"bots_par_{suffix}"

    env = dict(os.environ)
    env["DATABASE_URL"] = _url()
    env["ALEMBIC_SCHEMA"] = sch

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "h8a_msg_embedding_fp",
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    assert proc.returncode == 0, f"upgrade a h8a falló:\n{out.decode()[-1500:]}"

    async def _snapshot() -> tuple[set[str], bool]:
        async with get_async_session() as s:
            rows = await s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=:s AND table_name='conversation'"
                ),
                {"s": sch},
            )
            names = {r[0] for r in rows.fetchall()}
            ix = await s.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname=:s "
                    "AND indexname='uq_conversation_bot_chat'"
                ),
                {"s": sch},
            )
            return names, ix.first() is not None

    try:
        async with bound_schema(sch):
            names, had_index = await _snapshot()
            assert "bot_id" not in names and not had_index, "el schema debería ser legado"

            # Paridad del módulo runtime (lo que hace cada switch de workspace)
            await create_tables_in_schema(sch)

            names, had_index = await _snapshot()
            assert {"bot_id", "is_canonical", "is_hidden", "capability_epoch"} <= names
            assert had_index, "el índice parcial DEBE existir tras la paridad"
    finally:
        await drop_schema(sch)
