# core/mcp/client.py
"""MCP client — connect to external MCP servers, discover and proxy their tools."""

import asyncio
import logging
import os

from core.mcp.adapter import mcp_result_to_morphix, mcp_tool_to_morphix_params
from core.mcp.config import MCPServerConfig
from core.mcp.protocol import (
    build_notification,
    build_request,
    get_id,
    is_response,
    read_message,
    write_message,
)

logger = logging.getLogger(__name__)

# Global registry of connected MCP clients
_clients: dict[str, "MCPClient"] = {}


class MCPClient:
    """Manages one MCP server connection (subprocess via stdio)."""

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._pending: dict[int | str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._tools: dict[str, dict] = {}  # tool_name -> schema
        self._initialized = False

    async def connect(self, register_tools: bool = True) -> bool:
        """Spawn the MCP server subprocess and perform initialization handshake."""
        if self.process is not None:
            return True

        env = os.environ.copy()
        env.update(self.config.env)

        try:
            self.process = await asyncio.create_subprocess_exec(
                self.config.command,
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            logger.error(
                f"MCP server '{self.config.name}': command not found: {self.config.command}"
            )
            return False
        except Exception:
            logger.exception(f"MCP server '{self.config.name}': failed to start")
            return False

        # Start background reader
        self._reader_task = asyncio.create_task(self._read_loop())

        # Initialize handshake
        try:
            result = await self._send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "morphix", "version": "1.0"},
                },
            )
            logger.info(
                f"MCP server '{self.config.name}' initialized: "
                f"{result.get('serverInfo', {}).get('name', 'unknown')} "
                f"v{result.get('protocolVersion', '?')}"
            )
        except Exception as e:
            logger.error(f"MCP server '{self.config.name}' init failed: {e}")
            await self.disconnect()
            return False

        # Send initialized notification
        try:
            stdin = self.process.stdin
            if stdin is None:
                logger.error(f"MCP server '{self.config.name}' has no stdin stream")
                return False
            await write_message(
                stdin,
                build_notification("notifications/initialized"),
            )
        except Exception:
            logger.warning(f"MCP server '{self.config.name}' initialized notification failed")

        # Discover tools
        try:
            result = await self._send_request("tools/list", {})
            tools = result.get("tools", [])
            for tool in tools:
                full_name = f"mcp:{self.config.tools_prefix}.{tool['name']}"
                self._tools[tool["name"]] = mcp_tool_to_morphix_params(tool)
                self._tools[tool["name"]]["full_name"] = full_name
            logger.info(f"MCP server '{self.config.name}': {len(tools)} tool(s) discovered")
        except Exception as e:
            logger.error(f"MCP server '{self.config.name}' tools/list failed: {e}")
            await self.disconnect()
            return False

        # Register tools in Morphix tool registry
        if register_tools:
            await self._register_discovered_tools()

        self._initialized = True
        return True

    async def _register_discovered_tools(self) -> None:
        """Register discovered MCP tools in Morphix tools_registry."""
        from tools.registry import tools_registry
        from tools.specs import TOOL_DEFINITIONS, ToolDefinition

        for native_name, params in self._tools.items():
            full_name = params.get("full_name", f"mcp:{self.config.tools_prefix}.{native_name}")
            # Sanitize: DeepSeek strict mode rejects : and . in tool names
            sanitized_name = full_name.replace(":", "_").replace(".", "_")
            description = f"[MCP:{self.config.name}] {params.get('description', '')}"

            # Create a closure that calls this client for the specific tool
            client_ref = self
            tool_native_name = native_name

            async def _mcp_proxy(
                _native=tool_native_name,
                _client=client_ref,
                **kwargs,
            ):
                return await _client.call_tool(_native, kwargs)

            _mcp_proxy.__name__ = sanitized_name

            # Register in tool specs with SANITIZED name for OpenAI function-calling
            TOOL_DEFINITIONS[sanitized_name] = ToolDefinition(
                name=sanitized_name,
                description=description,
                parameters=params.get("parameters", {}),
                required=params.get("required", []),
            )

            # Register callable under sanitized name (primary)
            tools_registry.register(sanitized_name)(_mcp_proxy)
            # Also register with original full name as alias
            if full_name != sanitized_name and full_name not in tools_registry.list_tools():
                tools_registry.register(full_name)(_mcp_proxy)
            # Also register with native short name as alias
            if native_name not in tools_registry.list_tools():
                tools_registry.register(native_name)(_mcp_proxy)
            logger.debug(f"MCP tool registered: {sanitized_name}")

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server. Returns Morphix-format result dict."""
        if not self._initialized:
            return {
                "success": False,
                "error": "mcp_not_connected",
                "output": f"MCP server '{self.config.name}' not connected",
            }

        # Strip mcp: prefix to get the native tool name
        native_name = name.split(".", 1)[-1] if "." in name else name

        try:
            result = await self._send_request(
                "tools/call",
                {"name": native_name, "arguments": arguments},
            )
            return mcp_result_to_morphix(result)
        except Exception as e:
            return {
                "success": False,
                "error": "mcp_tool_error",
                "output": f"MCP tool '{name}' error: {e}",
            }

    async def _send_request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        if self.process is None or self.process.stdin is None:
            raise ConnectionError("MCP client not connected")

        self._request_id += 1
        msg_id = self._request_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        await write_message(self.process.stdin, build_request(msg_id, method, params))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def _read_loop(self) -> None:
        """Background task: read responses from server stdout and resolve futures."""
        if self.process is None or self.process.stdout is None:
            return
        try:
            while True:
                msg = await read_message(self.process.stdout)
                if is_response(msg):
                    msg_id = get_id(msg)
                    if msg_id is not None and msg_id in self._pending:
                        if "error" in msg:
                            self._pending[msg_id].set_exception(
                                RuntimeError(msg["error"].get("message", "MCP error"))
                            )
                        else:
                            self._pending[msg_id].set_result(msg.get("result", {}))
        except ConnectionError:
            logger.info(f"MCP server '{self.config.name}' stream closed")
        except asyncio.CancelledError:
            logger.warning("MCP read loop cancelled", exc_info=True)
        except Exception:
            logger.exception(f"MCP server '{self.config.name}' read loop error")

    async def disconnect(self) -> None:
        """Terminate the MCP server subprocess."""
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None

        # Reject all pending futures
        for _msg_id, future in self._pending.items():
            if not future.done():
                future.set_exception(ConnectionError("MCP client disconnected"))
        self._pending.clear()

        if self.process is not None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except (ProcessLookupError, TimeoutError):
                try:
                    self.process.kill()
                except ProcessLookupError:
                    logger.warning(
                        "MCP process already terminated, ignoring kill failure", exc_info=True
                    )
        self.process = None
        self._initialized = False
        self._tools.clear()
        logger.info(f"MCP server '{self.config.name}' disconnected")

    @property
    def tools(self) -> dict[str, dict]:
        return self._tools


async def connect_mcp_servers(workspace: str) -> None:
    """Connect to all MCP servers configured for a workspace."""
    from core.mcp.config import load_mcp_servers

    configs = load_mcp_servers(workspace)
    for cfg in configs:
        if cfg.name in _clients:
            continue
        client = MCPClient(cfg)
        if await client.connect():
            _clients[cfg.name] = client


async def disconnect_mcp_servers() -> None:
    """Disconnect all MCP clients."""
    for name in list(_clients):
        await _clients[name].disconnect()
    _clients.clear()


def get_mcp_client_for_tool(tool_name: str) -> "MCPClient | None":
    """Find the MCP client that owns a given tool name (mcp:<prefix>.name)."""
    if not tool_name.startswith("mcp:"):
        return None
    prefix = tool_name[4:].rsplit(".", 1)[0] if "." in tool_name else tool_name[4:]
    for client in _clients.values():
        if client.config.tools_prefix == prefix:
            return client
    return None
