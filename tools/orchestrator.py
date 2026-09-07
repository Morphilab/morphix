# tools/orchestrator.py
"""
Tool Orchestrator Avanzado - Versión Final Robusta (Prioridad 3)
- Retries y backoff alineados con models_controller
- Mejor manejo de errores y mensajes amigables
- Token budget aislado por workflow vía contextvars (thread/async-safe)
"""

import asyncio
import contextvars
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core.config import settings
from core.feature_flags import kairos
from core.hooks_registry import HookContext, hooks_registry
from core.security.undercover_mode import undercover
from core.token_counter import get_encoding
from tools.registry import tools_registry

logger = logging.getLogger(__name__)


# Token budget — estado mutable compartido por referencia entre el task raíz
# del workflow y sus subtasks (asyncio.create_task/gather copian el ContextVar,
# pero el OBJETO es el mismo → las mutaciones de los hijos son visibles para el
# raíz). Esto hace el presupuesto determinista en cualquier topología
# (secuencial como development, o gather como coordinated).
class _TokenBudgetState:
    __slots__ = ("total", "max_budget", "warned")

    def __init__(self, max_budget: int) -> None:
        self.total = 0
        self.max_budget = max_budget
        self.warned = False

    def reserve(self, estimated: int) -> bool:
        """Reserva tokens de forma atómica (sync, sin awaits).

        Cierra el TOCTOU del check-then-charge: bajo ejecución paralela
        (gather) dos tareas podían leer el mismo total, pasar ambas el
        chequeo y cargar después, excediendo el presupuesto. Al reservar en
        el mismo paso sincrónico, el loop no puede entrelazar las tareas.

        Retorna False si la reserva excedería el presupuesto (sin cargar).
        """
        if self.total + estimated > self.max_budget:
            return False
        self.total += estimated
        return True

    def refund(self, estimated: int) -> None:
        """Devuelve la reserva cuando la tool falló definitivamente —
        sin esto, fallos deterministas queman presupuesto y provocan
        token_budget_exceeded espurios en subtareas posteriores."""
        self.total = max(0, self.total - max(0, estimated))

    def reconcile(self, estimated: int, actual: int) -> None:
        """Reemplaza la reserva por el gasto real de tokens.

        Bajo paralelismo el total puede exceder max por la varianza entre
        estimación y gasto real de cada tool (best-effort, acotado por
        tool); la defensa principal es reserve() contra el overcommit.
        """
        self.total += actual - estimated


_token_budget_ctx: contextvars.ContextVar[_TokenBudgetState | None] = contextvars.ContextVar(
    "tool_token_budget", default=None
)

# callback de aprobación por-run. Reemplaza el atributo de
# clase como fuente primaria: cada task de workflow setea el suyo, de modo que
# dos runs concurrentes no se pisan el callback ni se lo limpian mutuamente.
_approval_cb_ctx: contextvars.ContextVar[
    Callable[[str, dict[str, Any]], Awaitable[bool]] | None
] = contextvars.ContextVar("tool_approval_cb", default=None)


def set_approval_callback(cb: Callable[[str, dict[str, Any]], Awaitable[bool]] | None) -> None:
    """Setea el callback de aprobación para el task actual (ContextVar)."""
    _approval_cb_ctx.set(cb)


def clear_approval_callback() -> None:
    """Limpia el callback de aprobación del task actual."""
    _approval_cb_ctx.set(None)


def get_approval_callback() -> Callable[[str, dict[str, Any]], Awaitable[bool]] | None:
    """Callback de aprobación efectivo: ContextVar si está seteado, sino el
    atributo de clase (fallback legacy para MCP standalone y tests)."""
    cb = _approval_cb_ctx.get()
    if cb is not None:
        return cb
    return ToolOrchestrator.on_approval_required


# token del run actual para detección de colisión de archivos.
_file_write_run_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "file_write_run", default=None
)


def set_file_write_run(token: int | None) -> None:
    """Vincula el task actual al run `token` para colisión de escrituras."""
    _file_write_run_ctx.set(token)


def get_file_write_run() -> int | None:
    """Token de run del task actual (None si no hay workflow activo)."""
    return _file_write_run_ctx.get()


class ToolOrchestrator:
    MAX_RETRIES = settings.tool_max_retries
    BACKOFF_BASE = settings.tool_backoff_base
    MAX_TOKENS_PER_WORKFLOW = settings.tool_max_tokens_per_workflow
    ENABLE_TOKEN_BUDGET = settings.tool_enable_token_budget

    # Tools/actions that require explicit user approval
    # git_manager.push no existe como acción; re-añadir
    # solo si algún día se implementa push.
    DANGEROUS_ACTIONS: set[str] = {
        "bash_manager",
        "code_exec",
        "file_manager.delete",
        # aplicar un diff es escritura de archivo → exige aprobación.
        # create/read siguen fluidos (solo generan/leen el diff).
        "diff_editor.apply",
    }
    # NOTA: `git_manager.commit` salió de este set — commit es
    # reversible y sin efecto externo (push ni existe). La decisión de CUÁNDO
    # committear es del agente (tool a petición) o del workflow (commit_after);
    # el auto-commit implícito del sistema fue eliminado (interfería en flujos
    # largos con commits de estados intermedios sin valor semántico).

    # Global approval callback — set by UI layer (CLI or GUI)
    on_approval_required: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None

    @staticmethod
    async def execute_tool(
        tool_name: str,
        parameters: dict[str, Any],
        role: str = "agent",
        max_tokens: int | None = None,
        workspace: str | None = None,
        session_id: str | None = None,
        skip_budget: bool = False,
    ) -> dict[str, Any]:
        if workspace is None:
            workspace = settings.active_workspace
        hooks_on = settings.hooks_enabled
        max_retries = settings.tool_max_retries
        backoff_base = settings.tool_backoff_base
        if not settings.tools_enabled:
            if hooks_on:
                await hooks_registry.dispatch(
                    "on_tools_disabled",
                    HookContext(
                        hook_point="on_tools_disabled",
                        tool_name=tool_name,
                        parameters=parameters,
                        role=role,
                        workspace=workspace,
                        session_id=session_id,
                    ),
                )
            return {
                "success": False,
                "error": "tools_disabled",
                "output": "❌ Herramientas desactivadas por configuración del sistema",
            }

        tool = tools_registry.get_tool(tool_name)
        if not tool:
            return {
                "success": False,
                "error": "tool_not_found",
                "output": f"❌ La herramienta '{tool_name}' no existe",
            }

        if not ToolOrchestrator._check_permissions(tool_name, role):
            if hooks_on:
                await hooks_registry.dispatch(
                    "on_permission_denied",
                    HookContext(
                        hook_point="on_permission_denied",
                        tool_name=tool_name,
                        parameters=parameters,
                        role=role,
                        workspace=workspace,
                        session_id=session_id,
                    ),
                )
            return {
                "success": False,
                "error": "permission_denied",
                "output": f"❌ Permiso denegado para usar '{tool_name}'",
            }

        # Interactive approval for dangerous operations
        # Si los arguments crudos llegan SIN 'action' (p.ej. MCP no
        # valida required del schema), el key sin acción no matchearía el set
        # y diff_editor ejecutaría su rama apply (default interno) sin
        # aprobación → normalizar conservadoramente a la acción de escritura.
        raw_action = parameters.get("action")
        if raw_action:
            action_key = f"{tool_name}.{raw_action}"
        elif tool_name == "diff_editor":
            action_key = "diff_editor.apply"
        else:
            action_key = tool_name
        is_dangerous = (
            tool_name in ToolOrchestrator.DANGEROUS_ACTIONS
            or action_key in ToolOrchestrator.DANGEROUS_ACTIONS
        )
        approval_cb = get_approval_callback()
        if is_dangerous and approval_cb is None:
            # sin callback (MCP standalone/headless) NUNCA se ejecuta
            # una acción peligrosa en silencio; opt-in explícito para CI.
            import os as _os

            if _os.getenv("ALLOW_UNATTENDED_DANGEROUS", "").lower() != "true":
                return {
                    "success": False,
                    "error": "approval_unavailable",
                    "output": (
                        f"❌ '{tool_name}' es peligrosa y este proceso no tiene UI "
                        "de aprobación (MCP standalone). Configura un callback o "
                        "exporta ALLOW_UNATTENDED_DANGEROUS=true si aceptas el riesgo."
                    ),
                }
        if approval_cb is not None and is_dangerous:
            approved = await approval_cb(tool_name, parameters)
            if not approved:
                return {
                    "success": False,
                    "error": "approval_denied",
                    "output": f"❌ Operation '{tool_name}' was denied by user.",
                }

        # Token budget — estado mutable compartido entre tasks (ver _TokenBudgetState)
        estimated = ToolOrchestrator._estimate_tokens(parameters)
        budget_state = _token_budget_ctx.get()
        budget_reserved = False
        if ToolOrchestrator.ENABLE_TOKEN_BUDGET and budget_state is not None and not skip_budget:
            if not budget_state.reserve(estimated):
                if hooks_on:
                    await hooks_registry.dispatch(
                        "on_token_budget_exceeded",
                        HookContext(
                            hook_point="on_token_budget_exceeded",
                            tool_name=tool_name,
                            parameters=parameters,
                            role=role,
                            workspace=workspace,
                            session_id=session_id,
                        ),
                    )
                return {
                    "success": False,
                    "error": "token_budget_exceeded",
                    "output": (
                        f"❌ Presupuesto de tokens excedido "
                        f"({budget_state.total + estimated}/{budget_state.max_budget})"
                    ),
                }
            budget_reserved = True

        start_time = time.time()
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                if hooks_on:
                    await hooks_registry.dispatch(
                        "on_before_tool",
                        HookContext(
                            hook_point="on_before_tool",
                            tool_name=tool_name,
                            parameters=parameters,
                            role=role,
                            attempt=attempt,
                            workspace=workspace,
                            session_id=session_id,
                        ),
                    )

                with undercover:
                    result = (
                        await tool(**parameters)
                        if asyncio.iscoroutinefunction(tool)
                        else await asyncio.to_thread(tool, **parameters)
                    )

                duration = time.time() - start_time
                # Solo las tools que REPORTAN tokens_used cargan gasto propio;
                # para el resto, el coste real ya se contó como completion del
                # LLM (add_llm_token_usage) y facturar la estimación de
                # parámetros sería doble conteo. La reserva transitoria sigue
                # acotando el overcommit concurrente.
                reported_tokens = result.get("tokens_used") if isinstance(result, dict) else None
                charged_tokens = reported_tokens if reported_tokens is not None else 0
                actual_tokens = reported_tokens if reported_tokens is not None else estimated

                # Check internal tool success before logging
                internal_ok = (
                    result.get("success", True)
                    if isinstance(result, dict)
                    else not str(result).startswith("❌")
                )
                if not internal_ok:
                    error_msg = (
                        result.get("output", result.get("text", "unknown error"))
                        if isinstance(result, dict)
                        else str(result)
                    )
                    # Fast-fail: skip retry for unrecoverable failures
                    # (file/path, security blocks, budget, git/diff state)
                    fast_fail_keywords = [
                        "no se encontró",
                        "no encontrado",
                        "not found",
                        "file not found",
                        "fuera del workspace",
                        "bloqueado por seguridad",
                        "blocked for security",
                        "blocked segment",
                        "presupuesto de tokens excedido",
                        "presupuesto excedido",
                        "token_budget_exceeded",
                        "no hay un repositorio git",
                        "no hay repositorio git",
                        "se revirtió el cambio",
                        "no se pudo aplicar el diff",
                    ]
                    if any(kw in str(error_msg).lower() for kw in fast_fail_keywords):
                        logger.warning(
                            f"Tool {tool_name} failed (fast-fail: file/path error) — no retry"
                        )
                        if budget_reserved and budget_state is not None:
                            budget_state.refund(estimated)
                        return {
                            "success": False,
                            "error": "tool_reported_failure",
                            "output": f"❌ La herramienta '{tool_name}' reportó fallo: {str(error_msg)[:300]}",
                            "tool": tool_name,
                        }
                    # Fast-fail: skip retry for deterministic test failures
                    if tool_name == "test_runner" and isinstance(result, dict):
                        if result.get("failed_count", 0) > 0 or result.get("error_count", 0) > 0:
                            logger.warning(
                                f"test_runner: {result['failed_count']} failures, "
                                f"{result['error_count']} errors — no retry (tests are deterministic)"
                            )
                            if budget_reserved and budget_state is not None:
                                budget_state.refund(estimated)
                            return {
                                "success": False,
                                "error": "tests_failed",
                                "output": str(error_msg)[:300],
                                "tool": tool_name,
                            }
                    logger.warning(
                        f"Tool {tool_name} reported failure (attempt {attempt}): {str(error_msg)[:200]}"
                    )
                    if attempt < max_retries:
                        delay = backoff_base**attempt + random.uniform(0, 0.5)
                        await asyncio.sleep(delay)
                        continue
                    if budget_reserved and budget_state is not None:
                        budget_state.refund(estimated)
                    return {
                        "success": False,
                        "error": "tool_reported_failure",
                        "output": f"❌ La herramienta '{tool_name}' reportó fallo: {str(error_msg)[:300]}",
                        "tool": tool_name,
                    }

                # Verificar presupuesto con tokens reales antes de contabilizar
                if budget_reserved and budget_state is not None:
                    projected = budget_state.total + (charged_tokens - estimated)
                    if projected > budget_state.max_budget:
                        budget_state.reconcile(estimated, 0)  # reembolsa la reserva
                        logger.warning(
                            f"Tool {tool_name} excede presupuesto con tokens reales "
                            f"({projected}/{budget_state.max_budget}). Resultado descartado."
                        )
                        return {
                            "success": False,
                            "error": "token_budget_exceeded",
                            # la tool ya ejecutó efectos (archivos/
                            # comandos); el agente debe saberlo antes de
                            # reintentar a ciegas.
                            "side_effects_executed": True,
                            "output": (
                                f"⚠️ Presupuesto excedido ({projected}/"
                                f"{budget_state.max_budget}). La herramienta YA "
                                f"ejecutó efectos en disco/sistema; su resultado "
                                f"fue descartado. Verifica el estado real antes "
                                f"de reintentar."
                            ),
                        }
                    budget_state.reconcile(estimated, charged_tokens)

                logger.info(
                    f"✅ Tool {tool_name} OK (attempt {attempt}) | tokens={actual_tokens} | duration={duration:.2f}s"
                )

                if hooks_on:
                    await hooks_registry.dispatch(
                        "on_after_tool",
                        HookContext(
                            hook_point="on_after_tool",
                            tool_name=tool_name,
                            parameters=parameters,
                            role=role,
                            result=result,
                            duration=duration,
                            attempt=attempt,
                            workspace=workspace,
                            session_id=session_id,
                        ),
                    )

                return {
                    "success": True,
                    "tool": tool_name,
                    "output": result,
                    "tokens_used": actual_tokens,
                    "duration": duration,
                    "attempt": attempt,
                }

            except Exception as e:
                last_error = str(e)
                # If file-not-found error, skip retry
                if "no encontrado" in last_error or "not found" in last_error:
                    logger.warning(f"Tool {tool_name} failed (file not found) — no retry")
                    break
                logger.warning(f"Tool {tool_name} falló (attempt {attempt}/{max_retries}): {e}")

                if hooks_on:
                    await hooks_registry.dispatch(
                        "on_tool_error",
                        HookContext(
                            hook_point="on_tool_error",
                            tool_name=tool_name,
                            parameters=parameters,
                            role=role,
                            error=last_error,
                            attempt=attempt,
                            workspace=workspace,
                            session_id=session_id,
                        ),
                    )

                if attempt < max_retries:
                    delay = backoff_base**attempt + random.uniform(0, 0.5)
                    await asyncio.sleep(delay)

        # Fallback error amigable
        if budget_reserved and budget_state is not None:
            budget_state.refund(estimated)
        return {
            "success": False,
            "error": "max_retries_exceeded",
            "output": (
                f"❌ La herramienta '{tool_name}' falló después de "
                f"{ToolOrchestrator.MAX_RETRIES} attempts.\nLast error: {last_error}"
            ),
            "tool": tool_name,
        }

    @staticmethod
    def _check_permissions(tool_name: str, role: str) -> bool:
        # ALLOW_CODE_EXECUTION flag gating
        if tool_name in ("code_exec", "bash_manager") and not settings.allow_code_execution:
            return False
        # Extension point de permisos dinámicos por-tool (kairos):
        # no hay callers que registren claves `allow_*`, por lo que el
        # default es allow. Si se implementa, registrar las claves en
        # core/feature_flags.py::_init_flags y NO confiar en el default.
        key = f"allow_{tool_name}_{role}"
        return kairos.get(key, kairos.get(f"allow_{tool_name}", True))

    @staticmethod
    def _estimate_tokens(parameters: dict[str, Any]) -> int:
        try:
            text = json.dumps(parameters, ensure_ascii=False)
            return len(get_encoding().encode(text))
        except (TypeError, ValueError, AttributeError):
            return len(str(parameters)) // 4

    @staticmethod
    def reset_token_budget():
        """Reinicia el presupuesto de tokens al inicio de cada workflow.

        El estado es un objeto mutable: los tasks hijos lo comparten por
        referencia, así que la acumulación es visible para el task raíz
        (topología determinista, coordinated y development)."""
        ToolOrchestrator.MAX_TOKENS_PER_WORKFLOW = settings.tool_max_tokens_per_workflow
        ToolOrchestrator.ENABLE_TOKEN_BUDGET = settings.tool_enable_token_budget
        _token_budget_ctx.set(_TokenBudgetState(ToolOrchestrator.MAX_TOKENS_PER_WORKFLOW))


def add_llm_token_usage(total_tokens: int) -> None:
    """Track actual LLM API tokens in the workflow token budget."""
    if not ToolOrchestrator.ENABLE_TOKEN_BUDGET:
        return
    state = _token_budget_ctx.get()
    if state is None:
        return
    state.total += total_tokens
    if state.total > state.max_budget and not state.warned:
        state.warned = True
        logger.warning(
            "Token budget exceeded by LLM call: %d/%d tokens (further warnings suppressed)",
            state.total,
            state.max_budget,
        )


# Global instance (kept for compatibility; the budget is now context-local)
tool_orchestrator = ToolOrchestrator()


def get_llm_token_usage() -> int:
    """Tokens LLM acumulados en el workflow actual (budget ContextVar).

    Retorna 0 si no hay presupuesto activo (p. ej. tests o chat simple
    sin reset_token_budget previo).
    """
    state = _token_budget_ctx.get()
    return state.total if state is not None else 0


def get_token_budget_max() -> int:
    """Techo del presupuesto activo (0 si no hay presupuesto o está off).

    Los consumidores (p. ej. la compresión del agent-loop) lo usan para
    alinearse al límite real del run sin re-leer settings.
    """
    if not ToolOrchestrator.ENABLE_TOKEN_BUDGET:
        return 0
    state = _token_budget_ctx.get()
    return state.max_budget if state is not None else 0
