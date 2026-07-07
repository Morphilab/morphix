# tests/test_task_decomposer.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_decompose_llm():
    """Mock del LLM para task_decomposer."""
    with patch(
        "orchestration.decomposer.models.call",
        new_callable=AsyncMock,
    ) as mock_call:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        yield mock_call, mock_response


@pytest.mark.asyncio
async def test_decompose_returns_list(mock_decompose_llm):
    mock_call, mock_response = mock_decompose_llm
    mock_response.choices[0].message.content = '```json\n["Tarea 1", "Tarea 2", "Tarea 3"]\n```'
    mock_call.return_value = mock_response

    from orchestration.decomposer import decompose_task

    result = await decompose_task("Crear una API REST")
    assert isinstance(result, list)
    assert len(result) >= 1
    mock_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_decompose_fallback_regex(mock_decompose_llm):
    mock_call, mock_response = mock_decompose_llm
    # Simular respuesta que el JSON parser no puede parsear
    mock_response.choices[0].message.content = "1. Primera tarea\n2. Segunda tarea\n3. Tercera"

    mock_call.return_value = mock_response

    from orchestration.decomposer import decompose_task

    result = await decompose_task("Tarea compleja")
    assert isinstance(result, list)
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_decompose_llm_error_fallback(mock_decompose_llm):
    mock_call, _ = mock_decompose_llm
    mock_call.side_effect = RuntimeError("LLM timeout")

    from orchestration.decomposer import decompose_task

    result = await decompose_task("Cualquier tarea")
    # Debe devolver al menos una tarea (fallback)
    assert isinstance(result, list)
    assert len(result) >= 1


@pytest.mark.asyncio
async def test_decompose_empty_response(mock_decompose_llm):
    mock_call, mock_response = mock_decompose_llm
    mock_response.choices[0].message.content = ""

    mock_call.return_value = mock_response

    from orchestration.decomposer import decompose_task

    result = await decompose_task("")
    assert isinstance(result, list)
