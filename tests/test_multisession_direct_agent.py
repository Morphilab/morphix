# tests/test_multisession_direct_agent.py
"""Multi-sesión: el chat directo con agente reserva/libera el token de ejecución.

Igual que `run_full_workflow`, una conversación directa con agente lee
`agents_registry`/memoria del workspace activo, así que debe vetar el switch
de workspace mientras corre (`begin_workflow_run`/`end_workflow_run`).
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_run_direct_agent_reserves_and_releases_token():
    from core.workspaces import workflow_running
    from desktop.services.workflow_runner import WorkflowRunner

    assert not workflow_running()

    runner = WorkflowRunner()
    runner.on_assistant = AsyncMock()
    session = AsyncMock()
    session.events = None
    session.context.conversation_history = []

    observed: dict[str, bool] = {}

    async def fake_execute_agent(*a, **k):
        observed["running"] = workflow_running()
        return "respuesta"

    with (
        patch("agents.registry.agents_registry.get_profile", return_value={}),
        patch("agents.service.AgentsService.execute_agent", new=fake_execute_agent),
    ):
        result = await runner.run_direct_agent(session, "hola", "developer")

    assert result == "respuesta"
    assert observed["running"] is True
    assert not workflow_running()


@pytest.mark.asyncio
async def test_run_direct_agent_releases_token_on_error():
    from core.workspaces import workflow_running
    from desktop.services.workflow_runner import WorkflowRunner

    assert not workflow_running()

    runner = WorkflowRunner()
    session = AsyncMock()
    session.events = None
    session.context.conversation_history = []

    with (
        patch("agents.registry.agents_registry.get_profile", return_value={}),
        patch(
            "agents.service.AgentsService.execute_agent",
            side_effect=RuntimeError("boom"),
        ),
    ):
        await runner.run_direct_agent(session, "hola", "developer")

    assert not workflow_running()
