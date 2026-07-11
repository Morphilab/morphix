# core/tools_orchestrator.py
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

# Token budget isolated per async context (Python 3.12+ copies contextvars in create_task)
_token_budget_ctx: contextvars.ContextVar[int] = contextvars.ContextVar(
    "tool_token_budget", default=0
)

# Flag to suppress repeated budget-exceeded warnings within a single workflow
_budget_warned_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "tool_budget_warned", default=False
)


class ToolOrchestrator:
    MAX_RETRIES = settings.tool_max_retries
    BACKOFF_BASE = settings.tool_backoff_base
    MAX_TOKENS_PER_WORKFLOW = settings.tool_max_tokens_per_workflow
    ENABLE_TOKEN_BUDGET = settings.tool_enable_token_budget

    # Tools/actions that require explicit user approval
    DANGEROUS_ACTIONS: set[str] = {
        "bash_manager",
        "code_exec",
        "file_manager.delete",
        "git_manager.commit",
        "git_manager.push",
    }

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
        action_key = (
            f"{tool_name}.{parameters.get('action', '')}" if parameters.get("action") else tool_name
        )
        if ToolOrchestrator.on_approval_required is not None and (
            tool_name in ToolOrchestrator.DANGEROUS_ACTIONS
            or action_key in ToolOrchestrator.DANGEROUS_ACTIONS
        ):
            approved = await ToolOrchestrator.on_approval_required(tool_name, parameters)
            if not approved:
                return {
                    "success": False,
                    "error": "approval_denied",
                    "output": f"❌ Operation '{tool_name}' was denied by user.",
                }

        # Token budget — isolated per async context (contextvars)
        estimated = ToolOrchestrator._estimate_tokens(parameters)
        current_budget = _token_budget_ctx.get()
        max_budget = ToolOrchestrator.MAX_TOKENS_PER_WORKFLOW
        if ToolOrchestrator.ENABLE_TOKEN_BUDGET and current_budget + estimated > max_budget:
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
                    f"({current_budget + estimated}/{max_budget})"
                ),
            }

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
                        else tool(**parameters)
                    )

                duration = time.time() - start_time
                actual_tokens = (
                    result.get("tokens_used", estimated) if isinstance(result, dict) else estimated
                )

                # Check internal tool success before logging
                internal_ok = result.get("success", True) if isinstance(result, dict) else True
                if not internal_ok:
                    error_msg = (
                        result.get("output", result.get("text", "unknown error"))
                        if isinstance(result, dict)
                        else str(result)
                    )
                    # Fast-fail: skip retry for file-not-found or path errors
                    fast_fail_keywords = [
                        "no se encontró",
                        "no encontrado",
                        "not found",
                        "file not found",
                        "fuera del workspace",
                    ]
                    if any(kw in str(error_msg).lower() for kw in fast_fail_keywords):
                        logger.warning(
                            f"Tool {tool_name} failed (fast-fail: file/path error) — no retry"
                        )
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
                    return {
                        "success": False,
                        "error": "tool_reported_failure",
                        "output": f"❌ La herramienta '{tool_name}' reportó fallo: {str(error_msg)[:300]}",
                        "tool": tool_name,
                    }

                # Verificar presupuesto con tokens reales antes de contabilizar
                new_total = current_budget + actual_tokens
                if ToolOrchestrator.ENABLE_TOKEN_BUDGET and new_total > max_budget:
                    logger.warning(
                        f"Tool {tool_name} excede presupuesto con tokens reales "
                        f"({new_total}/{max_budget}). Resultado descartado."
                    )
                    return {
                        "success": False,
                        "error": "token_budget_exceeded",
                        "output": f"❌ Presupuesto excedido ({new_total}/{max_budget})",
                    }
                _token_budget_ctx.set(new_total)

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
        key = f"allow_{tool_name}_{role}"
        return kairos.get(key, kairos.get(f"allow_{tool_name}", True))  # dynamic permission flags

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
        El contextvar garantiza aislamiento entre workflows concurrentes."""
        _token_budget_ctx.set(0)
        _budget_warned_ctx.set(False)
        ToolOrchestrator.MAX_TOKENS_PER_WORKFLOW = settings.tool_max_tokens_per_workflow
        ToolOrchestrator.ENABLE_TOKEN_BUDGET = settings.tool_enable_token_budget


def add_llm_token_usage(total_tokens: int) -> None:
    """Track actual LLM API tokens in the workflow token budget."""
    if not ToolOrchestrator.ENABLE_TOKEN_BUDGET:
        return
    current = _token_budget_ctx.get()
    if current is None:
        return
    max_budget = ToolOrchestrator.MAX_TOKENS_PER_WORKFLOW
    new_total = current + total_tokens
    _token_budget_ctx.set(new_total)
    if new_total > max_budget:
        _warned = _budget_warned_ctx.get()
        if not _warned:
            _budget_warned_ctx.set(True)
            logger.warning(
                "Token budget exceeded by LLM call: %d/%d tokens (further warnings suppressed)",
                new_total,
                max_budget,
            )


# Global instance (kept for compatibility; the budget is now context-local)
tool_orchestrator = ToolOrchestrator()
