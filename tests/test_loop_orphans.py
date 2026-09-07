"""Ningún assistant con tool_calls huérfanas en el historial."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_no_orphan_assistant_when_all_names_empty():
    """Si ninguna tool call tiene nombre, no debe quedar assistant huérfano."""
    mock_memory = MagicMock()
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = None

    bad_tc = MagicMock()
    bad_tc.function.name = ""  # nombre vacío → no ejecutable
    bad_tc.function.arguments = '{"action": "read", "path": "a.py"}'
    bad_response = MagicMock()
    bad_response.choices = [MagicMock()]
    bad_response.choices[0].message.content = None
    bad_response.choices[0].message.tool_calls = [bad_tc]

    final_response = MagicMock()
    final_response.choices = [MagicMock()]
    final_response.choices[0].message.content = "Listo."
    final_response.choices[0].message.tool_calls = None

    with (
        patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock),
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_indexer_cls,
    ):
        mock_indexer_cls.return_value.index_project.return_value = None
        mock_indexer_cls.return_value.find_relevant_code.return_value = ""
        mock_llm.side_effect = [bad_response, final_response]

        from orchestration.loop import execute_agent_loop

        result = await execute_agent_loop(
            task="lee el archivo",
            agent_type="developer",
            allowed_tools=["file_manager"],
            workspace="main",
        )
        assert result["status"] == "completed"

        # Historial enviado a la 2ª llamada: sin assistants con tool_calls huérfanas
        second_msgs = mock_llm.await_args_list[1].kwargs["messages"]
        orphans = []
        call_ids = set()
        for m in second_msgs:
            for tc in m.get("tool_calls") or []:
                call_ids.add(tc["id"])
        result_ids = {m["tool_call_id"] for m in second_msgs if m["role"] == "tool"}
        assert call_ids == result_ids, (
            f"A-IV: assistant con tool_calls sin resultado quedó en historial "
            f"(calls={call_ids}, results={result_ids})"
        )


@pytest.mark.asyncio
async def test_repeat_stall_increments_once():
    """repeats>=3 debe producir UN solo incremento de stall por iteración."""
    from unittest.mock import AsyncMock, patch

    import orchestration.loop as loop_mod
    from orchestration.loop import _execute_tool_calls_and_check_stall

    calls = [
        {"name": "file_manager", "id": f"c{i}", "arguments": {"action": "read", "path": "a.py"}}
        for i in range(3)
    ]
    files_written: list[str] = []
    actions_taken = 0

    with patch.object(
        loop_mod,
        "_execute_single_tool_call",
        new=AsyncMock(return_value=("contenido", False, None, False)),
    ):
        actions_taken, modified, files_written, stalls, early = (
            await _execute_tool_calls_and_check_stall(
                calls,
                [],
                files_written,
                actions_taken,
                False,
                0,
                1,
                MagicMock(max_stall_iterations=2),
                None,
                None,
                None,
                {},
                provider_kind="openai",
                allowed_tools=["file_manager"],
            )
        )

    assert stalls == 1, f"doble incremento de stall detectado (stalls={stalls})"


@pytest.mark.asyncio
async def test_skip_task_message_avoids_user_user_in_resume():
    """En resume el historial ya trae la respuesta del usuario como último
    mensaje — re-añadir la tarea original produce user/user consecutivo."""
    mock_memory = MagicMock()
    mock_memory.search_async = AsyncMock(return_value=[])
    mock_memory.get_user_profile.return_value = None

    final_response = MagicMock()
    final_response.choices = [MagicMock()]
    final_response.choices[0].message.content = "continuado"
    final_response.choices[0].message.tool_calls = None

    history = [
        {"role": "user", "content": "tarea original"},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "user", "content": "[Respuesta a: ¿qué formato?] json"},
    ]

    with (
        patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock),
        patch("orchestration.loop.models.call", new_callable=AsyncMock) as mock_llm,
        patch("orchestration.loop.memory_manager", mock_memory),
        patch("core.memory.manager.memory", mock_memory),
        patch("orchestration.loop.CodebaseIndexer") as mock_indexer_cls,
    ):
        mock_indexer_cls.return_value.index_project.return_value = None
        mock_indexer_cls.return_value.find_relevant_code.return_value = ""
        mock_llm.return_value = final_response

        from orchestration.loop import execute_agent_loop

        await execute_agent_loop(
            task="tarea original",
            agent_type="developer",
            history=history,
            workspace="main",
            skip_task_message=True,
        )

        sent = mock_llm.await_args.kwargs["messages"]
        user_contents = [m["content"] for m in sent if m["role"] == "user"]
        assert (
            user_contents.count("tarea original") == 1
        ), f"task re-añadida tras la respuesta del usuario en resume: {user_contents}"
        assert user_contents[-1] == "[Respuesta a: ¿qué formato?] json"
