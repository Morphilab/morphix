"""Collaborative Orchestrator — multi-agent debate with rounds and consensus.

Type 'collaborative' workflow: multiple agents with different perspectives
analyze a question, debate in rounds, and reach consensus via moderator.
"""

import asyncio
import logging
from collections.abc import Callable

from agents.registry import agents_registry
from agents.service import AgentsService
from core.config import settings
from core.utils import clean_llm_response
from llm import models, tool_calls_from_response
from orchestration.context import (
    WorkflowEvents,
    emit_agent,
    emit_agent_status,
    emit_agent_stream,
    emit_stats,
    emit_system,
)
from tools.specs import build_tool_definitions, tool_matches_allowlist
from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)


class CollaborativeOrchestrator:
    """Orquestador de debate colaborativo con rondas iterativas."""

    @staticmethod
    async def run(
        query: str,
        template: dict,
        events: WorkflowEvents,
        history: list | None = None,
        project_root: str | None = None,
        workspace: str | None = None,
        force_agent: str | None = None,
        workflow_allowed_tools: list[str] | None = None,
        start_time: float = 0.0,
        cancelled: Callable | None = None,
    ) -> str:
        if workspace is None:
            workspace = settings.active_workspace
        panel = template.get("panel", [])
        rounds = template.get("rounds", 3)
        round_timeout = template.get("round_timeout", 300)
        moderator_name = template.get("moderator", "moderador")
        requires_project = template.get("requires_project")

        if len(panel) < 2:
            await emit_system(events, "⚠️ El panel colaborativo necesita al menos 2 agentes.")
            return "Error: panel insuficiente."

        conversation_history = history or []

        # Build project context if available
        project_context = ""
        if project_root and (requires_project in (True, "optional", "true")):
            project_context = await CollaborativeOrchestrator._build_project_context(
                project_root, workspace
            )
            if project_context:
                await emit_system(events, f"📁 Contexto del proyecto cargado: `{project_root}`")

        # Forced leader agent: add to panel if not present
        leader = None
        if force_agent:
            if force_agent not in panel:
                panel = list(panel) + [force_agent]
            leader = force_agent
            await emit_system(events, f"🎯 **{force_agent.capitalize()}** lidera el debate")

        # ── RONDA 1: Opiniones iniciales ──
        await emit_system(
            events, f"🤝 **Debate colaborativo iniciado** — {len(panel)} agentes, {rounds} rondas"
        )
        await emit_stats(events, {"status": f"Ronda 1/{rounds}", "current_agent": "Panel"})

        previous_opinions: dict[str, str] = {}

        for r in range(1, rounds + 1):
            if cancelled and cancelled():
                await emit_system(events, "🛑 Debate cancelado por el usuario")
                return "Cancelado"

            await emit_system(events, f"\n--- **Ronda {r} de {rounds}** ---")

            round_opinions: dict[str, str] = {}

            if r == 1:
                # Round 1: question + project context only
                tasks = [
                    CollaborativeOrchestrator._ask_agent(
                        agent_name=name,
                        query=query,
                        others_opinions=None,
                        events=events,
                        history=conversation_history,
                        project_context=project_context,
                        project_root=project_root,
                        workspace=workspace,
                        workflow_allowed_tools=workflow_allowed_tools,
                        round_label=f"Ronda {r}",
                    )
                    for name in panel
                ]
            else:
                # Subsequent rounds: question + previous opinions + context
                tasks = [
                    CollaborativeOrchestrator._ask_agent(
                        agent_name=name,
                        query=query,
                        others_opinions=previous_opinions,
                        events=events,
                        history=conversation_history,
                        project_context=project_context,
                        project_root=project_root,
                        workspace=workspace,
                        workflow_allowed_tools=workflow_allowed_tools,
                        round_label=f"Ronda {r}",
                    )
                    for name in panel
                ]

            futures: dict[asyncio.Task, str] = {}
            for c, n in zip(tasks, panel, strict=True):
                futures[asyncio.ensure_future(c)] = n
            done, pending = await asyncio.wait(list(futures), timeout=round_timeout)
            for fut in pending:
                fut.cancel()

            if pending:
                logger.warning(
                    f"Round {r} timed out after {round_timeout}s — {len(pending)} agent(s) pending"
                )
                await emit_system(
                    events,
                    f"⚠️ Ronda {r} excedió el tiempo límite ({round_timeout}s) — {len(pending)} agente(s) perdieron su turno.",
                )

            for fut, name in futures.items():
                if fut in done:
                    try:
                        result = fut.result()
                        if isinstance(result, Exception):
                            await emit_system(events, f"⚠️ {name.capitalize()}: error — {result}")
                            round_opinions[name] = f"[Error: {result}]"
                        else:
                            round_opinions[name] = str(result)
                    except Exception as e:
                        await emit_system(events, f"⚠️ {name.capitalize()}: error — {e}")
                        round_opinions[name] = f"[Error: {e}]"
                else:
                    round_opinions[name] = "[Timeout: no respondió a tiempo]"

            previous_opinions = round_opinions
            await emit_stats(events, {"status": f"Ronda {r}/{rounds} completada"})

        # ── MODERATOR: Final synthesis ──
        await emit_system(
            events, f"\n⚖️ **{moderator_name.capitalize()}** sintetizando consenso final..."
        )
        await emit_stats(
            events, {"status": "Consenso final", "current_agent": moderator_name.capitalize()}
        )

        debate_summary = CollaborativeOrchestrator._build_debate_summary(
            query, previous_opinions, leader
        )

        try:
            final_answer = await CollaborativeOrchestrator._ask_moderator(
                agent_name=moderator_name,
                debate_summary=debate_summary,
                events=events,
                history=conversation_history,
                workflow_allowed_tools=workflow_allowed_tools,
            )
        except Exception as e:
            logger.error(f"Moderator failed: {e}", exc_info=True)
            final_answer = CollaborativeOrchestrator._fallback_consensus(debate_summary)

        await emit_system(events, "✅ Debate colaborativo finalizado.")
        await emit_stats(events, {"status": "Completado", "current_agent": "—"})

        # ── Unified finalization (shared with development/coordinated) ──
        from orchestration.finalizer import finalize_workflow
        from orchestration.utils import generate_scorecard

        # Build results dict compatible with generate_scorecard / finalize_workflow
        panel_results = {
            name: {"status": "completed", "result": opinion, "files_written": []}
            for name, opinion in previous_opinions.items()
        }
        scorecard = generate_scorecard(
            results=panel_results,
            G=None,
            final_content=final_answer,
            query=query,
            task_analysis={"primary_type": "collaborative", "requires_full_orchestration": True},
            start_time=start_time,
        )

        await finalize_workflow(
            query=query,
            final_output=final_answer,
            conversation_history=conversation_history,
            scorecard=scorecard,
            subtasks_list=[f"{name}: {op[:100]}" for name, op in previous_opinions.items()],
            task_analysis={"primary_type": "collaborative", "requires_full_orchestration": True},
            G=None,
            events=events,
            workspace=workspace,
            project_root=project_root,
            files_written=[],
        )

        return final_answer

    @staticmethod
    async def _ask_agent(
        agent_name: str,
        query: str,
        others_opinions: dict[str, str] | None,
        events: WorkflowEvents,
        history: list,
        project_context: str = "",
        project_root: str | None = None,
        workspace: str | None = None,
        workflow_allowed_tools: list[str] | None = None,
        round_label: str = "",
    ) -> str:
        """Ask an agent for their opinion, with tool access (1 round).

        Agents can use their registered tools (file_manager, code_search, etc.)
        to inspect the project before responding. Tool results feed back
        into a second LLM call for an informed response.
        """
        if workspace is None:
            workspace = settings.active_workspace
        enriched_query = CollaborativeOrchestrator._build_query(
            query, agent_name, others_opinions, project_context
        )

        await emit_system(events, f"💬 **{agent_name.capitalize()}** está pensando...")

        # Get agent profile and allowed tools
        profile = agents_registry.get_profile(agent_name)
        agent_tools = profile.get("tools", []) if profile else []
        tool_defs = None
        effective_tools: list[str] = []

        if agent_tools:
            from tools.specs import expand_allowed_tools

            expanded_profile = expand_allowed_tools(agent_tools) or []
            # Filter against workflow allowlist if provided
            if workflow_allowed_tools is not None:
                effective_tools = [
                    t for t in expanded_profile if tool_matches_allowlist(t, workflow_allowed_tools)
                ]
            else:
                effective_tools = expanded_profile
            tool_defs = build_tool_definitions(effective_tools) if effective_tools else None
        else:
            tool_defs = None
        model_role = profile.get("model_role", "agent") if profile else "agent"
        temperature = profile.get("temperature", 0.4) if profile else 0.4

        # Build messages with token-aware compression
        from core.config import settings as _settings
        from core.context_manager import ContextManager

        budget = int(_settings.max_context_tokens * 0.6)
        messages = ContextManager.compress_history(history, max_tokens=budget)
        if not messages:
            messages = history.copy()
        messages.append({"role": "user", "content": enriched_query})

        try:
            import json as _json

            # First call — agent may request a tool
            response = await models.call(
                messages=messages,
                role=model_role,
                temperature=temperature,
                tools=tool_defs,
                tool_choice="auto" if tool_defs else "none",
            )
            text = clean_llm_response(response)

            # Extract reasoning_content (DeepSeek thinking mode — must be passed back)
            reasoning = None
            try:
                choice = response.choices[0]
                msg = choice.message
                reasoning = getattr(msg, "reasoning_content", None)
            except (AttributeError, IndexError, TypeError):
                pass

            # If agent requested tools, execute and feed results back
            tool_calls = tool_calls_from_response(response)
            if tool_calls and effective_tools:
                # Build assistant message WITH tool_calls (API requirement)
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [],
                }
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning
                for tc in tool_calls[:3]:  # max 3 tool calls per round
                    tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                    tc_func = (
                        tc.get("function", {})
                        if isinstance(tc, dict)
                        else getattr(tc, "function", None)
                    )
                    name = (
                        tc_func.get("name", "")
                        if isinstance(tc_func, dict)
                        else getattr(tc_func, "name", "") if tc_func else ""
                    )
                    raw_args = (
                        tc_func.get("arguments", "{}")
                        if isinstance(tc_func, dict)
                        else getattr(tc_func, "arguments", "{}") if tc_func else "{}"
                    )
                    if isinstance(raw_args, str):
                        try:
                            args = _json.loads(raw_args)
                        except Exception:
                            args = {}
                    else:
                        args = raw_args if isinstance(raw_args, dict) else {}
                    if project_root:
                        args.setdefault("project_root", project_root)
                    args.setdefault("workspace", workspace)
                    assistant_msg["tool_calls"].append(
                        {
                            "id": tc_id,
                            "type": "function",
                            "function": {"name": name, "arguments": _json.dumps(args)},
                        }
                    )
                messages.append(assistant_msg)

                # Execute tools and append results with matching tool_call_id
                for tc_data in assistant_msg["tool_calls"]:
                    call_id = tc_data["id"]
                    tool_name = tc_data["function"]["name"]
                    tool_args = _json.loads(tc_data["function"]["arguments"])
                    try:
                        result = await safe_tool_call(tool_name, tool_args, role="agent")
                    except Exception as e:
                        logger.warning(
                            f"Tool call '{tool_name}' failed in collaborative agent: {e}"
                        )
                        result = {
                            "success": False,
                            "error": str(e),
                            "output": f"Tool error: {e}",
                        }
                    output = (
                        result.get("output", str(result))
                        if isinstance(result, dict)
                        else str(result)
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": f"[{tool_name}]: {output}",
                        }
                    )

                # Second call — now valid: tool messages follow assistant with tool_calls
                response = await models.call(
                    messages=messages,
                    role=model_role,
                    temperature=temperature,
                )
                text = clean_llm_response(response)

        except Exception as e:
            logger.error(f"Error in collaborative agent {agent_name}: {e}")
            text = f"[Error: {e}]"
            await emit_agent_status(events, agent_name, "error")

        # Emit agent response incrementally for UI streaming
        await emit_agent_status(events, agent_name, "thinking")
        chunk_size = 4
        for i in range(0, max(len(text), 1), chunk_size):
            chunk = text[i : i + chunk_size]
            await emit_agent_stream(events, agent_name, round_label, chunk)
            await asyncio.sleep(0.008)  # yield to event loop for UI updates
        await emit_agent_status(events, agent_name, "ready")

        await emit_agent(events, agent_name, round_label, text)
        return text

    @staticmethod
    async def _ask_moderator(
        agent_name: str,
        debate_summary: str,
        events: WorkflowEvents,
        history: list,
        workflow_allowed_tools: list[str] | None = None,
    ) -> str:
        """Ask the moderator to synthesize the final consensus.

        If the moderator profile has tools, they are filtered against
        workflow_allowed_tools and executed via execute_agent_loop.
        Otherwise falls back to AgentsService.execute_agent (text-only).
        """
        await emit_system(events, f"⚖️ **{agent_name.capitalize()}** deliberando...")

        stream_callback = events.on_stream_chunk if events else None

        # Filter moderator tools against workflow allowlist
        profile = agents_registry.get_profile(agent_name)
        agent_tools = profile.get("tools", []) if profile else []
        effective_tools: list[str] = []

        if agent_tools:
            from tools.specs import expand_allowed_tools

            expanded_profile = expand_allowed_tools(agent_tools) or []
            if workflow_allowed_tools is not None:
                effective_tools = [
                    t for t in expanded_profile if tool_matches_allowlist(t, workflow_allowed_tools)
                ]
            else:
                effective_tools = expanded_profile

        if effective_tools:
            from orchestration.loop import execute_agent_loop

            loop_result = await execute_agent_loop(
                task=debate_summary,
                agent_type=agent_name,
                history=history,
                allowed_tools=effective_tools,
                on_stream_chunk=stream_callback,
            )
            text = (
                loop_result.get("result", str(loop_result))
                if isinstance(loop_result, dict)
                else str(loop_result)
            )
            return clean_llm_response(text)

        final = await AgentsService.execute_agent(
            agent_type=agent_name,
            query=debate_summary,
            history=history,
            on_stream_chunk=stream_callback,
        )
        return clean_llm_response(final)

    @staticmethod
    def _fallback_consensus(debate_summary: str) -> str:
        """Fallback consensus when moderator fails — simple concatenation."""
        return (
            "El panel debatió pero no se pudo alcanzar un consenso formal. "
            "A continuación el resumen de las opiniones:\n\n" + debate_summary
        )

    @staticmethod
    def _build_query(
        query: str,
        agent_name: str,
        others_opinions: dict[str, str] | None,
        project_context: str = "",
    ) -> str:
        """Construye el prompt enriquecido para un agente del panel."""
        parts = []

        if project_context:
            parts.append(f"Contexto del proyecto:\n{project_context}\n")

        parts.append(f"Pregunta: {query}")

        if others_opinions and len(others_opinions) > 1:
            others_text = "\n".join(
                f"  **{n.capitalize()}**: {o[:300]}"
                for n, o in others_opinions.items()
                if n != agent_name
            )
            parts.append(f"\nEsto opinaron los demás en la ronda anterior:\n{others_text}")
            parts.append(
                "Responde desde tu personalidad. Puedes mantener tu postura, "
                "refinarla, o cambiar de opinión si te convencieron. "
                "Sé fiel a tu personaje. Responde en primera persona."
            )
        else:
            parts.append(
                "Responde desde tu personalidad. Sé fiel a tu personaje. Responde en primera persona."
            )

        return "\n\n".join(parts)

    @staticmethod
    async def _build_project_context(project_root: str, workspace: str) -> str:
        """Construye un resumen del proyecto para inyectar en el debate."""
        import asyncio

        from core.path_resolver import paths

        base = paths.memory_dir(workspace) / project_root
        if not base.exists():
            return ""

        def _scan_sync() -> list[str]:
            lines = []
            try:
                items = sorted(base.iterdir())[:30]
                dirs = [d.name + "/" for d in items if d.is_dir() and not d.name.startswith(".")]
                files = [f.name for f in items if f.is_file() and not f.name.startswith(".")]

                if dirs:
                    lines.append(f"Directorios: {', '.join(dirs)}")
                if files:
                    lines.append(f"Archivos: {', '.join(files[:15])}")

                for fname in ("requirements.txt", "pyproject.toml", "package.json"):
                    fpath = base / fname
                    if fpath.exists():
                        content = fpath.read_text(encoding="utf-8")[:800]
                        lines.append(f"\n{fname}:\n{content}")
                        break
            except Exception:
                logger.debug("File read skipped during project context scan", exc_info=True)
            return lines

        lines = await asyncio.to_thread(_scan_sync)
        return "\n".join(lines) if lines else ""

    @staticmethod
    def _build_debate_summary(
        query: str, final_opinions: dict[str, str], leader: str | None = None
    ) -> str:
        """Construye el resumen del debate para el moderador."""
        opinions_text = "\n\n".join(f"**{n.capitalize()}**: {o}" for n, o in final_opinions.items())
        leader_note = ""
        if leader:
            leader_note = (
                f"\n\n**Nota:** {leader.capitalize()} es el líder designado de este debate. "
                f"Su opinión tiene peso especial en la decisión final."
            )
        return (
            f"Eres el moderador de un debate. Resume el consenso al que llegó el panel "
            f"sobre la siguiente pregunta:\n\n"
            f"**Pregunta:** {query}\n\n"
            f"**Opiniones finales del panel:**\n\n{opinions_text}"
            f"{leader_note}\n\n"
            f"Sintetiza la conclusión final del grupo. Combina las mejores ideas de cada uno. "
            f"Si hay desacuerdo, señálalo con diplomacia pero inclina la balanza con tu criterio neutral. "
            f"Estructura tu respuesta como un veredicto final."
        )
