# core/mcp/adapter.py
"""Convert between Morphix ToolDefinition and MCP tool schema.

MCP tool format:
    {"name": "...", "description": "...", "inputSchema": {"type": "object", "properties": {...}, "required": [...]}}

Morphix tools use OpenAI function-calling format — we convert at the bridge.
"""

from typing import Any


def morphix_to_mcp_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a Morphix tool dict to MCP tool format.

    Input: {"name": "...", "description": "...", "parameters": {...}, "required": [...]}
    Output: {"name": "...", "description": "...", "inputSchema": {"type": "object", "properties": {...}, "required": [...]}}
    """
    return {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "inputSchema": {
            "type": "object",
            "properties": tool.get("parameters", {}),
            "required": tool.get("required", []),
        },
    }


def mcp_tool_to_morphix_params(mcp_tool: dict[str, Any]) -> dict[str, Any]:
    """Extract Morphix-compatible params from an MCP tool schema.

    Returns a dict suitable for tools_registry + tool_specs registration.
    """
    input_schema = mcp_tool.get("inputSchema", {})
    return {
        "name": mcp_tool["name"],
        "description": mcp_tool.get("description", ""),
        "parameters": input_schema.get("properties", {}),
        "required": input_schema.get("required", []),
    }


def mcp_result_to_morphix(result: dict[str, Any]) -> dict[str, Any]:
    """Convert MCP tools/call result to Morphix tool output format.

    MCP content: [{"type": "text", "text": "..."}, {"type": "image", "data": "...", "mimeType": "..."}]
    Morphix expects: {"success": bool, "output": str, ...}
    """
    content = result.get("content", [])
    text_parts = []
    for item in content:
        if item.get("type") == "text":
            text_parts.append(item.get("text", ""))
        elif item.get("type") == "image":
            text_parts.append(f"[image: {item.get('mimeType', 'unknown')}]")
    return {
        "success": not result.get("isError", False),
        "output": "\n".join(text_parts),
        "raw": result,
    }
