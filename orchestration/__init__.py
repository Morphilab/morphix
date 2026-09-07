"""Orchestration — workflow orchestration layer.

Retiro del legacy: este paquete solo expone los componentes
activos (DSL, bots, loop, finalización). Los módulos del orquestador legacy
(supervisor, analyzer, router, executor, workflows/tdd|collaborative|
coordinated|development|pipeline) fueron eliminados — ver git history.
"""

from orchestration.aggregator import ResultAggregator
from orchestration.context import (
    Session,
    WorkflowContext,
    WorkflowEvents,
    emit_agent,
    emit_assistant,
    emit_refresh,
    emit_stats,
    emit_stream_chunk,
    emit_system,
    emit_user,
)
from orchestration.decomposer import decompose_task
from orchestration.finalizer import finalize_workflow
from orchestration.loader import list_workflows, load_workflow_document
from orchestration.loop import execute_agent_loop
from orchestration.status import render as render_status
from orchestration.status import save_status_snapshot
from orchestration.utils import clean_generated_code, generate_scorecard
from orchestration.workflows.orchestrator import WorkflowOrchestrator

__all__ = [
    "WorkflowOrchestrator",
    "execute_agent_loop",
    "decompose_task",
    "finalize_workflow",
    "ResultAggregator",
    "generate_scorecard",
    "clean_generated_code",
    "list_workflows",
    "load_workflow_document",
    "render_status",
    "save_status_snapshot",
    "Session",
    "WorkflowContext",
    "WorkflowEvents",
    "emit_agent",
    "emit_assistant",
    "emit_refresh",
    "emit_stats",
    "emit_stream_chunk",
    "emit_system",
    "emit_user",
]
