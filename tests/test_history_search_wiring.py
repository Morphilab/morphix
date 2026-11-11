# tests/test_history_search_wiring.py — el buscador del Historial usa
# session_search (FTS+ILIKE con rank) y enriquece con estado derivado.
import secrets

import pytest

from core.database import (
    bound_schema,
    create_schema,
    create_tables_in_schema,
    drop_schema,
    get_async_session,
)
from core.models import Conversation, Message
from desktop.services.history_service import HistoryService

_pg = pytest.mark.skipif(
    not __import__("os").environ.get("DATABASE_URL"), reason="requiere DATABASE_URL (PG real)"
)


@_pg
@pytest.mark.asyncio
async def test_load_conversations_finds_by_message_content_only(monkeypatch):
    """El hueco del camino viejo: cuando el título matchea la conversación A
    pero el CONTENIDO matchea la B (y solo el contenido), B se perdía — el
    fallback de contenido solo corría cuando título/tags no encontraban
    NADA. La capa lexical (FTS+ILIKE con rank) debe sumar a ambos."""
    from datetime import UTC, datetime

    sch = f"hist_wir_{secrets.token_hex(4)}"
    ws_stub = type("WS", (), {"current": ""})()
    import desktop.services.history_service as _hs

    monkeypatch.setattr(_hs, "get_global_workspaces", lambda: ws_stub)
    ws_stub.current = sch
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            now = datetime.now(UTC).replace(tzinfo=None)
            async with get_async_session() as s:
                s.add(Conversation(id=1, title="receta de café", created_at=now, tags=""))
                s.add(
                    Message(
                        id=1,
                        conversation_id=1,
                        role="user",
                        content="hola",
                        timestamp=now,
                    )
                )
                s.add(Conversation(id=2, title="charla", created_at=now, tags=""))
                s.add(
                    Message(
                        id=2,
                        conversation_id=2,
                        role="user",
                        content="el café zxqwv suena genial",
                        timestamp=now,
                    )
                )
            found = await HistoryService.load_conversations("café")
            ids = sorted(c.id for c in found)
            assert ids == [1, 2], f"la conversación 2 (match SOLO en contenido) se perdió: {ids}"
    finally:
        await drop_schema(sch)


@_pg
@pytest.mark.asyncio
async def test_list_conversations_enriches_derived_status(monkeypatch):
    """Las filas del buscador llevan estado derivado (status/last_role/
    message_count) — session_search.classify lo calcula sin columnas nuevas."""
    from datetime import UTC, datetime

    sch = f"hist_wir_{secrets.token_hex(4)}"
    ws_stub = type("WS", (), {"current": ""})()
    import desktop.services.history_service as _hs

    monkeypatch.setattr(_hs, "get_global_workspaces", lambda: ws_stub)
    ws_stub.current = sch
    try:
        await create_schema(sch)
        async with bound_schema(sch):
            await create_tables_in_schema(sch)
            now = datetime.now(UTC).replace(tzinfo=None)
            async with get_async_session() as s:
                s.add(Conversation(id=1, title="chatzx", created_at=now, tags=""))
                s.add(
                    Message(
                        id=1,
                        conversation_id=1,
                        role="user",
                        content="hola",
                        timestamp=now,
                    )
                )
            rows = await HistoryService.list_conversations("chatzx")
            assert rows, "la conversación debe aparecer por título"
            row = rows[0]
            assert "status" in row, "fila sin estado derivado"
            assert row.get("last_role") == "user"
            assert row.get("message_count") == 1
    finally:
        await drop_schema(sch)
