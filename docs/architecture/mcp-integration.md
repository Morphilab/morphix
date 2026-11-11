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

The MCP server exposes Morphix's tool suite to external MCP clients.

### Starting the server

```bash
uv run python -m core.mcp.server
```

!!! note "No console-script entrypoint"
    `pyproject.toml` uses `tool.uv package = false`, so uv does not install the `[project.scripts]` entrypoints — `uv run morphix-mcp` does not exist. Always invoke the module form above.

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
| `ping` | `_handle_ping` | Spec MCP: prompt empty response (liveness) |
| `tools/list` | `_handle_tools_list` | Returns all registered Morphix tools as MCP tool schemas |
| `tools/call` | `_handle_tools_call` | Executes a Morphix tool and returns the result |

### Tools exposed

All tools from `TOOL_DEFINITIONS` (in `tools/specs.py`) are exposed — 23 function-calling tools. Each tool is converted to MCP format via the adapter:

```python
def _get_tools(self) -> list[dict]:
    from tools.specs import TOOL_DEFINITIONS
    tools = []
    for name, tdef in sorted(TOOL_DEFINITIONS.items()):
        if self._tools_allowlist is not None and name not in self._tools_allowlist:
            continue
        tool_dict = {
            "name": name, "description": tdef.description,
            "parameters": tdef.parameters, "required": tdef.required,
        }
        tools.append(morphix_to_mcp_tool(tool_dict))
    return tools
```

### Tool execution

`tools/call` invokes tools through `safe_tool_call()` — the same wrapper used internally, ensuring Safety Net and hook interception apply uniformly. **Dangerous tools (`bash_manager`, `code_exec`, `file_manager.delete`, `diff_editor.apply`) fail closed headless**: without an approval callback the call is rejected with `approval_unavailable` (opt-in escape hatch: `ALLOW_UNATTENDED_DANGEROUS=true`).

!!! note "`ask_clarification` is not exposed via MCP"
    `ask_clarification` is interception-only (it pauses workflows for user input)
    and lives in `tools_registry` only — it was never part of `TOOL_DEFINITIONS`,
    which is exactly what the server lists. The effect: it never appears in
    `tools/list`. Adding a registry-only tool to `TOOL_DEFINITIONS` would expose
    it, so interception-only tools must stay out of that dict.

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
    "name": "example",
    "command": "example-mcp-server",
    "args": [],
    "env": {},
    "enabled": true,
    "tools_prefix": "example"
  }
]
```

### Connection lifecycle

```python
async def connect_mcp_servers(workspace: str) -> None:
    """Connect to all MCP servers configured for a workspace (in parallel)."""
    configs = [cfg for cfg in load_mcp_servers(workspace) if cfg.name not in _clients]
    await asyncio.gather(*(connect_one(c) for c in configs))  # concurrente

async def disconnect_mcp_servers() -> None:
    """Disconnect all MCP clients (workspace switch tears down + rebuilds)."""
```

### Resilience (reconnect)

If the server subprocess dies or the stream is closed, the client **fails all pending
requests promptly** (`mcp_stream_closed` — they never wait the full 120s timeout) and
schedules a **background reconnect with exponential backoff** (5 attempts, 1s→30s with
jitter). Line-overflow (`mcp_line_limit_exceeded`) and unexpected read-loop errors follow
the same recovery path. After a successful reconnect the tools are re-registered.

### Tool registration

Discovered MCP tools are registered in Morphix with namespacing:

```
mcp:<tools_prefix>.<tool_name>    →    mcp_example_navigate
```

The client sanitizes names for DeepSeek strict mode (replacing `:` and `.` with `_`, then coercing to the function-calling-safe charset `[A-Za-z0-9_-]` with a 64-char cap; descriptions are capped at 2000 chars — both travel in every LLM payload, so a hostile/broken remote server cannot break or bloat them):

```python
sanitized_name = _sanitize_mcp_tool_name(full_name)  # "mcp:example.navigate" → "mcp_example_navigate"
```

Registration is **guarded against collisions**: the primary name is always the sanitized
one (spec in `TOOL_DEFINITIONS` + proxy in `tools_registry`); the `mcp:<prefix>.<tool>`
full-name alias and the native short name are registered **only if no tool already owns
that name** (a second server exposing the same sanitized name is skipped with a warning,
and the native alias never shadows a built-in or workspace tool). Each registration is
owned by its client (`_SPEC_OWNERS`) and unregistered on disconnect without touching
registrations owned by others.

Each is a proxy function that forwards calls to the MCP server via `client.call_tool()`.

### Per-workspace trust

MCP tools are only callable from the workspace that **declared** their server. The trust
registry lives in `core/mcp/trust.py` as `(workspace, server)` pairs:

- **Recorded at connect time**: `connect_mcp_servers(workspace)` marks every server declared
  in *that* workspace's config as trusted *for that workspace*. Workspace B never inherits
  workspace A's trust — registrations are islands.
- **Gate at execution**: before an MCP proxy tool runs, the wrapper consults
  `trust_gate_check(workspace, tool_name)` (`tools/wrapper.py`). A caller whose workspace
  never registered the tool's server gets a **stable fail-closed JSON error** — the call never
  reaches the remote server.
- **Legacy path**: calls that carry no workspace (the historical global path) bypass the gate;
  the only cross-workspace gap is when a caller *does* carry a workspace it never declared.
- **Lifecycle**: `disconnect_mcp_servers()` clears the registry alongside the clients.

Tests: `tests/test_mcp_trust.py`.

### Tool calling

```python
async def call_tool(self, name: str, arguments: dict) -> dict:
    """Call a tool on the MCP server. Returns Morphix-format result dict."""
    native_name = name.split(".", 1)[-1] if "." in name else name
    # kwargs internos del host (project_root/workspace) NUNCA viajan al
    # server remoto: los argumentos se filtran a las keys del inputSchema
    # declarado; sin schema, viajan sin filtrar con warning auditable.
    declared = self._tools.get(native_name, {}).get("parameters")
    if isinstance(declared, dict) and declared:
        remote_args = {k: v for k, v in arguments.items() if k in declared}
    else:
        remote_args = dict(arguments)
    result = await self._send_request("tools/call", {"name": native_name, "arguments": remote_args})
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

## Example: Connecting an MCP Server

### 1. Create config

```json
// workspaces/main/mcp_servers.json
[
  {
    "name": "example",
    "command": "example-mcp-server",
    "args": [],
    "tools_prefix": "example"
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
mcp_example_navigate           (sanitized, for OpenAI function-calling)
mcp:example.navigate           (original MCP name)
navigate                       (native short name)
```

### 4. Agent can use them

The LLM sees these tools in `TOOL_DEFINITIONS` alongside native Morphix tools **only when the active workflow opts in**: the `tools.allowed` list must contain the wildcard `mcp`. `development` and `coordinated` declare the wildcard; a workflow without it never exposes MCP tools to the model. When the model calls `mcp_example_navigate`, the tool orchestrator routes to `get_mcp_client_for_tool()` → `client.call_tool()`.

## Integration with ToolOrchestrator

MCP tools integrate transparently with the tool orchestration layer:

1. **Registration**: MCP tools are added to `tools_registry` and `TOOL_DEFINITIONS` at connect time
2. **Routing**: `safe_tool_call()` (tools/wrapper.py) checks `tool_name.startswith("mcp:")` and fails fast with `mcp_client_not_found` if no connected client owns the prefix; the registered proxy then executes through `tool_orchestrator.execute_tool()` like any other tool — hooks, budget and permissions apply uniformly
3. **Safety Net**: `safe_tool_call()` wrapper applies to MCP tools as well
4. **Hooks**: The hook system fires events for MCP tool calls like any other tool call

## Architecture Diagram

```
┌──────────────────────────────────────────────────────┐
│ External MCP Client                                  │
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
│ External MCP Server (e.g., example-mcp-server)       │
└──────────────────────────────────────────────────────┘
```
