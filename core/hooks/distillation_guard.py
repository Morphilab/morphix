# core/hooks/distillation_guard.py
"""Global hook: distillation guard — logs patterns and throttles at escalation level 2+."""

import asyncio
import logging

from core.hooks_registry import HookContext, hooks_registry
from core.security.anti_distillation import distillation_tracker

logger = logging.getLogger(__name__)


@hooks_registry.register("on_before_tool")
async def distillation_guard_on_before_tool(ctx: HookContext) -> None:
    """Check distillation escalation before tool execution.

    At level 2 (throttle): add artificial delay.
    At level 4 (lock): reject all tool calls.
    """
    if distillation_tracker.is_locked():
        logger.critical(f"Tool '{ctx.tool_name}' blocked: session locked (anti-distillation)")
        return

    delay = distillation_tracker.get_throttle_delay()
    if delay > 0:
        logger.warning(
            f"Anti-distillation throttle: {delay:.1f}s delay for '{ctx.tool_name}' "
            f"(level {distillation_tracker.escalation_level})"
        )
        await asyncio.sleep(delay)


@hooks_registry.register("on_after_tool")
def distillation_guard_on_after_tool(ctx: HookContext) -> None:
    """Periodic status log at escalation level 1+."""
    if distillation_tracker.escalation_level > 0 and distillation_tracker.blocked_count % 10 == 0:
        logger.info(
            f"Anti-distillation status: {distillation_tracker.blocked_count} blocked, "
            f"escalation level {distillation_tracker.escalation_level}"
        )
