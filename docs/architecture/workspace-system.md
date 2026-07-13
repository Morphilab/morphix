# Workspace System

Morphix uses **PostgreSQL schemas** as the isolation mechanism for workspaces. Each workspace is a fully independent namespace with its own tables, agents, tools, workflows, and hooks.

## Schema Naming Convention

Workspace names must match `^[a-z][a-z0-9_]*$` — lowercase letters and numbers only, starting with a letter. Underscores are permitted. Examples:

- `main` (default, always exists)
- `project_alpha`
- `team2_web`

Invalid names (rejected with `ValueError`):

- `Main` (uppercase)
- `123project` (starts with digit)
- `my-project` (hyphens)

Validation happens in `core/database.py:48-49`:

```python
if not re.match(r"^[a-z][a-z0-9_]*$", schema):
    raise ValueError(f"Nombre de schema inválido: '{schema}'")
```

## The `search_path` Mechanism

Every async session sets PostgreSQL's `search_path` to the current workspace schema before yielding:

```python
# core/database.py:108-122
@asynccontextmanager
async def get_async_session():
    factory = get_async_session_factory()
    async with _get_schema_lock():
        current_schema = _current_async_schema
    session = factory()
    try:
        await session.execute(text(f"SET search_path TO {current_schema}"))
        yield session
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error(f"Error en sesión asíncrona: {e}")
        raise
    finally:
        await session.close()
```

A schema-level lock (`_get_schema_lock()`) ensures the schema name doesn't change mid-session. The lock is **loop-aware** — it's recreated when the running event loop changes to avoid `"bound to a different loop"` errors in test suites.

### Why `search_path`?

- **Transparent**: ORM models don't need workspace-aware queries. `SELECT * FROM conversation` automatically resolves to the current schema's table.
- **Safe**: A missing `WHERE workspace_id = ?` clause can't leak data — the schema boundary is enforced by PostgreSQL itself.
- **Performant**: No application-level filtering overhead.

## Workspace Lifecycle

### Creation

A workspace is created on first use via `Workspaces.switch_workspace()`:

```python
# core/workspaces.py:30-92 — simplified
async def switch_workspace(self, name: str) -> bool:
    async with self._switch_lock:
        await create_schema(name)          # CREATE SCHEMA IF NOT EXISTS
        await create_tables_in_schema(name) # SQLModel.metadata.create_all
        await set_async_schema(name)        # Set current schema

        # Bootstrap workspace files from templates
        self._bootstrap_workspace_agents(...)
        self._bootstrap_workspace_workflows(...)
        self._bootstrap_workspace_hooks(...)

        # Load workspace-specific agents, tools, hooks
        unload_workspace_agents()
        load_workspace_agents(name)

        unload_workspace_tools()
        load_workspace_tools(name)

        unload_workspace_hooks()
        load_workspace_hooks(name)

        # Reconnect MCP servers for new workspace
        await disconnect_mcp_servers()
        await connect_mcp_servers(name)

        self.current = name
        switch_workflow_state(name)
```

### Table Creation

`create_tables_in_schema()` sets the `search_path` and calls `SQLModel.metadata.create_all`:

```python
# core/database.py:135-141
async def create_tables_in_schema(schema: str) -> None:
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f"SET search_path TO {schema}"))
        await conn.run_sync(SQLModel.metadata.create_all)
```

Each schema contains these tables:

| Table | Purpose |
|-------|---------|
| `Conversation` | Conversation metadata (title, timestamps, status) |
| `Message` | Individual messages with role, content, and tool_call_id |
| `Workflow` | Workflow execution records (scorecards, subtasks) |
| `User` | User profiles and preferences |
| `PausedSession` | Persisted state for clarification requests (question, paused loop state) |
| `BlackboardEntry` | Shared key-value storage for coordinated workflows (`blackboard_entries` table) |

### Bootstrap from Templates

When a workspace is created for the first time, template files are copied from the global `templates/` directory:

- `templates/agents/*.yaml` → `workspaces/<name>/agents/`
- `templates/workflows/*.yaml` → `workspaces/<name>/workflows/`
- `templates/hooks/*.yaml` → `workspaces/<name>/hooks/`

If the workspace directory already exists and contains files, templates are **not** overwritten.

### Agent and Tool Loading

**Agents** are loaded from YAML profiles:

```python
from agents.loader import load_workspace_agents, unload_workspace_agents
unload_workspace_agents()     # Clear workspace agents from global registry
load_workspace_agents(name)   # Load new workspace's agent profiles
```

Agents are registered in the global `agents_registry` (an `AgentsRegistry` instance).

**Tools** are loaded from `.py` files:

```python
from tools.loader import load_workspace_tools, unload_workspace_tools
unload_workspace_tools()     # Clear workspace tools
load_workspace_tools(name)   # Load workspace-specific tools
```

Tools use decorator-based registration: `@tools_registry.register("name")`.

Global tools in `tools/*.py` are loaded once at startup and persist across workspace switches.

### MCP Server Reconnection

MCP (Model Context Protocol) servers are workspace-specific. On switch, all existing connections are closed and new ones established:

```python
from core.mcp.client import connect_mcp_servers, disconnect_mcp_servers
await disconnect_mcp_servers()
await connect_mcp_servers(name)
```

### Memory Switching

The FAISS memory index is workspace-scoped:

```python
await memory.switch_workspace(name)
```

### Fallback to `main`

If a workspace switch fails, the system falls back to `main`:

```python
# core/workspaces.py:93-100
except Exception as e:
    retries -= 1
    if name != "main":
        logger.warning(f"Fallback a 'main' desde '{name}': {e}")
        name = "main"
        retries = max(retries, 0)
    else:
        logger.critical(f"switch_workspace('main') falló: {e}", exc_info=True)
```

## Loop-Aware Engine

The async engine must be recreated when the running event loop changes. This is critical for test suites where each test function gets its own event loop (`pytest-asyncio` with `asyncio_mode = "auto"`):

```python
# core/database.py:55-83
def _get_async_engine():
    global _async_engine, _async_session_factory, _engine_loop
    loop = _current_running_loop()
    if (
        _async_engine is not None
        and _engine_loop is not None
        and loop is not None
        and _engine_loop is not loop
    ):
        # Loop changed — drop stale engine
        _async_engine = None
        _async_session_factory = None
    if _async_engine is None:
        async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
        _async_engine = create_async_engine(
            async_url,
            echo=False,
            pool_size=settings.db_pool_size,       # Default: 5
            max_overflow=settings.db_max_overflow,  # Default: 10
            pool_pre_ping=settings.db_pool_pre_ping, # Default: True
            pool_recycle=settings.db_pool_recycle,   # Default: 3600
        )
        _engine_loop = loop
    return _async_engine
```

Without this, asyncpg connections from a previous (now-closed) loop would raise `"bound to a different loop"` errors.

## Database Pool Settings

| Setting | Default | `.env` Variable | Description |
|---------|---------|-----------------|-------------|
| `pool_size` | 5 | `DB_POOL_SIZE` | Number of persistent connections |
| `max_overflow` | 10 | `DB_MAX_OVERFLOW` | Extra connections beyond pool_size |
| `pool_pre_ping` | `true` | `DB_POOL_PRE_PING` | Verify connection liveness before use |
| `pool_recycle` | 3600 | `DB_POOL_RECYCLE` | Max connection age in seconds |

## Database URL Rewriting

User configuration uses standard PostgreSQL URLs. The system transparently adds the `+asyncpg` driver prefix:

```python
async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://").replace(
    "postgres://", "postgresql+asyncpg://"
)
```

Example:

- `.env`: `DATABASE_URL=postgresql://user:pass@localhost:5432/morphix`
- Internal: `postgresql+asyncpg://user:pass@localhost:5432/morphix`

## Cleanup

`dispose_engine()` closes the connection pool cleanly:

```python
# core/database.py:86-94
async def dispose_engine() -> None:
    global _async_engine, _async_session_factory, _engine_loop
    if _async_engine is not None:
        await _async_engine.dispose()
        _async_engine = None
        _async_session_factory = None
        _engine_loop = None
```

This is called during graceful shutdown. Workspace schemas can be removed with `drop_schema()`:

```python
async def drop_schema(schema: str) -> None:
    engine = _get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
```

## Workspace Listing

```python
async def list_schemas() -> list[str]:
    engine = _get_async_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg_%' AND schema_name != 'information_schema'"
            )
        )
        return [row[0] for row in result.fetchall()]
```

Excludes PostgreSQL system schemas (`pg_catalog`, `pg_toast`, etc.) and `information_schema`.
