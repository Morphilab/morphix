# core/mcp/server.py
"""MCP server — expose Morphix tools over stdio JSON-RPC.

Usage:
    morphix mcp-server
    poetry run python -m core.mcp.server

Other MCP clients (opencode, Claude Desktop) can connect and use
Morphix tools: file_manager, bash_manager, web_search, etc.
"""

import asyncio
import logging
import sys

from core.mcp.adapter import morphix_to_mcp_tool
from core.mcp.protocol import (
    build_error,
    build_response,
    is_notification,
    read_message,
    write_message,
)

logger = logging.getLogger(__name__)


class MCPServer:
    def __init__(self):
        self._initialized = False
        self._server_info = {"name": "morphix", "version": "1.0"}

    def _get_tools(self) -> list[dict]:
        """Collect all registered Morphix tools as MCP tool schemas."""
        from tools.specs import TOOL_DEFINITIONS

        tools = []
        for name, tdef in sorted(TOOL_DEFINITIONS.items()):
            tool_dict = {
                "name": name,
                "description": tdef.description,
                "parameters": tdef.parameters,
                "required": tdef.required,
            }
            tools.append(morphix_to_mcp_tool(tool_dict))
        return tools

    async def _handle_initialize(self, msg: dict) -> dict:
        params = msg.get("params", {})
        client_info = params.get("clientInfo", {})
        logger.info(
            f"MCP client connected: {client_info.get('name', 'unknown')} "
            f"v{client_info.get('version', '?')}"
        )
        self._initialized = True
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": self._server_info,
        }

    async def _handle_tools_list(self, msg: dict) -> dict:
        tools = self._get_tools()
        return {"tools": tools}

    async def _handle_tools_call(self, msg: dict) -> dict:
        params = msg.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if not tool_name:
            return build_error(msg.get("id"), -32602, "Missing tool name")

        from tools.wrapper import safe_tool_call

        try:
            result = await safe_tool_call(tool_name, arguments)
        except Exception as e:
            logger.exception(f"MCP tool '{tool_name}' failed")
            return {
                "content": [{"type": "text", "text": f"Tool error: {e}"}],
                "isError": True,
            }

        if isinstance(result, dict):
            output_text = str(result.get("output", result.get("error", str(result))))
            is_error = not result.get("success", False)
        else:
            output_text = str(result)
            is_error = False

        return {
            "content": [{"type": "text", "text": output_text}],
            "isError": is_error,
        }

    async def run(self) -> None:
        """Main loop: read JSON-RPC from stdin, respond on stdout."""
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        write_transport, write_protocol = await asyncio.get_running_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout.buffer
        )
        writer = asyncio.StreamWriter(
            write_transport, write_protocol, reader, asyncio.get_running_loop()
        )

        logger.info("MCP server ready (stdio)")
        handlers = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
        }

        try:
            while True:
                msg = await read_message(reader)

                if is_notification(msg):
                    method = msg.get("method", "")
                    if method == "notifications/initialized":
                        logger.info("MCP client initialized notification received")
                    continue

                msg_id = msg.get("id")
                method = msg.get("method", "")

                handler = handlers.get(method)
                if handler is None:
                    await write_message(
                        writer, build_error(msg_id, -32601, f"Method not found: {method}")
                    )
                    continue

                try:
                    result = await handler(msg)
                    if isinstance(result, dict) and "jsonrpc" in result:
                        await write_message(writer, result)
                    else:
                        await write_message(writer, build_response(msg_id or 0, result))
                except Exception as e:
                    logger.exception(f"MCP handler '{method}' error")
                    await write_message(writer, build_error(msg_id, -32603, str(e)))

        except ConnectionError:
            logger.info("MCP client disconnected")
        except asyncio.CancelledError:
            pass


def run_mcp_server() -> None:
    """Entry point for 'morphix mcp-server'."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        stream=sys.stderr,
    )
    # Load global tools so they're available in the registry
    from tools.loader import load_global_tools

    load_global_tools()

    server = MCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    run_mcp_server()
