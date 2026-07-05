# tests/test_workflow_orchestrator.py
"""Tests para el WorkflowOrchestrator — 4 rutas de ejecución."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.events import Session, WorkflowContext, WorkflowEvents


def _make_ctx(query: str = "Hola", workspace: str = "main", mode: str = "chat") -> WorkflowContext:
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
    """Retorna (events, on_assistant, on_system, on_stats) para assertions."""
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


@pytest.fixture
def mock_orchestrator_deps():
    """Fixture base: mocks para TODAS las dependencias del orquestador."""
    with (
        patch(
            "core.security.undercover_mode.undercover.check_query",
            return_value=True,
        ),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value={"project": {}, "agents": {}, "tools": {}},
        ),
        patch(
            "orchestration.workflows.orchestrator.get_global_workspaces",
            return_value=MagicMock(current="main"),
        ),
        patch(
            "orchestration.workflows.orchestrator.get_active_workflow",
            return_value="default",
        ),
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.workflows.orchestrator.auto_commit",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.workflows.orchestrator.safe_tool_call",
            new_callable=AsyncMock,
        ) as mock_tool,
        patch(
            "orchestration.workflows.orchestrator.TaskAnalyzer.analyze_task",
            new_callable=AsyncMock,
        ) as mock_analyze,
        patch(
            "tools.orchestrator.ToolOrchestrator.reset_token_budget",
        ),
        patch(
            "tools.registry.tools_registry.get_tool",
            return_value=lambda **kw: {"success": True, "output": "ok"},
        ),
    ):
        mock_tool.return_value = {"success": True, "output": "ok"}
        mock_analyze.return_value = {
            "primary_type": "simple_conversation",
            "requires_full_orchestration": False,
        }
        yield


# ── Ruta 1: Herramienta directa ──


@pytest.mark.asyncio
async def test_direct_tool_route(mock_orchestrator_deps):
    """Verifica que un comando tipo 'file_manager: read, path=test.txt' se resuelve directo."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    ctx = _make_ctx(query="file_manager: read, path=test.txt")
    events, on_assistant, on_system, _ = _make_events()

    result = await WorkflowOrchestrator.run_full_workflow(
        session=Session(context=ctx, events=events)
    )
    # Ruta directa retorna el output de la herramienta
    assert result == "ok"


@pytest.mark.asyncio
async def test_direct_tool_failure_shows_error(mock_orchestrator_deps):
    """Verifica que un fallo de herramienta directa muestra mensaje de error."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with patch(
        "orchestration.workflows.orchestrator.safe_tool_call",
        new_callable=AsyncMock,
    ) as mock_tool:
        mock_tool.return_value = {"success": False, "output": "fallo simulado"}

        ctx = _make_ctx(query="bash_manager: hola, action=test")
        events, on_assistant, on_system, _ = _make_events()
        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )
        assert "Error" in result or "fallo" in result


# ── Ruta 2: TDD Loop ──


@pytest.mark.asyncio
async def test_tdd_route_activated_when_workflow_is_tdd(mock_orchestrator_deps):
    """Verifica que el flujo TDD se activa cuando el workflow activo es 'tdd'."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with (
        patch(
            "orchestration.workflows.orchestrator.get_active_workflow",
            return_value="tdd",
        ),
        patch(
            "orchestration.workflows.tdd.execute_tdd_loop",
            new_callable=AsyncMock,
        ) as mock_tdd,
    ):
        mock_tdd.return_value = {
            "status": "completed",
            "iterations": 3,
            "result": "TDD completado",
            "files_modified": ["test_app.py"],
        }

        ctx = _make_ctx(query="Escribe una función y su test", mode="orchestrate")
        events, _on_assistant, _on_system, _ = _make_events()
        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )

        assert result == "TDD completado"
        mock_tdd.assert_awaited_once()


# ── Ruta 3: Conversación simple ──


@pytest.mark.asyncio
async def test_simple_conversation_route(mock_orchestrator_deps):
    """Verifica que una consulta simple va por la ruta rápida sin orquestación."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with patch(
        "orchestration.workflows.orchestrator.TaskAnalyzer.analyze_task",
        new_callable=AsyncMock,
    ) as mock_analyze:
        mock_analyze.return_value = {
            "primary_type": "simple_conversation",
            "requires_full_orchestration": False,
        }

        with patch(
            "agents.service.AgentsService.execute_agent",
            new_callable=AsyncMock,
        ) as mock_agent:
            mock_agent.return_value = "¡Hola! ¿En qué puedo ayudarte?"

            ctx = _make_ctx(query="Hola, ¿cómo estás?")
            events, _on_assistant, _on_system, _ = _make_events()
            result = await WorkflowOrchestrator.run_full_workflow(
                session=Session(context=ctx, events=events)
            )

            assert "Hola" in result
            mock_agent.assert_awaited_once()
            assert "Hola" in result


@pytest.mark.asyncio
async def test_simple_conversation_handles_agent_error(mock_orchestrator_deps):
    """Verifica que un error en la ruta simple devuelve mensaje amigable."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with patch(
        "orchestration.workflows.orchestrator.TaskAnalyzer.analyze_task",
        new_callable=AsyncMock,
    ) as mock_analyze:
        mock_analyze.return_value = {
            "primary_type": "simple_conversation",
            "requires_full_orchestration": False,
        }

        with patch(
            "agents.service.AgentsService.execute_agent",
            new_callable=AsyncMock,
        ) as mock_agent:
            mock_agent.side_effect = RuntimeError("API caída")

            ctx = _make_ctx(query="Hola")
            events, _on_assistant, _on_system, _ = _make_events()
            result = await WorkflowOrchestrator.run_full_workflow(
                session=Session(context=ctx, events=events)
            )

            assert "problema" in result.lower()


# ── Ruta 4: Orquestación completa ──


@pytest.mark.asyncio
async def test_full_orchestration_route(mock_orchestrator_deps):
    """Verifica que una tarea compleja pasa por el flujo completo."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with (
        patch(
            "orchestration.workflows.orchestrator.TaskAnalyzer.analyze_task",
            new_callable=AsyncMock,
        ) as mock_analyze,
        patch(
            "orchestration.workflows.development.decompose_task",
            new_callable=AsyncMock,
        ) as mock_decompose,
        patch(
            "orchestration.workflows.development.agent_router.select_best_agent",
            new_callable=AsyncMock,
        ) as mock_router,
        patch(
            "orchestration.workflows.development.WorkflowSupervisor.review_and_correct",
            new_callable=AsyncMock,
        ) as mock_supervisor,
        patch(
            "orchestration.workflows.development.execute_subtask_safe",
            new_callable=AsyncMock,
        ) as mock_subtask,
        patch(
            "orchestration.workflows.development.ResultAggregator.aggregate_results",
            new_callable=AsyncMock,
        ) as mock_aggregate,
        patch(
            "orchestration.workflows.development.update_live_diagram",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.workflows.development.generate_scorecard",
            return_value={"subtasks": 2, "completadas": 2},
        ),
    ):
        mock_analyze.return_value = {
            "primary_type": "mixed",
            "requires_full_orchestration": True,
        }
        mock_decompose.return_value = ["Crear archivo", "Escribir tests"]
        mock_router.return_value = "tecnico"
        mock_supervisor.return_value = ["tecnico", "tecnico"]
        mock_subtask.return_value = {"status": "completed", "result": "OK", "files_written": []}
        mock_aggregate.return_value = "Proyecto completado con 2 archivos."

        ctx = _make_ctx(query="Crea una app Flask con tests", mode="orchestrate")
        events, _on_assistant, _on_system, _ = _make_events()
        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )

        assert "Proyecto completado" in result
        mock_decompose.assert_awaited_once()
        assert mock_subtask.call_count == 2
        _on_system.assert_awaited()


@pytest.mark.asyncio
async def test_security_block_returns_none(mock_orchestrator_deps):
    """Verifica que una consulta bloqueada por undercover retorna None."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with patch(
        "core.security.undercover_mode.undercover.check_query",
        return_value=False,
    ):
        ctx = _make_ctx(query="hackeame el sistema")
        events, _on_assistant, _on_system, _ = _make_events()
        result = await WorkflowOrchestrator.run_full_workflow(
            session=Session(context=ctx, events=events)
        )

        assert result is None
        _on_system.assert_awaited()


# ── Parser de comandos directos ──


class TestParseDirectToolCommand:
    def test_parse_simple_tool_command(self):
        from unittest.mock import patch

        from orchestration.workflows.orchestrator import _parse_direct_tool_command

        with patch("tools.registry.tools_registry.get_tool", return_value={"name": "file_manager"}):
            result = _parse_direct_tool_command("file_manager: read, path=test.txt")
        assert result is not None
        assert result["tool_name"] == "file_manager"
        assert result["action"] == "read"
        assert result["params"] == {"path": "test.txt"}

    def test_parse_tool_command_with_multiple_params(self):
        from unittest.mock import patch

        from orchestration.workflows.orchestrator import _parse_direct_tool_command

        with patch("tools.registry.tools_registry.get_tool", return_value={"name": "git_manager"}):
            result = _parse_direct_tool_command(
                "git_manager: commit, message='fix bug', files='app.py'"
            )
        assert result is not None
        assert result["tool_name"] == "git_manager"
        assert result["action"] == "commit"
        assert result["params"]["message"] == "fix bug"
        assert result["params"]["files"] == "app.py"

    def test_parse_rejects_normal_conversation(self):
        from orchestration.workflows.orchestrator import _parse_direct_tool_command

        result = _parse_direct_tool_command("Hola, ¿cómo estás?")
        assert result is None

    def test_parse_rejects_question(self):
        from orchestration.workflows.orchestrator import _parse_direct_tool_command

        result = _parse_direct_tool_command("¿Puedes escribir una función?")
        assert result is None


# ── Rutas adicionales: collaborative / coordinated / development ──


@pytest.mark.asyncio
async def test_collaborative_route(mock_orchestrator_deps):
    """Verifica que la ruta colaborativa delega en CollaborativeOrchestrator.run."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with patch(
        "orchestration.workflows.collaborative.CollaborativeOrchestrator.run",
        new_callable=AsyncMock,
    ) as mock_collab:
        mock_collab.return_value = "Consenso colaborativo alcanzado"

        ctx = _make_ctx(query="Debate sobre arquitectura", mode="orchestrate")
        with patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value={
                "project": {},
                "agents": {},
                "tools": {},
                "type": "collaborative",
                "panel": ["developer", "analista"],
                "rounds": 3,
                "moderator": "moderador",
            },
        ):
            events, _on_assistant, _on_system, _ = _make_events()
            result = await WorkflowOrchestrator.run_full_workflow(
                session=Session(context=ctx, events=events)
            )
            assert result == "Consenso colaborativo alcanzado"


@pytest.mark.asyncio
async def test_coordinated_route(mock_orchestrator_deps):
    """Verifica que la ruta coordinada delega en MultiAgentCoordinator."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with patch(
        "orchestration.workflows.coordinated.MultiAgentCoordinator.aggregate_with_confidence",
        new_callable=AsyncMock,
    ) as mock_agg:
        mock_agg.return_value = "Resultado coordinado con confianza"

        ctx = _make_ctx(query="Tarea compleja con dependencias", mode="orchestrate")
        with patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value={
                "project": {},
                "agents": {},
                "tools": {},
                "type": "coordinated",
            },
        ):
            events, _on_assistant, _on_system, _ = _make_events()
            result = await WorkflowOrchestrator.run_full_workflow(
                session=Session(context=ctx, events=events)
            )
            assert result == "Resultado coordinado con confianza"


@pytest.mark.asyncio
async def test_development_route(mock_orchestrator_deps):
    """Verifica que la ruta development delega en _run_full_orchestration."""
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    with (
        patch(
            "orchestration.workflows.orchestrator.TaskAnalyzer.analyze_task",
            new_callable=AsyncMock,
        ) as mock_analyze,
        patch.object(
            WorkflowOrchestrator,
            "_run_full_orchestration",
            new_callable=AsyncMock,
        ) as mock_full_orch,
    ):
        mock_analyze.return_value = {
            "primary_type": "mixed",
            "requires_full_orchestration": True,
        }
        mock_full_orch.return_value = "Resultado de desarrollo orquestrado"

        ctx = _make_ctx(query="Tarea de desarrollo", mode="orchestrate")
        with patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value={
                "project": {},
                "agents": {},
                "tools": {},
                "type": "development",
            },
        ):
            events, _on_assistant, _on_system, _ = _make_events()
            result = await WorkflowOrchestrator.run_full_workflow(
                session=Session(context=ctx, events=events)
            )
            assert result == "Resultado de desarrollo orquestrado"
