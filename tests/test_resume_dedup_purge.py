"""Dedup de historial en resume + purga de pausas resueltas."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _session_with_existing(rows: list[tuple[str, str]]):
    session = MagicMock()
    conv = MagicMock(id=7)
    session.get = AsyncMock(return_value=conv)

    async def fake_execute(stmt):
        if "message" in str(stmt).lower() and "role" in str(stmt).lower():
            result = MagicMock()
            result.all.return_value = rows
            return result
        return MagicMock()

    session.execute = AsyncMock(side_effect=fake_execute)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_resume_does_not_duplicate_persisted_history():
    """Re-save con el MISMO historial no inserta duplicados."""
    history = [
        {"role": "user", "content": "pregunta original"},
        {"role": "agent", "content": "[Developer] trabajo hecho"},
        {"role": "assistant", "content": "respuesta final del turno anterior"},
        # Nuevo turno: solo este agent entry es NUEVO
        {"role": "agent", "content": "[Analyst] revisión adicional nueva"},
    ]
    existing = [
        ("agent", "[Developer] trabajo hecho"),
        ("assistant", "respuesta final del turno anterior"),
    ]

    session = _session_with_existing(existing)
    added: list[MagicMock] = []
    session.add.side_effect = lambda m: added.append(m)

    with patch("core.repositories.conversation_repository.get_async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        from core.repositories.conversation_repository import ConversationRepository

        await ConversationRepository.save(
            title="t",
            user_message="nueva instrucción",
            conversation_id=7,
            conversation_history=history,
        )

    new_contents = [m.content for m in added if getattr(m, "role", "") != "user"]
    assert "[Analyst] revisión adicional nueva" in new_contents, new_contents
    assert all(
        "[Developer]" not in c for c in new_contents
    ), f"entradas ya persistidas re-insertadas: {new_contents}"
    assert not any("respuesta final" in c for c in new_contents)


@pytest.mark.asyncio
async def test_purge_resolved_paused_deletes_only_resolved():
    """Purge elimina SOLO pausas resueltas (resolved_at NOT NULL < cutoff)."""
    captured_stmts: list = []

    class FakeResult:
        rowcount = 3

    async def fake_execute(stmt):
        captured_stmts.append(stmt)
        return FakeResult()

    session = MagicMock()
    session.execute = fake_execute

    with patch("core.repositories.conversation_repository.get_async_session") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        from core.repositories.conversation_repository import ConversationRepository

        deleted = await ConversationRepository.purge_resolved_paused(max_age_days=30)

    assert deleted == 3
    assert captured_stmts, "no se ejecutó DELETE"
    compiled = str(captured_stmts[0].compile(compile_kwargs={"literal_binds": True})).lower()
    assert "paused_sessions" in compiled
    assert "resolved_at" in compiled
