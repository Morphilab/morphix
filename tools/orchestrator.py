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

# Fallos deterministas fast-fail repetidos: el agente puede insistir N
# veces con el mismo error (no-repo, file_path vacío); tras el primer
# aviso por (tool, error) la repetición se loggea a INFO — mismo ruido
# de siempre degradado sin perder el primer aviso.
_fast_fail_warned: set[tuple[str, str]] = set()


def _tool_failure_payload(
    tool_name: str,
    result: object,
    error_msg: object,
    error_kind: str = "tool_reported_failure",
) -> dict:
    """Envuelve un fallo de tool PRESERVANDO lo estructurado.

    Los conteos/returncode de la tool viajan intactos (el until/verdict los
    consume de forma determinista) y la salida de test_runner llega COMPLETA
    (la propia tool ya la recorta al tail: el agente necesita ver los fallos,
    no la cabecera de pytest); el resto de tools mantiene el head de 300.
    """
    payload: dict = {
        "success": False,
        "error": error_kind,
        "output": (
            str(error_msg)
            if tool_name == "test_runner"
            else f"❌ La herramienta '{tool_name}' reportó fallo: {str(error_msg)[:300]}"
        ),
        "tool": tool_name,
    }
    if isinstance(result, dict):
        # claves estructuradas de la tool (conteos, returncode): el consumidor
        # determinista (until/verdict) las necesita intactas
        for key, value in result.items():
            if key not in ("success", "error", "output", "tool"):
                payload.setdefault(key, value)
    return payload


def _is_timeout_result(result: object, error_msg: object) -> bool:
    """Timeout determinista: key estructurada o prefijo exacto del mensaje.

    No se busca "timeout" en todo el output: el stderr de un programa puede
    contener la palabra y un crash no es un timeout.
    """
    if isinstance(result, dict) and result.get("error_kind") == "timeout":
        return True
    text = str(error_msg)
    return text.startswith("⏱️ Timeout") or text.startswith("❌ Execution time exceeded")


# Token budget — estado mutable compartido por referencia entre el task raíz
# del workflow y sus subtasks (asyncio.create_task/gather copian el ContextVar,
# pero el OBJETO es el mismo → las mutaciones de los hijos son visibles para el
# raíz). Esto hace el presupuesto determinista en cualquier topología
# (secuencial como development, o gather como coordinated).
class _TokenBudgetState:
    __slots__ = ("total", "max_budget", "limit", "warned", "ceiling_stack", "reserved")

    def __init__(self, max_budget: int) -> None:
        self.total = 0
        self.max_budget = max_budget
        # Techo duro del preset: push_budget_extra puede abrir la
        # reserva final, pero jamás superar este límite.
        self.limit = max_budget
        self.warned = False
        self.ceiling_stack: list[int] = []
        # Reservas VIVAS (LLM/tools) incluidas en `total` hasta su refund o
        # reconcile. El warning de overshoot evalúa el gasto comprometido
        # (total - reserved): con la reserva contada anunciaba "exceeded" en
        # runs que terminaban bajo el techo.
        self.reserved = 0

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
        self.reserved += estimated
        return True

    def refund(self, estimated: int) -> None:
        """Devuelve la reserva cuando la tool falló definitivamente —
        sin esto, fallos deterministas queman presupuesto y provocan
        token_budget_exceeded espurios en subtareas posteriores."""
        amount = max(0, estimated)
        self.total = max(0, self.total - amount)
        self.reserved = max(0, self.reserved - amount)

    def reconcile(self, estimated: int, actual: int) -> None:
        """Reemplaza la reserva por el gasto real de tokens.

        Bajo paralelismo el total puede exceder max por la varianza entre
        estimación y gasto real de cada tool (best-effort, acotado por
        tool); la defensa principal es reserve() contra el overcommit.
        """
        self.total += actual - estimated
        self.reserved = max(0, self.reserved - max(0, estimated))


# Contador de tokens LLM POR-TAREA (ContextVar): bajo gather, cada loop
# hereda una copia y cuenta solo sus propias llamadas — el estado de
# presupuesto compartido por referencia no sirve para métrica per-step.
_llm_call_tokens: contextvars.ContextVar[int] = contextvars.ContextVar("llm_call_tokens", default=0)

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


def bind_approval_from_events(events: Any) -> None:
    """Engancha el callback de aprobación que provee el host (``events``).

    Punto ÚNICO del enganche, compartido por el orquestador (workflows) y el
    chat directo de agente: antes solo lo llamaba el orquestador, así que en el
    chat directo toda acción peligrosa (bash_manager, code_exec, delete, apply)
    se denegaba fail-closed por "no hay UI de aprobación" aunque la GUI la
    tuviera. Sin host (headless/MCP) ⇒ None: fail-closed intacto.
    """
    cb = getattr(events, "on_approval_required", None) if events is not None else None
    set_approval_callback(cb)


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
                        # bloqueos y errores de argumentos: deterministas —
                        # reintentar args idénticos produce el mismo fallo
                        "está bloqueado",
                        "command blocked",
                        "requiere un parámetro",
                        "no está permitida",
                        "file_path vacío",
                        # denegaciones deterministas propias: reintentar args
                        # idénticos produce el mismo fallo
                        "sin rutas que verificar",
                        "ruta no permitida",
                    ]
                    if any(kw in str(error_msg).lower() for kw in fast_fail_keywords):
                        _ff_key = (tool_name, str(error_msg)[:80])
                        if _ff_key in _fast_fail_warned:
                            logger.info(
                                f"Tool {tool_name} fast-fail (determinista, repetido): "
                                f"{str(error_msg)[:120]}"
                            )
                        else:
                            _fast_fail_warned.add(_ff_key)
                            logger.warning(
                                f"Tool {tool_name} failed (fast-fail: file/path error) — no retry"
                            )
                        if budget_reserved and budget_state is not None:
                            budget_state.refund(estimated)
                        return _tool_failure_payload(tool_name, result, error_msg)
                    # Fast-fail: un timeout determinista no se arregla
                    # reintentando (el comando bloqueado repite el mismo
                    # estado); los tests rojos también son deterministas.
                    if (
                        isinstance(result, dict)
                        and tool_name in {"test_runner", "bash_manager", "code_exec"}
                        and _is_timeout_result(result, error_msg)
                    ):
                        logger.warning("%s timeout — no retry (determinista)", tool_name)
                        if budget_reserved and budget_state is not None:
                            budget_state.refund(estimated)
                        return _tool_failure_payload(
                            tool_name, result, error_msg, error_kind="timeout"
                        )
                    if tool_name == "test_runner" and isinstance(result, dict):
                        if result.get("failed_count", 0) > 0 or result.get("error_count", 0) > 0:
                            logger.warning(
                                f"test_runner: {result['failed_count']} failures, "
                                f"{result['error_count']} errors — no retry (tests are deterministic)"
                            )
                            if budget_reserved and budget_state is not None:
                                budget_state.refund(estimated)
                            return _tool_failure_payload(
                                tool_name, result, error_msg, error_kind="tests_failed"
                            )
                    logger.warning(
                        f"Tool {tool_name} reported failure (attempt {attempt}): {str(error_msg)[:200]}"
                    )
                    if attempt < max_retries:
                        delay = backoff_base**attempt + random.uniform(0, 0.5)
                        await asyncio.sleep(delay)
                        continue
                    if budget_reserved and budget_state is not None:
                        budget_state.refund(estimated)
                    return _tool_failure_payload(tool_name, result, error_msg)

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
    _llm_call_tokens.set(_llm_call_tokens.get() + max(0, total_tokens))
    if not ToolOrchestrator.ENABLE_TOKEN_BUDGET:
        return
    state = _token_budget_ctx.get()
    if state is None:
        return
    state.total += total_tokens
    # El warning evalúa el gasto COMPROMETIDO: durante una llamada la reserva
    # del input vive en total hasta el refund, y contarla anunciaba
    # "exceeded" en runs que cerraban bajo el techo.
    committed = state.total - state.reserved
    if committed > state.max_budget and not state.warned:
        state.warned = True
        logger.warning(
            "Token budget exceeded by LLM call: %d/%d tokens (further warnings suppressed)",
            committed,
            state.max_budget,
        )


# Global instance (kept for compatibility; the budget is now context-local)
tool_orchestrator = ToolOrchestrator()


def push_budget_ceiling(budget_tokens: int) -> None:
    """Techo de presupuesto por-subtarea (presupuesto por-vuelta de loop).

    Apila el techo vigente y lo acota a min(techo, consumo_al_entrar +
    budget_tokens): la subtarea puede gastar a lo sumo su presupuesto sin
    tocar el techo global del run. En loops paralelos el scope envuelve al
    LOTE (los hermanos comparten el techo — el estado de presupuesto es
    único por diseño atómico).
    """
    if not ToolOrchestrator.ENABLE_TOKEN_BUDGET:
        return
    state = _token_budget_ctx.get()
    if state is None:
        return
    try:
        budget = int(budget_tokens)
    except (TypeError, ValueError):
        return
    if budget <= 0:
        return
    state.ceiling_stack.append(state.max_budget)
    state.max_budget = min(state.max_budget, state.total + budget)


def pop_budget_ceiling() -> None:
    """Restaura el techo vigente antes del scope (LIFO)."""
    state = _token_budget_ctx.get()
    if state is None or not state.ceiling_stack:
        return
    state.max_budget = state.ceiling_stack.pop()


def push_budget_extra(extra: int) -> None:
    """Abre la reserva final del step de cierre sin superar el preset.

    La reserva final está excluida de `available_for_work` para que los
    loops no la consuman; este scope sube el techo ACTIVO hasta el límite
    del preset (la reserva ya está DENTRO de ese límite, no encima) y
    `pop_budget_ceiling` lo restaura. Es la contraparte de
    `push_budget_ceiling` (que solo baja).
    """
    if not ToolOrchestrator.ENABLE_TOKEN_BUDGET:
        return
    state = _token_budget_ctx.get()
    if state is None:
        return
    try:
        extra = int(extra)
    except (TypeError, ValueError):
        return
    if extra <= 0:
        return
    state.ceiling_stack.append(state.max_budget)
    state.max_budget = min(state.limit, state.max_budget + extra)


def get_llm_call_tokens() -> int:
    """Tokens LLM consumidos por LA TAREA actual (ContextVar aislada).

    Base de la métrica per-step exacta bajo paralelismo: cada loop de
    agente mide su propio consumo sin colisiones entre hermanos.
    """
    return _llm_call_tokens.get()


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


def set_token_budget_max(max_budget: int) -> None:
    """Ajusta el techo del presupuesto ACTIVO (budget_tokens del preset DSL).

    Un preset acota SU run: puede bajar el techo o subirlo hasta el HARD CAP de
    la plataforma (``TOOL_MAX_TOKENS_HARD_CAP``) para perfiles long-horizon.
    Jamás supera el hard cap: esa es la red de la plataforma.
    Sin estado activo (chat simple/tests) es no-op.
    """
    try:
        budget = int(max_budget)
    except (TypeError, ValueError):
        return
    if not ToolOrchestrator.ENABLE_TOKEN_BUDGET or budget <= 0:
        return
    state = _token_budget_ctx.get()
    if state is not None:
        hard_cap = int(getattr(settings, "tool_max_tokens_hard_cap", 0) or 0)
        if hard_cap > 0:
            budget = min(budget, hard_cap)
        state.max_budget = budget
        state.limit = budget
