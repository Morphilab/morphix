"""
Workflow Orchestrator — dispatches tasks through 4 execution routes.

Receives a Session (WorkflowContext + WorkflowEvents) and delegates routing
to _dispatch_route. All UI interaction flows through the events bridge,
keeping the orchestrator framework-agnostic.
"""

import asyncio
import datetime
import json
import logging
import re
import time

import networkx as nx

from agents.base import _execute_specialized_agent
from core.config import settings
from core.context_manager import ContextManager
from core.database import get_async_session
from core.git_operations import auto_commit
from core.models import PausedSession
from core.path_resolver import paths
from core.utils import clean_llm_response
from core.workflow_state import get_active_workflow
from core.workspaces import get_global_workspaces
from llm import parse_plan_json
from orchestration.analyzer import TaskAnalyzer
from orchestration.events import Session, WorkflowContext, WorkflowEvents
from orchestration.executor.plan import _execute_plan_actions
from orchestration.executor.subtask import execute_subtask_safe
from orchestration.executor.verify import _extract_and_validate_actions
from orchestration.finalizer import finalize_workflow
from orchestration.loader import load_workflow_template
from orchestration.utils import generate_scorecard
from orchestration.workflows.collaborative import CollaborativeOrchestrator
from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)

SUBTASK_TIMEOUT = 300  # seconds — per-subtask timeout for development workflows

# ── Safe emission helpers (canonical in events.py) ──
from orchestration.events import emit_stats, emit_system


def _collect_files_written(results: dict[int, dict] | dict[str, dict]) -> list[str]:
    """Collect unique files_written from a dict of subtask results."""
    files: list[str] = []
    for r in results.values():
        for f in r.get("files_written", []):
            if isinstance(f, str) and f not in files:
                files.append(f)
    return files


# ─── ╔══════════════════════════════════════════════════════════════╗ ───
# ─── ║         DIRECT TOOL CALL DETECTION                        ║ ───
# ─── ╚══════════════════════════════════════════════════════════════╝ ───
TOOL_CALL_PATTERN = re.compile(r"^\s*(.+):\s*([\w_]+)\s*,?\s*(.*)$", re.IGNORECASE)

_VALID_DECOMPOSITIONS = {"flat": "development", "dag": "coordinated", "panel": "collaborative"}
_VALID_EXECUTIONS = {
    "sequential": "development",
    "parallel_levels": "coordinated",
    "parallel_rounds": "collaborative",
}


def _validate_template_consistency(template: dict) -> None:
    """Log warnings if decomposition/execution fields mismatch template type."""
    ttype = template.get("type", "")
    decomp = template.get("decomposition", "")
    exec_mode = template.get("execution", "")

    if decomp and decomp in _VALID_DECOMPOSITIONS and _VALID_DECOMPOSITIONS[decomp] != ttype:
        logger.warning(
            f"Template '{ttype}': decomposition '{decomp}' expected for "
            f"'{_VALID_DECOMPOSITIONS[decomp]}', not '{ttype}'"
        )
    if exec_mode and exec_mode in _VALID_EXECUTIONS and _VALID_EXECUTIONS[exec_mode] != ttype:
        logger.warning(
            f"Template '{ttype}': execution '{exec_mode}' expected for "
            f"'{_VALID_EXECUTIONS[exec_mode]}', not '{ttype}'"
        )


def _parse_direct_tool_command(query: str) -> dict | None:
    """Devuelve {tool_name, action, params} si la consulta es un comando de herramienta.
    Validates that the parsed tool_name exists in the tools registry to avoid
    false positives on natural language (e.g., 'navega y analiza : URL')."""
    match = TOOL_CALL_PATTERN.match(query.strip())
    if not match:
        return None
    tool_name = match.group(1).lower()
    action = match.group(2).lower()
    params_str = match.group(3)

    # Validate tool exists in registry (prevents false positives on natural language)
    from tools.registry import tools_registry

    if tools_registry.get_tool(tool_name) is None:
        return None

    params = {}
    if params_str:
        for part in params_str.split(","):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                params[k.strip()] = v.strip().strip("'\"")
    return {"tool_name": tool_name, "action": action, "params": params}


class WorkflowOrchestrator:

    @staticmethod
    async def _run_direct_tool(
        direct_tool: dict,
        query: str,
        start_time: float,
        conversation_history: list,
        events: WorkflowEvents,
        conversation_id: int | None = None,
    ) -> str:
        workspaces = get_global_workspaces()
        params = direct_tool["params"]
        params["workspace"] = workspaces.current
        params["action"] = direct_tool["action"]

        tool_result = await safe_tool_call(
            tool_name=direct_tool["tool_name"],
            parameters=params,
            role="agent",
        )

        if tool_result.get("success"):
            raw_output = tool_result.get("output", str(tool_result))
            if isinstance(raw_output, dict) and "output" in raw_output:
                output = raw_output["output"]
            elif isinstance(raw_output, str):
                output = raw_output
            else:
                output = str(raw_output)
        else:
            output = f"❌ Error en herramienta: {tool_result.get('output', 'desconocido')}"

        await emit_system(
            events,
            f"🛠️ Herramienta ejecutada: {direct_tool['tool_name']} {direct_tool['action']}",
        )

        elapsed = round(time.monotonic() - start_time, 1)
        await emit_stats(
            events,
            {
                "subtasks_total": 0,
                "subtasks_completed": 0,
                "tokens_used": 0,
                "elapsed_time": f"{elapsed}s",
                "current_agent": "—",
                "status": "Completado (tool directa)",
            },
        )
        await finalize_workflow(
            query=query,
            final_output=output,
            conversation_history=conversation_history,
            conversation_id=conversation_id,
            scorecard={"subtasks": 0, "tokens": 0, "tiempo": f"{elapsed}s"},
            subtasks_list=[f"Tool directa: {direct_tool['tool_name']}"],
            task_analysis={"primary_type": "tool_direct", "requires_full_orchestration": False},
            G=None,
            events=events,
        )
        return output

    @staticmethod
    async def _run_tdd_loop(
        query: str,
        project_root: str | None,
        workspace: str,
        workflow_allowed_tools: list | None,
        conversation_history: list,
        start_time: float,
        events: WorkflowEvents,
        conversation_id: int | None = None,
    ) -> str:
        from orchestration.workflows.tdd import execute_tdd_loop

        logger.info("🧪 Modo TDD activado — ejecutando ciclo TDD autónomo")
        await emit_system(events, "🧪 Modo TDD: ejecutando tests y corrigiendo...")
        await emit_stats(events, {"status": "TDD Loop", "current_agent": "TDD Agent"})

        tdd_result = await execute_tdd_loop(
            task=query,
            workspace=workspace,
            project_root=project_root,
            allowed_tools=workflow_allowed_tools,
            agent_type=settings.default_agent,
            conversation_history=conversation_history,
        )

        final_content = tdd_result["result"]
        elapsed = round(time.monotonic() - start_time, 1)

        scorecard = {
            "subtasks": tdd_result["iterations"],
            "completadas": 1 if tdd_result["status"] == "completed" else 0,
            "recuperadas": 0,
            "fallidas": 0 if tdd_result["status"] == "completed" else 1,
            "tokens": len(final_content) // 4,
            "tiempo": f"{elapsed}s",
            "calidad": "Alta" if tdd_result["status"] == "completed" else "Requiere revisión",
            "tipo_tarea": "tdd",
            "complejidad": "media",
        }

        await emit_stats(
            events,
            {
                "subtasks_total": tdd_result["iterations"],
                "subtasks_completed": 1,
                "tokens_used": scorecard["tokens"],
                "elapsed_time": scorecard["tiempo"],
                "current_agent": "—",
                "status": "Completado" if tdd_result["status"] == "completed" else "Fallido",
                "files_written": tdd_result.get("files_modified", []),
            },
        )

        await finalize_workflow(
            query=query,
            final_output=final_content,
            conversation_history=conversation_history,
            conversation_id=conversation_id,
            scorecard=scorecard,
            subtasks_list=[f"TDD iteración {i}" for i in range(1, tdd_result["iterations"] + 1)],
            task_analysis={"primary_type": "tdd", "requirements": query},
            G=None,
            events=events,
            project_root=project_root,
            workspace=workspace,
            files_written=tdd_result.get("files_modified", []),
        )
        return final_content

    @staticmethod
    async def run_full_workflow(
        session: Session,
    ) -> str | None:
        """Execute the full workflow pipeline.

        Receives a Session with unified context and events. Delegates routing
        to _dispatch_route to keep cyclomatic complexity low.

        Returns:
            str: final response, or None if the request was blocked.
        """
        ctx = session.context
        events = session.events
        query = ctx.query
        start_time = time.monotonic()

        # ── Checks de seguridad ──
        from core.security.undercover_mode import undercover

        if not await undercover.check_query(query):
            await emit_system(events, "❌ Solicitud bloqueada por razones de seguridad.")
            return None

        # ── Tool orchestrator setup ──
        from tools.orchestrator import ToolOrchestrator

        ToolOrchestrator.reset_token_budget()
        ToolOrchestrator.on_approval_required = events.on_approval_required

        try:
            # ── Ruta directa: comando de herramienta (fast path) ──
            direct_tool = _parse_direct_tool_command(query)
            if direct_tool:
                return await WorkflowOrchestrator._run_direct_tool(
                    direct_tool,
                    query,
                    start_time,
                    ctx.conversation_history,
                    events,
                    ctx.conversation_id,
                )

            # ── Dispatch according to active template / workflow ──
            return await WorkflowOrchestrator._dispatch_route(
                session=session,
                query=query,
                ctx=ctx,
                events=events,
                start_time=start_time,
            )
        finally:
            ToolOrchestrator.on_approval_required = None

    @staticmethod
    async def _dispatch_route(
        session: Session,
        query: str,
        ctx: WorkflowContext,
        events: WorkflowEvents,
        start_time: float,
    ) -> str:
        """Evaluate the template and active workflow to choose the execution route.

        Orden de precedencia:
        1. TDD loop (cuando active_wf == 'tdd')
        2. Collaborative (template.type == 'collaborative')
        3. Coordinated (template.type == 'coordinated')
        4. Development (template.type == 'development')
        5. Default: analiza la tarea y decide entre conversación simple u orquestación completa.
        """
        conversation_history = ctx.conversation_history
        conversation_id = ctx.conversation_id

        # ── Resolver workspace + plantilla ──
        workspaces = get_global_workspaces()
        template = load_workflow_template(workspaces.current, get_active_workflow())
        project_root = template.get("project", {}).get("root")
        if project_root:
            ctx.project_root = project_root
        elif not ctx.project_root:
            ctx.project_root = "."

        active_wf = get_active_workflow()
        logger.info("📌 Workflow activo: %s (workspace: %s)", active_wf, workspaces.current)

        # ── Agentes y herramientas permitidos ──
        allowed_agents = template.get("agents", {}).get("allowed")
        if allowed_agents is not None and not isinstance(allowed_agents, list):
            allowed_agents = None
        workflow_allowed_tools = template.get("tools", {}).get("allowed", None)
        if workflow_allowed_tools is not None:
            ctx.allowed_tools = workflow_allowed_tools
        elif template.get("type") == "collaborative":
            logger.warning(
                "⚠️ Collaborative template sin 'tools.allowed'. "
                "Los agentes del panel usarán todas sus herramientas registradas."
            )

        _validate_template_consistency(template)

        # ── Ruta 1: TDD ──
        if active_wf == "tdd":
            return await WorkflowOrchestrator._run_tdd_loop(
                query,
                ctx.project_root or project_root,
                workspaces.current,
                workflow_allowed_tools,
                conversation_history,
                start_time,
                events,
                conversation_id,
            )

        # ── Ruta 2: Colaborativa ──
        if template.get("type") == "collaborative":
            return await CollaborativeOrchestrator.run(
                query=query,
                template=template,
                events=events,
                history=conversation_history,
                project_root=ctx.project_root,
                workspace=workspaces.current,
                force_agent=ctx.force_agent,
                workflow_allowed_tools=workflow_allowed_tools,
                start_time=start_time,
                cancelled=lambda: ctx.cancelled,
            )

        # ── Ruta 3: Coordinada ──
        if template.get("type") == "coordinated":
            return await WorkflowOrchestrator._run_coordinated(
                query=query,
                ctx=ctx,
                events=events,
                template=template,
                allowed_agents=allowed_agents,
                workflow_allowed_tools=workflow_allowed_tools,
                conversation_history=conversation_history,
                workspaces=workspaces,
                project_root=ctx.project_root,
                start_time=start_time,
                session=session,
            )

        # ── Route 4: Development ──
        if template.get("type") == "development":
            task_analysis = await TaskAnalyzer.analyze_task(query, is_follow_up=ctx.is_follow_up)
            if not task_analysis.get("requires_full_orchestration", True):
                await emit_stats(events, {"status": "Simple chat"})
                return await WorkflowOrchestrator._run_simple_conversation(
                    query,
                    conversation_history,
                    task_analysis,
                    template,
                    allowed_agents,
                    start_time,
                    events,
                    conversation_id,
                )
            return await WorkflowOrchestrator._run_full_orchestration(
                query,
                conversation_history,
                task_analysis,
                ctx,
                events,
                project_root,
                workspaces.current,
                allowed_agents,
                workflow_allowed_tools,
                start_time,
            )

        # ── Ruta 5: Default — analizar y decidir simple vs full ──
        await emit_stats(
            events,
            {
                "subtasks_total": 0,
                "subtasks_completed": 0,
                "tokens_used": 0,
                "elapsed_time": "0s",
                "current_agent": "—",
                "status": "Iniciando",
            },
        )
        await emit_system(events, "🔍 Analizando tu solicitud...")
        await emit_stats(events, {"status": "Analizando", "current_agent": "TaskAnalyzer"})

        task_analysis = await TaskAnalyzer.analyze_task(query)

        if not task_analysis.get("requires_full_orchestration", True):
            return await WorkflowOrchestrator._run_simple_conversation(
                query,
                conversation_history,
                task_analysis,
                template,
                allowed_agents,
                start_time,
                events,
                conversation_id,
            )

        return await WorkflowOrchestrator._run_full_orchestration(
            query,
            conversation_history,
            task_analysis,
            ctx,
            events,
            project_root,
            workspaces.current,
            allowed_agents,
            workflow_allowed_tools,
            start_time,
        )

    @staticmethod
    async def _run_simple_conversation(
        query: str,
        conversation_history: list,
        task_analysis: dict,
        template: dict,
        allowed_agents: list | None,
        start_time: float,
        events: WorkflowEvents,
        conversation_id: int | None = None,
    ) -> str:
        logger.info("🚀 Modo conversación simple activado → ruta rápida")
        from agents.service import AgentsService

        default_agent = template.get("agents", {}).get("default_simple", settings.fallback_agent)
        if allowed_agents and default_agent not in allowed_agents:
            default_agent = (
                allowed_agents[0] if len(allowed_agents) > 0 else settings.fallback_agent
            )

        await emit_stats(
            events, {"status": "Respondiendo", "current_agent": default_agent.capitalize()}
        )

        try:
            stream_callback = events.on_stream_chunk if events else None

            # Apply context compression if enabled and history exceeds limit
            if settings.context_compression:
                max_tokens = settings.max_context_tokens
                if ContextManager.estimate_tokens(conversation_history) > max_tokens * 0.7:
                    conversation_history = ContextManager.compress_history(
                        conversation_history, max_tokens=max_tokens
                    )

            # Check if the agent has tools → use agent loop with function-calling
            from agents.registry import agents_registry as _reg

            agent_profile = _reg.get_profile(default_agent)
            agent_tools = agent_profile.get("tools", []) if agent_profile else []

            if agent_tools:
                # Use execute_agent_loop with the agent's tools
                from tools.specs import expand_allowed_tools

                expanded_tools = expand_allowed_tools(agent_tools)
                from orchestration.loop import execute_agent_loop

                loop_result = await execute_agent_loop(
                    task=query,
                    agent_type=default_agent,
                    history=conversation_history,
                    allowed_tools=expanded_tools,
                    workspace=get_global_workspaces().current,
                    on_stream_chunk=stream_callback,
                )
                final_content = clean_llm_response(
                    loop_result.get("result", str(loop_result))
                    if isinstance(loop_result, dict)
                    else str(loop_result)
                )
            else:
                raw_response = await AgentsService.execute_agent(
                    agent_type=default_agent,
                    query=query,
                    history=conversation_history,
                    on_stream_chunk=stream_callback,
                )
                final_content = clean_llm_response(raw_response)
        except Exception as e:
            logger.error(f"Error en ruta rápida: {e}")
            final_content = (
                "❌ Hubo un problema al procesar tu solicitud. ¿Puedes intentarlo de nuevo?"
            )

        # Apply anti-distillation protection
        from core.security.undercover_mode import undercover

        final_content = await undercover.get_safe_response_async(final_content)

        scorecard = {
            "subtasks": 1,
            "completadas": 1,
            "recuperadas": 0,
            "fallidas": 0,
            "tokens": len(final_content) // 4,
            "tiempo": f"{round(time.monotonic() - start_time, 2)}s",
            "calidad": "Alta",
            "tipo_tarea": "simple_conversation",
            "complejidad": "simple",
        }

        await emit_stats(
            events,
            {
                "subtasks_total": 1,
                "subtasks_completed": 1,
                "tokens_used": scorecard["tokens"],
                "elapsed_time": scorecard["tiempo"],
                "current_agent": "Conversacional",
                "status": "Completado",
            },
        )

        await finalize_workflow(
            query=query,
            final_output=final_content,
            conversation_history=conversation_history,
            conversation_id=conversation_id,
            scorecard=scorecard,
            subtasks_list=["Respuesta directa"],
            task_analysis=task_analysis,
            G=None,
            events=events,
        )
        return final_content

    @staticmethod
    async def _run_coordinated(
        query: str,
        ctx,
        events,
        template,
        allowed_agents,
        workflow_allowed_tools,
        conversation_history,
        workspaces,
        project_root,
        start_time,
        session=None,
    ) -> str:
        """Run the multi-agent coordinator with DAG execution and shared blackboard."""
        from orchestration.decomposer import decompose_task_with_phases
        from orchestration.workflows.coordinated import MultiAgentCoordinator

        coordinator = MultiAgentCoordinator()
        all_files_written: list[str] = []

        # Try phase-aware decomposition first
        decomposition = await decompose_task_with_phases(
            query,
            is_follow_up=ctx.is_follow_up,
            conversation_history=conversation_history,
            project_root=project_root,
            workspace=workspaces.current,
        )
        phases = decomposition.get("phases", [])

        if phases and len(phases) > 1:
            # Multi-phase execution
            await emit_system(events, f"🧩 Decomposed into {len(phases)} phases...")
            await emit_stats(events, {"status": "Executing phases", "current_agent": "Coordinator"})

            all_phase_results = {}
            subtasks_list = []
            for pi, phase in enumerate(phases):
                phase_name = phase["phase"]
                phase_subtasks = phase["subtasks"]
                subtasks_list.extend(phase_subtasks)
                await emit_system(
                    events,
                    f"📌 Phase {pi+1}/{len(phases)}: {phase_name} ({len(phase_subtasks)} subtasks)",
                )

                st_objs = [
                    {"id": f"{phase_name}_{i}", "description": s}
                    for i, s in enumerate(phase_subtasks)
                ]
                assignments = await coordinator.assign_agents(st_objs, allowed_agents)

                phase_results = await coordinator.execute_dag(
                    subtasks=st_objs,
                    assignments=assignments,
                    project_root=project_root,
                    workspace=workspaces.current,
                    allowed_tools=workflow_allowed_tools,
                    events=events,
                    session=session,
                    ctx=ctx,
                )

                # Write to blackboard with phase namespace
                for sid, r in phase_results.items():
                    await coordinator.blackboard.write(
                        f"{sid}_result",
                        {
                            "agent": r.get("agent", "?"),
                            "task": str(r.get("result", ""))[:300],
                            "status": r.get("status", "completed"),
                            "files_written": r.get("files_written", []),
                        },
                        phase=phase_name,
                    )
                all_phase_results.update(phase_results)
                all_files_written.extend(_collect_files_written(phase_results))

                # Persist blackboard to DB after each phase
                if ctx.conversation_id:
                    await coordinator.blackboard.sync_to_db(f"coord_{ctx.conversation_id}")

            results = all_phase_results
            total_subtasks = sum(len(p["subtasks"]) for p in phases)
        else:
            # Fallback: single-phase DAG (original behavior)
            await emit_system(events, "🧩 Decomposing task into coordinated DAG...")
            await emit_stats(events, {"status": "Decomposing DAG", "current_agent": "Coordinator"})

            dag = await coordinator.decompose_task_dag(query)
            subtasks = dag["subtasks"]
            logger.info(f"Coordinator DAG: {len(subtasks)} subtask(s)")

            await emit_system(events, f"📋 {len(subtasks)} subtask(s) planned, assigning agents...")
            await emit_stats(
                events,
                {
                    "subtasks_total": len(subtasks),
                    "subtasks_completed": 0,
                    "status": "Assigning agents",
                    "current_agent": "Coordinator",
                },
            )

            assignments = await coordinator.assign_agents(subtasks, allowed_agents)

            await emit_system(
                events, f"🚀 Executing {len(subtasks)} subtask(s) with DAG parallelism..."
            )
            await emit_stats(events, {"status": "Executing DAG", "current_agent": "Coordinator"})

            results = await coordinator.execute_dag(
                subtasks=subtasks,
                assignments=assignments,
                project_root=project_root,
                workspace=workspaces.current,
                allowed_tools=workflow_allowed_tools,
                events=events,
                session=session,
                ctx=ctx,
            )
            all_files_written.extend(_collect_files_written(results))
            total_subtasks = len(subtasks)
            subtasks_list = [st.get("description", st.get("id", "")) for st in subtasks]

            if ctx.conversation_id:
                await coordinator.blackboard.sync_to_db(f"coord_{ctx.conversation_id}")

        completed = sum(1 for r in results.values() if r.get("status") == "completed")
        failed = sum(1 for r in results.values() if r.get("status") == "failed")
        await emit_stats(
            events,
            {
                "subtasks_completed": completed,
                "status": f"Aggregating ({completed} done, {failed} failed)",
                "current_agent": "Coordinator",
                "files_written": all_files_written,
            },
        )

        await emit_system(events, "📊 Aggregating results with confidence evaluation...")
        final_content = await coordinator.aggregate_with_confidence(
            query, results, project_root, workspaces.current
        )

        # Finalize
        from orchestration.finalizer import finalize_workflow

        scorecard = generate_scorecard(
            results=results,
            G=None,
            final_content=final_content,
            query=query,
            task_analysis={"primary_type": "coordinated", "requires_full_orchestration": True},
            start_time=start_time,
        )

        await finalize_workflow(
            query=query,
            final_output=final_content,
            conversation_history=conversation_history,
            conversation_id=ctx.conversation_id,
            scorecard=scorecard,
            subtasks_list=subtasks_list,
            task_analysis={"primary_type": "coordinated", "requires_full_orchestration": True},
            G=None,
            events=events,
            workspace=workspaces.current,
            project_root=ctx.project_root or project_root,
            files_written=all_files_written,
        )

        return final_content

    @staticmethod
    async def _run_full_orchestration(
        query: str,
        conversation_history: list,
        task_analysis: dict,
        ctx: WorkflowContext,
        events: WorkflowEvents,
        project_root: str | None,
        workspace: str,
        allowed_agents: list | None,
        workflow_allowed_tools: list | None,
        start_time: float,
    ) -> str:
        """Delegate to DevelopmentOrchestrator.run()."""
        from orchestration.workflows.development import DevelopmentOrchestrator

        return await DevelopmentOrchestrator.run(
            query=query,
            conversation_history=conversation_history,
            task_analysis=task_analysis,
            ctx=ctx,
            events=events,
            project_root=project_root,
            workspace=workspace,
            allowed_agents=allowed_agents,
            workflow_allowed_tools=workflow_allowed_tools,
            start_time=start_time,
        )

    @staticmethod
    async def resume_workflow(session: Session, answer: str) -> str | None:
        """Resume a paused workflow after the user provides a clarification answer.

        Loads the most recent PausedSession, injects the answer back into the
        agent loop state, and continues execution from where it was paused.
        """
        ctx = session.context
        events = session.events
        conv_id = ctx.conversation_id

        async with get_async_session() as db_session:
            from sqlalchemy import select

            stmt = (
                select(PausedSession)
                .where(PausedSession.conversation_id == conv_id)  # type: ignore[arg-type]
                .where(PausedSession.resolved_at == None)  # type: ignore[arg-type]  # noqa: E711
                .order_by(PausedSession.created_at.desc())  # type: ignore[attr-defined]
                .limit(1)
            )
            result = await db_session.execute(stmt)
            paused = result.scalar()
            if paused is None:
                logger.warning(f"No paused session found for conversation {conv_id}")
                return None

            paused.clarification_answer = answer
            paused.resolved_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            db_session.add(paused)

        paused_data = paused.paused_state
        if isinstance(paused_data, str):
            paused_data = json.loads(paused_data)
        question = paused.clarification_question

        # Restore blackboard if saved
        blackboard_snap = paused_data.get("blackboard_snapshot")
        if blackboard_snap and ctx.blackboard is not None:
            ctx.blackboard.restore(blackboard_snap)
            logger.info("Blackboard restored from pause snapshot")

        await emit_system(events, f"📝 Respuesta de clarificación: {answer}")

        # Reconstruir el estado del agent loop
        loop_state = paused_data.get("paused_loop_state", {})
        messages = loop_state.get("messages", [])
        messages.append({"role": "user", "content": f"[Respuesta a: {question}] {answer}"})

        from orchestration.loop import execute_agent_loop

        result = await execute_agent_loop(
            task=loop_state.get("task", ""),
            agent_type=loop_state.get("agent_type", settings.default_agent),
            history=messages,
            allowed_tools=loop_state.get("allowed_tools"),
            project_root=ctx.project_root,
            workspace=ctx.workspace,
            session=session,
        )

        # Continue with remaining subtasks
        subtasks = paused_data.get("subtasks", [])
        current_idx = paused_data.get("subtask_index", 0)
        remaining = subtasks[current_idx:]

        # If the current subtask finished successfully, update results
        paused_results = paused_data.get("results", {})
        if result.get("status") == "completed":
            paused_results[current_idx] = {
                "node": current_idx,
                "result": result["result"],
                "status": "completed",
                "files_written": result.get("files_written", []),
            }

        # Ejecutar las subtareas restantes (si hay)

        G = nx.DiGraph()
        for i, task in enumerate(subtasks):
            G.add_node(i, task=task, agent="developer", status="pending")

        corrected_agents = paused_data.get("corrected_agents", ["developer"] * len(subtasks))
        conversation_history = paused_data.get("conversation_history", [])
        conversation_history.append({"role": "user", "content": f"[Clarificación] {answer}"})

        for node in range(current_idx + 1, len(subtasks)):
            if session.is_cancelled or ctx.cancelled:
                break
            forced_agent = corrected_agents[node] if node < len(corrected_agents) else None
            task_desc = G.nodes[node]["task"]

            await emit_stats(
                events,
                {
                    "current_agent": forced_agent or "developer",
                    "status": f"Ejecutando subtarea {node + 1}",
                },
            )

            try:
                subtask_result = await asyncio.wait_for(
                    execute_subtask_safe(
                        node=node,
                        task=task_desc,
                        G=G,
                        conversation_history=conversation_history,
                        current_pdf_text=ctx.current_pdf_text,
                        ctx=ctx,
                        events=events,
                        forced_agent=forced_agent,
                        task_analysis=paused_data.get("task_analysis"),
                    ),
                    timeout=SUBTASK_TIMEOUT,
                )
                if subtask_result.get("status") == "clarification_needed":
                    # Pausa anidada — guardar y retornar
                    ctx.last_clarification = subtask_result["clarification_question"]
                    try:
                        await _save_paused_session(
                            conv_id=conv_id,
                            query=paused_data.get("query", ""),
                            question=subtask_result["clarification_question"],
                            options=subtask_result.get("clarification_options", []),
                            paused_state={
                                **paused_data,
                                "subtask_index": node,
                                "paused_loop_state": subtask_result["paused_loop_state"],
                                "corrected_agents": corrected_agents,
                            },
                        )
                    except Exception:
                        logger.warning("Failed to save paused session (DB error)", exc_info=True)
                    return "[PAUSED:clarification_needed]"
                paused_results[node] = subtask_result
            except (TimeoutError, Exception) as e:
                logger.error(f"Subtask {node} failed during resume: {e}")
                paused_results[node] = {"status": "failed", "result": str(e), "files_written": []}

        # Finalizar — agregar y finalizar
        query = ctx.query
        from orchestration.aggregator import ResultAggregator

        all_files = _collect_files_written(paused_results)
        final_content = await ResultAggregator.aggregate_results(
            query=query,
            results=paused_results,
            G=G,
            task_analysis=paused_data.get("task_analysis", {}),
            files_written=all_files,
            project_root=ctx.project_root,
            workspace=ctx.workspace,
            agent_type=paused_data.get("task_analysis", {}).get("primary_type", "developer"),
        )

        await finalize_workflow(
            query=query,
            final_output=final_content,
            conversation_history=conversation_history,
            conversation_id=conv_id,
            scorecard={},
            subtasks_list=subtasks,
            task_analysis=paused_data.get("task_analysis"),
            G=G,
            events=events,
            project_root=ctx.project_root,
            workspace=ctx.workspace,
            files_written=all_files,
        )

        await emit_stats(
            events,
            {
                "subtasks_total": len(subtasks),
                "subtasks_completed": len(paused_results),
                "status": "Completado",
                "current_agent": "—",
                "files_written": all_files,
            },
        )

        if final_content and final_content.strip():
            from orchestration.context import emit_assistant

            await emit_assistant(events, final_content)

        return final_content


async def _save_paused_session(
    conv_id: int | None,
    query: str,
    question: str,
    options: list[str],
    paused_state: dict,
) -> None:
    """Persist a paused workflow session to DB for later resume."""
    import json

    async with get_async_session() as db_session:
        paused = PausedSession(
            conversation_id=conv_id,
            clarification_question=question,
            clarification_options=json.dumps(options) if options else None,
            paused_state=json.dumps({**paused_state, "query": query}),
        )
        db_session.add(paused)


async def _run_global_verification(
    query: str,
    project_root: str,
    workspace: str,
    allowed_tools: list[str],
    best_agent: str,
    events,
):
    """Collect the full project, run LSP diagnostics, and fix only what's needed."""
    from llm.prompts import VERIFY_GLOBAL_PROMPT

    base = paths.memory_dir(workspace) / project_root
    if not base.exists():
        return True

    def _read_project_files_sync() -> list[str]:
        """Read project files synchronously (runs in thread)."""
        texts = []
        total = 0
        max_chars_local = 10_000
        for fpath in base.rglob("*"):
            if fpath.is_file() and fpath.suffix in {
                ".py",
                ".txt",
                ".md",
                ".example",
                ".gitignore",
                ".yml",
                ".yaml",
            }:
                rel_path = str(fpath.relative_to(base))
                try:
                    content = fpath.read_text(encoding="utf-8")
                    if not content.strip():
                        content = "[ARCHIVO VACÍO]"
                except Exception:
                    content = "[No se pudo leer]"
                entry = f"--- {rel_path} ---\n{content}\n"
                if total + len(entry) > max_chars_local:
                    texts.append("(... y otros archivos omitidos por límite de tamaño)")
                    break
                texts.append(entry)
                total += len(entry)
        return texts

    files_text = await asyncio.to_thread(_read_project_files_sync)

    if not files_text:
        return True

    full_files_text = "\n".join(files_text)

    # ── Quick LSP report ──
    lsp_report = ""
    try:
        from tools.lsp_manager import lsp_manager_tool

        lsp_raw = await lsp_manager_tool(
            action="diagnostics",
            project_root=project_root,
            workspace=workspace,
        )
        lsp_report = str(lsp_raw)[:3000]
    except Exception as e:
        logger.warning("LSP no disponible durante verificación global", exc_info=True)
        lsp_report = "No se pudo ejecutar el LSP."

    prompt = VERIFY_GLOBAL_PROMPT.format(
        task=query,
        files_text=full_files_text,
        lsp_report=lsp_report,
    )

    raw_response = await _execute_specialized_agent(
        agent_type=best_agent,
        task=prompt,
        history=[],
        extra_tool_instructions="",
    )
    response_text = clean_llm_response(raw_response)
    result = parse_plan_json(response_text)

    if not result or result.get("is_correct", True):
        return True

    fix_plan = result.get("fix_plan", {})
    fix_actions = _extract_and_validate_actions(fix_plan, allowed_tools)

    if fix_actions:
        await emit_system(events, "🔧 Ejecutando correcciones globales...")
        report, written, commit_done, _ = await _execute_plan_actions(
            fix_actions, project_root, workspace, lambda msg: emit_system(events, msg)
        )
        if written and not commit_done:
            await auto_commit(
                workspace=workspace,
                project_root=project_root,
                message="Correcciones globales",
            )
        return True
    else:
        await emit_system(
            events,
            "⚠️ La verificación global encontró incumplimientos, pero no se pudo generar un plan válido.",
        )
        return False
