"""Agent Loop — task execution with native function calling.

Core intelligence:
  1. CodebaseIndexer: el agente entiende tu código antes de actuar
  2. ContextManager: comprime el historial para no exceder la ventana
  3. ReAct Pattern: razonamiento → acción → observación → ajuste
  4. Self-Reflection: detecta estancamiento y hace early exit
  5. Memoria FAISS: inyecta contexto de tareas similares pasadas
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from core.codebase_indexer import CodebaseIndexer
from core.config import settings
from core.constants import PROJECTS_DIR_NAME
from core.context_manager import ContextManager
from core.memory.manager import memory as memory_manager
from core.utils import clean_llm_response
from llm import models, tool_calls_from_response
from llm.tool_calls import (
    detect_provider_from_raw_tool_call,
    has_tool_association,
    is_valid_tool_call,
    log_raw_tool_args,
    model_supports_tool_calling,
    normalize_arguments,
    tool_result_message,
)
from orchestration.context import Session, emit_stats, emit_system
from tools.specs import build_tool_definitions, build_tool_instructions
from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)


@dataclass
class AgentLoopConfig:
    """Injectable configuration for execute_agent_loop.

    Sustituye el acceso directo a constants globales y a kairos/settings,
    facilitando el testing y la inyección de dependencias.
    """

    max_agent_iterations: int = 15
    max_stall_iterations: int = 2
    max_tool_call_repairs: int = 2
    context_compression_threshold: float = 0.7
    context_compression_enabled: bool = True

    @classmethod
    def from_settings(cls) -> "AgentLoopConfig":
        """Crea una config con los valores por defecto del sistema."""
        return cls(
            max_agent_iterations=getattr(settings, "max_agent_iterations", 8),
            max_stall_iterations=2,
            max_tool_call_repairs=2,
            context_compression_threshold=getattr(settings, "context_compression_threshold", 0.7),
            context_compression_enabled=settings.context_compression,
        )


# Tool actions that indicate actual file modification
_MODIFYING_ACTIONS = {
    "file_manager": {"write", "append", "delete"},
    "diff_editor": {"apply"},
    "git_manager": {"commit"},
}


async def _accumulate_stream(stream, on_chunk) -> tuple[str, list[dict], str | None, str]:
    """Consume un stream de LLM acumulando texto, tool calls y reasoning.

    Returns:
        (full_text, tool_calls, finish_reason, reasoning_content)
        tool_calls: lista de dicts [{id, function: {name, arguments}}]
    """
    full_text = ""
    tool_call_by_id: dict[str, dict] = {}
    orphan_args: dict[str, str] = {}
    finish_reason = None
    reasoning = ""

    async for chunk in stream:
        if chunk.text:
            full_text += chunk.text
            if on_chunk:
                try:
                    await on_chunk(chunk.text)
                except Exception:
                    logger.debug("Error en callback de streaming", exc_info=True)

        if chunk.reasoning_content:
            reasoning += chunk.reasoning_content

        if chunk.tool_name and chunk.tool_call_id:
            tid = chunk.tool_call_id
            if tid not in tool_call_by_id:
                tool_call_by_id[tid] = {
                    "id": tid,
                    "function": {
                        "name": chunk.tool_name,
                        # Re-attach arguments that arrived before the name
                        "arguments": orphan_args.pop(tid, ""),
                    },
                }
            else:
                tool_call_by_id[tid]["function"]["name"] = chunk.tool_name

        if chunk.tool_arguments and chunk.tool_call_id:
            tid = chunk.tool_call_id
            if tid not in tool_call_by_id:
                # Only create entry if we already know the name, or defer until name arrives
                if chunk.tool_name:
                    tool_call_by_id[tid] = {
                        "id": tid,
                        "function": {"name": chunk.tool_name, "arguments": ""},
                    }
                else:
                    # Arguments arrived before name — buffer them (bug 2026-08-15:
                    # `continue` descartaba los args y el tool call quedaba incompleto)
                    orphan_args[tid] = orphan_args.get(tid, "") + chunk.tool_arguments
                    continue
            tool_call_by_id[tid]["function"]["arguments"] += chunk.tool_arguments

        # Ollama native format: complete tool call (name + arguments) in a
        # single chunk WITHOUT an id — synthesise one instead of dropping it.
        if chunk.tool_arguments and chunk.tool_name and not chunk.tool_call_id:
            tid = f"call_{len(tool_call_by_id)}"
            tool_call_by_id[tid] = {
                "id": tid,
                "function": {"name": chunk.tool_name, "arguments": chunk.tool_arguments},
            }

        if chunk.is_done:
            finish_reason = chunk.finish_reason
            # Track usage + cache metrics from streaming response
            if chunk.usage:
                from core.cache_manager import cache_manager
                from core.metrics import metrics as m

                m.record_llm_usage(
                    prompt_tokens=chunk.usage.get("prompt_tokens", 0),
                    completion_tokens=chunk.usage.get("completion_tokens", 0),
                    cache_hit_tokens=chunk.usage.get("prompt_cache_hit_tokens", 0),
                    cache_miss_tokens=chunk.usage.get("prompt_cache_miss_tokens", 0),
                )
                cache_manager.track_usage(
                    prompt_tokens=chunk.usage.get("prompt_tokens", 0),
                    completion_tokens=chunk.usage.get("completion_tokens", 0),
                    prompt_cache_hit_tokens=chunk.usage.get("prompt_cache_hit_tokens", 0),
                    prompt_cache_miss_tokens=chunk.usage.get("prompt_cache_miss_tokens", 0),
                )

    tool_calls = list(tool_call_by_id.values()) if tool_call_by_id else []

    return full_text, tool_calls, finish_reason, reasoning


def _has_any_valid_tool_call(parsed: list[dict]) -> bool:
    """Backwards-compatible wrapper delegating to the provider-aware normaliser."""
    return any(is_valid_tool_call(tc) for tc in parsed)


def _is_modifying_action(tool_name: str, parameters: dict) -> bool:
    """Determine if a tool call modifies files (useful for progress detection).
    Solo las herramientas registradas en _MODIFYING_ACTIONS pueden ser modificadoras."""
    if tool_name not in _MODIFYING_ACTIONS:
        return False
    modifying = _MODIFYING_ACTIONS[tool_name]
    action = parameters.get("action", "")
    # file_manager without explicit 'action' but with 'content' = write intent
    # (DeepSeek sometimes omits 'action'); counts as modification for stall detection.
    if not action and tool_name == "file_manager" and parameters.get("content"):
        action = "write"
    return action in modifying


async def _execute_single_tool_call(
    tool_name: str, arguments: dict, project_root: str | None, workspace: str
) -> tuple[str, bool, str, bool]:
    """Ejecuta un tool call y retorna (result_output, is_modifying, file_path, tool_success)."""
    params = dict(arguments) if isinstance(arguments, dict) else {}
    if project_root:
        params.setdefault("project_root", project_root)
    result = await safe_tool_call(
        tool_name=tool_name,
        parameters={**params, "workspace": workspace},
        role="agent",
    )
    result_output = result.get("output", str(result)) if isinstance(result, dict) else str(result)
    is_modifying = _is_modifying_action(tool_name, arguments)
    tool_success = result.get("success", False) if isinstance(result, dict) else False
    file_path = arguments.get("path", arguments.get("file_path", "")) if is_modifying else ""
    return result_output, is_modifying, file_path, tool_success


def _check_stall(
    consecutive_stalls: int,
    iteration_modified: bool,
    iteration: int,
    actions_taken: int,
    files_written: list,
    max_stall_iterations: int = 2,
) -> tuple[int, dict | None]:
    """Checks for agent stall and updates counter.

    Progress is defined as file modification OR tool execution.
    Browser/navigation tools don't modify files but do make progress.
    """
    if not iteration_modified:
        consecutive_stalls += 1
        if consecutive_stalls >= max_stall_iterations:
            if files_written:
                return 0, None
            return consecutive_stalls, {
                "status": "stalled",
                "result": (
                    f"Agent stalled: {consecutive_stalls} iterations without file modifications."
                ),
                "actions_taken": actions_taken,
                "iterations": iteration,
                "files_written": files_written,
            }
        return consecutive_stalls, None

    # File was modified — agent is making progress
    consecutive_stalls = 0
    return consecutive_stalls, None


async def _execute_tool_calls_and_check_stall(
    tool_calls: list[dict],
    messages: list,
    files_written: list,
    actions_taken: int,
    iteration_modified: bool,
    consecutive_stalls: int,
    iteration: int,
    config: AgentLoopConfig,
    project_root: str | None,
    workspace: str,
    events,
    repeat_tracker: dict[str, int] | None = None,
    provider_kind: str = "openai",
) -> dict | tuple[int, bool, list, int, dict | None]:
    """Execute parsed tool calls, track progress, check stall.

    Shared by streaming and non-streaming paths to eliminate ~55
    duplicated lines of tool-execution logic.

    repeat_tracker: optional dict mapping tool:args_hash → count.
    If the same non-modifying tool+args repeats 3+ times without
    progress, the stall counter is incremented. Resets on modification.

    provider_kind: "ollama" | "openai" — determines the native format of
    tool-result messages.
    """
    if repeat_tracker is None:
        repeat_tracker = {}

    for tc in tool_calls:
        if tc["name"] == "ask_clarification":
            question = tc["arguments"].get("question", "")
            options = tc["arguments"].get("options") or []
            messages.append(
                tool_result_message(
                    provider_kind,
                    tc["name"],
                    tc["id"],
                    f"[ask_clarification]: {question}",
                )
            )
            return {
                "status": "clarification_needed",
                "clarification_question": question,
                "clarification_options": options,
                "paused_loop_state": {
                    "messages": messages,
                    "iteration": iteration,
                    "files_written": files_written,
                },
            }

        result_output, is_mod, file_path, tool_success = await _execute_single_tool_call(
            tc["name"], tc["arguments"], project_root, workspace
        )
        actions_taken += 1

        if tc["name"] == "bash_manager" and events:
            await emit_system(events, f"[bash_manager]\n{result_output}")

        if is_mod or tool_success:
            iteration_modified = True
            if file_path and file_path not in files_written:
                files_written.append(file_path)
        if is_mod:
            repeat_tracker.clear()
        else:
            call_key = _make_repeat_key(tc["name"], tc["arguments"])
            repeat_tracker[call_key] = repeat_tracker.get(call_key, 0) + 1

        messages.append(
            tool_result_message(
                provider_kind,
                tc["name"],
                tc["id"],
                f"[{tc['name']}]: {result_output}",
            )
        )

    # Repetitive non-modifying calls override tool_success progress
    max_repeats = max(repeat_tracker.values(), default=0)
    if max_repeats >= 3:
        iteration_modified = False
        consecutive_stalls += 1

    has_productive = any(
        tc["name"].startswith("mcp_") or tc["name"].startswith("mcp:") for tc in tool_calls
    )
    if has_productive:
        consecutive_stalls = 0

    consecutive_stalls, early = _check_stall(
        consecutive_stalls,
        iteration_modified,
        iteration,
        actions_taken,
        files_written,
        config.max_stall_iterations,
    )
    return actions_taken, iteration_modified, files_written, consecutive_stalls, early


def _make_repeat_key(tool_name: str, arguments: dict) -> str:
    """Canonical repeat key for a tool call: 'tool_name:json_sorted_args'."""
    try:
        canon = json.dumps(arguments, sort_keys=True)
    except Exception:
        logger.warning("Unhandled exception in _make_repeat_key", exc_info=True)
        canon = str(arguments)
    return f"{tool_name}:{canon}"


def _load_tool_skills(allowed_tools: list[str] | None) -> str:
    """Load skill YAMLs for the allowed tools and build a context string."""
    if not allowed_tools:
        return ""

    try:
        import yaml

        skills_dir = Path(__file__).parent.parent / "tools" / "skills"
        if not skills_dir.is_dir():
            return ""

        parts: list[str] = []
        for tool_name in allowed_tools:
            skill_file = skills_dir / f"{tool_name}.yaml"
            if not skill_file.exists():
                continue
            try:
                skill = yaml.safe_load(skill_file.read_text())
                parts.append(_format_skill(skill))
            except Exception:
                logger.warning("Unhandled exception in _load_tool_skills", exc_info=True)
                continue

        if parts:
            return "\n\n".join(parts)
    except Exception:
        logger.warning("Unhandled exception in _load_tool_skills", exc_info=True)

    return ""


def _format_skill(skill: dict) -> str:
    """Format a single skill YAML into a concise text block for the agent."""
    tool = skill.get("tool", "?")
    lines = [f"--- {tool.upper()} SKILL ---"]

    when = skill.get("when_to_use", [])
    if when:
        lines.append("When to use:")
        lines.extend(f"  • {item}" for item in when)

    when_not = skill.get("when_not_to_use", [])
    if when_not:
        lines.append("When NOT to use:")
        lines.extend(f"  ✗ {item}" for item in when_not)

    examples = skill.get("examples", [])
    if examples:
        lines.append("Examples:")
        lines.extend(f"  → {item}" for item in examples)

    tips = skill.get("tips", [])
    if tips:
        lines.append("Tips:")
        lines.extend(f"  💡 {item}" for item in tips)

    return "\n".join(lines)


def _load_tool_kits(allowed_tools: list[str] | None) -> str:
    """Load kit YAMLs describing multi-tool workflows for the allowed tools."""
    if not allowed_tools:
        return ""

    try:
        import yaml

        kits_dir = Path(__file__).parent.parent / "tools" / "kits"
        if not kits_dir.is_dir():
            return ""

        parts: list[str] = []
        allowed_set = set(allowed_tools)
        for kit_file in sorted(kits_dir.glob("*.yaml")):
            try:
                kit = yaml.safe_load(kit_file.read_text())
                # Only include kit if at least one step's tools are available
                has_applicable = any(
                    not step.get("tools") or allowed_set.intersection(step["tools"])
                    for step in kit.get("steps", [])
                )
                if has_applicable:
                    parts.append(_format_kit(kit))
            except Exception:
                logger.warning("Unhandled exception in _load_tool_kits", exc_info=True)
                continue

        if parts:
            return (
                "[TOOL KITS — workflows predefinidos que combinan múltiples herramientas]\n\n"
                + "\n\n".join(parts)
            )
    except Exception:
        logger.warning("Unhandled exception in _load_tool_kits", exc_info=True)

    return ""


def _format_kit(kit: dict) -> str:
    """Format a single kit YAML into a concise text block for the agent."""
    name = kit.get("kit", "?").upper()
    goal = kit.get("goal", "")
    lines = [f"--- {name} KIT ---"]
    if goal:
        lines.append(f"Goal: {goal}")

    for i, step in enumerate(kit.get("steps", []), 1):
        desc = step.get("step", "")
        tools = step.get("tools", [])
        actions = step.get("actions", [])
        condition = step.get("condition", "")
        note = step.get("note", "")

        tool_str = ", ".join(tools) if tools else "[razoná]"
        action_str = f" ({', '.join(actions)})" if actions else ""
        lines.append(f"  {i}. {tool_str}{action_str} → {desc}")
        if condition:
            lines.append(f"     ⚠️ Condición: {condition}")
        if note:
            lines.append(f"     💡 {note}")

    return "\n".join(lines)


async def _build_extra_context(
    task: str,
    project_root: str | None,
    workspace: str,
    existing_context: str,
) -> str:
    """Construye contexto adicional: memoria FAISS + codebase indexado."""
    parts = [existing_context] if existing_context else []

    # 2.5 — Memoria FAISS: buscar tareas similares anteriores
    try:
        past = await memory_manager.search_async(task, k=2, min_similarity=0.5)
        if past:
            past_text = "\n".join(
                f"- [{r.get('key', '?')}]: {str(r.get('value', ''))[:300]}" for r in past
            )
            parts.append(f"--- TAREAS SIMILARES ANTERIORES ---\n{past_text}")
    except Exception:
        logger.warning("Búsqueda FAISS no disponible, omitiendo.", exc_info=True)

    # 2.1 — CodebaseIndexer: index and search relevant code
    if project_root:
        try:
            indexer = CodebaseIndexer(workspace=workspace, project_root=project_root)
            await asyncio.to_thread(indexer.index_project)
            relevant = indexer.find_relevant_code(task, max_results=5)
            if relevant:
                parts.append(f"--- CÓDIGO RELEVANTE DEL PROYECTO ({project_root}) ---\n{relevant}")
                logger.info(
                    "Codebase indexado: %d resultados relevantes para la tarea",
                    len(relevant.split("\n\n")),
                )
        except Exception:
            logger.warning("CodebaseIndexer no disponible, omitiendo.", exc_info=True)

    return "\n\n".join(parts)


def _is_trivial_profile(profile: dict | None) -> bool:
    """Perfil trivial: sin datos útiles más allá del nombre (o vacío).

    Los perfiles auto-inferidos con solo un nombre (ej. {"name": "ChatGPT"})
    contaminan las tareas: el agente ancla su salida en ese dato stale.
    """
    from core.utils import is_trivial_profile

    return is_trivial_profile(profile)


async def execute_agent_loop(
    task: str,
    agent_type: str | None = None,
    history: list | None = None,
    allowed_tools: list | None = None,
    project_root: str | None = None,
    workspace: str | None = None,
    extra_context: str = "",
    on_stream_chunk=None,
    session: Session | None = None,
    events=None,
    config: AgentLoopConfig | None = None,
) -> dict:
    """Ejecuta una tarea usando el Agent Loop con function-calling nativo.

    Improvements:
    - CodebaseIndexer: busca código relevante del proyecto antes de actuar

    Args:
        session: Session unificada (contexto + eventos). Si se provee,
                 los eventos (bash output, etc.) se emiten automáticamente.
        config: Configuración inyectable (opcional). Si es None, usa valores
                por defecto del sistema.
    """
    if workspace is None:
        workspace = settings.active_workspace
    if config is None:
        config = AgentLoopConfig.from_settings()

    events = events if events is not None else (session.events if session else None)

    if events:
        await emit_stats(
            events,
            {
                "status": "Agent Loop started",
                "current_agent": agent_type or "agent",
                "total_tools": len(allowed_tools) if allowed_tools else 0,
            },
        )

    # 2.1 + 2.5 — Construir contexto enriquecido
    enriched_context = await _build_extra_context(task, project_root, workspace, extra_context)

    # Inject tool skills and kits into context
    skills_context = _load_tool_skills(allowed_tools)
    kits_context = _load_tool_kits(allowed_tools)
    if skills_context or kits_context:
        enriched_context = (
            (kits_context + "\n\n" + skills_context if kits_context else skills_context)
            + "\n\n"
            + enriched_context
        )

    from llm.provider import LLMProvider

    initial_provider = LLMProvider.get_provider_name("agent")
    provider_kind: str = "ollama" if initial_provider == "ollama" else "openai"

    # ── Model capability gate (síntesis 3.2 + 3.6) ──
    role_config = settings.model_roles.get("agent", settings.model_roles["default"])
    active_model: str = (
        str(role_config.get("ollama_model") or settings.ollama_model)
        if initial_provider == "ollama"
        else str(role_config["model"])
    )
    native_tools_supported = model_supports_tool_calling(active_model)
    if not native_tools_supported:
        logger.warning(
            "Modelo '%s' sin soporte de tool calling (model_capabilities) — "
            "degradando a modo texto + Safety Net.",
            active_model,
        )
        if events:
            await emit_system(
                events,
                f"⚠️ El modelo '{active_model}' no soporta tool calling nativo — "
                "usando modo texto.",
            )

    tools_defs = (
        build_tool_definitions(allowed_tools, provider_kind=provider_kind)
        if native_tools_supported
        else []
    )
    tool_instructions_text = build_tool_instructions(allowed_tools, project_root, plan_mode=False)

    messages = list(history) if history else []

    # 2.3 — System prompt with ReAct pattern
    # Inject user profile from FAISS memory
    from core.memory.manager import memory as memory_manager

    profile_context = ""
    user_profile = memory_manager.get_user_profile()
    if user_profile and not _is_trivial_profile(user_profile):
        summary = memory_manager.get_user_summary()
        if summary:
            profile_context = f"\n[PERFIL DEL USUARIO]:\n{summary}\n"

    system_msg = (
        f"Eres un agente de desarrollo de software experto. Trabajas con el patrón ReAct:\n"
        f"1. RAZONA: analiza la tarea y el contexto disponible.\n"
        f"2. ACTÚA: usa las herramientas apropiadas para avanzar.\n"
        f"3. OBSERVA: evalúa el resultado de cada acción.\n"
        f"4. AJUSTA: si el resultado no es el esperado, cambia de estrategia.\n\n"
        f"Workspace: {workspace}\n"
        f"Project root: {project_root or 'N/A'}\n"
        f"{enriched_context}\n"
        f"{profile_context}\n"
        "Reglas importantes:\n"
        f"- Los paths en file_manager son relativos al project root. NO antepongas directorios como '{PROJECTS_DIR_NAME}/'.\n"
        f"  Ejemplo: para crear 'api_tareas/main.py', usa path='api_tareas/main.py', NO '{PROJECTS_DIR_NAME}/api_tareas/main.py'.\n"
        f"- NUNCA uses paths absolutos como '/home/user/{PROJECTS_DIR_NAME}/...'. Todos los paths son relativos al project root.\n"
        "- Para archivos NUEVOS escribe directamente con file_manager(action='write', path=..., content=...).\n"
        "  NO intentes leerlos primero: no existen y la lectura te hará perder tiempo.\n"
        "- Para MODIFICAR un archivo EXISTENTE, LEELO primero con file_manager(action='read', path='archivo.py').\n"
        "- Después de escribir, VERIFICA que el archivo existe con file_manager(action='read', path='archivo.py').\n"
        "- bash_manager SIEMPRE requiere el parámetro 'command'. Sin él, la herramienta falla.\n"
        "  Ejemplo correcto: command='pytest tests/'. NO llames bash_manager() sin command.\n"
        "- code_exec es un sandbox RESTRINGIDO. SOLO puedes usar: math, random, collections,\n"
        "  datetime, re, json, numpy (como 'np'), matplotlib (como 'plt').\n"
        "  NO uses 'import subprocess', 'import io', 'import os', 'import sys' — están bloqueados.\n"
        "  Para ejecutar scripts o tests, usa bash_manager o test_runner, NO code_exec.\n"
        "  Para VER el resultado de code_exec, usa print(...) o deja el valor como ÚLTIMA expresión\n"
        "  (ej: termina el código con 'np.mean(arr)'). Sin eso, no habrá salida visible.\n"
        "- Si recibes contexto compartido de otros agentes (Shared Context), LEELO primero.\n"
        "  Puede contener resultados previos que eviten trabajo duplicado.\n"
        "- Si una acción falla, NO la repitas. Prueba otra estrategia.\n"
        "- SI tu modelo soporta function-calling, DEBES usar file_manager o\n"
        "  diff_editor para crear o modificar archivos. Las llamadas a\n"
        "  file_manager requieren SIEMPRE los parámetros 'action' y 'path'.\n"
        "  Sin ellos la herramienta falla y la subtarea se estanca.\n"
        "- Si NO soportas function-calling, responde con el código completo\n"
        "  en bloques ```. El progreso REAL requiere archivos en disco —\n"
        "  responder solo con texto eventualmente agota los intentos.\n"
        "- Cuando la tarea esté completa Y los archivos estén escritos, responde con\n"
        "  un RESUMEN de lo que creaste.\n"
        "- Si te estancas, explica por qué y sugiere alternativas."
    )

    messages.insert(0, {"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": task})

    actions_taken = 0
    final_result = ""
    files_written: list[str] = []
    consecutive_stalls = 0
    repeat_tracker: dict[str, int] = {}
    tool_call_repairs = 0

    for iteration in range(1, config.max_agent_iterations + 1):
        if events and iteration > 1:
            await emit_stats(
                events,
                {
                    "status": f"Agent iteration {iteration}/{config.max_agent_iterations}",
                    "current_agent": agent_type or "agent",
                    "actions_taken": actions_taken,
                    "files_written": list(files_written),
                },
            )

        # 2.2 — Comprimir historial si el contexto crece demasiado
        estimated_tokens = ContextManager.estimate_tokens(messages)
        max_tokens = ContextManager._max_tokens()
        if estimated_tokens > max_tokens * config.context_compression_threshold:
            if config.context_compression_enabled:
                target = int(max_tokens * 0.5)
                logger.info(
                    "Comprimiendo contexto: %d tokens -> objetivo %d",
                    estimated_tokens,
                    target,
                )
                from core.cache_manager import cache_manager

                # Filter orphaned tool messages (missing association field causes provider errors)
                messages = [
                    m for m in messages if m.get("role") != "tool" or has_tool_association(m)
                ]

                messages = cache_manager.stabilize_messages(messages, max_tokens=target)

        use_native_tools = len(tools_defs) > 0

        if use_native_tools and on_stream_chunk:
            # ── Streaming con function-calling nativo ──
            stream = models.call_stream(
                messages=messages,
                role="agent",
                tools=tools_defs,
                tool_choice="auto",
            )
            streamed_text, streamed_tool_calls, _, reasoning = await _accumulate_stream(
                stream, on_stream_chunk
            )

            if streamed_tool_calls:
                # Build assistant_msg from accumulated stream tool_calls
                # NOTE: reasoning_content is NOT re-sent to the model —
                # thinking models amplify their own reasoning chains when
                # they see them in history, growing context and latency.
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": streamed_text or None,
                    "tool_calls": [],
                }
                for tc in streamed_tool_calls:
                    assistant_msg["tool_calls"].append(
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        }
                    )
                messages.append(assistant_msg)

                # Ejecutar cada tool call
                parsed = []
                for tc in streamed_tool_calls:
                    tool_name = tc["function"]["name"]
                    if not tool_name:
                        continue
                    args = normalize_arguments(tc["function"]["arguments"])
                    parsed.append({"name": tool_name, "id": tc["id"], "arguments": args})

                if not parsed:
                    continue

                # Safety net: if all tool calls have empty/invalid args,
                # treat as text response instead of executing broken tools
                if not _has_any_valid_tool_call(parsed):
                    if tool_call_repairs < config.max_tool_call_repairs:
                        logger.warning(
                            "Tool calls empty — repair %d/%d",
                            tool_call_repairs + 1,
                            config.max_tool_call_repairs,
                        )
                        from core.metrics import metrics as _metrics

                        _metrics.record_tool_call_repair(active_model)
                        for tc in parsed:
                            messages.append(
                                tool_result_message(
                                    provider_kind,
                                    tc["name"],
                                    tc["id"],
                                    "Error: la llamada no incluye argumentos válidos. "
                                    "Debes proporcionar parámetros con valores reales "
                                    "como action='write' y path='archivo.py'.",
                                )
                            )
                        tool_call_repairs += 1
                        continue
                    else:
                        logger.warning("Tool calls empty — repair budget exhausted, using text")
                        from core.metrics import metrics as _metrics

                        _metrics.record_tool_call_repair(active_model)
                        if events:
                            await emit_system(
                                events,
                                "⚠️ El modelo no generó tool calls válidas — "
                                "degradando a respuesta de texto.",
                            )
                        final_result = streamed_text.strip()
                        final_result = clean_llm_response(final_result)
                        break

                result = await _execute_tool_calls_and_check_stall(
                    parsed,
                    messages,
                    files_written,
                    actions_taken,
                    False,
                    consecutive_stalls,
                    iteration,
                    config,
                    project_root,
                    workspace,
                    events,
                    repeat_tracker,
                    provider_kind=provider_kind,
                )
                if isinstance(result, dict):
                    return result
                (actions_taken, _, files_written, consecutive_stalls, early) = result
                if early:
                    return early
                continue

            # No tool calls — final response via streaming
            final_result = streamed_text.strip()
            final_result = clean_llm_response(final_result)
            break

        elif use_native_tools:
            response = await models.call(
                messages=messages,
                role="agent",
                tools=tools_defs,
                tool_choice="auto",
            )
            tool_calls = tool_calls_from_response(response)

            if tool_calls:
                # Detect provider from the raw tool-call format
                provider_kind = detect_provider_from_raw_tool_call(tool_calls[0])
                # NOTE: reasoning_content is NOT re-sent to the model —
                # thinking models amplify their own reasoning chains when
                # they see them in history, growing context and latency.
                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [],
                }

                for tc in tool_calls:
                    func = tc.function if hasattr(tc, "function") else tc.get("function", {})
                    call_id = getattr(
                        tc, "id", f"call_{iteration}_{len(assistant_msg['tool_calls'])}"
                    )
                    call_name = func.name if hasattr(func, "name") else func.get("name", "")
                    try:
                        call_args = (
                            func.arguments
                            if hasattr(func, "arguments")
                            else json.dumps(func.get("arguments", {}))
                        )
                    except Exception:
                        logger.warning("Error serializando argumentos de tool call", exc_info=True)
                        call_args = "{}"

                    assistant_msg["tool_calls"].append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": call_name,
                                "arguments": call_args,
                            },
                        }
                    )
                messages.append(assistant_msg)

                parsed = []
                for i, tc in enumerate(tool_calls):
                    func = tc.function if hasattr(tc, "function") else tc.get("function", {})
                    tool_name = func.name if hasattr(func, "name") else func.get("name", "")
                    if not tool_name:
                        continue
                    call_id = assistant_msg["tool_calls"][i]["id"]
                    raw_args = (
                        func.arguments if hasattr(func, "arguments") else func.get("arguments", {})
                    )
                    log_raw_tool_args(f"non-streaming:{tool_name}", raw_args)
                    arguments = normalize_arguments(raw_args)
                    parsed.append({"name": tool_name, "id": call_id, "arguments": arguments})

                if not parsed:
                    continue

                # Safety net: if all tool calls have empty/invalid args,
                # treat as text response instead of executing broken tools
                if not _has_any_valid_tool_call(parsed):
                    if tool_call_repairs < config.max_tool_call_repairs:
                        logger.warning(
                            "Tool calls empty — repair %d/%d",
                            tool_call_repairs + 1,
                            config.max_tool_call_repairs,
                        )
                        from core.metrics import metrics as _metrics

                        _metrics.record_tool_call_repair(active_model)
                        for tc in parsed:
                            messages.append(
                                tool_result_message(
                                    provider_kind,
                                    tc["name"],
                                    tc["id"],
                                    "Error: la llamada no incluye argumentos válidos. "
                                    "Debes proporcionar parámetros con valores reales "
                                    "como action='write' y path='archivo.py'.",
                                )
                            )
                        tool_call_repairs += 1
                        continue
                    else:
                        logger.warning("Tool calls empty — repair budget exhausted, using text")
                        from core.metrics import metrics as _metrics

                        _metrics.record_tool_call_repair(active_model)
                        if events:
                            await emit_system(
                                events,
                                "⚠️ El modelo no generó tool calls válidas — "
                                "degradando a respuesta de texto.",
                            )
                        choice = response.choices[0] if hasattr(response, "choices") else None
                        content = ""
                        if choice and hasattr(choice, "message"):
                            content = choice.message.content or ""
                        final_result = str(content) if content else str(response)
                        final_result = clean_llm_response(final_result)
                        break

                result = await _execute_tool_calls_and_check_stall(
                    parsed,
                    messages,
                    files_written,
                    actions_taken,
                    False,
                    consecutive_stalls,
                    iteration,
                    config,
                    project_root,
                    workspace,
                    events,
                    repeat_tracker,
                    provider_kind=provider_kind,
                )
                if isinstance(result, dict):
                    return result
                (actions_taken, _, files_written, consecutive_stalls, early) = result
                if early:
                    return early
                continue

            # No tool calls — LLM dio respuesta final
            choice = response.choices[0] if hasattr(response, "choices") else None
            content = ""
            if choice and hasattr(choice, "message"):
                content = choice.message.content or ""
            final_result = str(content) if content else str(response)
            final_result = clean_llm_response(final_result)
            break

        else:
            # ── Modo texto: sin tools nativas (modelo sin soporte o lista vacía) ──
            response = await models.call(messages=messages, role="agent")
            choice = response.choices[0] if hasattr(response, "choices") else None
            content = ""
            if choice and hasattr(choice, "message"):
                content = choice.message.content or ""
            final_result = str(content) if content else str(response)
            final_result = clean_llm_response(final_result)
            break
    else:
        final_result = (
            f"⚠️ Límite de {config.max_agent_iterations} iteraciones alcanzado.\n"
            f"Acciones ejecutadas: {actions_taken}. Archivos modificados: {len(files_written)}.\n"
            "La tarea podría necesitar descomponerse en partes más pequeñas."
        )

    if events:
        await emit_stats(
            events,
            {
                "status": "completed",
                "current_agent": agent_type or "agent",
                "actions_taken": actions_taken,
                "iterations": iteration,
                "files_written": list(files_written),
            },
        )

    return {
        "status": "completed",
        "result": final_result,
        "actions_taken": actions_taken,
        "iterations": iteration,
        "files_written": files_written,
    }
