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


@pytest.mark.asyncio
async def test_run_direct_agent_engancha_el_callback_de_aprobacion():
    """El chat directo también engancha el approval que provee el host.

    Si solo lo hace el orquestador, en el chat directo toda acción peligrosa
    (bash_manager, code_exec, delete, apply) se deniega fail-closed por
    "no hay UI de aprobación" aunque la GUI la tenga.
    """
    from desktop.services.workflow_runner import WorkflowRunner
    from tools.orchestrator import get_approval_callback

    async def _aprobar(_tool: str, _params: dict) -> bool:
        return True

    eventos = type("E", (), {"on_approval_required": staticmethod(_aprobar)})()

    runner = WorkflowRunner()
    session = AsyncMock()
    session.events = eventos
    session.context.conversation_history = []

    visto: dict = {}

    async def fake_execute_agent(*a, **k):
        visto["cb"] = get_approval_callback()
        return "respuesta"

    with (
        patch("agents.registry.agents_registry.get_profile", return_value={}),
        patch("agents.service.AgentsService.execute_agent", new=fake_execute_agent),
    ):
        await runner.run_direct_agent(session, "hola", "developer")

    assert visto["cb"] is not None, "el host proveyó callback: debe estar enganchado"
    assert await visto["cb"]("bash_manager", {"command": "ls"}) is True
    assert get_approval_callback() is None, "el callback se limpia al terminar"


@pytest.mark.asyncio
async def test_run_direct_agent_sin_host_sigue_fail_closed():
    """Sin host (headless) el fail-closed se conserva: callback None."""
    from desktop.services.workflow_runner import WorkflowRunner
    from tools.orchestrator import get_approval_callback

    runner = WorkflowRunner()
    session = AsyncMock()
    session.events = None
    session.context.conversation_history = []

    visto: dict = {}

    async def fake_execute_agent(*a, **k):
        visto["cb"] = get_approval_callback()
        return "respuesta"

    with (
        patch("agents.registry.agents_registry.get_profile", return_value={}),
        patch("agents.service.AgentsService.execute_agent", new=fake_execute_agent),
    ):
        await runner.run_direct_agent(session, "hola", "developer")

    assert visto["cb"] is None
