# Core Layer

The `core/` layer contains all business logic with **zero UI dependencies**. It is the single source of truth for database access, configuration, memory, security, code execution, and system-level utilities.

## Module Inventory

### Database (`database.py`)

> Loop-aware async engine, workspace schemas, session factory, pool settings.

Key components:

```python
# Engine management
_get_async_engine()              # Creates/reuses async engine, handles loop changes
dispose_engine()                 # Clean pool shutdown

# Session management
get_async_session_factory()      # Returns async_sessionmaker
get_async_session()              # Context manager — sets search_path, yields session

# Schema management
set_async_schema(schema: str)    # Switch current schema (validated: ^[a-z][a-z0-9_]*$)
```

Features:

- **Loop-aware**: The engine is recreated if the running asyncio event loop changes (critical for per-test event loops in pytest-asyncio).
- **URL rewriting**: `postgresql://` → `postgresql+asyncpg://` for the async engine.
- **Schema isolation**: Every `get_async_session()` executes `SET search_path TO {schema}` on the connection, implementing workspace-level database isolation.
- **Pool settings**: Configurable via `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_PRE_PING`, `DB_POOL_RECYCLE`.
- **Schema lock**: Thread-safe schema switching via an event-loop-bound `asyncio.Lock`.

### Configuration (`config.py`)

> Pydantic-settings model with all environment variables.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # General
    dark_mode: bool               # DARK_MODE (default: true)
    offline_mode: bool            # OFFLINE_MODE (default: false)

    # API Keys
    openai_api_key: str           # OPENAI_API_KEY
    deepseek_api_key: str         # DEEPSEEK_API_KEY
    grok_api_key: str             # GROK_API_KEY
    google_api_key: str           # GOOGLE_API_KEY

    # Infrastructure
    ollama_base_url: str          # OLLAMA_BASE_URL (default: http://localhost:11434)
    database_url: str             # DATABASE_URL
    redis_url: str                # REDIS_URL
    ollama_model: str             # OLLAMA_MODEL (default: phi3:mini)
    llm_timeout: int              # LLM_TIMEOUT (default: 60)
    deepseek_strict_mode: bool    # DEEPSEEK_STRICT_MODE
    max_context_tokens: int       # MAX_CONTEXT_TOKENS (default: 128000)

    # Security
    encryption_key: str           # ENCRYPTION_KEY (auto-gen in dev, required in prod)
    password_hash: str            # PASSWORD_HASH

    # Model roles (centralized model selection)
    model_roles: dict             # Per-role provider/model/temperature config
```

**Model roles** define which provider and model to use for each role:

```python
model_roles = {
    "default":    {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.7},
    "fast":       {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.3},
    "reasoning":  {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.0},
    "agent":      {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.7},
    "creative":   {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.9},
    "critique":   {"provider": "deepseek", "model": "deepseek-v4-flash", "temperature": 0.0},
}
```

**Encryption key** validation: In production (`MORPHIX_ENV=production`), a missing `ENCRYPTION_KEY` raises `ValueError`. In development, a temporary key is auto-generated.

### Path Resolver (`path_resolver.py`)

> Centralized paths — never hardcode paths.

```python
paths = PathResolver()  # Global singleton

# All paths derive from project root (parent of core/)
paths.memory_base()              # → memory/
paths.memory_dir(workspace)      # → memory/{workspace}/
paths.workspaces_base()          # → workspaces/
paths.workspace_dir(name)        # → workspaces/{name}/
paths.templates_dir()            # → templates/
paths.charts_dir()               # → charts/
paths.exports_dir()              # → exports/
paths.log_file()                 # → logs/morphix.log
paths.analytics_charts_dir()     # → charts/analytics/
paths.mcp_servers_file(ws)       # → workspaces/{ws}/mcp_servers.json
paths.workspace_tools_dir(ws)    # → workspaces/{ws}/tools/
paths.workspace_agents_dir(ws)   # → workspaces/{ws}/agents/
paths.workspace_workflows_dir(ws)# → workspaces/{ws}/workflows/
paths.workspace_hooks_dir(ws)    # → workspaces/{ws}/hooks/
```

### Health Check (`health.py`)

> 5 probes → 6 report rows.

```python
@dataclass
class HealthReport:
    checks: dict[str, dict]
    all_ok: bool

    def add(name: str, ok: bool, detail: str, **extra) -> None
    def format() -> str  # Table with ✅/❌ icons
```

Probes:

| Probe | Function | Checks |
|-------|----------|--------|
| **Database** | `check_database()` | `SELECT 1` on async engine |
| **LLM** | `check_llm()` | HTTP GET to provider API endpoint |
| **Redis** | `check_redis()` | `PING` if configured |
| **Filesystem** | `check_filesystem()` | MEMORY_BASE and TEMPLATES_DIR existence |
| **Workspace** | `check_workspace()` | Active workflow integrity |

Usage: `poetry run python -m core.health`

### Bootstrap (`bootstrap.py`)

> Startup sequence for desktop mode.

```python
validate_config()                          # (bool, list[str]): fatal errors + warnings
async def init_backend(workspace, on_progress)  # Init DB, workspace, agents
async def start_daemons(on_offline_changed)     # Launch Kairos daemon + OfflineManager
async def stop_daemons()                        # Cancel all background tasks
```

Startup sequence:
1. Load `.env` via `python-dotenv`
2. `validate_config()` — check DATABASE_URL, API keys, encryption
3. `init_backend()` — init async engine, switch workspace, load hooks
4. `start_daemons()` — if `DAEMON_MODE=true`, launch Kairos daemon + offline check loop

### Feature Flags (`feature_flags.py`)

Kairos feature flags system (Claude Code style). Enabled/disabled via environment variables and runtime overrides.

### Circuit Breaker (`circuit_breaker.py`)

See [Security Model](../security-model.md) for full details.

```
CircuitBreaker (per provider)
    failure_threshold: int = 5
    recovery_timeout: float = 30.0

    allow_request() → bool
    record_success()
    record_failure()
    state → "closed" | "open" | "half_open"

CircuitBreakerRegistry
    get(provider) → CircuitBreaker
    reset_all()
    get_all_states() → dict
```

### Rate Limiter (`rate_limiter.py`)

See [Security Model](../security-model.md) for full details.

```python
RateLimiter(max_per_minute=20, max_per_hour=200)
    async def acquire() → bool
    async def wait_and_acquire(timeout=30) → bool
    async def remaining() → int
```

### Token Counter (`token_counter.py`)

Lazy-loads `tiktoken` encoding (`cl100k_base`). First call loads (~1-2 MB, ~100ms). Subsequent calls return cached instance.

```python
get_encoding()  # → tiktoken.Encoding | None
```

Returns `None` if `tiktoken` is not installed.

### Cache Manager (`cache_manager.py`)

> Multi-provider prompt cache abstraction with per-workspace stats.

```python
CacheManager (singleton)
    track_usage(response, workspace)     # Extract cache hit/miss from provider usage
    get_stats(workspace) → dict          # Hit rate, token savings
    stabilize_messages(messages)         # Keep prefix intact for DeepSeek disk caching
```

Monitors `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` from provider response `usage` fields. Future-ready for Anthropic ephemeral caching and OpenAI automatic caching.

### Context Manager (`context_manager.py`)

> Intelligent context window management for LLMs.

```python
class ContextManager:
    CHARS_PER_TOKEN = 3.5

    estimate_tokens(messages) → int      # Character-based estimation + 4 token overhead/msg
    compress_history(messages, max_tokens) → list[dict]  # Trim to fit budget
    chunk_large_file(content, file_path, chunk_size) → list[dict]  # Split large files
```

### Change Tracker (`change_tracker.py`)

> Undo/redo for file operations.

```python
class ChangeTracker:
    save_backup(file_path)              # Save pre-write snapshot
    get_backup(file_path) → Path | None # Retrieve backup
    undo(file_path) → bool              # Restore from backup
    clear_backups(age_days=7)           # Purge old backups
```

Backups are stored with URL-encoded paths in a dedicated directory. Each `file_manager.write` saves a backup beforehand.

### Codebase Indexer (`codebase_indexer.py`)

> Semantic indexing of project codebases with FAISS + disk cache.

```python
CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java",
                    ".c", ".cpp", ".h", ".hpp", ".rb", ".php", ".swift", ...}

class CodebaseIndexer:
    def __init__(self, workspace="main", project_root=None)
    index_project(patterns, max_files, force, progress_callback) → int
    search(query, k=10) → list[dict]
    find_relevant_code(task, max_results=5) → str
```

Chunks files by function/class boundaries where possible. Uses file hash for cache invalidation.

### Embedding Provider (`embedding_provider.py`)

See [Memory System](../memory-system.md) for full details.

```python
EmbeddingProvider
    _model_name = "intfloat/multilingual-e5-large"
    get_instance() → SentenceTransformer | None
    encode(text) → ndarray | None
    wait_until_ready(timeout=60) → bool
```

### FAISS Indexer (`faiss_indexer.py`)

See [Memory System](../memory-system.md) for full details.

```python
FAISSIndexer(dimension=1024)
    add(key, value)
    search(query, k=5) → list[dict]
    remove(key)
    rebuild_index()
    save(directory) / load(directory)
```

### Git Operations (`git_operations.py`)

> Centralized git helper for auto-commit and common operations.

```python
async def auto_commit(workspace, project_root=None, message="Auto-commit") → dict
    # git init → git add -A → git commit -m message

async def smart_auto_commit(workspace, project_root=None, task_description="", files_written=None) → dict
    # Generates commit message via LLM based on task description
```

Uses `safe_tool_call` to invoke `git_manager` tool. Supports LLM-generated commit messages based on the task context.

### Workspaces (`workspaces.py`)

> List, create, switch, delete workspaces.

```python
class Workspaces:
    current: str = "main"

    async def list_workspaces() → list[str]
    async def switch_workspace(name, retries=1) → bool
    async def create_workspace(name) → bool
    async def delete_workspace(name) → bool
```

On `switch_workspace`:
1. Creates PostgreSQL schema if not exists
2. Creates tables in schema
3. Sets `search_path` via `set_async_schema()`
4. Copies templates (agents, workflows) if first switch
5. Switches memory workspace
6. Updates workflow state
7. Connects MCP servers
8. Loads workspace tools and hooks

### Workflow State (`workflow_state.py`)

> Tracks the active workflow per workspace.

```python
set_active_workflow(name)      # Set for current workspace
get_active_workflow() → str    # Get for current workspace (defaults to settings.default_workflow)
switch_workspace(workspace)    # Update current workspace without losing saved preferences
```

Stores workflow preferences in memory across workspace switches.

### Metrics (`metrics.py`)

> Cumulative system usage counters.

```python
@dataclass
class Metrics (singleton):
    total_tokens: int           # LLM tokens consumed
    total_workflows: int        # Workflows started
    completed_workflows: int    # Successful completions
    failed_workflows: int       # Failed workflows
    tool_calls: int             # Total tool invocations
    llm_calls: int              # Total LLM calls
    rate_limited: int           # Times rate-limited
    cache_hit_tokens: int       # DeepSeek cache hits
    cache_miss_tokens: int      # DeepSeek cache misses

    record_workflow_completed(tokens, tool_calls)
    record_rate_limited()
    get_report() → dict
```

### LRU Cache (`lru_cache.py`)

> Thread-safe, TTL-based LRU cache for LLM response caching.

```python
LRUCache(max_size=500, ttl=300)
    get(key) → Any | None
    set(key, value)
    invalidate(key)
    clear()
    size → int
```

Used by `TaskAnalyzer` and `AgentRouter` to cache decomposition and routing results.

### Utils (`utils.py`)

```python
def clean_llm_response(response) → str:
    """Strip LLM response down to plain text.
    Handles: OpenAI objects, coroutine detection, watermarks, JSON blocks."""
```

### Models (`models.py`)

SQLModel definitions for the database:

```python
class Conversation(SQLModel, table=True):
    id: int, title: str, created_at: datetime, tags: str, workflow_id: int

class Message(SQLModel, table=True):
    id: int, conversation_id: int, role: str, content: str, timestamp: datetime

class Workflow(SQLModel, table=True):
    id: int, query: str, subtasks: str, status: str, scorecard: str | None, created_at: datetime

class User(SQLModel, table=True):
    id: int, username: str, password_hash: str

class PausedSession(SQLModel, table=True):
    # Persisted state for clarification-request pauses
```

### Hooks Registry (`hooks_registry.py`)

> 6 interception points for tool call lifecycle.

```python
@dataclass
class HookContext:
    hook_point: str      # on_before_tool, on_after_tool, on_tool_error, ...
    tool_name: str
    parameters: dict
    role: str
    result: Any
    error: str | None
    duration: float
    attempt: int
    workspace: str
    session_id: str | None

class HooksRegistry:
    register(hook_point: str)(callable)   # Decorator-based registration
    execute(hook_point, context)          # Fire all hooks for a point
    clear()                               # Remove all hooks
```

**Hook points:**

| Hook point | Trigger |
|------------|---------|
| `on_before_tool` | Before tool execution |
| `on_after_tool` | After successful tool execution |
| `on_tool_error` | After tool failure |
| `on_permission_denied` | When tool call is blocked by security |
| `on_token_budget_exceeded` | When context exceeds limit |
| `on_tools_disabled` | When tool execution is globally disabled |

### Hook Loader (`hook_loader.py`)

> Dynamic loading of workspace hook modules.

```python
def load_global_hooks()                    # Load from hooks/ directory
def load_workspace_hooks(workspace)        # Load from workspaces/{name}/hooks/
def unload_workspace_hooks(workspace)      # De-register workspace hooks
```

Hooks are `.py` files loaded dynamically via `importlib`, using the `@hooks_registry.register("hook_point")` decorator pattern.

## Subsystems

### `memory/` — Memory Manager

See [Memory System](../memory-system.md) for full documentation.

```
memory/manager.py           # MemoryManager singleton (CRUD, self-healing, workspace isolation)
```

### `security/` — Security

See [Security Model](../security-model.md) for full documentation.

```
security/undercover_mode.py      # UndercoverMode singleton (query blocking, output scrubbing)
security/anti_distillation.py    # WatermarkRotator, DistillationTracker, HoneypotInjector
security/frustration_detector.py # FrustrationDetector (regex patterns, calming prompts)
```

### `mcp/` — MCP Protocol

See [MCP Integration](../mcp-integration.md) for full documentation.

```
mcp/server.py     # MCPServer — expose Morphix tools over stdio JSON-RPC
mcp/client.py     # MCPClient — connect to external MCP servers
mcp/adapter.py    # Format conversion (Morphix ↔ MCP)
mcp/config.py     # MCPServerConfig, load_mcp_servers()
mcp/protocol.py   # JSON-RPC 2.0 framing, read/write helpers
```

### `sandbox/` — Code Execution

See [Security Model](../security-model.md) for full details.

```
sandbox/restricted_executor.py   # RestrictedExecutor with SAFE_MODULES allowlist
```

### `hooks/` — Hook Implementations

```
hooks/audit.py              # Logs every tool call to memory for audit trail
hooks/distillation_guard.py # Additional distillation checks at the hook level
```

### `repositories/` — Data Access

```
repositories/conversation_repository.py  # Conversation + Message CRUD operations
```

## Layer Boundaries

The `core/` layer:

- **Imports from**: Standard library, third-party libraries (sqlalchemy, pydantic, faiss, etc.)
- **Does NOT import from**: `desktop/` (UI), `tools/` (individual tool implementations)
- **Is imported by**: `llm/`, `agents/`, `tools/` (registry/loader), `orchestration/`, `desktop/services/`
- **Exposes**: Global singletons (`settings`, `memory`, `paths`, `undercover`, `metrics`)
