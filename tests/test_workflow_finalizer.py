# tests/test_workflow_finalizer.py
"""Tests para el finalizador de workflows."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.finalizer import (
    _extract_personal_facts,
    _truncate_safe_summary,
    finalize_workflow,
)


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

    @pytest.mark.asyncio
    async def test_extract_facts_rejects_trivial_name_only(self):
        """Perfil trivial (solo name, p. ej. 'ChatGPT' de un contexto contaminado)
        no debe devolverse como hecho — rompe el bucle de contaminación
        (evidencia 2026-08-14: prueba 2 re-escribió user_profile con el dato stale)."""
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = '{"name": "ChatGPT"}'

            result = await _extract_personal_facts("Hola", "Crea script.py")
            assert result == {}

    @pytest.mark.asyncio
    async def test_extract_facts_keeps_name_with_context(self):
        """Un nombre acompañado de otro dato contextual SÍ es un hecho válido."""
        with patch(
            "llm.controller.models.call",
            new_callable=AsyncMock,
        ) as mock_call:
            mock_call.return_value = MagicMock()
            mock_call.return_value.choices = [MagicMock()]
            mock_call.return_value.choices[0].message.content = '{"name": "Ana", "city": "Lima"}'

            result = await _extract_personal_facts("Hola", "Me llamo Ana y vivo en Lima")
            assert result.get("name") == "Ana"
            assert result.get("city") == "Lima"


class TestTruncateSafeSummary:
    def test_short_summary_unchanged(self):
        assert _truncate_safe_summary("hola mundo") == "hola mundo"

    def test_exactly_4000_chars_not_truncated(self):
        """Un resumen de exactamente 4000 chars NO fue cortado — no debe recortarse."""
        s = "a" * 4000
        assert _truncate_safe_summary(s) == s

    def test_over_4000_truncated_to_last_period(self):
        s = ("a" * 3000) + ". fin de frase " + ("b" * 2000)
        out = _truncate_safe_summary(s)
        assert len(out) <= 4000
        assert out.endswith(".")
        assert len(out) < len(s)


def test_build_subtask_list_shows_failed_as_failed():
    """Un subtask con status 'failed' no debe mostrarse como 'completed'."""
    from orchestration.workflows.development import _build_subtask_list

    out = _build_subtask_list(
        ["a", "b"],
        {0: {"status": "failed"}},
        current_node=1,
        current_status="running",
    )
    assert out[0]["status"] == "failed"
    assert out[1]["status"] == "running"
