# tests/test_workflow_allowlist_enforcement.py
"""PR 2 — El allowlist se enforcea en el MOMENTO de ejecutar la tool.

Antes: el allowlist solo filtraba las definiciones que veía el LLM; un
tool-call alucinado (p.ej. bash_manager en collaborative) se ejecutaba igual.
Estos tests bloquean esa regresión en las 5 rutas:
loop, collaborative, direct tool, simple conversation y run_direct_agent.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.context import Session, WorkflowContext, WorkflowEvents


def _make_ctx(query: str) -> WorkflowContext:
    return WorkflowContext(
        query=query,
        mode="chat",
        conversation_history=[],
        workspace="main",
        project_root=None,
        current_pdf_text="",
        active_workflow="default",
        settings=MagicMock(),
        agents_registry=MagicMock(),
        enc=MagicMock(),
        allowed_tools=None,
    )


def _make_events() -> WorkflowEvents:
    return WorkflowEvents(
        on_stream_chunk=AsyncMock(),
        on_system_message=AsyncMock(),
        on_assistant_message=AsyncMock(),
        on_stats_update=AsyncMock(),
        on_ui_refresh=AsyncMock(),
    )


# ── Ruta 1: agent loop ──


@pytest.mark.asyncio
async def test_loop_rejects_tool_not_in_allowlist():
    """Un tool-call fuera del allowlist NO se ejecuta y devuelve error al agente."""
    from orchestration.loop import _execute_single_tool_call

    with patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock) as mock_safe:
        output, is_mod, file_path, success = await _execute_single_tool_call(
            "bash_manager",
            {"command": "ls"},
            project_root=None,
            workspace="main",
            allowed_tools=["file_manager"],
        )

    mock_safe.assert_not_awaited()
    assert success is False
    assert "no está permitida" in output


@pytest.mark.asyncio
async def test_loop_executes_tool_in_allowlist():
    from orchestration.loop import _execute_single_tool_call

    with patch("orchestration.loop.safe_tool_call", new_callable=AsyncMock) as mock_safe:
        mock_safe.return_value = {"success": True, "output": "ok"}
        output, _is_mod, _file_path, success = await _execute_single_tool_call(
            "file_manager",
            {"action": "read", "path": "a.py"},
            project_root=None,
            workspace="main",
            allowed_tools=["file_manager"],
        )

    mock_safe.assert_awaited_once()
    assert success is True


# ── Ruta 2: collaborative panelist ──
# PR 5: los panelistas ya no usan el mini-loop de 2 llamadas — delegan en
# execute_agent_loop, donde el allowlist se enforcea en ejecución (ver
# test_loop_rejects_tool_not_in_allowlist y test_panelist_uses_agent_loop).


# ── Ruta 3: comando directo ──


@pytest.mark.asyncio
async def test_direct_tool_blocked_when_not_in_workflow_allowlist():
    """Con collaborative activo, 'bash_manager: run' se bloquea antes de ejecutar."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with (
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_document",
            return_value={
                "version": 1,
                "name": "collaborative",
                "agents": {"allowed": ["developer"]},
                "tools": {"allowed": ["file_manager"]},
                "steps": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "g"}],
            },
        ),
        patch(
            "orchestration.workflows.orchestrator.get_global_workspaces",
            return_value=MagicMock(current="main"),
        ),
        patch(
            "orchestration.workflows.orchestrator.safe_tool_call",
            new_callable=AsyncMock,
        ) as mock_tool,
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            new_callable=AsyncMock,
        ),
        patch("tools.orchestrator.ToolOrchestrator.reset_token_budget"),
        patch(
            "tools.registry.tools_registry.get_tool",
            return_value=lambda **kw: {"success": True, "output": "ok"},
        ),
    ):
        mock_tool.return_value = {"success": True, "output": "ok"}
        ctx = _make_ctx(query="bash_manager: run, command=ls")
        events = _make_events()
        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )

    mock_tool.assert_not_awaited()
    assert result is not None
    assert "bloqueado" in result


@pytest.mark.asyncio
async def test_run_direct_agent_empty_intersection_denies_tools():
    """Intersección vacía = agente sin tools, NO el toolset completo."""
    from desktop.services.workflow_runner import WorkflowRunner

    runner = WorkflowRunner()
    session = AsyncMock()
    session.events = None
    session.context.conversation_history = []

    with (
        patch(
            "agents.registry.agents_registry.get_profile",
            return_value={"tools": ["file_manager"]},
        ),
        patch(
            "desktop.services.workflow_view.load_workflow_view",
            return_value={
                "description": "",
                "agents_allowed": [],
                "tools_allowed": ["web_search"],
                "project_required": False,
                "skills": False,
                "dsl": False,
                "kind_summary": None,
                "raw": {
                    "type": "development",
                    "tools": {"allowed": ["web_search"]},
                    "agents": {},
                    "project": {},
                },
            },
        ),
        patch("core.workspaces.get_global_workspaces", return_value=MagicMock(current="main")),
        patch("core.workflow_state.get_active_workflow", return_value="development"),
        patch(
            "orchestration.loop.execute_agent_loop",
            new_callable=AsyncMock,
        ) as mock_loop,
    ):
        mock_loop.return_value = {"status": "done", "result": "ok", "files_written": []}
        await runner.run_direct_agent(session, "haz x", "developer")

    allowed = mock_loop.call_args.kwargs["allowed_tools"]
    assert allowed == []
