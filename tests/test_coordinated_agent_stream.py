"""Coordinated emite agent_stream/agent_status → actividad visible en vivo (spec G5)."""

from unittest.mock import AsyncMock, patch

import pytest

from orchestration.context import Session, WorkflowContext
from orchestration.workflows.coordinated import MultiAgentCoordinator


@pytest.mark.asyncio
async def test_execute_one_emits_agent_stream_and_status():
    events = AsyncMock()
    ctx = WorkflowContext(
        query="haz x",
        mode="orchestrate",
        workspace="main",
        conversation_history=[],
    )
    session = Session(context=ctx, events=events)

    with (
        patch(
            "orchestration.workflows.coordinated.execute_agent_loop",
            new_callable=AsyncMock,
            return_value={
                "status": "done",
                "result": "implementé el módulo x",
                "files_written": ["x.py"],
            },
        ),
        patch(
            "agents.registry.agents_registry.get_profile",
            return_value={"tools": ["file_manager"]},
        ),
    ):
        coordinator = MultiAgentCoordinator()
        result = await coordinator._execute_one(
            sid="1",
            st={"id": "1", "description": "haz x"},
            agent="developer",
            project_root=None,
            workspace="main",
            allowed_tools=["file_manager"],
            events=events,
            session=session,
        )

    assert result["status"] == "done"
    # status: thinking antes de ejecutar, ready después
    status_calls = [c.args for c in events.on_agent_status.await_args_list]
    assert any("thinking" in call for call in status_calls)
    assert any("ready" in call for call in status_calls)
    # stream de chunks del resultado
    assert events.on_agent_stream.await_count >= 1
