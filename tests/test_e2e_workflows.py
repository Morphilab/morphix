"""E2E integration tests — workflow pipelines con mocks mínimos (solo LLM y tools).

A diferencia de los tests unitarios que mockean componentes internos individualmente,
estos tests validan el pipeline completo: entrada → descomposición → ejecución →
agregación → salida, mockeando solo las fronteras externas (LLM y tools).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.events import Session, WorkflowContext, WorkflowEvents


def _make_ctx(query: str = "Hola", workspace: str = "main", mode: str = "orchestrate"):
    return WorkflowContext(
        query=query,
        mode=mode,
        conversation_history=[],
        workspace=workspace,
        project_root=None,
        current_pdf_text=None,
        active_workflow=None,
        settings=MagicMock(),
        agents_registry=MagicMock(),
        enc=MagicMock(),
        allowed_tools=None,
    )


def _make_events():
    on_system = AsyncMock()
    on_assistant = AsyncMock()
    on_stats = AsyncMock()
    events = WorkflowEvents(
        on_stream_chunk=AsyncMock(),
        on_system_message=on_system,
        on_assistant_message=on_assistant,
        on_stats_update=on_stats,
        on_diagram_update=AsyncMock(),
        on_ui_refresh=AsyncMock(),
    )
    return events, on_assistant, on_system, on_stats


# ═══════════════════════════════════════════════════════════════════
#  Collaborative workflow E2E
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_collaborative_workflow_e2e():
    """E2E: collaborative debate pipeline con mocks solo en LLM y finalizer."""
    with (
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
        patch(
            "orchestration.workflows.orchestrator.get_global_workspaces",
            return_value=MagicMock(current="main"),
        ),
        patch(
            "orchestration.workflows.orchestrator.get_active_workflow",
            return_value="default",
        ),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value={
                "project": {},
                "agents": {},
                "tools": {},
                "type": "collaborative",
                "panel": ["developer", "analista"],
                "rounds": 2,
                "moderator": "moderador",
            },
        ),
        patch("tools.orchestrator.ToolOrchestrator.reset_token_budget"),
        # Mock LLM responses for panel agents (text only, no tool calls)
        patch("llm.models.call", new_callable=AsyncMock) as mock_llm,
        # Mock safe_tool_call (agents might try tools)
        patch("tools.wrapper.safe_tool_call", new_callable=AsyncMock) as mock_tool,
        # Mock moderator response
        patch(
            "orchestration.workflows.collaborative.AgentsService.execute_agent",
            new_callable=AsyncMock,
        ) as mock_moderator,
        # Mock finalizer (DB write) — imported locally in CollaborativeOrchestrator.run()
        patch(
            "orchestration.finalizer.finalize_workflow",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.utils.generate_scorecard",
            return_value={"subtasks": 2, "completadas": 2, "tokens": 500, "tiempo": "1.5s"},
        ),
    ):
        # Panel agent responses for each round (2 rounds × 2 agents = 4 calls)
        # Each agent makes 1 LLM call (text only, no tools)
        round1_dev = MagicMock()
        round1_dev.choices = [MagicMock()]
        round1_dev.choices[0].message = MagicMock(tool_calls=None)
        round1_dev.choices[0].message.content = "Opinión developer ronda 1: usar microservicios"

        round1_ana = MagicMock()
        round1_ana.choices = [MagicMock()]
        round1_ana.choices[0].message = MagicMock(tool_calls=None)
        round1_ana.choices[0].message.content = "Opinión analista ronda 1: mejor monolito"

        round2_dev = MagicMock()
        round2_dev.choices = [MagicMock()]
        round2_dev.choices[0].message = MagicMock(tool_calls=None)
        round2_dev.choices[0].message.content = (
            "Opinión developer ronda 2: microservicios con API gateway"
        )

        round2_ana = MagicMock()
        round2_ana.choices = [MagicMock()]
        round2_ana.choices[0].message = MagicMock(tool_calls=None)
        round2_ana.choices[0].message.content = "Opinión analista ronda 2: acepto, con condiciones"

        mock_llm.side_effect = [round1_dev, round1_ana, round2_dev, round2_ana]
        mock_tool.return_value = {"success": True, "output": "ok"}
        mock_moderator.return_value = (
            "Consenso: implementar microservicios con API gateway y monitoreo."
        )

        from orchestration.workflows.orchestrator import WorkflowOrchestrator

        ctx = _make_ctx(query="¿Microservicios o monolito para el nuevo servicio?")
        events, _on_assistant, _on_system, _ = _make_events()

        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )

        assert "microservicios" in result.lower()
        assert "api gateway" in result.lower()
        # Verify 4 panel agent calls (2 rounds × 2 agents), + possibly 1 for context
        assert 4 <= mock_llm.call_count <= 6
        # Verify moderator was called once
        mock_moderator.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════
#  Coordinated workflow E2E
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_coordinated_workflow_e2e():
    """E2E: coordinated DAG pipeline con 2 subtareas dependientes."""
    with (
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
        patch(
            "orchestration.workflows.orchestrator.get_global_workspaces",
            return_value=MagicMock(current="main"),
        ),
        patch(
            "orchestration.workflows.orchestrator.get_active_workflow",
            return_value="default",
        ),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value={
                "project": {},
                "agents": {},
                "tools": {},
                "type": "coordinated",
            },
        ),
        patch("tools.orchestrator.ToolOrchestrator.reset_token_budget"),
        # Force the single-phase DAG path (sprint-25 added phase-aware decomposition first)
        patch(
            "orchestration.decomposer.decompose_task_with_phases",
            new_callable=AsyncMock,
            return_value={"phases": []},
        ),
        # Mock DAG decomposition via LLM
        patch(
            "orchestration.workflows.coordinated.models.call", new_callable=AsyncMock
        ) as mock_llm,
        # Mock execute_agent_loop for subtasks (returns structured result)
        patch(
            "orchestration.workflows.coordinated.execute_agent_loop",
            new_callable=AsyncMock,
        ) as mock_loop,
        # Mock finalizer
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            new_callable=AsyncMock,
        ),
    ):
        # First LLM call: DAG decomposition
        dag_response = MagicMock()
        dag_response.choices = [MagicMock()]
        dag_response.choices[0].message = MagicMock()
        dag_response.choices[0].message.content = (
            '{"subtasks": ['
            '{"id": "define_schema", "description": "Define database schema for users table", "depends_on": [], "agent_hint": "analista"},'
            '{"id": "build_api", "description": "Build REST API endpoint for user CRUD", "depends_on": ["define_schema"], "agent_hint": "developer"}'
            "]}"
        )

        # Second+ LLM call: aggregation
        agg_response = MagicMock()
        agg_response.choices = [MagicMock()]
        agg_response.choices[0].message = MagicMock()
        agg_response.choices[0].message.content = (
            "Schema defined successfully. REST API endpoint created. "
            "Full CRUD for users table is ready."
        )

        mock_llm.side_effect = [dag_response, agg_response]

        # Subtask results from execute_agent_loop
        mock_loop.side_effect = [
            {
                "status": "completed",
                "result": "Database schema: users(id, name, email) created with migrations",
                "actions_taken": 3,
                "iterations": 2,
                "files_written": ["models.py", "migration.sql"],
            },
            {
                "status": "completed",
                "result": "REST API endpoints: GET/POST/PUT/DELETE /api/users implemented",
                "actions_taken": 4,
                "iterations": 3,
                "files_written": ["api.py", "test_api.py"],
            },
        ]

        from orchestration.workflows.orchestrator import WorkflowOrchestrator

        ctx = _make_ctx(query="Crea un CRUD de usuarios con schema y API REST")
        events, _on_assistant, _on_system, _ = _make_events()

        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )

        assert "CRUD" in result or "schema" in result.lower() or "REST" in result
        # Verify 2 subtasks executed
        assert mock_loop.call_count == 2


# ═══════════════════════════════════════════════════════════════════
#  Development workflow E2E
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_development_workflow_e2e():
    """E2E: development (full orchestration) pipeline con 2 subtareas secuenciales."""
    with (
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
        patch(
            "orchestration.workflows.orchestrator.get_global_workspaces",
            return_value=MagicMock(current="main"),
        ),
        patch(
            "orchestration.workflows.orchestrator.get_active_workflow",
            return_value="default",
        ),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value={
                "project": {},
                "agents": {},
                "tools": {},
                "type": "development",
            },
        ),
        patch("tools.orchestrator.ToolOrchestrator.reset_token_budget"),
        # Mock TaskAnalyzer via LLM
        patch(
            "orchestration.workflows.orchestrator.TaskAnalyzer.analyze_task",
            new_callable=AsyncMock,
        ) as mock_analyze,
        # Mock task decomposition
        patch(
            "orchestration.workflows.development.decompose_task",
            new_callable=AsyncMock,
        ) as mock_decompose,
        # Mock agent routing
        patch(
            "orchestration.workflows.orchestrator.agent_router.select_best_agent",
            new_callable=AsyncMock,
        ) as mock_router,
        # Mock supervisor correction
        patch(
            "orchestration.workflows.orchestrator.WorkflowSupervisor.review_and_correct",
            new_callable=AsyncMock,
        ) as mock_supervisor,
        # Mock subtask execution
        patch(
            "orchestration.workflows.orchestrator.execute_subtask_safe",
            new_callable=AsyncMock,
        ) as mock_subtask,
        # Mock result aggregation
        patch(
            "orchestration.workflows.orchestrator.ResultAggregator.aggregate_results",
            new_callable=AsyncMock,
        ) as mock_aggregate,
        # Mock diagram
        patch(
            "orchestration.workflows.orchestrator.update_live_diagram",
            new_callable=AsyncMock,
        ),
        # Mock scorecard
        patch(
            "orchestration.workflows.orchestrator.generate_scorecard",
            return_value={"subtasks": 2, "completadas": 2, "tokens": 1000, "tiempo": "2.5s"},
        ),
        # Mock finalizer
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            new_callable=AsyncMock,
        ),
    ):
        mock_analyze.return_value = {
            "primary_type": "feature_implementation",
            "requires_full_orchestration": True,
        }
        mock_decompose.return_value = ["Crear modelo User", "Implementar endpoints CRUD"]
        mock_router.return_value = "developer"
        mock_supervisor.return_value = ["developer", "developer"]
        mock_subtask.side_effect = [
            {
                "status": "completed",
                "result": "Modelo User creado con validaciones",
                "files_written": ["models.py"],
            },
            {
                "status": "completed",
                "result": "Endpoints CRUD implementados con tests",
                "files_written": ["api.py", "test_api.py"],
            },
        ]
        mock_aggregate.return_value = (
            "Feature completada: modelo User + API CRUD con tests. 3 archivos creados."
        )

        from orchestration.workflows.orchestrator import WorkflowOrchestrator

        ctx = _make_ctx(query="Crea un CRUD completo de usuarios con tests")
        events, _on_assistant, _on_system, _ = _make_events()

        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )

        assert "CRUD" in result or "Feature completada" in result
        # Verify pipeline stages were called
        mock_decompose.assert_awaited_once()
        mock_router.assert_awaited()
        mock_supervisor.assert_awaited_once()
        assert mock_subtask.call_count == 2
        mock_aggregate.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════
#  Collaborative workflow E2E — moderator with tools
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_collaborative_moderator_with_tools_e2e():
    """E2E: moderator with tools filtered by workflow_allowed_tools.

    Verifies that the moderator fix is exercised: when the moderator profile
    has tools and workflow_allowed_tools restricts them, the moderator runs
    with filtered tools via execute_agent_loop instead of unrestricted.
    """
    with (
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
        patch(
            "orchestration.workflows.orchestrator.get_global_workspaces",
            return_value=MagicMock(current="main"),
        ),
        patch(
            "orchestration.workflows.orchestrator.get_active_workflow",
            return_value="default",
        ),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value={
                "project": {},
                "agents": {},
                "tools": {"allowed": ["file_manager"]},  # restrict tools
                "type": "collaborative",
                "panel": ["developer", "analista"],
                "rounds": 1,
                "moderator": "moderador",
            },
        ),
        patch("tools.orchestrator.ToolOrchestrator.reset_token_budget"),
        patch("llm.models.call", new_callable=AsyncMock) as mock_llm,
        patch("tools.wrapper.safe_tool_call", new_callable=AsyncMock) as mock_tool,
        # Patch the moderator path: simulate a moderator that HAS tools
        patch(
            "orchestration.workflows.collaborative.agents_registry.get_profile",
            return_value={
                "name": "moderador",
                "tools": ["file_manager", "bash_manager"],  # moderator has tools
                "model_role": "reasoning",
                "temperature": 0.4,
            },
        ) as mock_profile,
        # Mock execute_agent_loop for moderator (imported locally in _ask_moderator)
        patch(
            "orchestration.loop.execute_agent_loop",
            new_callable=AsyncMock,
        ) as mock_mod_loop,
        # Mock finalizer + scorecard (imported locally in CollaborativeOrchestrator.run())
        patch(
            "orchestration.finalizer.finalize_workflow",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.utils.generate_scorecard",
            return_value={"subtasks": 1, "completadas": 1, "tokens": 300, "tiempo": "0.5s"},
        ),
    ):
        # Panel agents: just text, no tools
        round_dev = MagicMock()
        round_dev.choices = [MagicMock()]
        round_dev.choices[0].message = MagicMock(tool_calls=None)
        round_dev.choices[0].message.content = "Usemos microservicios"

        round_ana = MagicMock()
        round_ana.choices = [MagicMock()]
        round_ana.choices[0].message = MagicMock(tool_calls=None)
        round_ana.choices[0].message.content = "De acuerdo con microservicios"

        mock_llm.side_effect = [round_dev, round_ana]
        mock_tool.return_value = {"success": True, "output": "ok"}
        mock_mod_loop.return_value = {
            "status": "completed",
            "result": "Consenso: microservicios.",
            "actions_taken": 0,
            "iterations": 1,
            "files_written": [],
        }

        from orchestration.workflows.orchestrator import WorkflowOrchestrator

        ctx = _make_ctx(query="¿Microservicios o monolito?")
        events, _on_assistant, _on_system, _ = _make_events()

        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )

        assert "microservicios" in result.lower()
        # Moderator should have been called via execute_agent_loop with filtered tools
        mock_mod_loop.assert_awaited_once()
        # Verify the tools passed are filtered: only file_manager (bash_manager filtered out)
        call_kwargs = mock_mod_loop.call_args.kwargs
        assert call_kwargs["allowed_tools"] is not None
        assert "file_manager" in call_kwargs["allowed_tools"]
        assert "bash_manager" not in call_kwargs["allowed_tools"]
