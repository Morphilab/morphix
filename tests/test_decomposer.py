# tests/test_decomposer.py
"""Tests del descompositor de tareas."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.decomposer import decompose_task


@pytest.mark.asyncio
async def test_decompose_task_still_works():
    """decompose_task original sigue funcionando."""
    with patch("llm.controller.models.call", new_callable=AsyncMock) as mock_llm:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"subtasks": ["Crear src/main.py con print hello", ' '"Crear tests/test_main.py"]}'
        )
        mock_llm.return_value = mock_response

        result = await decompose_task("Crear un script")

    assert isinstance(result, list)
    assert len(result) >= 2
