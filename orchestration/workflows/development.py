"""Development Orchestrator — linear sequential subtask execution.

Type 'development' workflow: decompose → route → execute → supervise → aggregate.

Extracted from WorkflowOrchestrator._run_full_orchestration to follow the same
module-per-workflow pattern used by coordinated.py, collaborative.py, and tdd.py.
"""

import logging
from typing import Any

import networkx as nx

from core.config import settings
from core.constants import SUBTASK_TIMEOUT_SECONDS
from core.context_manager import ContextManager
from core.path_resolver import paths
from orchestration.aggregator import ResultAggregator
from orchestration.context import (
    WorkflowContext,
    WorkflowEvents,
    emit_assistant,
    emit_system,
)
from orchestration.decomposer import decompose_task
from orchestration.diagram import update_live_diagram
from orchestration.emitter import WorkflowEmitter
from orchestration.executor.subtask import run_subtask_safe
from orchestration.finalizer import finalize_workflow
from orchestration.router import agent_router
from orchestration.supervisor import WorkflowSupervisor
from orchestration.utils import generate_scorecard
from orchestration.workflows.blackboard import SharedBlackboard
from orchestration.workflows.orchestrator import (
    _collect_files_written,
    _run_global_verification,
    _save_paused_session,
)

logger = logging.getLogger(__name__)

SUBTASK_TIMEOUT = SUBTASK_TIMEOUT_SECONDS  # backward-compat alias


def _build_subtask_list(subtasks, results, current_node, current_status):
    """Build a list of {name, status} dicts for the progress dashboard."""

    def _status(i):
        if i == current_node:
            return current_status
        if i in results:
            return results[i].get("status", "completed")
        return "completed" if i < current_node else "pending"

    return [
        {
            "name": (t if isinstance(t, str) else t.get("description", str(t)))[:60],
            "status": _status(i),
        }
        for i, t in enumerate(subtasks)
    ]


class DevelopmentOrchestrator:

    @staticmethod
    async def run(
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
        emitter: "WorkflowEmitter | None" = None,
    ) -> str:
        ctx.blackboard = SharedBlackboard()

        emitter = emitter or WorkflowEmitter(None)  # no-op si no hay emitter (invocaciones legacy)

        agent_results: list[dict] = []

        if settings.context_compression:
            if (
                ContextManager.estimate_tokens(conversation_history)
                > settings.max_context_tokens * 0.8
            ):
                conversation_history = ContextManager.compress_history(
                    conversation_history, max_tokens=settings.max_context_tokens
                )

        subtasks_list = await decompose_task(
            query,
            is_follow_up=ctx.is_follow_up,
            conversation_history=conversation_history,
            project_root=project_root,
            workspace=workspace,
        )

        await emit_system(events, f"📊 {len(subtasks_list)} subtareas generadas")
        await emitter.emit(
            subtasks_total=len(subtasks_list),
            subtasks_completed=0,
            status="Descomponiendo",
            phase="Descomponiendo",
            subtask_list=[
                {
                    "name": (t if isinstance(t, str) else t.get("description", str(t)))[:60],
                    "status": "pending",
                }
                for t in subtasks_list
            ],
        )

        G = nx.DiGraph()
        for i, task in enumerate(subtasks_list):
            if isinstance(task, dict):
                task_desc = task.get("description", str(task))
                agent = task.get("agent", settings.fallback_agent)
            else:
                task_desc = str(task)
                agent = settings.fallback_agent

            G.add_node(i, task=task_desc, agent=agent, status="pending")
            if i > 0:
                G.add_edge(i - 1, i)

        logger.info("🔍 Supervisor revisando selección de agentes...")
        router_selections = []

        primary_type = task_analysis.get("primary_type", "mixed")

        for task in subtasks_list:
            desc = task if isinstance(task, str) else task.get("description", str(task))
            best_agent = await agent_router.select_best_agent(
                desc,
                primary_type=primary_type,
                allowed_agents=allowed_agents,
            )
            router_selections.append(best_agent)

        corrected_agents = await WorkflowSupervisor.review_and_correct(
            task_analysis,
            router_selections,
            subtasks_list,
            allowed_agents=allowed_agents or [],
        )

        for node in G.nodes():
            if node < len(corrected_agents):
                G.nodes[node]["agent"] = corrected_agents[node]

        await update_live_diagram(G, events)

        results: dict[int, dict[str, Any]] = {}
        for node in nx.topological_sort(G):
            if ctx.cancelled:
                await emit_system(events, "🛑 Workflow cancelado por el usuario")
                break
            forced_agent = corrected_agents[node] if node < len(corrected_agents) else None
            task_desc = G.nodes[node]["task"]
            agent = G.nodes[node]["agent"]

            await emitter.emit(
                current_agent=agent.capitalize(),
                status=f"Ejecutando subtarea {node + 1}",
                phase="Ejecutando",
                subtask_list=_build_subtask_list(subtasks_list, results, node, "running"),
            )

            clean_history = [
                m
                for m in conversation_history
                if m.get("role") not in ("tool",) or m.get("tool_call_id") or m.get("tool_name")
            ]
            result = await run_subtask_safe(
                node=node,
                task=task_desc,
                G=G,
                conversation_history=clean_history,
                current_pdf_text=ctx.current_pdf_text,
                ctx=ctx,
                events=events,
                forced_agent=forced_agent,
                task_analysis=task_analysis,
                timeout=SUBTASK_TIMEOUT,
            )

            results[node] = result

            if ctx.blackboard is not None:
                await ctx.blackboard.write(
                    f"subtask_{node}_result",
                    {
                        "task": task_desc[:200],
                        "agent": agent,
                        "status": result.get("status", "completed"),
                        "files_written": result.get("files_written", []),
                    },
                    phase="default",
                )

            result_text = str(result.get("result", ""))[:800]
            if result_text.strip():
                agent_results.append(
                    {
                        "role": "agent",
                        "content": f"[{agent.capitalize()} - {str(task_desc)[:60]}]\n{result_text}",
                    }
                )
            files_written = result.get("files_written", [])
            if files_written:
                agent_results.append(
                    {"role": "tool", "content": f"Files written: {', '.join(files_written[:10])}"}
                )
            if result.get("status") == "clarification_needed":
                ctx.last_clarification = result["clarification_question"]
                blackboard_snap = ctx.blackboard.snapshot() if ctx.blackboard is not None else None
                paused_data = {
                    "subtask_index": node,
                    "subtasks": subtasks_list,
                    "results": results,
                    "corrected_agents": corrected_agents,
                    "paused_loop_state": result["paused_loop_state"],
                    "conversation_history": conversation_history,
                    "task_analysis": task_analysis,
                    "G_nodes": [G.nodes[i] for i in range(len(G.nodes))],
                    "blackboard_snapshot": blackboard_snap,
                }
                try:
                    await _save_paused_session(
                        conv_id=ctx.conversation_id,
                        query=query,
                        question=result["clarification_question"],
                        options=result.get("clarification_options", []),
                        paused_state=paused_data,
                    )
                except Exception:
                    logger.warning("Failed to save paused session (DB error)", exc_info=True)
                logger.info(
                    f"⏸️ Workflow pausado en subtarea {node + 1}: {result['clarification_question'][:80]}"
                )
                return "[PAUSED:clarification_needed]"

            completed = sum(1 for r in results.values() if r.get("status") == "completed")
            await emitter.emit(
                subtasks_completed=completed,
                phase="Ejecutando",
                subtask_list=_build_subtask_list(subtasks_list, results, node, "completed"),
            )
            await update_live_diagram(G, events)

        all_files_written = _collect_files_written(results)

        # ────────────────────────────────────────────────────────
        # ╔══════════════════════════════════════════════════════╗
        # ║         GLOBAL PROJECT VERIFICATION                  ║
        # ╚══════════════════════════════════════════════════════╝
        if project_root:
            await emit_system(events, "🔍 Realizando verificación global del proyecto...")
            await emitter.emit(
                status="Verificando", current_agent="WorkflowSupervisor", phase="Verificando"
            )

            best_agent = corrected_agents[0] if corrected_agents else settings.default_agent

            global_ok = await _run_global_verification(
                query=query,
                project_root=project_root,
                workspace=ctx.workspace,
                allowed_tools=workflow_allowed_tools or ["file_manager", "git_manager"],
                best_agent=best_agent,
                events=events,
            )
            if global_ok:
                await emit_system(events, "✅ Verificación global superada.")
            else:
                await emit_system(
                    events,
                    "⚠️ Se detectaron incumplimientos globales; se aplicaron correcciones automáticas.",
                )
        # ────────────────────────────────────────────────────────
        if project_root:
            base = paths.memory_dir(ctx.workspace) / project_root
            if base.exists():
                for fpath in base.rglob("*"):
                    if fpath.is_file() and fpath.suffix in {
                        ".py",
                        ".txt",
                        ".md",
                        ".yml",
                        ".yaml",
                        ".json",
                        ".cfg",
                        ".ini",
                        ".toml",
                    }:
                        rel = str(fpath.relative_to(base))
                        if rel not in all_files_written:
                            all_files_written.append(rel)

        await emit_system(events, "🔄 Preparando la respuesta final...")
        await emitter.emit(
            status="Sintetizando", current_agent="ResultAggregator", phase="Sintetizando"
        )

        try:
            final_content = await ResultAggregator.aggregate_results(
                query,
                results,
                G,
                task_analysis,
                files_written=all_files_written,
                project_root=project_root,
                workspace=workspace,
                agent_type=task_analysis.get("primary_type", "developer"),
            )
        except Exception as e:
            logger.error(f"Result aggregation failed: {e}")
            result_summaries = []
            for node, r in results.items():
                status = r.get("status", "unknown")
                output = r.get("result", str(r))[:200]
                result_summaries.append(f"- Subtask {node}: {status} — {output}")
            final_content = (
                "⚠️ Result aggregation encountered an error. Partial results:\n\n"
                + "\n".join(result_summaries)
            )

        from core.security.undercover_mode import undercover

        final_content = await undercover.get_safe_response_async(final_content)

        scorecard = generate_scorecard(
            results, G, final_content, query, task_analysis, start_time, ctx.enc
        )

        await emitter.emit(
            subtasks_total=len(subtasks_list),
            subtasks_completed=len(results),
            current_agent="—",
            status="Completado",
            files_written=list(all_files_written),
            phase=None,
            subtask_list=_build_subtask_list(subtasks_list, results, None, "completed"),
        )

        export_history = list(conversation_history) + agent_results
        await finalize_workflow(
            query=query,
            final_output=final_content,
            conversation_history=export_history,
            conversation_id=ctx.conversation_id,
            scorecard=scorecard,
            subtasks_list=subtasks_list,
            task_analysis=task_analysis,
            G=G,
            events=events,
            project_root=project_root,
            workspace=ctx.workspace,
            files_written=all_files_written,
        )

        if final_content and final_content.strip():
            await emit_assistant(events, final_content)

        return final_content
