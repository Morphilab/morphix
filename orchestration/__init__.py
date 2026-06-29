"""Orchestration — workflow orchestration layer."""

from core.workflow_state import get_active_workflow, switch_workspace
from orchestration.aggregator import ResultAggregator
from orchestration.analyzer import TaskAnalyzer
from orchestration.context import (
    Session,
    WorkflowContext,
    WorkflowEvents,
    _emit,
    emit_agent,
    emit_assistant,
    emit_diagram,
    emit_refresh,
    emit_stats,
    emit_stream_chunk,
    emit_system,
    emit_user,
)
from orchestration.decomposer import decompose_task
from orchestration.diagram import update_live_diagram
from orchestration.finalizer import finalize_workflow
from orchestration.loader import list_workflows, load_workflow_template
from orchestration.loop import execute_agent_loop
from orchestration.router import AgentRouter, agent_router
from orchestration.status import render as render_status
from orchestration.status import save_status_snapshot
from orchestration.supervisor import WorkflowSupervisor
from orchestration.utils import clean_generated_code, generate_scorecard
from orchestration.workflows.orchestrator import WorkflowOrchestrator

__all__ = [
    "WorkflowOrchestrator",
    "execute_agent_loop",
    "decompose_task",
    "update_live_diagram",
    "finalize_workflow",
    "ResultAggregator",
    "TaskAnalyzer",
    "WorkflowSupervisor",
    "AgentRouter",
    "agent_router",
    "load_workflow_template",
    "list_workflows",
    "clean_generated_code",
    "generate_scorecard",
    "render_status",
    "save_status_snapshot",
    "WorkflowContext",
    "WorkflowEvents",
    "Session",
    "get_active_workflow",
    "switch_workspace",
    "_emit",
    "emit_agent",
    "emit_assistant",
    "emit_diagram",
    "emit_refresh",
    "emit_stats",
    "emit_stream_chunk",
    "emit_system",
    "emit_user",
]
