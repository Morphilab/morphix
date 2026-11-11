# tests/_agent_loop_support.py — fixture compartida del split de test_agent_loop
"""mock_agent_deps: mock de todas las dependencias de execute_agent_loop
(módulo interno, NO colectado por pytest)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_agent_deps():
    """Mock de todas las dependencias de execute_agent_loop."""
    mock_memory = MagicMock()
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = None

    with (
        patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock) as mock_tool,
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_indexer_cls,
    ):
        mock_indexer = MagicMock()
        mock_indexer.index_project.return_value = None
        mock_indexer.find_relevant_code.return_value = ""
        mock_indexer_cls.return_value = mock_indexer

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Tarea completada exitosamente."
        mock_response.choices[0].message.tool_calls = None
        mock_llm.return_value = mock_response
        mock_tool.return_value = {"output": "ok", "success": True}

        yield mock_llm, mock_tool, mock_response
