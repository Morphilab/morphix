"""
Subtask Executor — main coordinator for individual task execution.
Usa Agent Loop con function-calling nativo para ejecutar subtareas.

Decoupled: receives WorkflowEvents instead of framework-specific objects.
"""

import logging

import networkx as nx

from core.memory.manager import memory as memory_manager
from tools.specs import tool_matches_allowlist

logger = logging.getLogger(__name__)


from core.security.undercover_mode import undercover
from orchestration.diagram import update_live_diagram
from orchestration.executor.plan import _resolve_agent_and_task
from orchestration.executor.post import _post_execution_checks
from orchestration.loop import execute_agent_loop

logger = logging.getLogger(__name__)


from orchestration.context import WorkflowContext, WorkflowEvents
from orchestration.events import emit_assistant, emit_system


async def _direct_file_creation(
    task: str, project_root: str, workspace: str
) -> tuple[str | None, str | None]:
    """Safety Net: if the agent fails with function calling, create the file directly
    pidiéndole al LLM un JSON y escribiendo con FileManager sin pasar por el agent loop.

    Retorna (file_path, file_content) o (None, None).
    """
    import json

    from llm import models
    from tools.file_manager import FileManager

    prompt = (
        "Responde ÚNICAMENTE con un JSON válido. Sin explicaciones, sin markdown.\n\n"
        f"Tarea: {task}\n\n"
        'Formato: {"file": "ruta/archivo.ext", "content": "contenido aquí"}\n\n'
        "Reglas:\n"
        "- 'file' debe ser una ruta relativa al proyecto\n"
        "  (ej: 'script.py', 'src/main.py', 'README.md', '.gitignore', 'Makefile', '.env')\n"
        "- 'content' debe ser el contenido completo del archivo, listo para guardar\n"
        "- Para archivos de código (.py, .js, .ts, .html): incluye el código fuente completo\n"
        "- Para archivos de configuración (.gitignore, .env, .toml, .yaml, .json): "
        "incluye el texto de configuración\n"
        "- Para documentación (README.md, CHANGELOG.md): incluye el markdown completo\n"
        "- Si la tarea pide un test, incluye el archivo de test con pytest\n"
        "- Si la tarea pide múltiples archivos, prioriza el archivo principal"
    )

    try:
        response = await models.call(
            messages=[{"role": "user", "content": prompt}],
            role="fast",
            temperature=0.0,
            max_tokens=4000,
        )
        text = response.choices[0].message.content.strip()

        # Robust JSON extraction: try whole text first, then brace-balanced extraction
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Find balanced braces containing both "file" and "content"
            idx = max(text.find('"file"'), text.find('"content"'))
            if idx >= 0:
                start = text.rfind("{", 0, idx)
                if start >= 0:
                    depth = 0
                    end = start
                    for i in range(start, len(text)):
                        if text[i] == "{":
                            depth += 1
                        elif text[i] == "}":
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    if end > start:
                        try:
                            data = json.loads(text[start:end])
                        except json.JSONDecodeError:
                            pass

        if not isinstance(data, dict):
            logger.warning("Safety Net: no se encontró JSON con file+content en la respuesta")
            return None, None
        file_path = data.get("file", "").strip()
        content = data.get("content", "").strip()
        if not file_path or not content:
            return None, None

        result = await FileManager.execute(
            action="write",
            path=file_path,
            content=content,
            workspace=workspace,
            project_root=project_root,
        )
        if "escrito correctamente" in result or "Archivo" in result:
            logger.info(f"🛟 Safety Net: archivo creado directamente → {file_path}")
            return file_path, content
        return None, None
    except Exception:
        logger.warning("Safety Net: error creando archivo directamente", exc_info=True)
        return None, None


async def execute_subtask_safe(
    node: int,
    task: str,
    G: nx.DiGraph,
    conversation_history: list,
    current_pdf_text: str,
    ctx: WorkflowContext,
    events: WorkflowEvents,
    forced_agent: str | None = None,
    task_analysis: dict | None = None,
):
    """Execute a subtask with safety verification and post-checks.

    Receives ctx (WorkflowContext) + events (WorkflowEvents)
    en vez de page, mermaid_image, settings, etc. individuales.
    """
    try:
        if not await undercover.check_query(task):
            await emit_system(events, "❌ Solicitud bloqueada por razones de seguridad.")
            G.nodes[node]["status"] = "failed"
            return {
                "node": node,
                "task": task,
                "result": "Bloqueada por seguridad",
                "status": "failed",
            }

        await emit_system(events, f"🚀 Ejecutando subtarea {node + 1}: {task[:80]}...")
        G.nodes[node]["status"] = "running"
        await update_live_diagram(G, events)

        best_agent, task = await _resolve_agent_and_task(
            task, conversation_history, forced_agent, ctx.agents_registry
        )

        agent_profile = ctx.agents_registry.get_profile(best_agent)
        agent_tools = agent_profile.get("tools", []) if agent_profile else []

        # Expand tool groups to real tool names BEFORE filtering
        from tools.specs import expand_allowed_tools

        expanded_agent_tools = expand_allowed_tools(agent_tools) or []

        # Filter against workflow allowlist with prefix/component matching
        if ctx.allowed_tools is None:
            allowed_tools = expanded_agent_tools
        else:
            workflow_tools: list[str] = ctx.allowed_tools  # type: ignore[assignment]
            allowed_tools = []
            for tool_name in expanded_agent_tools:
                if tool_matches_allowlist(tool_name, workflow_tools):
                    allowed_tools.append(tool_name)

        is_dev_task = "file_manager" in agent_tools or "git_manager" in agent_tools

        # Safety Net only fires for non-analysis agents.
        # Analysis agents (type: analysis) never fabricate files.
        agent_type = agent_profile.get("type", "development") if agent_profile else "development"
        _safety_net_allowed = agent_type != "analysis"

        extra_context = ""
        if task_analysis:
            primary_type = task_analysis.get("primary_type", "")
            if primary_type:
                extra_context += f"Tipo de tarea: {primary_type}\n"
            requirements = task_analysis.get("requirements", "")
            if requirements:
                extra_context += f"Requisitos: {requirements}\n"

        # Inject blackboard context if available (multi-phase support)
        if ctx.blackboard is not None:
            bb_ctx = await ctx.blackboard.get_agent_context()
            if bb_ctx:
                extra_context += (
                    "\n⚠️ BLACKBOARD — Resultados de subtareas anteriores:\n"
                    + bb_ctx
                    + "\n\nUsa esta información para evitar trabajo duplicado.\n"
                )

        from orchestration.context import Session

        result = await execute_agent_loop(
            task=task,
            agent_type=best_agent,
            history=conversation_history,
            allowed_tools=allowed_tools,
            project_root=ctx.project_root,
            workspace=ctx.workspace,
            extra_context=extra_context,
            on_stream_chunk=events.on_stream_chunk if events else None,
            session=Session(context=ctx, events=events) if events else None,
        )

        if result.get("status") == "clarification_needed":
            return {
                "node": node,
                "status": "clarification_needed",
                "clarification_question": result["clarification_question"],
                "clarification_options": result.get("clarification_options", []),
                "paused_loop_state": result["paused_loop_state"],
            }

        final_answer = result["result"]
        files_written = result.get("files_written", [])

        # ─── 🛟 Safety Net: if the agent didn't create files, try direct ───
        if not files_written and _safety_net_allowed and ctx.project_root:
            logger.info(f"🛟 Safety Net activado para subtarea: {task[:80]}")
            await emit_system(
                events, "🛟 El agente no pudo crear archivos. Intentando creación directa..."
            )
            path, content = await _direct_file_creation(task, ctx.project_root, ctx.workspace)
            if path:
                files_written.append(path)
                final_answer = f"✅ Archivo creado directamente: {path}\n\n{content}"
                await emit_system(events, f"🛟 Archivo creado directamente: {path}")

        # 4.3 — Per-subtask functional verification
        verification_report = None
        if is_dev_task and files_written:
            try:
                from orchestration.executor.verify import _run_functional_verification

                verification_report = await _run_functional_verification(
                    task=task,
                    best_agent=best_agent,
                    allowed_tools=allowed_tools,  # type: ignore[arg-type]
                    project_root=ctx.project_root,
                    intended_files=files_written,
                    add_system_message=lambda msg: emit_system(events, msg),
                    workspace=ctx.workspace,
                )
                if verification_report:
                    await emit_system(events, f"🔍 Verificación: {verification_report[:200]}")  # type: ignore[index]
            except Exception:
                logger.debug("Verificación por subtarea omitida", exc_info=True)

        if is_dev_task:
            post_check = await _post_execution_checks(
                [],  # type: ignore[arg-type]
                [],
                True,
                files_written,
                task,
                ctx.project_root,
                is_dev_task,
                ctx.workspace,
                best_agent,
                allowed_tools,  # type: ignore[arg-type]
                lambda msg: emit_system(events, msg),
            )
            if post_check:
                final_answer += "\n\n" + post_check

        agent_status = result.get("status", "completed")
        is_stalled = (
            "estancado" in final_answer.lower() or agent_status == "stalled"
        ) and not files_written

        if is_stalled:
            await emit_system(
                events,
                f"⚠️ Subtarea {node + 1}: el agente encontró dificultades, pero se continuará con verificación.",
            )
            await emit_assistant(
                events,
                f"**✅ Subtarea {node + 1} completada**\n\nEl agente no pudo completar esta subtarea automáticamente. Se aplicará verificación global para corregirlo.",
            )
        else:
            await emit_assistant(events, f"**✅ Subtarea {node + 1} completada**\n\n{final_answer}")
        G.nodes[node]["status"] = "completed"
        await update_live_diagram(G, events)
        await memory_manager.write(f"workflow_subtask_{node}", final_answer, validated=True)
        return {
            "node": node,
            "task": task,
            "result": final_answer,
            "status": "completed",
            "files_written": files_written,
        }

    except Exception as e:
        logger.error("Error en subtarea %d: %s", node, e, exc_info=True)
        G.nodes[node]["status"] = "failed"
        error_msg = f"❌ Error en subtarea {node}: {str(e)[:200]}"
        if events is not None:
            await emit_assistant(events, error_msg)
        return {"node": node, "task": task, "result": error_msg, "status": "failed"}
