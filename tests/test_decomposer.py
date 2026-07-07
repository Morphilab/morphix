# tests/test_decomposer.py
"""Tests for task decomposition including phase-aware decomposition."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.decomposer import decompose_task, decompose_task_with_phases


@pytest.mark.asyncio
async def test_decompose_with_phases_returns_structured_result():
    """decompose_task_with_phases debe retornar dict con fases."""
    with patch("llm.controller.models.call", new_callable=AsyncMock) as mock_llm:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"phases": ['
            '  {"phase": "implement", "order": 1, "description": "crear archivos", '
            '   "subtasks": ["crear main.py", "crear models.py"]},'
            '  {"phase": "test", "order": 2, "description": "escribir tests", '
            '   "subtasks": ["crear test_main.py"]}'
            '], "strategy": "sequential"}'
        )
        mock_llm.return_value = mock_response

        result = await decompose_task_with_phases("Crear un proyecto con tests")

    assert isinstance(result, dict)
    assert "phases" in result
    assert len(result["phases"]) == 2
    assert result["phases"][0]["phase"] == "implement"
    assert result["phases"][1]["phase"] == "test"
    assert len(result["phases"][0]["subtasks"]) == 2


@pytest.mark.asyncio
async def test_decompose_with_phases_falls_back_when_llm_fails():
    """Si el LLM falla, debe usar fallback single-phase con decompose_task."""
    with patch("llm.controller.models.call", side_effect=RuntimeError("LLM down")):
        result = await decompose_task_with_phases("Crear una app simple")

    assert isinstance(result, dict)
    assert "phases" in result
    assert len(result["phases"]) == 1
    assert result["phases"][0]["phase"] == "default"
    assert len(result["phases"][0]["subtasks"]) >= 1


@pytest.mark.asyncio
async def test_decompose_with_phases_falls_back_on_invalid_json():
    """Si el LLM devuelve JSON sin phases, usa fallback."""
    with patch("llm.controller.models.call", new_callable=AsyncMock) as mock_llm:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"subtasks": ["hacer algo"]}'
        mock_llm.return_value = mock_response

        result = await decompose_task_with_phases("Una tarea simple")

    assert len(result["phases"]) == 1
    assert result["phases"][0]["phase"] == "default"


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
