# core/hooks/audit.py
"""Global hook: audit every tool call to the audit log."""
import json
import logging

from agents.audit import log_operation
from core.hooks_registry import HookContext, hooks_registry

logger = logging.getLogger(__name__)


@hooks_registry.register("on_before_tool")
def audit_on_before_tool(ctx: HookContext) -> None:
    """Log tool invocation attempt before execution."""
    log_operation(
        operation="tool_before",
        details=json.dumps(
            {
                "tool": ctx.tool_name,
                "params": {k: str(v)[:200] for k, v in ctx.parameters.items()},
                "role": ctx.role,
                "workspace": ctx.workspace,
            }
        ),
        success=True,
    )


@hooks_registry.register("on_after_tool")
def audit_on_after_tool(ctx: HookContext) -> None:
    """Log tool result after successful execution."""
    log_operation(
        operation="tool_after",
        details=json.dumps(
            {
                "tool": ctx.tool_name,
                "duration": round(ctx.duration, 3),
                "role": ctx.role,
                "workspace": ctx.workspace,
            }
        ),
        success=True,
    )


@hooks_registry.register("on_tool_error")
def audit_on_tool_error(ctx: HookContext) -> None:
    """Log tool failure with error details."""
    log_operation(
        operation="tool_error",
        details=json.dumps(
            {
                "tool": ctx.tool_name,
                "error": ctx.error,
                "attempt": ctx.attempt,
                "role": ctx.role,
                "workspace": ctx.workspace,
            }
        ),
        success=False,
    )
