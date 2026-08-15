"""Servicios extraídos de maestro_tab — lógica async testable sin Qt (spec §5)."""

from unittest.mock import AsyncMock, patch

import pytest

from desktop.services.workflow_runner import WorkflowRunner


@pytest.mark.asyncio
async def test_run_delegates_to_orchestrator_and_reports_events():
    runner = WorkflowRunner()
    runner.on_system = AsyncMock()
    runner.on_assistant = AsyncMock()
    session = AsyncMock()
    session.context.project_root = "code_projects/x"
    session.context.last_clarification = ""
    session.context.conversation_history = []

    with patch(
        "orchestration.workflows.orchestrator.WorkflowOrchestrator.run_full_workflow",
        new_callable=AsyncMock,
        return_value="resultado final",
    ) as mock_run:
        await runner.run(session)

    mock_run.assert_awaited_once_with(session=session)
    runner.on_assistant.assert_awaited_once_with("resultado final")


@pytest.mark.asyncio
async def test_run_handles_clarification_pause():
    runner = WorkflowRunner()
    runner.on_system = AsyncMock()
    runner.on_assistant = AsyncMock()
    runner.on_pause = AsyncMock()
    session = AsyncMock()
    session.context.project_root = None
    session.context.last_clarification = "¿qué framework?"
    session.context.conversation_history = []

    with patch(
        "orchestration.workflows.orchestrator.WorkflowOrchestrator.run_full_workflow",
        new_callable=AsyncMock,
        return_value="[PAUSED:clarification_needed]",
    ):
        await runner.run(session)

    runner.on_pause.assert_awaited_once_with(session, "¿qué framework?")


@pytest.mark.asyncio
async def test_resume_delegates():
    runner = WorkflowRunner()
    runner.on_system = AsyncMock()
    runner.on_assistant = AsyncMock()
    session = AsyncMock()
    session.context.conversation_history = []

    with patch(
        "orchestration.workflows.orchestrator.WorkflowOrchestrator.resume_workflow",
        new_callable=AsyncMock,
        return_value="respuesta tras clarificar",
    ):
        await runner.resume(session, "usa FastAPI")

    runner.on_assistant.assert_awaited_once_with("respuesta tras clarificar")


@pytest.mark.asyncio
async def test_run_skips_warning_when_streaming_active():
    """Sin final pero con streaming activo → NO se emite el warning espurio."""
    runner = WorkflowRunner()
    runner.on_system = AsyncMock()
    runner.on_assistant = AsyncMock()
    runner.streaming_check = lambda: True
    session = AsyncMock()
    session.context.project_root = None
    session.context.last_clarification = ""
    session.context.conversation_history = []

    with patch(
        "orchestration.workflows.orchestrator.WorkflowOrchestrator.run_full_workflow",
        new_callable=AsyncMock,
        return_value="",
    ):
        await runner.run(session)

    runner.on_system.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_warns_when_no_output_and_no_streaming():
    """Sin final y sin streaming → warning de workflow sin respuesta."""
    runner = WorkflowRunner()
    runner.on_system = AsyncMock()
    runner.on_assistant = AsyncMock()
    runner.streaming_check = lambda: False
    session = AsyncMock()
    session.context.project_root = None
    session.context.last_clarification = ""
    session.context.conversation_history = []

    with patch(
        "orchestration.workflows.orchestrator.WorkflowOrchestrator.run_full_workflow",
        new_callable=AsyncMock,
        return_value="",
    ):
        await runner.run(session)

    runner.on_system.assert_awaited_once_with("⚠️ El workflow no produjo respuesta.")


@pytest.mark.asyncio
async def test_run_direct_agent_returns_response():
    """run_direct_agent retorna la respuesta para persistencia sin escanear history."""
    runner = WorkflowRunner()
    runner.on_assistant = AsyncMock()
    session = AsyncMock()
    session.events = None
    session.context.conversation_history = []

    with (
        patch("agents.registry.agents_registry.get_profile", return_value={}),
        patch(
            "agents.service.AgentsService.execute_agent",
            new_callable=AsyncMock,
            return_value="hola desde el agente",
        ),
    ):
        result = await runner.run_direct_agent(session, "hola", "developer")

    assert result == "hola desde el agente"
    runner.on_assistant.assert_awaited_once_with("hola desde el agente")


@pytest.mark.asyncio
async def test_run_direct_agent_template_failure_falls_back():
    """Un fallo de load_workflow_template no aborta la conversación directa."""
    runner = WorkflowRunner()
    runner.on_assistant = AsyncMock()
    session = AsyncMock()
    session.events = None
    session.context.conversation_history = []
    session.context.project_root = None

    with (
        patch(
            "agents.registry.agents_registry.get_profile",
            return_value={"tools": ["file_manager"]},
        ),
        patch(
            "tools.specs.expand_allowed_tools",
            return_value=["file_manager"],
        ),
        patch(
            "orchestration.loader.load_workflow_template",
            side_effect=Exception("template roto"),
        ),
        patch(
            "orchestration.loop.execute_agent_loop",
            new_callable=AsyncMock,
            return_value={"status": "done", "result": "ok", "files_written": []},
        ),
    ):
        result = await runner.run_direct_agent(session, "haz x", "developer")

    assert result == "ok"


import json

from desktop.services.conversation_export import export_history_to_file


@pytest.mark.asyncio
async def test_export_md_strips_internal_messages(tmp_path):
    history = [
        {"role": "user", "content": "hola"},
        {"role": "system", "content": "Eres Morphix, un asistente experto"},
        {"role": "assistant", "content": "¡hola!"},
    ]
    target = tmp_path / "conv.md"
    await export_history_to_file(history, str(target), "md")
    text = target.read_text(encoding="utf-8")
    assert "Eres Morphix" not in text
    assert "hola" in text


@pytest.mark.asyncio
async def test_export_json_preserves_agent_metadata(tmp_path):
    history = [
        {"role": "agent", "agent": "developer", "label": "op", "content": "hago x"},
    ]
    target = tmp_path / "conv.json"
    await export_history_to_file(history, str(target), "json")
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data[0]["agent"] == "developer"
    assert data[0]["role"] == "agent"


@pytest.mark.asyncio
async def test_export_unknown_format_raises(tmp_path):
    """Formato desconocido → ValueError (no un 'éxito' sin archivo)."""
    target = tmp_path / "conv.xyz"
    with pytest.raises(ValueError, match="Formato no soportado"):
        await export_history_to_file([{"role": "user", "content": "x"}], str(target), "xyz")
    assert not target.exists()
