# core/hooks_registry.py
import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HookContext:
    """Immutable context passed to every hook invocation."""

    hook_point: str
    tool_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    role: str = "agent"
    result: Any = None
    error: str | None = None
    duration: float = 0.0
    attempt: int = 1
    workspace: str = "main"
    session_id: str | None = None


class HooksRegistry:
    """Registry for hook callables organized by hook point name.

    Mirrors ToolsRegistry pattern: decorator-based registration,
    global + workspace-scoped via load/unload lifecycle.
    """

    def __init__(self):
        self._hooks: dict[str, list[Callable]] = {}
        self._source_map: dict[int, str] = {}

    def register(self, hook_point: str) -> Callable:
        """Decorator: @hooks_registry.register('on_before_tool')"""

        def decorator(func: Callable) -> Callable:
            handlers = self._hooks.setdefault(hook_point, [])
            if func not in handlers:
                handlers.append(func)
                logger.info(f"Hook registered: {func.__name__} -> {hook_point}")
            self._source_map[id(func)] = hook_point
            return func

        return decorator

    async def dispatch(self, hook_point: str, context: HookContext) -> None:
        """Invoke all hooks registered for hook_point. Exceptions are caught and logged."""
        handlers = self._hooks.get(hook_point, [])
        if not handlers:
            return

        context.hook_point = hook_point

        for hook in handlers:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(context)
                else:
                    hook(context)
            except Exception:
                logger.warning(
                    f"Hook {hook.__name__} at '{hook_point}' failed",
                    exc_info=True,
                )

    def unregister(self, hook_point: str, func: Callable) -> bool:
        """Remove a single hook from a hook point."""
        handlers = self._hooks.get(hook_point)
        if handlers and func in handlers:
            handlers.remove(func)
            if not handlers:
                del self._hooks[hook_point]
            logger.info(f"Hook unregistered: {func.__name__} from {hook_point}")
            return True
        return False

    def clear_hook_point(self, hook_point: str) -> None:
        """Remove all hooks for a given point."""
        self._hooks.pop(hook_point, None)

    def clear(self) -> None:
        """Remove all hooks (used on workspace switch to clean workspace hooks)."""
        self._hooks.clear()
        self._source_map.clear()
        logger.info("All hooks cleared from registry")

    def list_hooks(self) -> dict[str, list[str]]:
        """Return {hook_point: [function_names]} for introspection."""
        return {point: [h.__name__ for h in handlers] for point, handlers in self._hooks.items()}


hooks_registry = HooksRegistry()
