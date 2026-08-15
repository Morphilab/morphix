"""Contrato normalizado por workflow (spec §3.3): todos emiten los campos del contrato."""

import time
from unittest.mock import AsyncMock, patch

import pytest

from orchestration.context import Session, WorkflowContext
from orchestration.emitter import WorkflowEmitter

CONTRACT_FIELDS = {
    "status",
    "current_agent",
    "subtask_list",
    "subtasks_total",
    "subtasks_completed",
    "files_written",
    "tokens_used",
    "elapsed_time",
    "phase",
    "iterations",
    "actions_taken",
}


def _stats_of(session) -> list[dict]:
    events = session.events
    return [c.args[0] for c in events.on_stats_update.await_args_list]


@pytest.mark.asyncio
async def test_development_emits_contract_on_phase_changes():
    """Development migrado al emitter: descomponer/ejecutar/final con contrato completo."""
    from orchestration.workflows.development import DevelopmentOrchestrator

    events = AsyncMock()
    ctx = WorkflowContext(
        query="crea una api",
        mode="orchestrate",
        workspace="main",
        conversation_history=[],
        is_follow_up=False,
    )
    session = Session(context=ctx, events=events)
    session.emitter = WorkflowEmitter(session)

    with (
        patch(
            "orchestration.workflows.development.decompose_task",
            new_callable=AsyncMock,
            return_value=["sub 1", "sub 2"],
        ),
        patch(
            "orchestration.workflows.development.update_live_diagram",
            new_callable=AsyncMock,
        ),
        patch(
            "orchestration.workflows.development.agent_router.select_best_agent",
            new_callable=AsyncMock,
            return_value="developer",
        ),
        patch(
            "orchestration.workflows.development.WorkflowSupervisor.review_and_correct",
            new_callable=AsyncMock,
            return_value=["developer", "analista"],
        ),
        patch(
            "orchestration.workflows.development.run_subtask_safe",
            new_callable=AsyncMock,
            return_value={"status": "completed", "result": "ok", "files_written": ["a.py"]},
        ),
        patch(
            "orchestration.workflows.development.ResultAggregator.aggregate_results",
            new_callable=AsyncMock,
            return_value="resumen final",
        ),
        patch(
            "orchestration.workflows.development.generate_scorecard",
            return_value={"tokens": 42, "score": 0.9, "files": ["a.py"]},
        ),
        patch(
            "orchestration.workflows.development.finalize_workflow",
            new_callable=AsyncMock,
        ),
    ):
        await DevelopmentOrchestrator.run(
            query="crea una api",
            conversation_history=[],
            task_analysis={"primary_type": "development", "requires_full_orchestration": True},
            ctx=ctx,
            events=events,
            project_root=None,  # evita verificación global + escaneo de disco
            workspace="main",
            allowed_agents=None,
            workflow_allowed_tools=None,
            start_time=time.monotonic(),
            emitter=session.emitter,
        )

    stats = _stats_of(session)
    assert stats, "development no emitió stats"
    for data in stats:
        assert CONTRACT_FIELDS.issubset(
            data.keys()
        ), f"faltan campos: {CONTRACT_FIELDS - data.keys()}"
    # phase presente en el avance
    assert any(data.get("phase") for data in stats)
    # subtask_list con los 2 subtareas en algún punto
    assert any(len(data.get("subtask_list", [])) == 2 for data in stats)
    # tokens/elapsed reales al final (no "—")
    final = stats[-1]
    assert isinstance(final["tokens_used"], int)
    assert isinstance(final["elapsed_time"], str) and final["elapsed_time"].endswith("s")


@pytest.mark.asyncio
async def test_coordinated_emits_contract_and_phase_groups():
    """Coordinated emite contrato completo con phases design/implement/verify."""
    from unittest.mock import MagicMock

    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    events = AsyncMock()
    ctx = WorkflowContext(
        query="coordina el módulo x",
        mode="orchestrate",
        workspace="main",
        project_root=None,
        conversation_history=[],
        enc=MagicMock(),  # generate_scorecard real (orchestrator.py:739) requiere enc
    )
    session = Session(context=ctx, events=events)
    session.emitter = WorkflowEmitter(session)

    template = {
        "project": {},
        "agents": {},
        "tools": {},
        "type": "coordinated",
    }
    decomposition = {
        "phases": [
            {"phase": "design", "subtasks": ["diseña la api"]},
            {"phase": "implement", "subtasks": ["implementa la api"]},
            {"phase": "verify", "subtasks": ["verifica la api"]},
        ]
    }
    phase_results = {
        "design_0": {"status": "completed", "result": "ok", "files_written": ["a.py"]},
        "implement_0": {"status": "failed", "result": "error", "files_written": []},
        "verify_0": {"status": "completed", "result": "ok", "files_written": []},
    }

    with (
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value=template,
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
            "orchestration.workflows.orchestrator._parse_direct_tool_command",
            return_value=None,
        ),
        patch(
            "orchestration.decomposer.decompose_task_with_phases",
            new_callable=AsyncMock,
            return_value=decomposition,
        ),
        patch(
            "orchestration.workflows.coordinated.MultiAgentCoordinator.assign_agents",
            new_callable=AsyncMock,
            return_value={"design_0": "developer"},
        ),
        patch(
            "orchestration.workflows.coordinated.MultiAgentCoordinator.execute_dag",
            new_callable=AsyncMock,
            return_value=phase_results,
        ),
        patch(
            "orchestration.workflows.coordinated.MultiAgentCoordinator.aggregate_with_confidence",
            new_callable=AsyncMock,
            return_value="resumen coordinated",
        ),
        patch(
            "orchestration.finalizer.finalize_workflow",
            new_callable=AsyncMock,
        ),
        patch(
            "tools.orchestrator.ToolOrchestrator.reset_token_budget",
        ),
    ):
        await WorkflowOrchestrator.run_full_workflow(session=session)

    stats = _stats_of(session)
    assert stats
    # Contrato completo en TODA emisión
    for data in stats:
        assert CONTRACT_FIELDS.issubset(data.keys())
    # phase de ejecución del DAG presente
    assert any(data.get("phase") == "design" for data in stats)
    # subtask_list poblada en el avance del DAG
    assert any(len(data.get("subtask_list", [])) >= 3 for data in stats)
    # status real por subtarea en el final (no heurístico posicional)
    final_list = stats[-1].get("subtask_list", [])
    by_name = {item["name"]: item["status"] for item in final_list}
    assert by_name.get("implementa la api") == "failed"
    assert by_name.get("verifica la api") == "completed"


@pytest.mark.asyncio
async def test_collaborative_emits_contract_with_rounds():
    """Collaborative emite contrato completo: subtask_list = rondas, tokens/elapsed ya no son '—'."""
    from unittest.mock import MagicMock

    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    events = AsyncMock()
    ctx = WorkflowContext(
        query="debate: ¿monolito o microservicios?",
        mode="orchestrate",
        workspace="main",
        conversation_history=[],
    )
    session = Session(context=ctx, events=events)
    session.emitter = WorkflowEmitter(session)

    template = {
        "project": {},
        "agents": {},
        "tools": {},
        "type": "collaborative",
        "panel": ["developer", "analista"],
        "rounds": 2,
        "moderator": "moderador",
    }

    with (
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
        patch(
            "orchestration.workflows.orchestrator.load_workflow_template",
            return_value=template,
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
            "orchestration.workflows.orchestrator._parse_direct_tool_command",
            return_value=None,
        ),
        patch(
            "orchestration.workflows.collaborative.CollaborativeOrchestrator._ask_agent",
            new_callable=AsyncMock,
            return_value="opinión del agente",
        ),
        patch(
            "orchestration.workflows.collaborative.CollaborativeOrchestrator._ask_moderator",
            new_callable=AsyncMock,
            return_value="consenso final",
        ),
        patch(
            "orchestration.workflows.collaborative.CollaborativeOrchestrator._build_debate_summary",
            return_value="resumen",
        ),
        patch(
            "orchestration.finalizer.finalize_workflow",
            new_callable=AsyncMock,
        ),
        patch(
            "tools.orchestrator.ToolOrchestrator.reset_token_budget",
        ),
    ):
        await WorkflowOrchestrator.run_full_workflow(session=session)

    stats = _stats_of(session)
    assert stats
    for data in stats:
        assert CONTRACT_FIELDS.issubset(data.keys())
    # phase de ronda presente
    assert any(data.get("phase") == "Ronda 1" for data in stats)
    # subtask_list = rondas
    assert any(len(data.get("subtask_list", [])) == 2 for data in stats)
    # tokens_used y elapsed_time reales (no "—")
    final = stats[-1]
    assert isinstance(final["tokens_used"], int)
    assert isinstance(final["elapsed_time"], str) and final["elapsed_time"].endswith("s")


@pytest.mark.asyncio
async def test_default_init_and_simple_emit_contract():
    """La ruta default (init/analizando) y simple emiten el contrato completo."""
    from unittest.mock import MagicMock

    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    events = AsyncMock()
    ctx = WorkflowContext(query="hola", mode="chat", workspace="main", conversation_history=[])
    session = Session(context=ctx, events=events)
    session.emitter = WorkflowEmitter(session)

    with (
        patch("core.security.undercover_mode.undercover.check_query", return_value=True),
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
            "orchestration.workflows.orchestrator._parse_direct_tool_command",
            return_value=None,
        ),
        patch(
            "orchestration.workflows.orchestrator.TaskAnalyzer.analyze_task",
            new_callable=AsyncMock,
            return_value={"primary_type": "conversational", "requires_full_orchestration": False},
        ),
        patch(
            "agents.service.AgentsService.execute_agent",
            new_callable=AsyncMock,
            return_value="hola",
        ),
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            new_callable=AsyncMock,
        ),
        patch(
            "tools.orchestrator.ToolOrchestrator.reset_token_budget",
        ),
    ):
        await WorkflowOrchestrator.run_full_workflow(session=session)

    stats = _stats_of(session)
    assert stats
    for data in stats:
        assert CONTRACT_FIELDS.issubset(data.keys())
