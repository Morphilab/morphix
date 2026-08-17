import pytest


@pytest.mark.asyncio
async def test_run_subtask_safe_passes_result_through():
    """El wrapper de timeout pasa el resultado intacto cuando todo va bien."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestration.executor.subtask import run_subtask_safe

    fake = {"status": "completed", "result": "ok", "files_written": ["a.py"]}
    with patch(
        "orchestration.executor.subtask.execute_subtask_safe",
        AsyncMock(return_value=fake),
    ):
        result = await run_subtask_safe(
            node=0,
            task="t",
            G=MagicMock(),
            conversation_history=[],
            current_pdf_text="",
            ctx=MagicMock(),
            events=MagicMock(),
        )
    assert result == fake


@pytest.mark.asyncio
async def test_run_subtask_safe_converts_exception_to_failed():
    """Una excepción dentro del subtask se convierte en dict fallido (no propaga)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from orchestration.executor.subtask import run_subtask_safe

    with patch(
        "orchestration.executor.subtask.execute_subtask_safe",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await run_subtask_safe(
            node=3,
            task="t",
            G=MagicMock(),
            conversation_history=[],
            current_pdf_text="",
            ctx=MagicMock(),
            events=MagicMock(),
        )
    assert result["status"] == "failed"
    assert "boom" in result["result"]
    assert result["files_written"] == []
