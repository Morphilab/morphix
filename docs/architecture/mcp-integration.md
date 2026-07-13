# MCP Integration

Morphix supports the **Model Context Protocol (MCP)** as both a **server** and a **client**. MCP enables Morphix to expose its tools to external applications and consume tools from external MCP servers.

## Protocol Overview

**File:** `core/mcp/protocol.py`

MCP uses **JSON-RPC 2.0 over stdio** with newline-delimited JSON framing.

```
One JSON object per line (no pretty-print, no embedded newlines)
```

### Protocol primitives

```python
MCP_PROTOCOL_VERSION = "2024-11-05"

# Request/response cycle
build_request(msg_id, method, params)        # {"jsonrpc": "2.0", "id": 1, "method": "...", "params": {...}}
build_response(msg_id, result)               # {"jsonrpc": "2.0", "id": 1, "result": {...}}
build_error(msg_id, code, message)           # {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "..."}}

# Notifications (no id, no response expected)
build_notification(method, params)           # {"jsonrpc": "2.0", "method": "...", "params": {...}}

# Message type detection
is_notification(msg)  # "id" not in msg
is_response(msg)      # "result" or "error" in msg
get_id(msg)           # msg.get("id")
```

### Stream I/O

```python
async def read_message(stream: asyncio.StreamReader) -> dict
async def write_message(stream: asyncio.StreamWriter, data: dict) -> None
```

Messages are read line-by-line from stdin/stdout, parsed as JSON.

## MCP Server — Exposing Morphix Tools

**File:** `core/mcp/server.py`

The MCP server exposes Morphix's tool suite to external MCP clients (opencode, Claude Desktop, etc.).

### Starting the server

```bash
poetry run morphix-mcp
# or
poetry run python -m core.mcp.server
```

### Lifecycle

```
1. Client connects via stdio
2. Client sends: initialize
3. Server responds: protocolVersion, capabilities, serverInfo
4. Client sends: notifications/initialized
5. Client can now: tools/list, tools/call
```

### Supported methods

| Method | Handler | Description |
|--------|---------|-------------|
| `initialize` | `_handle_initialize` | Protocol handshake, capabilities exchange |
| `tools/list` | `_handle_tools_list` | Returns all registered Morphix tools as MCP tool schemas |
| `tools/call` | `_handle_tools_call` | Executes a Morphix tool and returns the result |

### Tools exposed

All tools from `TOOL_DEFINITIONS` (in `tools/specs.py`) are exposed — 11 function-calling tools. Each tool is converted to MCP format via the adapter:

```python
def _get_tools(self) -> list[dict]:
    from tools.specs import TOOL_DEFINITIONS
    tools = []
    for name, tdef in sorted(TOOL_DEFINITIONS.items()):
        tool_dict = {
            "name": name, "description": tdef.description,
            "parameters": tdef.parameters, "required": tdef.required,
        }
        tools.append(morphix_to_mcp_tool(tool_dict))
    return tools
```

### Tool execution

`tools/call` invokes tools through `safe_tool_call()` — the same wrapper used internally, ensuring Safety Net and hook interception apply uniformly.

!!! note "`ask_clarification` is NOT exposed via MCP"
    The `ask_clarification` tool is interception-only — it pauses workflows for user input and has no meaning in a headless MCP context. It is intentionally excluded from MCP's `tools/list`.

## MCP Client — Connecting External Servers

**File:** `core/mcp/client.py`

Morphix can connect to external MCP servers and use their tools as if they were native Morphix tools.

### Configuration

MCP server configs are loaded from JSON files:

- **Global**: `mcp_servers.json` (project root)
- **Per-workspace**: `workspaces/<name>/mcp_servers.json` (overrides global for same-named servers)

```json
[
  {
    "name": "playwright",
    "command": "npx",
    "args": ["-y", "@anthropic/mcp-server-playwright"],
    "env": {},
    "enabled": true,
    "tools_prefix": "playwright"
  }
]
```

### Connection lifecycle

```python
async def connect_mcp_servers(workspace: str) -> None:
    """Connect to all MCP servers configured for a workspace."""
    configs = load_mcp_servers(workspace)
    for cfg in configs:
        client = MCPClient(cfg)
        if await client.connect():
            _clients[cfg.name] = client

async def disconnect_mcp_servers() -> None:
    """Disconnect all MCP clients."""
```

### Tool registration

Discovered MCP tools are registered in Morphix with namespacing:

```
mcp:<tools_prefix>.<tool_name>    →    mcp_playwright_navigate
```

The client sanitizes names for DeepSeek strict mode (replacing `:` and `.` with `_`):

```python
sanitized_name = full_name.replace(":", "_").replace(".", "_")
```

Three registration aliases are created for each tool:
1. **Sanitized name** (primary): `mcp_playwright_navigate`
2. **Full name** (alias): `mcp:playwright.navigate`
3. **Native short name** (alias): `navigate`

Each is a proxy function that forwards calls to the MCP server via `client.call_tool()`.

### Tool calling

```python
async def call_tool(self, name: str, arguments: dict) -> dict:
    """Call a tool on the MCP server. Returns Morphix-format result dict."""
    native_name = name.split(".", 1)[-1]
    result = await self._send_request("tools/call", {"name": native_name, "arguments": arguments})
    return mcp_result_to_morphix(result)
```

### Finding the right client

```python
def get_mcp_client_for_tool(tool_name: str) -> MCPClient | None:
    """Find the MCP client that owns a given tool name."""
```

Used by `ToolOrchestrator` to route MCP-prefixed tools to the correct client.

## Adapter Layer

**File:** `core/mcp/adapter.py`

Converts between Morphix (OpenAI function-calling format) and MCP schema.

### Format conversion

```
Morphix format:                        MCP format:
{                                       {
  "name": "file_manager",                 "name": "file_manager",
  "description": "...",                   "description": "...",
  "parameters": {                         "inputSchema": {
    "type": "object",                       "type": "object",
    "properties": {...}                     "properties": {...}
  },                                      },
  "required": [...]                       "required": [...]
}                                       }
```

```python
def morphix_to_mcp_tool(tool: dict) -> dict:
    """Morphix → MCP tool schema"""

def mcp_tool_to_morphix_params(mcp_tool: dict) -> dict:
    """MCP → Morphix params dict"""

def mcp_result_to_morphix(result: dict) -> dict:
    """MCP content array → Morphix {success, output} dict"""
```

## Example: Connecting Playwright MCP Server

### 1. Create config

```json
// workspaces/main/mcp_servers.json
[
  {
    "name": "playwright",
    "command": "npx",
    "args": ["-y", "@anthropic/mcp-server-playwright"],
    "tools_prefix": "playwright"
  }
]
```

### 2. System connects on workspace switch

```python
# Called during workspace loading
await connect_mcp_servers("main")
```

### 3. Tools appear in registry

```
mcp_playwright_navigate       (sanitized, for OpenAI function-calling)
mcp:playwright.navigate        (original MCP name)
navigate                       (native short name)
```

### 4. Agent can use them

The LLM sees these tools in `TOOL_DEFINITIONS` alongside native Morphix tools. When the model calls `mcp_playwright_navigate`, the tool orchestrator routes to `get_mcp_client_for_tool()` → `client.call_tool()`.

## Integration with ToolOrchestrator

MCP tools integrate transparently with the tool orchestration layer:

1. **Registration**: MCP tools are added to `tools_registry` and `TOOL_DEFINITIONS` at connect time
2. **Routing**: `ToolOrchestrator` checks `tool_name.startswith("mcp:")` to route to MCP
3. **Safety Net**: `safe_tool_call()` wrapper applies to MCP tools as well (configurable)
4. **Hooks**: The hook system fires events for MCP tool calls like any other tool call

## Architecture Diagram

```
┌──────────────────────────────────────────────────────┐
│ External MCP Client (e.g., Claude Desktop, opencode)  │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio
               ▼
┌──────────────────────────────────────────────────────┐
│ MCPServer (core/mcp/server.py)                        │
│   tools/list → TOOL_DEFINITIONS → morphix_to_mcp_tool │
│   tools/call → safe_tool_call → tool result           │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│ MCPClient (core/mcp/client.py)                        │
│   connect → spawn subprocess → initialize             │
│   tools/list → register in tools_registry              │
│   tools/call → proxy to external server                │
└──────────────┬───────────────────────────────────────┘
               │ JSON-RPC over stdio (subprocess)
               ▼
┌──────────────────────────────────────────────────────┐
│ External MCP Server (e.g., @anthropic/mcp-server-*)   │
└──────────────────────────────────────────────────────┘
```
