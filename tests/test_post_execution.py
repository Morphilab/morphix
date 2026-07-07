# tests/test_post_execution.py
"""Tests para el módulo de post-ejecución del workflow."""

from unittest.mock import AsyncMock, patch

import pytest

from orchestration.executor.post import _post_execution_checks


@pytest.mark.asyncio
async def test_post_execution_no_issues():
    """Verifica que una ejecución limpia no genera advertencias."""
    with (
        patch("orchestration.executor.verify._verify_intended_files") as mock_verify,
        patch("orchestration.executor.verify._check_test_file_exists") as mock_test,
    ):
        mock_verify.return_value = []
        mock_test.return_value = True

        report = {
            "completed": 2,
            "failed": 0,
            "actions_taken": 3,
        }
        result = await _post_execution_checks(
            execution_report=report,
            files_written=["app.py"],
            commit_done=True,
            intended_files={"app.py"},
            task="Crear app.py",
            project_root="/tmp/test",
            is_dev_task=False,
            workspace="main",
            best_agent="developer",
            allowed_tools=["file_manager"],
            add_system_message=AsyncMock(),
        )
        assert isinstance(result, str)
        assert len(result) > 0


@pytest.mark.asyncio
async def test_post_execution_missing_files():
    """Verifica que se detectan archivos faltantes."""
    with patch("orchestration.executor.verify._verify_intended_files") as mock_verify:
        mock_verify.return_value = ["app.py (FALTA)"]

        report = {"completed": 1, "failed": 0, "actions_taken": 1}
        result = await _post_execution_checks(
            execution_report=report,
            files_written=[],
            commit_done=False,
            intended_files={"app.py"},
            task="Crear app.py",
            project_root="/tmp/test",
            is_dev_task=False,
            workspace="main",
            best_agent="developer",
            allowed_tools=["file_manager"],
            add_system_message=AsyncMock(),
        )
        assert "Faltan archivos" in result or "app.py" in result
