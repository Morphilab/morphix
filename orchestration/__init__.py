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
    emit_stats,
    emit_system,
)
from orchestration.decomposer import decompose_task
from orchestration.finalizer import finalize_workflow
from orchestration.loader import list_workflows, load_workflow_document
from orchestration.loop import execute_agent_loop
from orchestration.workflows.orchestrator import WorkflowOrchestrator

__all__ = [
    "WorkflowOrchestrator",
    "execute_agent_loop",
    "decompose_task",
    "finalize_workflow",
    "ResultAggregator",
    "list_workflows",
    "load_workflow_document",
    "Session",
    "WorkflowContext",
    "WorkflowEvents",
    "emit_stats",
    "emit_system",
]
