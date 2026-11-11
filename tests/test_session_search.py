# tests/test_session_search.py — FTS+respaldo ILIKE y estado derivado
"""Dos mitades: unidades puras (sin BD) y E2E contra PostgreSQL real
(patrón skipif de test_bots_chat.py). Sin red siempre."""

import secrets

import pytest

from core import session_search as ss
from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)
from core.models import Conversation, Message, PausedSession

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


# ── Unidades puras: query_rewrite ───────────────────────────────────────────


def test_rewrite_lowercases_and_strips_noise():
    assert ss.rewrite_query("  ¡Postgres FULL-TEXT, amigo!  ") == "postgres full text amigo"


def test_rewrite_cjk_becomes_bigrams():
    assert ss.rewrite_query("数据库检索") == "数据 据库 库检 检索"


def test_rewrite_dedupes_and_caps_terms():
    q = " ".join(f"t{i}" for i in range(30)) + " t0 t0"
    out = ss.rewrite_query(q)
    assert len(out.split()) == ss.MAX_TERMS
    assert len(set(out.split())) == ss.MAX_TERMS


def test_rewrite_empty_and_has_cjk():
    assert ss.rewrite_query("   !!!  ") == ""
    assert ss.has_cjk("hola 数据") is True
    assert ss.has_cjk("hola mundo") is False


def test_like_pattern_neutralizes_wildcards():
    pattern = ss._like_pattern("100% _sql\\")
    assert "%" not in pattern[1:-1] and "_" not in pattern[1:-1]
    assert pattern.startswith("%") and pattern.endswith("%")


# ── Unidades puras: clasificador de estado ─────────────────────────────────


def test_classify_session_status_matrix():
    assert ss.classify_session_status(None, 0, False) == ss.STATUS_EMPTY
    assert ss.classify_session_status("user", 0, False) == ss.STATUS_EMPTY
    assert ss.classify_session_status("user", 3, True) == ss.STATUS_PAUSED
    assert ss.classify_session_status("user", 3, False) == ss.STATUS_AWAITING_AGENT
    assert ss.classify_session_status("assistant", 4, False) == ss.STATUS_AWAITING_USER
    assert ss.classify_session_status("tool", 4, False) == ss.STATUS_AWAITING_USER
    assert ss.classify_session_status("raro", 2, False) == ss.STATUS_UNKNOWN


# ── E2E contra PostgreSQL real ──────────────────────────────────────────────


async def _fresh() -> str:
    sch = f"sess_search_{secrets.token_hex(4)}"
    await create_schema(sch)
    async with bound_schema(sch):
        await create_tables_in_schema(sch)
    return sch


async def _seed(sch: str) -> dict[str, int]:
    async with bound_schema(sch):
        async with get_async_session() as session:
            convs = {}
            for name in ("fts", "sqlite", "cjk"):
                conv = Conversation(title=f"conv-{name}")
                session.add(conv)
                await session.flush()
                convs[name] = conv.id
            session.add_all(
                [
                    Message(
                        conversation_id=convs["fts"],
                        role="user",
                        content="¿Cómo funciona postgres full text search aquí?",
                    ),
                    Message(
                        conversation_id=convs["fts"],
                        role="assistant",
                        content="postgres usa to_tsvector para indexar.",
                    ),
                    Message(
                        conversation_id=convs["sqlite"],
                        role="user",
                        content="postgres es mejor que sqlite para FTS pesado",
                    ),
                    Message(conversation_id=convs["cjk"], role="user", content="数据库检索很重要"),
                ]
            )
            await session.commit()
            return convs


@_pg
@pytest.mark.asyncio
async def test_fts_search_ranks_and_snippets():
    sch = await _fresh()
    try:
        await _seed(sch)
        rows = await ss.search_messages(sch, "postgres")
        assert rows, "FTS debe encontrar mensajes"
        assert all("snippet" in r and r["snippet"] for r in rows)
        ids = {r["conversation_id"] for r in rows}
        # el rank premia la conversación con DOS menciones de postgres
        assert rows[0]["conversation_id"] in ids
        assert rows[0]["rank"] > 0
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_fts_respects_conversation_filter():
    sch = await _fresh()
    try:
        convs = await _seed(sch)
        rows = await ss.search_messages(sch, "postgres", conversation_id=convs["sqlite"])
        assert rows and all(r["conversation_id"] == convs["sqlite"] for r in rows)
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_cjk_goes_straight_to_ilike():
    sch = await _fresh()
    try:
        convs = await _seed(sch)
        rows = await ss.search_messages(sch, "数据库检索")
        assert rows and rows[0]["conversation_id"] == convs["cjk"]
        assert "数据库检索" in rows[0]["snippet"]
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_fts_zero_rows_falls_back_to_ilike():
    sch = await _fresh()
    try:
        await _seed(sch)
        # 'tsvecto' es un lexema INCOMPLETO: 'simple' no stemiza → FTS no
        # arroja filas → el respaldo ILIKE pesca el texto parcial.
        rows = await ss.search_messages(sch, "tsvecto")
        assert rows and "to_tsvector" in rows[0]["snippet"]
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_empty_rewrite_returns_no_rows():
    sch = await _fresh()
    try:
        await _seed(sch)
        assert await ss.search_messages(sch, "!!!") == []
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_conversation_statuses_derived():
    sch = await _fresh()
    try:
        convs = await _seed(sch)
        async with bound_schema(sch):
            async with get_async_session() as session:
                # pausa sin resolver sobre la conversación 'sqlite'
                session.add(
                    PausedSession(
                        conversation_id=convs["sqlite"],
                        clarification_question="¿continuar?",
                    )
                )
                await session.commit()
        statuses = await ss.conversation_statuses(sch)
        assert statuses[convs["sqlite"]]["status"] == ss.STATUS_PAUSED
        assert statuses[convs["fts"]]["status"] == ss.STATUS_AWAITING_USER
        assert statuses[convs["fts"]]["message_count"] == 2
        assert statuses[convs["cjk"]]["status"] == ss.STATUS_AWAITING_AGENT
        # subset
        subset = await ss.conversation_statuses(sch, [convs["cjk"]])
        assert set(subset) == {convs["cjk"]}
    finally:
        await drop_schema(sch)
