# core/tools_registry.py
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class ToolsRegistry:
    """Registro de herramientas — instanciable, no singleton.

    La instancia global legacy 'tools_registry' se mantiene para backward compat.
    Para nuevos entry points o tests, crear ToolsRegistry() directamente.
    """

    def __init__(self):
        self._tools: dict[str, Callable] = {}

    def register(self, name: str) -> Callable:
        def decorator(func: Callable) -> Callable:
            self._tools[name] = func
            logger.info(f"Tool registrada: {name}")
            return func

        return decorator

    def get_tool(self, name: str) -> Callable | None:
        return self._tools.get(name)

    def list_tools(self) -> dict[str, Callable]:
        return self._tools.copy()

    def unregister(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def clear(self):
        self._tools.clear()


# Legacy global instance — compatibility with existing code.
tools_registry = ToolsRegistry()
