# tools/code_execution.py
"""
CodeExecutor - Wrapper simple y seguro al sandbox hardened.
La herramienta se autoregistra y devuelve texto limpio.
"""

import logging

from agents.audit import log_operation
from core.sandbox.restricted_executor import restricted_executor

logger = logging.getLogger(__name__)


class CodeExecutor:
    @staticmethod
    async def execute(code: str) -> dict:
        """Ejecución segura con sandbox hardened (retorna dict interno)."""
        return await restricted_executor.execute(code)


async def _code_exec_tool(code: str, **kwargs) -> dict:
    result = await CodeExecutor.execute(code)
    success = result.get("success", False)
    text = result.get("text", "")
    if not success:
        return {
            "success": False,
            "output": f"❌ Error de ejecución: {text or 'Error desconocido.'}",
        }
    log_operation("code_exec", code[:200], success=True)
    return {"success": True, "output": text or "✅ Code executed successfully (no output)."}


# Autoregistro
from tools.registry import tools_registry

tools_registry.register("code_exec")(_code_exec_tool)
