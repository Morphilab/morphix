# core/tool_call_wrapper.py
"""
Wrapper de alto nivel para tool calls (usado por workflow_orchestrator)
Incluye instrumentación de métricas por herramienta (latencia + éxito/fallo).
"""

import time

from core.metrics import metrics, tool_metrics
from tools.orchestrator import tool_orchestrator


async def safe_tool_call(
    tool_name: str,
    parameters: dict,
    role: str = "agent",
    workspace: str = "main",
    session_id: str | None = None,
):
    """Wrapper simple y seguro para usar en workflow_orchestrator.
    Routes MCP-prefixed tools to the appropriate MCP client.
    Records per-tool metrics (latency, success/failure).
    """
    if not tool_name or not tool_name.strip():
        return {
            "success": False,
            "error": "empty_tool_name",
            "output": "\u274c Tool name cannot be empty",
        }

    # Fast-fail: bash_manager requires 'command' parameter
    if tool_name == "bash_manager" and not str(parameters.get("command", "")).strip():
        return {
            "success": False,
            "error": "missing_required_param",
            "output": "❌ bash_manager requires 'command' parameter",
        }

    start = time.monotonic()
    try:
        if tool_name.startswith("mcp:"):
            from core.mcp.client import get_mcp_client_for_tool

            client = get_mcp_client_for_tool(tool_name)
            if client is None:
                result = {
                    "success": False,
                    "error": "mcp_client_not_found",
                    "output": f"MCP client not found for tool: {tool_name}",
                }
            else:
                result = await client.call_tool(tool_name, parameters)
        else:
            result = await tool_orchestrator.execute_tool(
                tool_name, parameters, role=role, workspace=workspace, session_id=session_id
            )
    except Exception:
        elapsed = (time.monotonic() - start) * 1000
        tool_metrics.record_call(tool_name, False, elapsed)
        raise

    elapsed = (time.monotonic() - start) * 1000
    success: bool = bool(result.get("success", False)) if isinstance(result, dict) else False
    tool_metrics.record_call(tool_name, success, elapsed)
    metrics.tool_calls += 1
    return result
