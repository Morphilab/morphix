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
from typing import Any

from core.codebase_indexer import CodebaseIndexer
from core.config import settings
from core.constants import PROJECTS_DIR_NAME
from core.context_manager import ContextManager
from core.errors import MorphixError
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
from orchestration.context import (
    Session,
    clarification_denied,
    emit_indexing_progress,
    emit_stats,
    emit_system,
    plan_mode_active,
    skill_allowlist_active,
)
from tools.specs import (
    build_tool_definitions,
    has_real_project,
    tool_matches_allowlist,
)
from tools.wrapper import safe_tool_call

logger = logging.getLogger(__name__)

# Tools que MIDEN sin escribir. Para un perfil read-only una medición roja es
# su entregable (el diagnóstico), no un stall: el detector de progreso no
# puede castigar al analista por no arreglar lo que solo puede reportar.
_MEASUREMENT_TOOLS = frozenset({"test_runner"})


# Re-prompt del final vacío: la firma stop-vacío que ni el starve-retry
# (length) ni el fantasma (tool_calls) atrapan. Re-correr el paso con el
# MISMO prompt reproduce el mismo vacío; el nudge pide el cierre explícito
# dentro del MISMO run. Una vez por run.
_NUDGE_FINAL_VACIO = (
    "Tu turno anterior terminó sin respuesta: no entregaste texto ni "
    "ejecutaste acciones. Entrega AHORA tu respuesta final en texto plano: "
    "resume brevemente qué hiciste y cuál es el resultado. Si no pudiste "
    "avanzar, explica el bloqueo en una o dos frases."
)

_PLAN_MODE_RULES = (
    "PLAN MODE activo: explorá y diseñá. En este modo no hay escrituras ni "
    "bash_manager/code_exec/diff_editor (pueden mutar el proyecto); usá "
    "file_manager (read), code_search y lsp_manager. Cuando el plan esté listo, "
    "salí con exit_plan_mode (Approve) para ejecutarlo."
)


def _nota_truncado(final: str, finish: str | None) -> str:
    """Marcador honesto cuando el proveedor declaró la respuesta incompleta.

    finish_reason='length' significa truncada POR CAP (thinking models
    agotando el presupuesto en reasoning): entregar el fragmento en
    silencio — como respuesta completa — es el bug de los mensajes
    cortados a media palabra en el chat.
    """
    if finish != "length":
        return final
    nota = "⚠️ (respuesta truncada por el límite de tokens — reintento con presupuesto duplicado ya agotado)"
    return f"{final}\n\n{nota}" if final.strip() else nota


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
    # Cap de prompt por llamada (0 = legado: 70 % del contexto del modelo).
    # Los runs DSL lo fijan por debajo del techo del modelo: con 128k de
    # contexto la compresión disparaba a ~90k y un run de 800k moría antes.
    prompt_cap_tokens: int = 0

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
    "file_manager": {"write", "append", "delete", "remove"},
    "diff_editor": {"apply"},
    "git_manager": {"commit"},
}

# Perfil read-only (p.ej. analista): el SOUL prohíbe escribir en prosa, pero
# nada lo impide a nivel de tool — y el modelo lo desobedeció en producción
# (escribió implementación desde el paso de spec). El motor acota, no la prosa.
# bash_manager/code_exec/diff_editor se bloquean ENTEROS: bash escribe por
# redirección y diff_editor solo existe para aplicar ediciones.
_READ_ONLY_BLOCKED_TOOLS = {"diff_editor", "bash_manager", "code_exec"}
_READ_ONLY_BLOCKED_ACTIONS = {
    "file_manager": {"write", "append", "delete", "remove"},
    "git_manager": {"init", "add", "commit"},
}


def _read_only_violation(tool_name: str, parameters: dict) -> str | None:
    """Mensaje de denegación si la llamada muta archivos desde un perfil read-only.

    None = permitida. La heurística de action implícito (content sin action =
    write) es paritaria con _is_modifying_action."""
    action = str(parameters.get("action", "") or "")
    if not action and tool_name == "file_manager" and parameters.get("content"):
        action = "write"
    if tool_name in _READ_ONLY_BLOCKED_TOOLS:
        return (
            f"⛔ Perfil de solo lectura: '{tool_name}' puede modificar archivos y "
            "está bloqueada para este agente. Consulta con file_manager "
            "(action='read'), code_search o lsp_manager; la edición la hace el "
            "agente developer."
        )
    if action and action in _READ_ONLY_BLOCKED_ACTIONS.get(tool_name, set()):
        return (
            f"⛔ Perfil de solo lectura: la acción '{action}' de '{tool_name}' "
            "modifica archivos y está bloqueada para este agente. Este rol "
            "analiza y entrega prosa; la edición la hace el agente developer."
        )
    return None


def _plan_mode_violation(tool_name: str, parameters: dict) -> str | None:
    """Denegación de escrituras mientras el plan está activo (plan-mode)."""
    if _read_only_violation(tool_name, parameters) is None:
        return None
    return (
        "❌ PLAN MODE: en plan mode no hay escrituras ni bash/code_exec/diff_editor "
        "(pueden mutar el proyecto). Explorá con file_manager (read), code_search y "
        "lsp_manager; salí con exit_plan_mode (Approve) para ejecutar el plan."
    )


_TOOL_RESULT_PRUNE_THRESHOLD_CHARS = 40_000
_TOOL_RESULT_KEEP_RECENT = 6
_TOOL_RESULT_MAX_CHARS = 600
_TOOL_RESULT_PRUNED_MARK = "…[resultado truncado: herramienta antigua fuera de ventana]"


def _prune_old_tool_results(messages: list, *, keep_recent: int = _TOOL_RESULT_KEEP_RECENT) -> int:
    """Trunca resultados de tools antiguos cuando el transcript crece sin techo.

    Escalado del prompt: cada iteración re-envía TODOS los tool results
    previos; en runs largos el coste por iteración crece con el acumulado.
    Se conservan íntegros los últimos N resultados (ventana de trabajo
    reciente) y los ya truncados no se re-truncan. Los mensajes nunca se
    eliminan: el protocolo exige cada tool_call emparejada con su resultado.
    El disparo por umbral alto protege el prefijo cacheable en sesiones
    normales.
    """
    tool_idxs = [
        i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "tool"
    ]
    if len(tool_idxs) <= keep_recent:
        return 0
    total_chars = sum(len(str(messages[i].get("content") or "")) for i in tool_idxs)
    if total_chars <= _TOOL_RESULT_PRUNE_THRESHOLD_CHARS:
        return 0
    podados = 0
    for i in tool_idxs[:-keep_recent]:
        msg = messages[i]
        content = str(msg.get("content") or "")
        if _TOOL_RESULT_PRUNED_MARK in content or len(content) <= _TOOL_RESULT_MAX_CHARS:
            continue
        msg["content"] = (
            content[:_TOOL_RESULT_MAX_CHARS]
            + f"\n{_TOOL_RESULT_PRUNED_MARK} ({len(content)} chars originales)"
        )
        podados += 1
    return podados


class LLMDeniedError(MorphixError, RuntimeError):
    """El controller denegó la llamada LLM (rate limit, breaker).

    El texto que entrega es un aviso del sistema, no del modelo: tratarlo
    como respuesta convierte la denegación en resultado falso del agente.
    """

    code = "llm/denied"


async def _accumulate_stream(
    stream, on_chunk, workspace: str | None = None
) -> tuple[str, list[dict], str | None, str, bool, dict | None]:
    """Consume un stream de LLM acumulando texto, tool calls y reasoning.

    Returns:
        (full_text, tool_calls, finish_reason, reasoning_content, system_notice, usage)
        tool_calls: lista de dicts [{id, function: {name, arguments}}]
        system_notice: True si el controller marcó el stream como aviso
            propio (rate limit) — no es salida del modelo.
        usage: último usage reportado por el stream.
    """
    full_text = ""
    tool_call_by_id: dict[str, dict] = {}
    orphan_args: dict[str, str] = {}
    finish_reason = None
    reasoning = ""
    system_notice = False
    usage_seen: dict | None = None

    async for chunk in stream:
        # estricto (is True): mocks de tests con attrs MagicMock no lo activan
        if getattr(chunk, "system_notice", None) is True:
            system_notice = True

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
                    # Arguments arrived before name — buffer them (un `continue` los descartaba
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

        if getattr(chunk, "reset", None) is True:  # estricto: mocks de tests no deben activarlo
            # retry del stream — descartar salida parcial previa
            full_text = ""
            tool_call_by_id.clear()
            orphan_args.clear()
            finish_reason = None
            usage_seen = None
            continue

        # El chunk de usage que SIGUE al de finish trae is_done=True y
        # finish_reason=None: sin el guard PISABA el 'length' del chunk de
        # finish y el starve-retry jamás disparaba (todo stream con
        # include_usage manda el usage chunk después del finish). El
        # tracking de usage corre para TODO is_done, con o sin finish.
        if chunk.is_done:
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            # Track usage + cache metrics from streaming response
            if chunk.usage:
                usage_seen = dict(chunk.usage)
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
                    workspace=workspace or "main",  # workspace real (fallback legacy)
                )

    tool_calls = list(tool_call_by_id.values()) if tool_call_by_id else []

    return full_text, tool_calls, finish_reason, reasoning, system_notice, usage_seen


def _has_any_valid_tool_call(parsed: list[dict]) -> bool:
    """Backwards-compatible wrapper delegating to the provider-aware normaliser."""
    return any(is_valid_tool_call(tc) for tc in parsed)


def _sanitize_tool_message_pairs(messages: list) -> list:
    """Delega en llm.tool_calls.sanitize_tool_message_pairs (capa
    compartida — agents/base.py también la necesita tras compress)."""
    from llm.tool_calls import sanitize_tool_message_pairs

    return sanitize_tool_message_pairs(messages)


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


def _inject_context_kwargs(
    tool_name: str,
    params: dict,
    *,
    project_root: str | None,
    workspace: str,
    allowed_tools: list[str] | None = None,
) -> dict:
    """Inyecta project_root/workspace SOLO si el handler los acepta.

    Los kwargs de contexto NO se inyectan a ciegas: se inspecciona la firma
    del handler registrado — los kwargs declarados se pasan, los **kwargs
    genéricos siguen recibiendo todo (fail-open), y una tool no registrada
    conserva el comportamiento previo. Sin el filtro, tools con firma
    estrecha (project_docs, memory_inspector) mueren con "unexpected
    keyword argument 'project_root'".
    """
    injectable: dict[str, object] = {}
    if workspace:
        injectable["workspace"] = workspace
    if project_root:
        injectable["project_root"] = project_root
    if allowed_tools is not None:
        injectable["allowed_tools"] = list(allowed_tools)
    if not injectable:
        return params

    try:
        import inspect

        from tools.orchestrator import tools_registry as _reg

        handler = _reg.get_tool(tool_name)
        if handler is None:
            return {**params, **injectable}
        sig = inspect.signature(handler)
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            return {**params, **injectable}
        accepted = set(sig.parameters)
        extra = {k: v for k, v in injectable.items() if k in accepted}
        return {**params, **extra}
    except (TypeError, ValueError):  # builtins sin firma inspeccionable
        return {**params, **injectable}


async def _execute_single_tool_call(
    tool_name: str,
    arguments: dict,
    project_root: str | None,
    workspace: str,
    allowed_tools: list[str] | None = None,
    read_only: bool = False,
) -> tuple[str, bool, str, bool]:
    """Ejecuta un tool call y retorna (result_output, is_modifying, file_path, tool_success).

    Allowlist en ejecución: si `allowed_tools` no es None y el tool no
    matchea, NO se ejecuta — se devuelve un error al agente.

    read_only: el perfil del agente activo es de solo lectura → las llamadas
    que mutan archivos se niegan ANTES de ejecutarse (la denegación viaja al
    modelo como tool_result accionable, igual que la del allowlist).

    Bot Mode: re-gate de send_to_bot ANTES del allowlist con su
    propio gate de sesión canónica (defensa en profundidad).
    """
    from core.bots_gate import BOT_DM_TOOL_NAME, handle_dm_call

    if tool_name == BOT_DM_TOOL_NAME:
        ok, out_str = await handle_dm_call(arguments, workspace)
        return out_str, False, "", bool(ok)
    if allowed_tools is not None and not tool_matches_allowlist(tool_name, allowed_tools):
        logger.warning("Tool '%s' bloqueada: no está en el allowlist del workflow", tool_name)
        return (
            f"❌ La herramienta '{tool_name}' no está permitida en este workflow.",
            False,
            "",
            False,
        )
    if read_only:
        denegada = _read_only_violation(tool_name, arguments if isinstance(arguments, dict) else {})
        if denegada:
            logger.warning("Tool '%s' bloqueada por perfil de solo lectura", tool_name)
            return denegada, False, "", False
    if plan_mode_active.get():
        denegada = _plan_mode_violation(tool_name, arguments if isinstance(arguments, dict) else {})
        if denegada:
            logger.warning("Tool '%s' bloqueada por plan-mode", tool_name)
            return denegada, False, "", False
    params = dict(arguments) if isinstance(arguments, dict) else {}
    call_params = _inject_context_kwargs(
        tool_name,
        params,
        project_root=project_root,
        workspace=workspace,
        allowed_tools=allowed_tools,
    )
    result = await safe_tool_call(
        tool_name=tool_name,
        parameters=call_params,
        role="agent",
        workspace=workspace,
    )
    result_output = result.get("output", str(result)) if isinstance(result, dict) else str(result)
    is_modifying = _is_modifying_action(tool_name, arguments)
    tool_success = result.get("success", False) if isinstance(result, dict) else False
    file_path = arguments.get("path", arguments.get("file_path", "")) if is_modifying else ""
    if file_path:
        from core.path_resolver import paths

        file_path = paths.normalize_path(str(file_path), project_root)
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


def _compression_plan(
    estimated_tokens: int,
    config: "AgentLoopConfig",
    max_tokens: int,
) -> tuple[bool, int]:
    """¿Comprimir el prompt y a qué objetivo?

    Con `prompt_cap_tokens > 0` manda el cap del run (objetivo 75 % de ese
    cap): la compresión deja de esperar al 70 % del contexto del modelo, que
    en un run de presupuesto corto no llega nunca. Sin cap, se conserva el
    comportamiento legacy (70 % del techo → 50 %).
    """
    if not config.context_compression_enabled:
        return False, 0
    if config.prompt_cap_tokens > 0:
        cap = int(config.prompt_cap_tokens)
        if estimated_tokens <= cap:
            return False, 0
        return True, max(4000, int(cap * 0.75))
    cap = int(max_tokens * config.context_compression_threshold)
    if estimated_tokens <= cap:
        return False, 0
    return True, int(max_tokens * 0.5)


def _read_only_tools(
    allowed_tools: list[str] | None,
    profile: dict | None,
) -> list[str] | None:
    """Intersección allowlist ∩ tools del perfil read-only.

    El allowlist del workflow es el contrato de lo permitido, pero un perfil
    read-only no debe VER tools que el gate le va a denegar: el modelo las
    intenta, el motor las bloquea y el paso se estanca. La intersección deja
    solo la superficie real del perfil (orden del allowlist preservado).
    """
    if allowed_tools is None or not profile:
        return allowed_tools
    profile_tools = set(profile.get("tools") or [])
    if not profile_tools:
        return allowed_tools
    return [t for t in allowed_tools if t in profile_tools]


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
    allowed_tools: list[str] | None = None,
    read_only: bool = False,
) -> dict | tuple[int, bool, list, int, dict | None, bool]:
    """Execute parsed tool calls, track progress, check stall.

    Shared by streaming and non-streaming paths to eliminate ~55
    duplicated lines of tool-execution logic.

    repeat_tracker: optional dict mapping tool:args_hash → count.
    If the same non-modifying tool+args repeats 3+ times without
    progress, the stall counter is incremented. Resets on modification.

    provider_kind: "ollama" | "openai" — determines the native format of
    tool-result messages.

    El sexto elemento del retorno es la evidencia de completitud: True
    solo si la ÚLTIMA tool del lote fue un test_runner con tests en verde
    — determinista, no depende de que el modelo declare 'completado'.
    """
    verification_green = False

    if repeat_tracker is None:
        repeat_tracker = {}

    for tc in tool_calls:
        if tc["name"] == "ask_clarification":
            # En rutinas programadas no hay humano que reanude — la
            # clarificación se deniega con tool_result y el turno continúa.
            if clarification_denied.get():
                messages.append(
                    tool_result_message(
                        provider_kind,
                        tc["name"],
                        tc["id"],
                        "[ask_clarification] no disponible en este contexto "
                        "(rutina programada): responde con la información "
                        "disponible y tu mejor criterio",
                    )
                )
                continue
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
            tc["name"],
            tc["arguments"],
            project_root,
            workspace,
            allowed_tools=allowed_tools,
            read_only=read_only,
        )
        actions_taken += 1

        # evidencia de completitud: la ÚLTIMA tool manda — test_runner
        # verde solo cuenta si no hubo tool después de él
        verification_green = tc["name"] == "test_runner" and bool(tool_success)

        if tc["name"] == "bash_manager" and events:
            await emit_system(events, f"[bash_manager]\n{result_output}")

        if is_mod or tool_success:
            iteration_modified = True
            if file_path and file_path not in files_written:
                files_written.append(file_path)
        elif tc["name"] in _MEASUREMENT_TOOLS:
            # Medir ES progreso (información nueva), aunque el resultado sea
            # rojo: un developer diagnosticando corre test_runner varias veces
            # y el stall detector lo cortaba a las 2 (cerrar_brechas quedaba
            # unmeasured con la entrega verde). La repetición IDÉNTICA sigue
            # siendo thrash: la caza el repeat_tracker (3+).
            iteration_modified = True
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
        # SOLO marcar la iteración como no-modificadora — _check_stall
        # aplica el incremento único (antes se duplicaba aquí y allí).
        iteration_modified = False

    # verificación en verde ES progreso: un test_runner exitoso no puede
    # alimentar el stall detector (el caso 'stalled-8 con tests verdes')
    if verification_green:
        iteration_modified = True

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
    return (
        actions_taken,
        iteration_modified,
        files_written,
        consecutive_stalls,
        early,
        (verification_green),
    )


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


_KITS_DIR = Path(__file__).resolve().parent.parent / "tools" / "kits"


def _kit_step_tools(step: dict) -> list[str]:
    """Herramientas de un paso — schema actual `tool` (str) con fallback legacy."""
    raw = step.get("tool") or step.get("tools") or []
    if isinstance(raw, str):
        return [raw]
    return list(raw)


def _load_tool_kits(allowed_tools: list[str] | None) -> str:
    """Load kit YAMLs describing multi-tool workflows for the allowed tools.

    allowed_tools=None carga todos los kits; el filtro compara la
    herramienta de cada paso (schema real `tool`) contra el allowlist.
    """
    try:
        import yaml

        kits_dir = _KITS_DIR
        if not kits_dir.is_dir():
            return ""

        parts: list[str] = []
        allowed_set = set(allowed_tools) if allowed_tools is not None else None
        for kit_file in sorted(kits_dir.glob("*.yaml")):
            try:
                kit = yaml.safe_load(kit_file.read_text())
                if not isinstance(kit, dict):
                    continue
                steps = [s for s in kit.get("steps", []) if isinstance(s, dict)]
                # Only include kit if at least one step's tools are available
                has_applicable = any(
                    not _kit_step_tools(step)
                    or allowed_set is None
                    or allowed_set.intersection(_kit_step_tools(step))
                    for step in steps
                )
                if has_applicable:
                    parts.append(_format_kit({**kit, "steps": steps}))
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
    """Format a single kit YAML into a concise text block for the agent.

    Schema actual: tool_kit/description + pasos order/tool/action/note.
    Los params literales NO se renderizan.
    """
    name = str(kit.get("tool_kit") or kit.get("kit") or "?").upper()
    goal = kit.get("description") or kit.get("goal") or ""
    lines = [f"--- {name} KIT ---"]
    if goal:
        lines.append(f"Goal: {goal}")

    for step in kit.get("steps", []):
        tools = _kit_step_tools(step)
        action = step.get("action", "")
        condition = step.get("condition", "")
        note = step.get("note", "")
        order = step.get("order", "")

        tool_str = ", ".join(tools) if tools else "[razoná]"
        action_str = f" ({action})" if action else ""
        lines.append(f"  {order}. {tool_str}{action_str}")
        if note:
            lines.append(f"     💡 {note}")
        if condition:
            lines.append(f"     ⚠️ Condición: {condition}")

    return "\n".join(lines)


async def _index_project_timeboxed(indexer, events=None) -> None:
    """Indexa el proyecto bajo time-box; vencido, el hilo sigue en background.

    El indexado completo dentro del presupuesto de timeout del paso
    rehén del indexador. Se espera hasta EMBED_INDEX_TIMEBOX_S; si no
    terminó, el paso
    continúa sin contexto de código y el hilo de fondo termina y persiste
    solo (single-flight evita que un retry lo duplique). El progreso viaja
    por events.on_indexing_progress (la GUI lo muestra en vivo).
    """
    from core.config import settings

    loop = asyncio.get_running_loop()

    def _progress(data: dict) -> None:
        if events is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(emit_indexing_progress(events, data), loop)
        except Exception:  # noqa: BLE001 — un loop muerto no mata el indexado
            logger.debug("Progreso de indexado no emitido", exc_info=True)

    # float: un time-box sub-segundo es legítimo (tests, índices casi listos)
    timebox = float(getattr(settings, "embed_index_timebox", 30) or 0)
    try:
        if timebox > 0:
            await asyncio.wait_for(
                asyncio.to_thread(indexer.index_project, progress_callback=_progress),
                timeout=timebox,
            )
        else:
            await asyncio.to_thread(indexer.index_project, progress_callback=_progress)
    except TimeoutError:
        logger.warning(
            "Indexado excedió time-box de %ss — sigue en background; "
            "el paso continúa sin contexto de código",
            timebox,
        )


async def _build_extra_context(
    task: str,
    project_root: str | None,
    workspace: str,
    events=None,
) -> str:
    """Construye contexto adicional: memoria FAISS + codebase indexado.

    El `extra_context` que manda el caller (p.ej. el bloque `<RUN_STATE>` del
    DSL) NO se mezcla aquí: viaja como capa propia del prompt para no competir
    por el presupuesto de FAISS/código.
    """
    parts: list[str] = []

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
    # Solo con proyecto real: un root '.' resuelve la base al directorio de
    # memoria del workspace (indexado de todo menos código, minutos de
    # embeddings) y el cache cae en el top-level compartido.
    if has_real_project(project_root):
        try:
            indexer = CodebaseIndexer(workspace=workspace, project_root=project_root)
            await _index_project_timeboxed(indexer, events)
            relevant = indexer.find_relevant_code(task, max_results=5)
            if relevant:
                parts.append(f"--- CÓDIGO RELEVANTE DEL PROYECTO ({project_root}) ---\n{relevant}")
                logger.info(
                    "Codebase: %d chars de código relevante inyectados (hasta %d resultados)",
                    len(relevant),
                    5,
                )
        except Exception:
            logger.warning("CodebaseIndexer no disponible, omitiendo.", exc_info=True)

    return "\n\n".join(parts)


def _build_blackboard_context(workspace: str | None) -> str:
    """Snapshot compacto de goals/todos del workspace para el prompt.

    Best-effort: un storage corrupto o inaccesible devuelve "" y el turno
    continúa — el blackboard es contexto, jamás un requisito del run.
    """
    if not workspace:
        return ""
    try:
        from tools.goal_todo import blackboard_context

        return blackboard_context(workspace)
    except Exception:
        logger.warning("blackboard goal/todo no disponible, omitiendo.", exc_info=True)
        return ""


def _system_prompt_layers(
    react_rules: str,
    enriched_context: str,
    profile_context: str,
    skills_kits_context: str,
    plan_mode: bool = False,
    extra_context: str = "",
    blackboard_context: str = "",
) -> list[tuple[str, str, int]]:
    """Capas del system prompt — orden = prioridad.

    kits+skills viven en la capa propia `skills_kits` y ya NO desplazan
    FAISS/código de `contexto_proyecto`. plan_mode va justo después de las
    reglas: si el prompt está ajustado, la notificación que explica las
    denegaciones no puede ser la primera capa recortada.

    `extra_context` (el `<RUN_STATE>` del DSL) y `blackboard_context`
    (goals/todo) tienen capa propia: diluirlos en `contexto_proyecto` los
    hacía competir con FAISS/código y los recortaba primero.
    """
    layers = [
        ("reglas_react", react_rules, 1200),
        ("contexto_proyecto", enriched_context or "", 800),
        ("perfil_usuario", profile_context or "", 400),
        ("skills_kits", skills_kits_context or "", 700),
    ]
    run_state_layer = ("run_state", extra_context or "", 800)
    blackboard_layer = ("blackboard", blackboard_context or "", 350)
    insert_at = 1
    if plan_mode:
        layers.insert(insert_at, ("plan_mode", _PLAN_MODE_RULES, 400))
        insert_at += 1
    if extra_context.strip():
        layers.insert(insert_at, run_state_layer)
        insert_at += 1
    if blackboard_context.strip():
        layers.insert(insert_at, blackboard_layer)
    return layers


def _bot_memory_snapshot(bot_context: dict) -> str:
    """Snapshot congelado de memoria del bot; nunca rompe el turno."""
    try:
        from core.bots_memory import DEFAULT_BUDGET_CHARS
        from core.bots_memory import snapshot as memory_snapshot

        return memory_snapshot(
            bot_context["slug"],
            workspace=bot_context.get("workspace"),
            max_chars=DEFAULT_MEMORY_SNAPSHOT_CHARS or DEFAULT_BUDGET_CHARS,
        )
    except Exception as e:
        logger.warning("memoria de bot indisponible (turno continúa): %s", e)
        return ""


def _bot_skills_bootstrap(bot_context: dict) -> str:
    """Bootstrap <SKILLS> limitado a la allowlist del bot."""
    try:
        from core.skills import build_bootstrap

        allow = list(bot_context.get("skill_allowlist") or [])
        # el filtro de aprobación y el onboarding viven por workspace:
        # sin él las locales ni siquiera se descubren (y las
        # pendientes/rechazadas quedarían sin filtrar).
        workspace = bot_context.get("workspace")
        return build_bootstrap(workspace, allowlist=allow) if allow else ""
    except Exception as e:
        logger.warning("skills bootstrap de bot falló: %s", e)
        return ""


DEFAULT_MEMORY_SNAPSHOT_CHARS = 6000


async def _bot_protocol_section_async(bot_context: dict) -> str:
    """Sección inter-bot SOLO canónicos con roster≥2; falla suave."""
    try:
        from core.bots import BotsService
        from core.bots_protocol import section_text

        roster = await BotsService.list_bots(include_disabled=False)
        if len(roster) < 2:
            return ""
        return section_text(
            bot_context.get("display_name") or bot_context["slug"],
            roster,
            self_slug=bot_context["slug"],
        )
    except Exception as e:
        logger.warning("sección de protocolo indisponible: %s", e)
        return ""


def _agent_loop_stats(
    status: str,
    agent_type: str | None,
    actions_taken: int = 0,
    files_written: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Payload de stats PARCIALES del agent-loop.

    - tokens_used incluido: el chip de tokens se actualiza en vivo, no solo
      en boundaries del emitter.
    - El status NUNCA debe ser terminal del workflow (`completed` apagaba el
      indicador ●/○ de la GUI a mitad de un run DSL/legacy): usar prefijo
      "Agent ..." (no-terminal según _ACTIVITY_TERMINAL_PREFIXES).
    """
    from tools.orchestrator import get_llm_token_usage

    payload: dict[str, Any] = {
        "status": status,
        "current_agent": agent_type or "agent",
        "actions_taken": actions_taken,
        "files_written": list(files_written or []),
        "tokens_used": get_llm_token_usage(),
    }
    payload.update(extra)
    return payload


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
    skip_task_message: bool = False,
    skills_enabled: bool = False,
    bot_context: dict | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
) -> dict:
    """Punto de entrada público del agent loop.

    Bot Mode: durante TODO el turno fija la sesión canónica activa
    (re-gate de send_to_bot) manteniendo el body intacto en el impl.
    """
    from core.bots_gate import set_active_canonical, set_active_room

    # Salas: bot_context restringido con dm_enabled=False ⇒ sin
    # sesión canónica activa (el re-gate de handle_dm_call rechaza cualquier
    # send_to_bot alucinado desde una respuesta grupal; defensa en profundidad).
    dm_enabled = bool(bot_context.get("dm_enabled", True)) if bot_context else False
    slug = bot_context.get("slug") if bot_context and dm_enabled else None
    room_ctx = None
    if bot_context and not dm_enabled and bot_context.get("room_id"):
        room_ctx = (str(bot_context["room_id"]), str(bot_context.get("slug") or ""))
    # Contador por-llamada (ContextVar): bajo paralelismo el delta global
    # dobla cifras entre hermanos — cada loop cuenta solo SUS llamadas LLM.
    from tools.orchestrator import get_llm_call_tokens

    _llm_tokens_at_start = get_llm_call_tokens()
    with set_active_canonical(slug), set_active_room(room_ctx):
        result = await _execute_agent_loop_impl(
            task=task,
            agent_type=agent_type,
            history=history,
            allowed_tools=allowed_tools,
            project_root=project_root,
            workspace=workspace,
            extra_context=extra_context,
            on_stream_chunk=on_stream_chunk,
            session=session,
            events=events,
            config=config,
            skip_task_message=skip_task_message,
            skills_enabled=skills_enabled,
            bot_context=bot_context,
            model_override=model_override,
            provider_override=provider_override,
        )
    result["tokens_used_call"] = max(0, get_llm_call_tokens() - _llm_tokens_at_start)
    return result


async def _execute_agent_loop_impl(
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
    skip_task_message: bool = False,
    skills_enabled: bool = False,
    bot_context: dict | None = None,
    model_override: str | None = None,
    provider_override: str | None = None,
) -> dict:
    """Ejecuta una tarea usando el Agent Loop con function-calling nativo.

    Improvements:
    - CodebaseIndexer: busca código relevante del proyecto antes de actuar

    Args:
        session: Session unificada (contexto + eventos). Si se provee,
                 los eventos (bash output, etc.) se emiten automáticamente.
        config: Configuración inyectable (opcional). Si es None, usa valores
                por defecto del sistema.
        skip_task_message: en resume el historial ya termina con la respuesta
                del usuario — evita user/user consecutivo no re-añadiendo
                la tarea como mensaje.
        bot_context: dict del bot cuando el turno corre sobre su CHAT CANÓNICO
                (Bot Mode): identidad+SOUL+memoria snapshot+skills.
        model_override: modelo del bot (selección por bot).
        provider_override: proveedor del bot (selección por bot) — el turno
                corre contra SU proveedor sin tocar los roles globales.
    """
    if workspace is None:
        workspace = settings.active_workspace
    if config is None:
        config = AgentLoopConfig.from_settings()

    # plan-mode persistido: un plan activo sobrevive entre steps y runs. Solo
    # se aplica donde el run PUEDE salir (exit_plan_mode en el allowlist): un
    # plan huérfano no puede dejar sin escritura a un preset que no tiene la tool.
    try:
        from tools.goal_todo import plan_get

        activo = bool(plan_get(workspace).get("active"))
        puede_salir = allowed_tools is None or "exit_plan_mode" in allowed_tools
        if activo and not puede_salir:
            logger.warning(
                "plan activo en '%s' pero el run no tiene exit_plan_mode — no se aplica plan-mode",
                workspace,
            )
        plan_mode_active.set(activo and puede_salir)
    except Exception:
        plan_mode_active.set(False)
        logger.debug("plan_get falló; plan-mode arranca inactivo", exc_info=True)

    # allowlist de skills del bot: el listado filtrado no basta — load_skill
    # debe rechazar nombres fuera de la lista del bot.
    _skill_allow = list((bot_context or {}).get("skill_allowlist") or [])
    skill_allowlist_active.set(_skill_allow or None)

    # Read-only a nivel de MOTOR: el perfil lo declara en su YAML y el gate
    # niega las tools mutantes ANTES de ejecutarlas (el SOUL es prosa, no
    # enforcement — el modelo puede desobedecer la prosa).
    read_only_profile = False
    _profile: dict = {}
    if agent_type:
        try:
            from agents.registry import agents_registry

            _profile = agents_registry.get_profile(agent_type) or {}
            read_only_profile = bool(_profile.get("read_only"))
        except Exception:
            read_only_profile = False

    # La superficie VISIBLE de un perfil read-only es su propia lista: el
    # allowlist del workflow autoriza bash/diff/git, pero el gate los deniega
    # después y el modelo gasta iteraciones intentándolos (revisar_spec
    # llegó a 10 iteraciones/139k en un run real).
    if read_only_profile:
        allowed_tools = _read_only_tools(allowed_tools, _profile)

    events = events if events is not None else (session.events if session else None)

    if events:
        await emit_stats(
            events,
            _agent_loop_stats(
                "Agent Loop started",
                agent_type,
                total_tools=len(allowed_tools) if allowed_tools else 0,
            ),
        )

    # 2.1 + 2.5 — Construir contexto enriquecido (FAISS + código)
    enriched_context = await _build_extra_context(task, project_root, workspace, events=events)

    # Inject tool skills and kits into context
    skills_context = _load_tool_skills(allowed_tools)
    kits_context = _load_tool_kits(allowed_tools)
    if kits_context and skills_context:
        skills_kits_context = kits_context + "\n\n" + skills_context
    else:
        skills_kits_context = kits_context or skills_context

    # Bot Mode: si el bot declaró toolset propio, restringe el allowlist
    # recibido a esas tools ANTES del bootstrap de skills — el bootstrap
    # agrega load_skill y el toolset del bot no debe recortarlo.
    if bot_context:
        own = {t for t in (bot_context.get("tool_names") or []) if t}
        if own and allowed_tools is not None:
            allowed_tools = [t for t in allowed_tools if t in own]

    # Skills procedimentales — solo workflows con skills: true.
    # FIX: sin flag activo NO se reasigna allowed_tools (None ⇒ todas las tools).
    if skills_enabled:
        from core.skills import apply_skills_bootstrap

        enriched_context, allowed_tools = apply_skills_bootstrap(
            enriched_context,
            allowed_tools,
            enabled=True,
            workspace=workspace,
            allowlist=(bot_context or {}).get("skill_allowlist"),
        )

    from llm.provider import LLMProvider

    initial_provider = LLMProvider.get_provider_name("agent")
    if bot_context and not provider_override:
        provider_override = (bot_context or {}).get("provider") or None
    if provider_override:
        # Bot Mode: el bot corre con SU proveedor — override honesto por
        # turno, sin tocar los roles globales de otros turnos.
        initial_provider = str(provider_override)
    provider_kind: str = "ollama" if initial_provider == "ollama" else "openai"

    # ── Model capability gate (síntesis 3.2 + 3.6) ──
    if bot_context and not model_override:
        model_override = (bot_context or {}).get("model") or None
    role_config = settings.model_roles.get("agent", settings.model_roles["default"])
    active_model: str = (
        str(role_config.get("ollama_model") or settings.ollama_model)
        if initial_provider == "ollama"
        else str(role_config["model"])
    )
    native_tools_supported = model_supports_tool_calling(active_model)
    if model_override:
        # Bot Mode: el bot corre con SU modelo — override honesto, sin
        # tocar roles globales de otros turnos.
        active_model = str(model_override)
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
                f"⚠️ El modelo '{active_model}' no soporta tool calling nativo — usando modo texto.",
            )

    tools_defs = (
        list(build_tool_definitions(allowed_tools, provider_kind=provider_kind))
        if native_tools_supported
        else []
    )
    if bot_context and bot_context.get("dm_enabled", True):
        # schema de send_to_bot INYECTADO aquí — jamás global.
        # dm_enabled=False (respuestas de sala) ⇒ la tool no existe para el
        # modelo: ni la ve, ni puede llamarla.
        from core.bots_gate import canonical_tool_schema

        bot_dm_schema = await canonical_tool_schema(bot_context["slug"])
        if bot_dm_schema is not None:
            tools_defs.append(bot_dm_schema)
    elif bot_context and bot_context.get("room_id"):
        # Sala con opt-in explícito (room_send_dm del caller): tool puntual
        # con gate propio de sala — TTL/caps del transporte DM intactos.
        from core.bots_gate import room_tool_schema

        bot_dm_schema = await room_tool_schema(str(bot_context.get("room_id")))
        if bot_dm_schema is not None:
            tools_defs.append(bot_dm_schema)

    messages = list(history) if history else []

    # 2.3 — System prompt with ReAct pattern
    # Inject user profile from FAISS memory
    from core.memory.manager import memory as memory_manager

    profile_context = ""
    user_profile = memory_manager.get_user_profile()
    # Cualquier dato real se inyecta, incluido un perfil solo-nombre recién
    # sembrado (el gate anti-stale vive en update_user_profile, no aquí).
    if user_profile and any(user_profile.values()):
        summary = memory_manager.get_user_summary()
        if summary:
            profile_context = f"\n[PERFIL DEL USUARIO]:\n{summary}\n"

    # system prompt por CAPAS con presupuesto — lo fijo sobrevive, lo
    # variable se recorta. Sin esto, un contexto gigante salía completo.
    react_rules = (
        f"Eres un agente de desarrollo de software experto. Trabajas con el patrón ReAct:\n"
        f"1. RAZONA: analiza la tarea y el contexto disponible.\n"
        f"2. ACTÚA: usa las herramientas apropiadas para avanzar.\n"
        f"3. OBSERVA: evalúa el resultado de cada acción.\n"
        f"4. AJUSTA: si el resultado no es el esperado, cambia de estrategia.\n\n"
        f"Workspace: {workspace}\n"
        f"Project root: {project_root or 'N/A'}\n"
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

    from orchestration.prompt_budget import enforce_budget

    hard_cap = max(2000, settings.max_context_tokens // 4)

    if bot_context:
        # Bot Mode: identidad+SOUL+snapshot memoria+
        # skills allowlist SOLO para chats canónicos; epoch verificado al
        # entrar al turno (rebuild ×1 si cambió).
        from core import bots_epoch

        # el stamp persiste side-effect del refresh; el
        # retorno rebuild aún no consume nada — descartado explícito.
        await bots_epoch.refresh_if_stale(bot_context["slug"])
        bot_memory_text = _bot_memory_snapshot(bot_context)
        skills_bootstrap = _bot_skills_bootstrap(bot_context)
        # Salas: sin sección de protocolo (dm_enabled=False) — las reglas de
        # inter-bot no aplican dentro de una respuesta grupal; las reglas de la
        # sala las da _room_prompt en el query.
        protocol_section = ""
        if bot_context.get("dm_enabled", True):
            protocol_section = await _bot_protocol_section_async(bot_context)

        identity_rules = (
            f"Eres @{bot_context['slug']} ({bot_context.get('display_name') or bot_context['slug']}), "
            "un bot con chat eterno propio. Tu persona:\n"
            f"{(bot_context.get('soul_md') or '').strip() or '(sin SOUL definido aún)'}\n\n"
            "Eres persistente: mantén hilo con el historial completo de esta "
            "conversación y sé directo."
        )
        if protocol_section:
            identity_rules += "\n\n" + protocol_section

        layers = [
            ("identidad_bot", identity_rules, 2500),
            ("memoria_bot", bot_memory_text, 1200),
            ("skills_kits", skills_bootstrap, 700),
        ]
        if plan_mode_active.get():
            layers.insert(1, ("plan_mode", _PLAN_MODE_RULES, 400))
    else:
        # Blackboard durable (plan/goals/todo): sus tools lo escribían y nadie
        # lo leía. Solo en el branch de agente — el prompt de bot tiene sus
        # capas propias (identidad/memoria/skills) y no lo consume.
        blackboard_context = _build_blackboard_context(workspace)
        layers = _system_prompt_layers(
            react_rules=react_rules,
            enriched_context=enriched_context or "",
            profile_context=profile_context or "",
            skills_kits_context=skills_kits_context,
            plan_mode=plan_mode_active.get(),
            extra_context=extra_context,
            blackboard_context=blackboard_context,
        )

    system_msg = enforce_budget(layers, hard_cap=hard_cap)

    messages.insert(0, {"role": "system", "content": system_msg})
    if not skip_task_message:
        messages.append({"role": "user", "content": task})
    else:
        logger.info("M5: resume — task original no re-añadida (historial ya trae respuesta)")

    actions_taken = 0
    final_result = ""
    reasoning = ""  # capturado del stream, expuesto en el retorno
    files_written: list[str] = []
    consecutive_stalls = 0
    repeat_tracker: dict[str, int] = {}
    tool_call_repairs = 0
    iteration = 0

    hit_limit = False

    # Transcript event-sourced (absorción deepseek): registro append-only del
    # turno — reconstrucción fina post-crash + observabilidad de prefijo.
    from core.transcript import TranscriptLog

    transcript = TranscriptLog.start(
        workspace=workspace,
        agent_type=agent_type or "agent",
        task=task,
        provider=initial_provider,
        model=active_model,
    )
    prev_step_state: dict | None = None

    # starvation de streaming (por-RUN, no por-iteración): un reintento por
    # llamada con max_tokens duplicado — paridad con el detector de call();
    # el override viaja a call_stream vía kwargs
    from core.config import settings as _settings

    _base_stream_max_tokens = _settings.model_roles.get("agent", {}).get("max_tokens") or 4096
    _stream_max_tokens: int | None = None
    _starve_retried = False
    _rate_retried = False
    _phantom_retried = False  # finish='tool_calls' sin deltas (una por run)
    _empty_final_nudged = False  # re-prompt del final vacío (una por run)
    # Evidencia determinista de completitud: el verde del ÚLTIMO lote de tools
    # sobrevive entre iteraciones — alimenta el rescate del final vacío y el
    # cierre por evidencia en el tope de iteraciones.
    verification_green = False

    for iteration in range(1, config.max_agent_iterations + 1):
        # Cancelación: el loop consulta ctx.cancelled entre iteraciones.
        if session is not None and getattr(session, "is_cancelled", False):
            logger.info("🛑 Agent loop cancelled by user")
            transcript.note("run/cancelled", iterations=iteration - 1)
            return {
                "status": "cancelled",
                "result": "Cancelado por el usuario.",
                "actions_taken": actions_taken,
                "iterations": iteration - 1,
                "files_written": files_written,
            }

        # Presupuesto agotado: cortar ANTES de otra llamada LLM. Las tools ya
        # se bloquean solas al cruzar el presupuesto; sin este corte el loop
        # seguía facturando llamadas completas hasta max_agent_iterations con
        # el presupuesto en rojo (126k reales para un hola-mundo).
        from tools.orchestrator import get_llm_token_usage, get_token_budget_max

        budget_max = get_token_budget_max()
        budget_used = get_llm_token_usage()
        if iteration > 1 and budget_max > 0 and budget_used >= budget_max:
            logger.warning(
                "🛑 Presupuesto de tokens agotado (%d/%d) — agent-loop se detiene",
                budget_used,
                budget_max,
            )
            transcript.note("run/budget_exhausted", iterations=iteration - 1, tokens=budget_used)
            if events:
                await emit_stats(
                    events,
                    _agent_loop_stats(
                        "⏹ Presupuesto de tokens agotado",
                        agent_type,
                        actions_taken=actions_taken,
                        files_written=files_written,
                    ),
                )
            return {
                "status": "stalled",
                "result": (
                    f"⏹ Presupuesto de tokens del workflow agotado "
                    f"({budget_used}/{budget_max}). El trabajo se detuvo antes "
                    f"de agotar las iteraciones y el estado queda persistido; "
                    f"re-lanza la tarea o sube TOOL_MAX_TOKENS_PER_WORKFLOW."
                ),
                "actions_taken": actions_taken,
                "iterations": iteration - 1,
                "files_written": files_written,
            }

        if events and iteration > 1:
            await emit_stats(
                events,
                _agent_loop_stats(
                    f"Agent iteration {iteration}/{config.max_agent_iterations}",
                    agent_type,
                    actions_taken=actions_taken,
                    files_written=files_written,
                ),
            )

        # 2.2 — Comprimir historial si el contexto crece demasiado
        estimated_tokens = ContextManager.estimate_tokens(messages)
        # El techo de compresión se alinea con el presupuesto real del run:
        # con el techo de contexto (128k) el recorte arrivaba a ~89k — por
        # encima del budget de 80k, que moría primero.
        max_ctx_tokens = ContextManager._max_tokens()
        from tools.orchestrator import get_token_budget_max as _budget_ceiling_fn

        budget_ceiling = _budget_ceiling_fn()
        max_tokens = min(max_ctx_tokens, budget_ceiling) if budget_ceiling > 0 else max_ctx_tokens
        _comprimir, target = _compression_plan(estimated_tokens, config, max_tokens)
        if _comprimir:
            logger.info(
                "Comprimiendo contexto: %d tokens -> objetivo %d",
                estimated_tokens,
                target,
            )
            from core.cache_manager import cache_manager

            # Filter orphaned tool messages (missing association field causes provider errors)
            messages = [m for m in messages if m.get("role") != "tool" or has_tool_association(m)]

            messages = cache_manager.stabilize_messages(messages, max_tokens=target)

            # tras comprimir, podar pares assistant(tool_calls)↔tool
            # desemparejados — el corte puede partir un par por la mitad.
            messages = _sanitize_tool_message_pairs(messages)

        use_native_tools = len(tools_defs) > 0

        # Reserva atómica de la llamada LLM: cierra el TOCTOU de pasos
        # paralelos — todos pasaban el chequeo de cabecera a 179.x y todos
        # despachaban (el overshoot del presupuesto venía de ahí). El uso
        # REAL lo carga el tracking existente; esta reserva solo acota el
        # overcommit con el input estimado y se devuelve tras la respuesta.
        # Una reserva filtrada por excepción solo vuelve el presupuesto
        # MÁS conservador (stall precoz honesto), nunca más laxo.
        from tools.orchestrator import _token_budget_ctx

        _budget_state = _token_budget_ctx.get()
        _llm_reserve = 0
        if _budget_state is not None and budget_max > 0:
            _llm_reserve = max(1, estimated_tokens)
            if not _budget_state.reserve(_llm_reserve):
                logger.warning(
                    "🛑 Presupuesto sin holgura para la llamada LLM "
                    "(%d/%d, reserva %d) — agent-loop se detiene",
                    _budget_state.total,
                    _budget_state.max_budget,
                    _llm_reserve,
                )
                transcript.note(
                    "run/budget_exhausted", iterations=iteration - 1, tokens=_budget_state.total
                )
                if events:
                    await emit_stats(
                        events,
                        _agent_loop_stats(
                            "⏹ Presupuesto de tokens agotado",
                            agent_type,
                            actions_taken=actions_taken,
                            files_written=files_written,
                        ),
                    )
                return {
                    "status": "stalled",
                    "result": (
                        f"⏹ Presupuesto de tokens del workflow agotado "
                        f"({_budget_state.total}/{_budget_state.max_budget}). "
                        "El trabajo se detuvo antes de despachar la llamada."
                    ),
                    "actions_taken": actions_taken,
                    "iterations": iteration - 1,
                    "files_written": files_written,
                }

        if iteration > 1:
            _podados = _prune_old_tool_results(messages)
            if _podados:
                logger.info("Poda de contexto: %d tool results antiguos truncados", _podados)

        # Transcript: registro del request con verificación de prefijo estable
        prev_step_state = transcript.step_request(iteration, messages, prev_step_state)

        if use_native_tools and on_stream_chunk:
            # ── Streaming con function-calling nativo ──
            stream = models.call_stream(
                messages=messages,
                role="agent",
                tools=tools_defs,
                tool_choice="auto",
                workspace=workspace,
                **({"max_tokens": _stream_max_tokens} if _stream_max_tokens else {}),
            )
            (
                streamed_text,
                streamed_tool_calls,
                stream_finish,
                reasoning,
                _stream_notice,
                _stream_usage,
            ) = await _accumulate_stream(stream, on_stream_chunk, workspace=workspace)
            if _llm_reserve and _budget_state is not None:
                _budget_state.refund(_llm_reserve)
                _llm_reserve = 0
            if _stream_notice:
                if not _rate_retried:
                    # un reintento inmediato de la MISMA iteración: la espera
                    # del limiter ya consumió 30s y la demanda puede haber
                    # bajado; el aviso jamás es la respuesta del agente.
                    # El fallback de streaming también viaja marcado con
                    # system_notice (rate limit O proveedor caído): el texto
                    # de error jamás es la respuesta del modelo.
                    _rate_retried = True
                    logger.warning(
                        "Aviso del controller en iteración %d (rate limit o fallo "
                        "de proveedor) — reintentando la iteración",
                        iteration,
                    )
                    continue
                raise LLMDeniedError(
                    "El controller no entregó una respuesta del modelo tras "
                    "reintento (rate limit o fallo de proveedor)"
                )
            if stream_finish == "length" and not streamed_tool_calls and not _starve_retried:
                # paridad con call(): el thinking model agotó max_tokens en
                # reasoning_content. Con content VACÍO (firma deepseek) o
                # PARCIAL cortado a media palabra (firma glm — el caso de los
                # mensajes truncados en GUI), el proveedor declaró la
                # respuesta incompleta: reintentar la MISMA iteración con el
                # doble de presupuesto en vez de entregar el fragmento.
                _starve_retried = True
                _stream_max_tokens = _base_stream_max_tokens * 2
                logger.warning(
                    "Respuesta %s por agotamiento de tokens (reasoning, "
                    "streaming) en iteración %d — reintentando con "
                    "max_tokens=%d",
                    "vacía" if not streamed_text.strip() else "truncada",
                    iteration,
                    _stream_max_tokens,
                )
                transcript.note(
                    "llm/starved_retry", iteration=iteration, max_tokens=_stream_max_tokens
                )
                continue
            if stream_finish == "tool_calls" and not streamed_tool_calls and not _phantom_retried:
                # El proveedor declaró el turno terminado en tool calls pero
                # NO llegó ningún delta. Con 0 texto es un final vacío; con
                # texto es prosa huérfana que anuncia una acción que nunca
                # ocurre — ninguna de las dos es una respuesta válida.
                # Reintentar la MISMA iteración, una vez por run.
                _phantom_retried = True
                logger.warning(
                    "finish='tool_calls' sin deltas entregados en iteración %d "
                    "— reintentando (el proveedor perdió la tool call)",
                    iteration,
                )
                transcript.note("llm/phantom_tool_calls", iteration=iteration)
                continue
            transcript.step_response(
                iteration,
                bool(streamed_tool_calls),
                len(streamed_text or ""),
                finish_reason=stream_finish,
                usage=_stream_usage,
            )

            if streamed_tool_calls:
                # construir parsed ANTES de persistir assistant_msg — si
                # ninguna tool call tiene nombre, no queda huérfano en historial.
                parsed = []
                for tc in streamed_tool_calls:
                    tool_name = tc["function"]["name"]
                    if not tool_name:
                        continue
                    args = normalize_arguments(tc["function"]["arguments"])
                    parsed.append({"name": tool_name, "id": tc["id"], "arguments": args})

                if not parsed:
                    continue

                # Build assistant_msg from accumulated stream tool_calls
                # NOTE: reasoning_content is NOT re-sent to the model —
                # thinking models amplify their own reasoning chains when
                # they see them in history, growing context and latency.
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": streamed_text or None,
                    "tool_calls": [],
                }
                for tc in streamed_tool_calls:
                    if not tc["function"]["name"]:
                        continue  # solo persistir calls ejecutables
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
                    allowed_tools=allowed_tools,
                    read_only=read_only_profile,
                )
                if isinstance(result, dict):
                    transcript.note("run/early_exit", iteration=iteration, reason="stalled")
                    return result
                (actions_taken, _, files_written, consecutive_stalls, early, verification_green) = (
                    result
                )
                if early:
                    transcript.note(
                        "run/early_exit",
                        iteration=iteration,
                        reason=(
                            str(early.get("status", "early"))
                            if isinstance(early, dict)
                            else "early"
                        ),
                    )
                    return early
                continue

            # No tool calls — final response via streaming
            final_result = streamed_text.strip()
            if not final_result and verification_green:
                # turno degenerado (sin content ni tool_calls) tras una
                # verificación en verde: la evidencia ES el resultado
                final_result = (
                    "✅ Verificación en verde confirmada por el motor — "
                    "el modelo no redactó resumen."
                )
            if not final_result and not verification_green and not _empty_final_nudged:
                # Final vacío que ni el starve-retry (length) ni el fantasma
                # (tool_calls) atraparon: re-prompt una vez por run pidiendo
                # el cierre explícito.
                _empty_final_nudged = True
                messages.append({"role": "user", "content": _NUDGE_FINAL_VACIO})
                logger.warning(
                    "Final vacío del modelo en iteración %d — re-prompt para cerrar el turno",
                    iteration,
                )
                continue
            final_result = clean_llm_response(final_result)
            final_result = _nota_truncado(final_result, stream_finish)
            break

        elif use_native_tools:
            response = await models.call(
                messages=messages,
                role="agent",
                tools=tools_defs,
                tool_choice="auto",
                workspace=workspace,
            )
            if _llm_reserve and _budget_state is not None:
                _budget_state.refund(_llm_reserve)
                _llm_reserve = 0
            _resp_error = getattr(response, "error", None)
            # isinstance: un mock sin el campo devuelve attr truthy no-string
            if isinstance(_resp_error, str) and _resp_error:
                raise LLMDeniedError(_resp_error)
            tool_calls = tool_calls_from_response(response)
            transcript.step_response(
                iteration,
                bool(tool_calls),
                len(
                    getattr(
                        getattr(getattr(response, "choices", [None])[0], "message", None),
                        "content",
                        "",
                    )
                    or ""
                ),
            )

            if tool_calls:
                # Detect provider from the raw tool-call format
                provider_kind = detect_provider_from_raw_tool_call(tool_calls[0])

                # construir parsed y assistant_msg EN UN SOLO PASO —
                # mismas tool calls ejecutables, mismos ids; si ninguna tiene
                # nombre no se persiste nada en el historial.
                parsed = []
                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [],
                }
                _msg_reasoning = getattr(
                    getattr(response.choices[0], "message", None), "reasoning_content", None
                )
                if _msg_reasoning:
                    reasoning = _msg_reasoning

                for i, tc in enumerate(tool_calls):
                    func = tc.function if hasattr(tc, "function") else tc.get("function", {})
                    tool_name = func.name if hasattr(func, "name") else func.get("name", "")
                    if not tool_name:
                        continue  # solo calls ejecutables
                    call_id = getattr(tc, "id", f"call_{iteration}_{i}")
                    raw_args = (
                        func.arguments if hasattr(func, "arguments") else func.get("arguments", {})
                    )
                    log_raw_tool_args(f"non-streaming:{tool_name}", raw_args)
                    try:
                        call_args = (
                            func.arguments
                            if hasattr(func, "arguments")
                            else json.dumps(func.get("arguments", {}))
                        )
                    except Exception:
                        logger.warning("Error serializando argumentos de tool call", exc_info=True)
                        call_args = "{}"
                    arguments = normalize_arguments(raw_args)
                    parsed.append({"name": tool_name, "id": call_id, "arguments": arguments})
                    assistant_msg["tool_calls"].append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": call_args,
                            },
                        }
                    )

                if not parsed:
                    continue

                # NOTE: reasoning_content is NOT re-sent to the model —
                # thinking models amplify their own reasoning chains when
                # they see them in history, growing context and latency.
                messages.append(assistant_msg)

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
                        final_result = content
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
                    allowed_tools=allowed_tools,
                    read_only=read_only_profile,
                )
                if isinstance(result, dict):
                    transcript.note("run/early_exit", iteration=iteration, reason="stalled")
                    return result
                (actions_taken, _, files_written, consecutive_stalls, early, verification_green) = (
                    result
                )
                if early:
                    transcript.note(
                        "run/early_exit",
                        iteration=iteration,
                        reason=(
                            str(early.get("status", "early"))
                            if isinstance(early, dict)
                            else "early"
                        ),
                    )
                    return early
                continue

            # No tool calls — LLM dio respuesta final
            choice = response.choices[0] if hasattr(response, "choices") else None
            content = ""
            if choice and hasattr(choice, "message"):
                content = choice.message.content or ""
            if not str(content).strip() and not verification_green and not _empty_final_nudged:
                # Paridad del re-prompt del final vacío en la ruta nativa
                _empty_final_nudged = True
                messages.append({"role": "user", "content": _NUDGE_FINAL_VACIO})
                logger.warning(
                    "Final vacío del modelo en iteración %d — re-prompt para cerrar el turno",
                    iteration,
                )
                continue
            final_result = content
            if not final_result.strip() and verification_green:
                final_result = (
                    "✅ Verificación en verde confirmada por el motor — "
                    "el modelo no redactó resumen."
                )
            final_result = clean_llm_response(final_result)
            final_result = _nota_truncado(final_result, getattr(choice, "finish_reason", None))
            break

        else:
            # ── Modo texto: sin tools nativas (modelo sin soporte o lista vacía) ──
            response = await models.call(messages=messages, role="agent", workspace=workspace)
            if _llm_reserve and _budget_state is not None:
                _budget_state.refund(_llm_reserve)
                _llm_reserve = 0
            _resp_error = getattr(response, "error", None)
            # isinstance: un mock sin el campo devuelve attr truthy no-string
            if isinstance(_resp_error, str) and _resp_error:
                raise LLMDeniedError(_resp_error)
            choice = response.choices[0] if hasattr(response, "choices") else None
            content = ""
            if choice and hasattr(choice, "message"):
                content = choice.message.content or ""
            transcript.step_response(iteration, False, len(content))
            final_result = content
            final_result = clean_llm_response(final_result)
            final_result = _nota_truncado(final_result, getattr(choice, "finish_reason", None))
            break
    else:
        hit_limit = True
        if verification_green:
            # evidencia determinista > declaración del modelo: la última
            # verificación está en verde — el trabajo está hecho aunque el
            # modelo no haya declarado completado (el patrón 'stalled con
            # tests verdes' documentado en 4 sesiones)
            hit_limit = False
            final_result = (
                "✅ Verificación en verde confirmada por el motor "
                "(test_runner sin fallos) — completado por evidencia, el "
                "modelo no declaró completado dentro del límite de "
                f"iteraciones ({actions_taken} acciones, "
                f"{len(files_written)} archivos)."
            )
        else:
            final_result = (
                f"⚠️ Límite de {config.max_agent_iterations} iteraciones alcanzado.\n"
                f"Acciones ejecutadas: {actions_taken}. "
                f"Archivos modificados: {len(files_written)}.\n"
                "La tarea podría necesitar descomponerse en partes más pequeñas."
            )

    if events:
        # "Agent listo" es NO-terminal: si fuera "completed", el indicador
        # ●/○ de la GUI se apagaría a mitad del workflow (es un parcial, el
        # workflow puede seguir con otra subtarea/iteración).
        await emit_stats(
            events,
            _agent_loop_stats(
                f"Agent listo ({iteration} iter)",
                agent_type,
                actions_taken=actions_taken,
                files_written=files_written,
                iterations=iteration,
            ),
        )

    # Transcript: cierre normal del turno (los caminos no-gráciles quedan
    # con su marcador terminal propio o como run abierto para diagnóstico).
    transcript.append(
        "run/end",
        status="stalled" if hit_limit else "completed",
        iterations=iteration,
        actions_taken=actions_taken,
        files_written=len(files_written),
    )

    return {
        # Honestidad: alcanzar el límite de iteraciones sin terminar
        # NO se pinta como completed.
        "status": "stalled" if hit_limit else "completed",
        "result": final_result,
        "actions_taken": actions_taken,
        "iterations": iteration,
        "files_written": files_written,
        # ': el reasoning ya no se descarta — disponible para debug/UI
        **({"reasoning": reasoning} if reasoning else {}),
    }
