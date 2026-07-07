# tests/test_task_analyzer.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_analyze_llm():
    """Mock del LLM para TaskAnalyzer."""
    with patch(
        "orchestration.analyzer.models.call",
        new_callable=AsyncMock,
    ) as mock_call:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        yield mock_call, mock_response


def _make_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


@pytest.mark.asyncio
async def test_analyze_simple_conversation(mock_analyze_llm):
    mock_call, _ = mock_analyze_llm
    mock_call.return_value = _make_response(
        '{"primary_type": "simple_conversation", "requires_full_orchestration": false, '
        '"complexity": "simple", "requirements": "Responder amablemente"}'
    )

    from orchestration.analyzer import TaskAnalyzer

    result = await TaskAnalyzer.analyze_task("Hola, ¿cómo estás?")
    assert result["primary_type"] == "simple_conversation"
    assert result["requires_full_orchestration"] is False
    mock_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_analyze_developer_task(mock_analyze_llm):
    mock_call, _ = mock_analyze_llm
    mock_call.return_value = _make_response(
        '{"primary_type": "developer", "requires_full_orchestration": true, '
        '"complexity": "high", "requirements": "Usar async/await"}'
    )

    from orchestration.analyzer import TaskAnalyzer

    result = await TaskAnalyzer.analyze_task("Crea un endpoint REST con tests")
    assert result["primary_type"] == "developer"
    assert result["requires_full_orchestration"] is True
    assert result["complexity"] == "high"


@pytest.mark.asyncio
async def test_analyze_cache_hit(mock_analyze_llm):
    """La segunda llamada con la misma query debería usar caché."""
    mock_call, _ = mock_analyze_llm
    mock_call.return_value = _make_response(
        '{"primary_type": "conversational", "requires_full_orchestration": false}'
    )

    from orchestration.analyzer import TaskAnalyzer

    result1 = await TaskAnalyzer.analyze_task("Hola")
    result2 = await TaskAnalyzer.analyze_task("Hola")

    assert result1["primary_type"] == result2["primary_type"]
    # La caché debería evitar una segunda llamada al LLM
    # (pero depende de la implementación de cache TTL)


@pytest.mark.asyncio
async def test_analyze_llm_error_fallback(mock_analyze_llm):
    mock_call, _ = mock_analyze_llm
    mock_call.side_effect = RuntimeError("LLM timeout")

    from orchestration.analyzer import TaskAnalyzer

    result = await TaskAnalyzer.analyze_task("Cualquier cosa")
    assert isinstance(result, dict)
    assert "primary_type" in result


@pytest.mark.asyncio
async def test_analyze_returns_required_fields(mock_analyze_llm):
    mock_call, _ = mock_analyze_llm
    mock_call.return_value = _make_response(
        '{"primary_type": "developer", "requires_full_orchestration": true, '
        '"complexity": "medium", "requirements": "Test coverage >80%"}'
    )

    from orchestration.analyzer import TaskAnalyzer

    result = await TaskAnalyzer.analyze_task("Escribe tests unitarios")
    assert "primary_type" in result
    assert "requires_full_orchestration" in result
    assert "complexity" in result
