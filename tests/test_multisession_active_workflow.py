# tests/test_multisession_active_workflow.py
"""Multi-sesión: el workflow activo es per-run, no global.

Cada sesión Maestro lleva su propio preset. `run_full_workflow` acepta
`active_workflow` (kwarg) que sobreescribe `ctx.active_workflow`, y el
dispatch usa ese valor en vez de `get_active_workflow()` (global):
resuelve el doc crudo por `ctx.active_workflow` y lo ejecuta como DSL.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.context import Session, WorkflowContext, WorkflowEvents


def _events() -> WorkflowEvents:
    return WorkflowEvents(
        on_stream_chunk=AsyncMock(),
        on_system_message=AsyncMock(),
        on_assistant_message=AsyncMock(),
        on_stats_update=AsyncMock(),
        on_ui_refresh=AsyncMock(),
    )


def _ctx(**overrides) -> WorkflowContext:
    base = dict(
        query="q",
        mode="orchestrate",
        conversation_history=[],
        workspace="main",
        project_root="proj",
        current_pdf_text="",
        active_workflow="development",
        settings=MagicMock(),
        agents_registry=MagicMock(),
        enc=MagicMock(),
        allowed_tools=["file_manager"],
    )
    base.update(overrides)
    return WorkflowContext(**base)


def _dsl_doc(name: str) -> dict:
    return {
        "version": 1,
        "name": name,
        "agents": {"allowed": ["developer"]},
        "tools": {"allowed": ["file_manager"]},
        "steps": [
            {
                "id": "ciclo",
                "kind": "loop",
                "max_iter": 2,
                "body": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "g"}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_active_workflow_kwarg_overrides_and_dispatches():
    """El kwarg active_workflow manda sobre get_active_workflow() global."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    ctx = _ctx()
    with (
        patch(
            "orchestration.bots_dispatch.bots_roster_safe",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_document",
            side_effect=lambda ws, name: _dsl_doc(name),  # resuelve POR-NOMBRE
        ) as mock_load,
        patch(
            "orchestration.workflows.orchestrator.get_global_workspaces",
            return_value=MagicMock(current="main"),
        ),
        patch(
            "orchestration.workflows.orchestrator._parse_direct_tool_command",
            return_value=None,
        ),
        patch(
            "orchestration.workflows.orchestrator.WorkflowOrchestrator._run_dsl_workflow",
            new_callable=AsyncMock,
            return_value="dsl-done",
        ) as mock_dsl,
    ):
        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=_events()),
            active_workflow="tdd",
        )

    assert result == "dsl-done"
    assert ctx.active_workflow == "tdd"
    mock_dsl.assert_awaited_once()
    assert mock_dsl.await_args.kwargs["raw"]["name"] == "tdd"
    mock_load.assert_called_once_with("main", "tdd")


@pytest.mark.asyncio
async def test_two_concurrent_runs_use_their_own_workflow():
    """Dos runs concurrentes resuelven cada uno su propio doc por nombre."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    async def run_workflow(name: str):
        ctx = _ctx()
        with (
            patch(
                "orchestration.bots_dispatch.bots_roster_safe",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "core.security.undercover_mode.undercover.check_query",
                return_value=True,
            ),
            patch(
                "orchestration.workflows.orchestrator._parse_direct_tool_command",
                return_value=None,
            ),
            patch(
                "orchestration.workflows.orchestrator.get_global_workspaces",
                return_value=MagicMock(current="main"),
            ),
            patch(
                "orchestration.workflows.orchestrator.load_workflow_document",
                side_effect=lambda ws, name_: _dsl_doc(name_),
            ),
            patch(
                "orchestration.workflows.orchestrator.WorkflowOrchestrator._run_dsl_workflow",
                new_callable=AsyncMock,
                return_value="dsl-done",
            ) as mock_dsl,
        ):
            result = await WorkflowOrchestrator.run_full_workflow(
                session=Session(context=ctx, events=_events()),
                active_workflow=name,
            )

        assert result == "dsl-done"
        return mock_dsl

    mock_a = await run_workflow("tdd")
    mock_b = await run_workflow("collaborative")
    assert mock_a.await_args.kwargs["raw"]["name"] == "tdd"
    assert mock_b.await_args.kwargs["raw"]["name"] == "collaborative"
