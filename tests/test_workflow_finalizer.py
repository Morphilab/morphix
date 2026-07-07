# tests/test_workflow_finalizer.py
"""Tests para el finalizador de workflows."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.finalizer import _extract_personal_facts, finalize_workflow


@pytest.fixture
def mock_deps():
    """Fixture base: mocks para BD, memoria, git, métricas."""
    with (
        patch(
            "orchestration.finalizer.ConversationRepository.save",
            new_callable=AsyncMock,
        ) as mock_save,
        patch(
            "orchestration.finalizer.get_async_session",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.finalizer.memory_manager.update_user_profile",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.finalizer.memory_manager.write",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.finalizer.update_live_diagram",
            new_callable=AsyncMock,
        ),
    ):
        mock_save.return_value = 42
        yield mock_save


@pytest.mark.asyncio
async def test_finalize_workflow_basic(mock_deps):
    """Verifica que finalize_workflow completa sin errores con datos mínimos."""
    events = MagicMock()
    events.on_diagram_update = AsyncMock()

    await finalize_workflow(
        query="Crear test.py",
        final_output="Archivo creado exitosamente.",
        conversation_history=[{"role": "user", "content": "Crear test.py"}],
        scorecard={"subtasks": 1, "tokens": 100},
        subtasks_list=["Crear test.py"],
        task_analysis={"primary_type": "ejecutor"},
        G=None,
        events=events,
    )
    mock_deps.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_workflow_with_files_triggers_commit(mock_deps):
    """Verifica que files_written dispara smart_auto_commit."""
    with patch(
        "core.git_operations.smart_auto_commit",
        new_callable=AsyncMock,
    ) as mock_commit:
        events = MagicMock()
        events.on_diagram_update = AsyncMock()

        await finalize_workflow(
            query="commit test",
            final_output="done",
            conversation_history=[{"role": "user", "content": "commit test"}],
            scorecard={"subtasks": 1, "tokens": 50},
            subtasks_list=["task"],
            task_analysis={"primary_type": "ejecutor"},
            G=None,
            events=events,
            project_root="code_projects/app",
            files_written=["app.py"],
        )
        mock_commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_workflow_handles_db_error(mock_deps):
    """Verifica que un error de BD no crashea el finalizador."""
    mock_deps.side_effect = RuntimeError("BD caída")

    events = MagicMock()
    events.on_diagram_update = AsyncMock()

    # No debe lanzar excepción
    await finalize_workflow(
        query="test",
        final_output="ok",
        conversation_history=[],
        scorecard={"subtasks": 0, "tokens": 0},
        subtasks_list=[],
        task_analysis={},
        G=None,
        events=events,
    )


class TestExtractPersonalFacts:
    @pytest.mark.asyncio
    async def test_extract_facts_returns_dict(self):
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = '{"name":"Ana","city":"Lima"}'

            result = await _extract_personal_facts("Hola", "Me llamo Ana y vivo en Lima")
            assert isinstance(result, dict)
            assert result.get("name") == "Ana"

    @pytest.mark.asyncio
    async def test_extract_facts_handles_llm_error(self):
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.side_effect = RuntimeError("LLM error")
            result = await _extract_personal_facts("Hola", "test")
            assert result == {}

    @pytest.mark.asyncio
    async def test_extract_facts_handles_invalid_json(self):
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = "not json"

            result = await _extract_personal_facts("Hola", "test")
            assert result == {}
