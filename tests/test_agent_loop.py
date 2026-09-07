# tests/test_agent_loop.py
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
    assert result["status"] == "stalled"
    assert result["iterations"] <= 5


@pytest.mark.asyncio
async def test_system_prompt_requires_file_writes(mock_agent_deps):
    """Verify the system prompt explicitly requires file writes over text responses."""
    mock_llm, mock_tool, mock_response = mock_agent_deps

    captured_messages = None

    async def capture_call(messages, **kwargs):
        nonlocal captured_messages
        captured_messages = messages
        return mock_response

    with patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = capture_call
        from orchestration.loop import execute_agent_loop

        await execute_agent_loop(
            task="test task",
            agent_type="developer",
            workspace="main",
        )

    system_msg = next((m["content"] for m in captured_messages if m["role"] == "system"), "")
    assert "DEBES" in system_msg
    assert "file_manager" in system_msg
    assert "function-calling" in system_msg
    assert "action" in system_msg and "path" in system_msg


@pytest.mark.parametrize(
    ("tool_calls", "expected"),
    [
        (
            [
                {
                    "name": "file_manager",
                    "id": "c1",
                    "arguments": {
                        "action": "",
                        "path": "",
                        "project_root": "/tmp",
                        "workspace": "main",
                    },
                },
            ],
            False,
        ),
        (
            [
                {
                    "name": "file_manager",
                    "id": "c1",
                    "arguments": {"project_root": "/tmp", "workspace": "main"},
                },
            ],
            False,
        ),
        (
            [
                {
                    "name": "file_manager",
                    "id": "c1",
                    "arguments": {
                        "action": "write",
                        "path": "test.py",
                        "project_root": "/tmp",
                    },
                },
            ],
            True,
        ),
        (
            [
                {
                    "name": "file_manager",
                    "id": "c1",
                    "arguments": {"action": "", "path": ""},
                },
                {
                    "name": "file_manager",
                    "id": "c2",
                    "arguments": {"action": "read", "path": "test.py"},
                },
            ],
            True,
        ),
        ([], False),
    ],
    ids=[
        "all_args_empty",
        "action_and_path_missing",
        "valid_write_args",
        "one_valid_among_invalid",
        "empty_list",
    ],
)
def test_has_any_valid_tool_call(tool_calls, expected):
    """_has_any_valid_tool_call returns True when any call has real args."""
    from orchestration.loop import _has_any_valid_tool_call

    assert _has_any_valid_tool_call(tool_calls) is expected


@pytest.mark.asyncio
async def test_accumulate_stream_without_tool_call_id():
    """Chunk sin tool_call_id (formato Ollama nativo) con name+args completos
    → la tool call se acumula con id sintético."""
    from llm.controller import StreamChunk
    from orchestration.loop import _accumulate_stream

    async def fake_stream():
        yield StreamChunk(
            tool_name="file_manager",
            tool_arguments='{"action": "write"}',
            tool_call_id=None,
        )
        yield StreamChunk(is_done=True, finish_reason="stop")

    text, tool_calls, finish_reason, reasoning = await _accumulate_stream(fake_stream(), None)
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "file_manager"
    assert '"action": "write"' in tool_calls[0]["function"]["arguments"]
    assert tool_calls[0]["id"].startswith("call_")


@pytest.mark.asyncio
async def test_ollama_dict_args_execute_without_repair(mock_agent_deps):
    """El bug exacto de la investigación: args como dict (SDK Ollama real)
    → la tool se ejecuta con sus argumentos, sin repair ni degradación."""
    try:
        from ollama._types import Message
    except ImportError:
        pytest.skip("ollama SDK not available")

    mock_llm, mock_tool, mock_response = mock_agent_deps

    ollama_msg = Message(
        role="assistant",
        content="",
        tool_calls=[
            {
                "function": {
                    "name": "file_manager",
                    "arguments": {
                        "action": "write",
                        "path": "main.py",
                        "content": "print(1)",
                    },
                }
            }
        ],
    )

    tool_response = MagicMock()
    tool_response.choices = [MagicMock()]
    tool_response.choices[0].message.content = None
    tool_response.choices[0].message.tool_calls = ollama_msg.tool_calls

    final_response = MagicMock()
    final_response.choices = [MagicMock()]
    final_response.choices[0].message.content = "Archivo creado."
    final_response.choices[0].message.tool_calls = None

    mock_llm.side_effect = [tool_response, final_response]

    from orchestration.loop import execute_agent_loop

    result = await execute_agent_loop(
        task="Crea main.py",
        agent_type="developer",
        allowed_tools=["file_manager"],
        workspace="main",
    )
    assert result["status"] == "completed"
    assert result["actions_taken"] >= 1
    parameters = mock_tool.call_args_list[0].kwargs["parameters"]
    assert parameters.get("action") == "write"
    assert parameters.get("path") == "main.py"


@pytest.mark.asyncio
async def test_empty_tool_calls_degrade_when_repair_budget_zero(mock_agent_deps):
    """With repair budget=0, empty tool calls degrade to text immediately."""
    mock_llm, mock_tool, mock_response = mock_agent_deps

    tool_response = MagicMock()
    tool_response.choices = [MagicMock()]
    tool_response.choices[0].message.content = "I'll write code..."
    tool_call = MagicMock()
    tool_call.function.name = "file_manager"
    tool_call.function.arguments = '{"action": "", "path": ""}'
    tool_response.choices[0].message.tool_calls = [tool_call]

    mock_llm.return_value = tool_response

    from orchestration.loop import AgentLoopConfig, execute_agent_loop

    config = AgentLoopConfig(max_tool_call_repairs=0)
    result = await execute_agent_loop(
        task="Write test.py",
        agent_type="developer",
        allowed_tools=["file_manager"],
        workspace="main",
        config=config,
    )
    assert isinstance(result, dict)
    assert result.get("status") in ("completed", "stalled", "text_response")


def test_check_stall_resets_on_successful_tool():
    """Iteraciones con tools exitosas (aunque no modifiquen archivos) NO son stall.

    Regresión: el patrón leer-antes-de-escribir hacía que reads fallidos
    (FileNotFoundError → success=False) acumularan stall y mataran al
    agente antes de que llegara a escribir. Con el read amigable
    (success=True) el contador debe resetearse.
    """
    from orchestration.loop import _check_stall

    # Successful (non-modifying) tool iteration resets the counter
    consecutive, early = _check_stall(1, True, 2, 1, [], max_stall_iterations=2)
    assert consecutive == 0
    assert early is None

    # Failed iterations accumulate...
    consecutive, early = _check_stall(0, False, 1, 1, [], max_stall_iterations=2)
    assert consecutive == 1
    assert early is None

    # ...and trigger stall at the limit
    consecutive, early = _check_stall(1, False, 2, 2, [], max_stall_iterations=2)
    assert consecutive == 2
    assert early is not None
    assert early["status"] == "stalled"


def test_check_stall_not_triggered_when_files_written():
    """Si ya se escribieron archivos, no se reporta stall aunque haya
    iteraciones no-modificadoras posteriores."""
    from orchestration.loop import _check_stall

    consecutive, early = _check_stall(1, False, 3, 3, ["a.py"], max_stall_iterations=2)
    assert consecutive == 0
    assert early is None


@pytest.mark.asyncio
async def test_degradation_emits_system_notice_and_metric(mock_agent_deps):
    """Repair budget exhausted → aviso visible (emit_system) + métrica de repairs."""
    from unittest.mock import AsyncMock

    from orchestration.context import WorkflowEvents

    mock_llm, mock_tool, mock_response = mock_agent_deps

    tool_response = MagicMock()
    tool_response.choices = [MagicMock()]
    tool_response.choices[0].message.content = "I'll write code..."
    tool_call = MagicMock()
    tool_call.function.name = "file_manager"
    tool_call.function.arguments = '{"action": "", "path": ""}'
    tool_response.choices[0].message.tool_calls = [tool_call]
    mock_llm.return_value = tool_response

    on_system = AsyncMock()
    events = WorkflowEvents(
        on_stream_chunk=AsyncMock(),
        on_system_message=on_system,
        on_assistant_message=AsyncMock(),
        on_stats_update=AsyncMock(),
        on_ui_refresh=AsyncMock(),
    )

    from core.metrics import metrics as m
    from orchestration.loop import AgentLoopConfig, execute_agent_loop

    m._tool_call_repairs.clear()
    config = AgentLoopConfig(max_tool_call_repairs=0)
    await execute_agent_loop(
        task="Write test.py",
        agent_type="developer",
        allowed_tools=["file_manager"],
        workspace="main",
        config=config,
        events=events,
    )

    system_calls = [str(c.args[0]) for c in on_system.call_args_list]
    assert any("degradando" in msg.lower() for msg in system_calls), system_calls

    repairs = m.get_tool_call_repairs()
    assert any(count >= 1 for count in repairs.values())

    # restaurar estado global para no contaminar tests posteriores
    m._tool_call_repairs.clear()


@pytest.mark.asyncio
async def test_model_without_tool_support_uses_text_mode(mock_agent_deps):
    """Modelo declarado sin soporte de tools → no se envían tools al LLM."""
    from unittest.mock import AsyncMock

    from orchestration.context import WorkflowEvents
    from orchestration.loop import AgentLoopConfig, execute_agent_loop

    mock_llm, mock_tool, mock_response = mock_agent_deps

    with (
        patch("orchestration.loop.model_supports_tool_calling", return_value=False),
        patch("orchestration.loop.settings") as mock_settings,
    ):
        mock_settings.model_roles = {
            "default": {"model": "deepseek-v4-flash"},
            "agent": {"model": "deepseek-v4-flash", "ollama_model": "minimax-m3:cloud"},
        }
        mock_settings.ollama_model = "phi3:mini"
        mock_settings.active_workspace = "main"
        mock_settings.verbose_logging = False
        mock_settings.max_context_tokens = 128000  # hard_cap real del presupuesto

        on_system = AsyncMock()
        events = WorkflowEvents(
            on_stream_chunk=AsyncMock(),
            on_system_message=on_system,
            on_assistant_message=AsyncMock(),
            on_stats_update=AsyncMock(),
            on_ui_refresh=AsyncMock(),
        )

        result = await execute_agent_loop(
            task="Crea un script",
            agent_type="developer",
            allowed_tools=["file_manager"],
            workspace="main",
            config=AgentLoopConfig(),
            events=events,
        )

    assert result["status"] == "completed"
    tools_kwargs = [kw for kw in (c.kwargs for c in mock_llm.call_args_list) if kw.get("tools")]
    assert tools_kwargs == []
    system_calls = [str(c.args[0]) for c in on_system.call_args_list]
    assert any("tool calling" in msg.lower() for msg in system_calls), system_calls


@pytest.mark.asyncio
async def test_build_extra_context_uses_stricter_similarity_threshold():
    """La inyección de tareas similares debe exigir similitud >= 0.5."""
    mock_memory = MagicMock()
    mock_memory.search_async = AsyncMock(
        return_value=[{"key": "regular_doc", "value": "resumen de tarea similar"}]
    )

    with (
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_indexer_cls,
    ):
        mock_indexer = MagicMock()
        mock_indexer.index_project.return_value = None
        mock_indexer.find_relevant_code.return_value = ""
        mock_indexer_cls.return_value = mock_indexer

        from orchestration.loop import _build_extra_context

        result = await _build_extra_context(
            "crear un script de ejemplo", "proj", "main", existing_context=""
        )
        assert "TAREAS SIMILARES ANTERIORES" in result
        assert mock_memory.search_async.call_args.kwargs["min_similarity"] >= 0.5


def test_is_trivial_profile_true_for_name_only():
    from core.utils import is_trivial_profile

    assert is_trivial_profile({"name": "ChatGPT", "country": None, "preferences": {}}) is True
    assert is_trivial_profile({"name": "Alice"}) is True
    assert is_trivial_profile({}) is True
    assert is_trivial_profile(None) is True


def test_is_trivial_profile_false_with_meaningful_fields():
    from core.utils import is_trivial_profile

    assert is_trivial_profile({"name": "Alice", "country": "España"}) is False
    assert is_trivial_profile({"name": "Bob", "preferences": {"color": "blue"}}) is False


@pytest.mark.asyncio
async def test_user_profile_injection_by_profile_content():
    """La inyección de perfil sigue el contenido: perfil vacío → nada; un
    perfil solo-nombre recién sembrado SÍ se inyecta (el gate anti-stale
    contextual vive en update_user_profile, no en la inyección)."""
    from orchestration.loop import execute_agent_loop

    async def _capture(profile: dict) -> str:
        mock_memory = MagicMock()
        mock_memory.search.return_value = []
        mock_memory.search_async = AsyncMock(return_value=[])
        mock_memory.get_user_profile.return_value = profile
        mock_memory.get_user_summary.return_value = (
            f"Nombre del usuario: {profile.get('name')}" if profile.get("name") else ""
        )

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
            mock_response.choices[0].message.content = "Tarea completada."
            mock_response.choices[0].message.tool_calls = None
            mock_llm.return_value = mock_response
            mock_tool.return_value = {"output": "ok", "success": True}

            await execute_agent_loop(
                task="Crea un script que declare una variable.",
                agent_type="developer",
                workspace="main",
                project_root="proj",
            )
            kwargs = mock_llm.call_args.kwargs
            messages = kwargs["messages"] if "messages" in kwargs else mock_llm.call_args.args[0]
            return "\n".join(str(m.get("content", "")) for m in messages)

    empty = {"name": None, "country": None, "preferences": {}}
    assert "[PERFIL DEL USUARIO]" not in await _capture(empty)

    name_only = {"name": "Ana", "country": None, "preferences": {}}
    assert "[PERFIL DEL USUARIO]" in await _capture(name_only)


@pytest.mark.asyncio
async def test_meaningful_user_profile_injected_into_prompt():
    """Un perfil con datos útiles sí se inyecta."""
    mock_memory = MagicMock()
    mock_memory.search.return_value = []
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = {
        "name": "Alice",
        "country": "España",
        "preferences": {},
    }
    mock_memory.get_user_summary.return_value = "Nombre: Alice. País: España."

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
        mock_response.choices[0].message.content = "Tarea completada."
        mock_response.choices[0].message.tool_calls = None
        mock_llm.return_value = mock_response
        mock_tool.return_value = {"output": "ok", "success": True}

        from orchestration.loop import execute_agent_loop

        await execute_agent_loop(
            task="Crea un script que declare una variable.",
            agent_type="developer",
            workspace="main",
            project_root="proj",
        )
        kwargs = mock_llm.call_args.kwargs
        messages = kwargs["messages"] if "messages" in kwargs else mock_llm.call_args.args[0]
        prompt = "\n".join(str(m.get("content", "")) for m in messages)
        assert "[PERFIL DEL USUARIO]" in prompt


@pytest.mark.asyncio
async def test_loop_final_stats_files_written_is_list():
    """El loop emite files_written como LISTA, no como int."""
    from types import SimpleNamespace

    from orchestration.loop import AgentLoopConfig, execute_agent_loop

    events = AsyncMock()
    captured: list[dict] = []

    async def _capture(stats: dict):
        captured.append(stats)

    events.on_stats_update = _capture
    events.on_system_message = AsyncMock()

    # sin esto, CodebaseIndexer/embeddings REALES cuestan ~25s
    mock_memory = MagicMock()
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = None

    with (
        patch("orchestration.loop.models.call") as mock_call,
        patch("orchestration.loop._execute_single_tool_call") as mock_tool,
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_indexer,
    ):
        mock_indexer.return_value.index_project.return_value = None
        mock_indexer.return_value.find_relevant_code.return_value = ""
        # Respuesta final sin tool calls
        mock_call.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="listo", tool_calls=None))]
        )
        mock_tool.side_effect = AssertionError("no debería llamar tools")

        await execute_agent_loop(
            task="tarea",
            agent_type="developer",
            allowed_tools=["file_manager"],
            events=events,
            config=AgentLoopConfig(max_agent_iterations=1),
        )

    final = captured[-1]
    assert isinstance(final["files_written"], list)


# ── inyección condicional de kwargs según firma del handler ──


def _register_fake(registry, name, handler):
    registry.register(name)(handler)


@pytest.mark.asyncio
async def test_kwargs_injected_only_if_handler_accepts_them():
    """El loop inyecta project_root/workspace SOLO si el handler los acepta.

    Las tools de firma estrecha (p. ej. project_docs/memory_inspector)
    reventaban con "unexpected keyword argument 'project_root'" porque el
    loop inyectaba kwargs a ciegas en TODA tool call.
    """

    from orchestration.loop import _execute_single_tool_call
    from tools.registry import ToolsRegistry

    registry = ToolsRegistry()

    async def project_docs_like(action="list", name=None, query=None, k=3):
        # Firma ESTRECHA (como project_docs real): sin project_root/workspace.
        return {"success": True, "output": "docs"}

    async def file_manager_like(action="write", workspace=None, project_root=None, **kw):
        return {
            "success": True,
            "output": f"ws={workspace} pr={project_root}",
        }

    _register_fake(registry, "project_docs", project_docs_like)
    _register_fake(registry, "file_manager", file_manager_like)

    # Ejecuta con el registry REAL de tools.orchestrator (via safe_tool_call)
    with (
        patch("tools.orchestrator.tools_registry", registry),
        patch("tools.orchestrator.ToolOrchestrator.ENABLE_TOKEN_BUDGET", False),
    ):
        out_narrow = await _execute_single_tool_call(
            "project_docs",
            {"action": "list"},
            project_root="prueba",
            workspace="main",
        )
        out_wide = await _execute_single_tool_call(
            "file_manager",
            {"action": "read", "path": "x.py"},
            project_root="prueba",
            workspace="main",
        )

    # El orquestador envuelve: {"output": <resultado interno>, ...}
    narrow_inner, wide_inner = out_narrow[0], out_wide[0]
    assert isinstance(narrow_inner, dict) and narrow_inner.get("output") == "docs"
    assert isinstance(wide_inner, dict) and wide_inner.get("output") == "ws=main pr=prueba"


@pytest.mark.asyncio
async def test_kwargs_injection_var_kwargs_handler_still_gets_everything():
    """Handlers con **kwargs siguen recibiendo project_root/workspace (file_manager real)."""
    from orchestration.loop import _execute_single_tool_call
    from tools.registry import ToolsRegistry

    registry = ToolsRegistry()
    seen: dict = {}

    async def greedy_handler(**kwargs):
        seen.update(kwargs)
        return {"success": True, "output": "ok"}

    _register_fake(registry, "greedy", greedy_handler)
    with (
        patch("tools.orchestrator.tools_registry", registry),
        patch("tools.orchestrator.ToolOrchestrator.ENABLE_TOKEN_BUDGET", False),
    ):
        await _execute_single_tool_call("greedy", {}, project_root="proj", workspace="main")
    assert seen.get("project_root") == "proj"
    assert seen.get("workspace") == "main"


@pytest.mark.asyncio
async def test_kwargs_unknown_tool_falls_back_to_current_behavior():
    """Tool sin registro conocido (no debería pasar): inyección fail-open."""
    from orchestration.loop import _execute_single_tool_call
    from tools.registry import ToolsRegistry

    registry = ToolsRegistry()  # vacía → handler None
    with (
        patch("tools.orchestrator.tools_registry", registry),
        patch("tools.orchestrator.ToolOrchestrator.ENABLE_TOKEN_BUDGET", False),
    ):
        out, _, _, ok = await _execute_single_tool_call(
            "inexistente", {}, project_root="p", workspace="main"
        )
    assert ok is False  # tool_not_found, sin crash


@pytest.mark.asyncio
async def test_agent_loop_stops_when_token_budget_exhausted(monkeypatch):
    """Con el presupuesto en rojo, el loop corta ANTES de la siguiente
    llamada LLM (status stalled, mensaje accionable) en vez de seguir
    facturando llamadas completas hasta max_agent_iterations."""
    from core.config import settings

    monkeypatch.setattr(settings, "max_agent_iterations", 5, raising=False)

    mock_memory = MagicMock()
    mock_memory.search.return_value = []
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = {"name": None, "preferences": {}}

    with (
        patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock) as mock_tool,
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_indexer_cls,
        patch("tools.orchestrator.get_llm_token_usage", return_value=500),
        patch("tools.orchestrator.get_token_budget_max", return_value=100),
    ):
        mock_indexer = MagicMock()
        mock_indexer.index_project.return_value = None
        mock_indexer.find_relevant_code.return_value = ""
        mock_indexer_cls.return_value = mock_indexer

        # 1ª respuesta: tool call (fuerza una 2ª iteración); 2ª: final.
        tool_response = MagicMock()
        tool_response.choices = [MagicMock()]
        tool_response.choices[0].message.content = None
        tool_call = MagicMock()
        tool_call.function.name = "file_manager"
        tool_call.function.arguments = '{"action": "write", "path": "a.py", "content": "x"}'
        tool_response.choices[0].message.tool_calls = [tool_call]
        mock_llm.side_effect = [tool_response]
        mock_tool.return_value = {"output": "ok", "success": True}

        from orchestration.loop import execute_agent_loop

        result = await execute_agent_loop(
            task="Tarea larga",
            agent_type="developer",
            workspace="main",
            project_root="proj",
        )

    assert result["status"] == "stalled"
    assert "Presupuesto" in str(result.get("result", ""))
    assert mock_llm.await_count == 1  # la 2ª iteración jamás llamó al LLM
