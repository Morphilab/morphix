# tests/test_multisession_conversation.py
"""Multi-sesión: el conv_id real se propaga vía ContextVar, no `list_all(1)`.

`finalize_workflow` persiste la conversación y registra el `conv_id` en un
ContextVar por-run, de modo que la GUI lee SU conversación sin la race de
"última conversación del workspace" (que cruza sesiones concurrentes).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_db_session():
    s = MagicMock()
    s.get = AsyncMock(return_value=None)
    s.add = MagicMock()
    s.flush = AsyncMock()
    return s


def _db_ctx():
    return AsyncMock(
        __aenter__=AsyncMock(return_value=_mock_db_session()),
        __aexit__=AsyncMock(return_value=False),
    )


@pytest.mark.asyncio
async def test_finalize_records_conversation_id():
    from orchestration.finalizer import finalize_workflow, get_finalized_conversation_id

    assert get_finalized_conversation_id() is None

    with (
        patch(
            "orchestration.finalizer.ConversationRepository.save",
            new_callable=AsyncMock,
            return_value=42,
        ),
        patch("orchestration.finalizer.get_async_session", return_value=_db_ctx()),
        patch(
            "orchestration.finalizer._extract_personal_facts",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch("orchestration.finalizer.memory_manager.update_user_profile", new_callable=AsyncMock),
        patch("orchestration.finalizer.memory_manager.write", new_callable=AsyncMock),
    ):
        conv_id = await finalize_workflow(
            query="q",
            final_output="respuesta",
            conversation_history=[],
            scorecard={},
            subtasks_list=[],
            task_analysis={},
            G=None,
            events=None,
        )

    assert conv_id == 42
    assert get_finalized_conversation_id() == 42


@pytest.mark.asyncio
async def test_two_concurrent_finalizes_isolate_their_conversation():
    """Dos finalizaciones concurrentes registran cada una SU conv_id (ContextVar)."""
    from orchestration.finalizer import finalize_workflow, get_finalized_conversation_id

    async def save_side_effect(*, title, **kw):
        return {"taskA": 1, "taskB": 2}[title]

    async def run_finalize(query: str) -> int:
        with (
            patch(
                "orchestration.finalizer.ConversationRepository.save",
                new_callable=AsyncMock,
                side_effect=save_side_effect,
            ),
            patch("orchestration.finalizer.get_async_session", return_value=_db_ctx()),
            patch(
                "orchestration.finalizer._extract_personal_facts",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "orchestration.finalizer.memory_manager.update_user_profile",
                new_callable=AsyncMock,
            ),
            patch("orchestration.finalizer.memory_manager.write", new_callable=AsyncMock),
        ):
            await finalize_workflow(
                query=query,
                final_output="respuesta",
                conversation_history=[],
                scorecard={},
                subtasks_list=[],
                task_analysis={},
                G=None,
                events=None,
            )
            return get_finalized_conversation_id()

    results = await asyncio.gather(run_finalize("taskA"), run_finalize("taskB"))

    assert results == [1, 2]
