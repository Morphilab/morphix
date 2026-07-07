# tests/test_agent_loop.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_agent_deps():
    """Mock de todas las dependencias de execute_agent_loop."""
    mock_memory = MagicMock()
    mock_memory.search.return_value = []
    mock_memory.get_user_profile.return_value = None

    with (
        patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock) as mock_tool,
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop.memory_manager", mock_memory),
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


@pytest.mark.asyncio
async def test_agent_loop_simple_completion(mock_agent_deps):
    mock_llm, mock_tool, mock_response = mock_agent_deps

    from orchestration.loop import execute_agent_loop

    result = await execute_agent_loop(
        task="Explica qué hace este código.",
        agent_type="conversacional",
        workspace="main",
    )
    assert result["status"] == "completed"
    assert "actions_taken" in result


@pytest.mark.asyncio
async def test_agent_loop_with_tool_calls(mock_agent_deps):
    mock_llm, mock_tool, mock_response = mock_agent_deps
    # Primera respuesta: tool call
    # Segunda respuesta: final
    tool_response = MagicMock()
    tool_response.choices = [MagicMock()]
    tool_response.choices[0].message.content = None
    tool_call = MagicMock()
    tool_call.function.name = "file_manager"
    tool_call.function.arguments = '{"action": "write", "path": "main.py", "content": "print(1)"}'
    tool_response.choices[0].message.tool_calls = [tool_call]

    final_response = MagicMock()
    final_response.choices = [MagicMock()]
    final_response.choices[0].message.content = "Archivo leído correctamente."
    final_response.choices[0].message.tool_calls = None

    mock_llm.side_effect = [tool_response, final_response]

    from orchestration.loop import execute_agent_loop

    result = await execute_agent_loop(
        task="Lee main.py",
        agent_type="developer",
        allowed_tools=["file_manager"],
        workspace="main",
    )
    assert result["status"] == "completed"
    assert result["actions_taken"] >= 1


@pytest.mark.asyncio
async def test_agent_loop_stalled_detection(mock_agent_deps):
    mock_llm, mock_tool, mock_response = mock_agent_deps

    # El LLM siempre responde con tool calls que no modifican archivos
    def make_reading_tool_response():
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = None
        tc = MagicMock()
        tc.function.name = "file_manager"
        tc.function.arguments = '{"action": "read", "path": "main.py"}'
        resp.choices[0].message.tool_calls = [tc]
        return resp

    # Override safe_tool_call to return failure — stall should detect non-productive loop
    with patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock) as mock_tool_fail:
        mock_tool_fail.return_value = {"output": "no se pudo", "success": False}

        # MAX_STALL_ITERATIONS=2 → stall at iteration 2
        mock_llm.side_effect = [make_reading_tool_response() for _ in range(4)]

        from orchestration.loop import execute_agent_loop

        result = await execute_agent_loop(
            task="Lee main.py repetidamente",
            agent_type="developer",
            allowed_tools=["file_manager"],
            workspace="main",
        )
        assert result["status"] == "stalled"
        assert result["iterations"] == 2


@pytest.mark.asyncio
async def test_agent_loop_with_project_root(mock_agent_deps):
    mock_llm, mock_tool, mock_response = mock_agent_deps

    from orchestration.loop import execute_agent_loop

    result = await execute_agent_loop(
        task="Crea un test unitario",
        agent_type="developer",
        allowed_tools=["file_manager", "test_runner"],
        project_root="code_projects/miapp",
        workspace="main",
    )
    assert "status" in result


@pytest.mark.asyncio
async def test_agent_loop_streaming(mock_agent_deps):
    """Verifica que el Agent Loop soporta streaming via on_stream_chunk callback."""
    mock_llm, mock_tool, mock_response = mock_agent_deps

    async def mock_stream(*args, **kwargs):
        chunk1 = MagicMock()
        chunk1.text = "Hola "
        chunk1.reasoning_content = None
        chunk1.tool_name = None
        chunk1.tool_call_id = None
        chunk1.tool_arguments = None
        chunk1.is_done = False

        chunk2 = MagicMock()
        chunk2.text = "mundo"
        chunk2.reasoning_content = None
        chunk2.tool_name = None
        chunk2.tool_call_id = None
        chunk2.tool_arguments = None
        chunk2.is_done = True
        chunk2.finish_reason = "stop"
        chunk2.usage = None

        yield chunk1
        yield chunk2

    with patch("orchestration.loop.models.call_stream", return_value=mock_stream()):
        from orchestration.loop import execute_agent_loop

        streamed_chunks = []

        def capture_chunk(chunk):
            streamed_chunks.append(chunk)

        result = await execute_agent_loop(
            task="Di hola mundo",
            agent_type="conversacional",
            history=[],
            workspace="main",
            on_stream_chunk=capture_chunk,
        )

        assert result["status"] == "completed"
        assert result["result"] == "Hola mundo"
        assert streamed_chunks == ["Hola ", "mundo"]


@pytest.mark.asyncio
async def test_repetitive_reads_detected_as_stall(mock_agent_deps):
    """Verify that reading the same file repeatedly without modifying is stalled."""
    mock_llm, mock_tool, _ = mock_agent_deps

    def make_read_response():
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = None
        tc = MagicMock()
        tc.function.name = "file_manager"
        tc.function.arguments = '{"action": "read", "path": "cli.py"}'
        resp.choices[0].message.tool_calls = [tc]
        return resp

    mock_tool.return_value = {"output": "file content", "success": True}

    # 5 iterations of identical read — should stall by iteration 3-4
    mock_llm.side_effect = [make_read_response() for _ in range(5)]

    from orchestration.loop import execute_agent_loop

    result = await execute_agent_loop(
        task="Read cli.py and explain it",
        agent_type="developer",
        allowed_tools=["file_manager"],
        workspace="main",
    )
    assert result["status"] in ("stalled", "completed")
    assert result["iterations"] <= 5
