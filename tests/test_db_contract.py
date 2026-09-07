"""Contrato de persistencia — índices, CASCADE,
orden estable por id y delete defensivo."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _model_indexes(table):
    return {frozenset(c.name for c in ix.columns) for ix in table.indexes}


def test_message_fk_has_index_and_cascade():
    """FK de Message indexada y con ON DELETE CASCADE."""
    from core.models import Message

    col = Message.__table__.c.conversation_id
    fks = list(col.foreign_keys)
    assert fks, "FK ausente"
    assert any(fk.ondelete == "CASCADE" for fk in fks), "sin ondelete CASCADE"
    indexed = any(
        "conversation_id" in [c.name for c in ix.columns] for ix in Message.__table__.indexes
    )
    assert indexed, "Message.conversation_id sin índice"


def test_paused_session_fk_indexed_and_cascade():
    """PausedSession.conversation_id también es FK que bloquea sin cascade."""
    from core.models import PausedSession

    col = PausedSession.__table__.c.conversation_id
    fks = list(col.foreign_keys)
    assert fks, "FK ausente"
    assert any(fk.ondelete == "CASCADE" for fk in fks)
    indexed = any(
        "conversation_id" in [c.name for c in ix.columns] for ix in PausedSession.__table__.indexes
    )
    assert indexed


def test_message_timestamp_indexed():
    from core.models import Message

    idx_cols = _model_indexes(Message.__table__)
    assert any("timestamp" in s for s in (map(str, idx_cols))), "Message.timestamp sin índice"


@pytest.mark.asyncio
async def test_delete_removes_children_first():
    """Delete borra messages y paused_sessions ANTES de la conversación."""
    session = MagicMock()
    conv = MagicMock(id=1)
    session.get = AsyncMock(return_value=conv)

    events: list[str] = []

    async def fake_execute(stmt):
        events.append(f"Delete:{stmt.table.name}")
        return MagicMock()

    session.execute = fake_execute

    async def fake_delete(obj):
        events.append(f"ORM:{type(obj).__name__}")

    session.delete = AsyncMock(side_effect=fake_delete)

    with patch("core.repositories.conversation_repository.get_async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        from core.repositories.conversation_repository import ConversationRepository

        ok = await ConversationRepository.delete(1)

    assert ok is True
    child_deletes = [i for i, e in enumerate(events) if e.startswith("Delete:")]
    orm_del = [i for i, e in enumerate(events) if e.startswith("ORM:")]
    assert child_deletes, f"sin DELETE de hijos: {events}"
    # Ambos hijos (message + paused_sessions) antes del ORM delete de la conversación
    assert len(child_deletes) == 2 and max(child_deletes) < min(
        orm_del or [10**9]
    ), f"orden incorrecto: {events}"
