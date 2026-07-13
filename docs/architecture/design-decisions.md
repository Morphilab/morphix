# Design Decisions

This document records *why* key architectural choices were made — the rationale, tradeoffs, and alternatives considered.

## PostgreSQL Schemas for Workspace Isolation

**Decision**: Each workspace maps to a separate PostgreSQL schema (`[a-z][a-z0-9_]*` naming convention) rather than row-level filtering or separate databases.

**Why**:

- **Data isolation at the database level**: `SET search_path TO <schema>` ensures queries never accidentally cross workspace boundaries. A missing `WHERE workspace_id = ?` clause won't silently leak data.
- **No migration conflicts**: New workspaces get fresh tables via `SQLModel.metadata.create_all`. Adding columns to existing workspaces doesn't affect others.
- **Simple cleanup**: `DROP SCHEMA <name> CASCADE` removes all workspace data atomically.
- **Multi-tenancy without overhead**: PostgreSQL handles schema-level isolation natively; no application-level filtering per query.

**Tradeoff**: Cross-workspace queries (e.g., "list all conversations across workspaces") require iterating schemas. This is acceptable because Morphix workspace are designed to be independent silos.

**Implementation**: `core/database.py:114` — every async session executes `SET search_path TO {current_schema}` before yielding.

## 4+ Workflow Routes (Not One-Size-Fits-All)

**Decision**: `WorkflowOrchestrator.run_full_workflow()` dispatches to one of six routes based on message format and active workflow template, rather than running full orchestration for every message.

**Why**:

- **Latency**: A `git commit` command doesn't need task analysis, decomposition, agent routing, and result aggregation. The direct-tool route (Route 1) executes in milliseconds.
- **Resource efficiency**: Full orchestration involves multiple LLM calls (analyzer, decomposer, router, agents, supervisor, aggregator). Avoiding these for simple tasks saves API costs and reduces latency by ~90%.
- **Appropriate complexity**: A casual question ("What does Python's `zip` do?") fits a simple conversation. A multi-file refactoring needs DAG-based coordinated execution with phase tracking and blackboard sharing.
- **Template-driven customization**: Users can define their own workflow templates (`coordinated.yaml`, `development.yaml`, `collaborative.yaml`) with custom agent pools, tool allowlists, and execution strategies. TDD is a hardcoded route (no YAML template).

**The routes** (ordered by precedence in `orchestration/workflows/orchestrator.py:324`):

| Priority | Route | Trigger |
|----------|-------|---------|
| 1 | Direct Tool | Message matches `tool_name: action, key=val` and tool exists in registry |
| 2 | TDD Loop | `active_workflow == "tdd"` |
| 3 | Collaborative | `template.type == "collaborative"` |
| 4 | Coordinated | `template.type == "coordinated"` |
| 5 | Development | `template.type == "development"` (skips TaskAnalyzer) |
| 6 | Default | `TaskAnalyzer` decides: simple conversation or full orchestration |

## ReAct Agent Loop (Reasoning + Acting)

**Decision**: Agents use a **ReAct** (Reasoning → Action → Observation → Adjust) loop with native function-calling rather than a fixed pipeline or purely conversational approach.

**Why**:

- **Autonomous tool use**: The agent decides *which* tool to call and *when* based on the task context. It's not scripted — the LLM reads tool descriptions and decides the next action.
- **Self-correction**: Tool outputs feed back into the conversation as observations. The agent can retry, change strategy, or abandon a dead-end approach.
- **Stall detection**: The loop tracks consecutive iterations without file modifications. After 2 stalled iterations, it performs early exit rather than burning API calls on a stuck agent.
- **Clarification requests**: Agents can pause and ask the user questions via `ask_clarification`, with state persisted to `PausedSession` — survives app restarts.
- **Parallel tool execution**: When the LLM returns multiple tool calls in one response, the loop executes them sequentially and feeds all observations back in one turn.

**Implementation**: `orchestration/loop.py` — `execute_agent_loop()` with configurable `max_agent_iterations` (default 8) and `max_stall_iterations` (default 2).

## DeepSeek Primary + Ollama Fallback

**Decision**: The LLM provider stack defaults to **DeepSeek-v4-flash** for all roles, with automatic fallback to Ollama when connectivity fails or `OFFLINE_MODE=true`.

**Why**:

- **Role-based model selection**: `settings.model_roles` maps agent roles to specific model names. Default: all roles use `deepseek-v4-flash`. Users can configure different models per role (e.g., `fast` role → cheaper model, `architect` → more capable model).
- **Connectivity check**: `LLMProvider` checks API connectivity on startup. If it fails, the system automatically falls back to Ollama.
- **Offline mode**: Setting `OFFLINE_MODE=true` forces Ollama without checking connectivity — useful for air-gapped environments.
- **Circuit breaker**: Each provider has a `CircuitBreaker` that tracks failures. After the threshold is reached, the breaker opens and blocks further requests to that provider for a cooldown period.
- **Ollama argument normalization**: Ollama's API expects dict arguments, not JSON strings. The controller converts `tool_calls.arguments` from string to dict before forwarding to Ollama (see `llm/controller.py:220-232`).

## No Docker

**Decision**: Morphix does not ship with Docker or Docker Compose.

**Why**: The user explicitly rejected containerization. The project runs as a native Python process with Poetry for dependency management. PostgreSQL must be installed separately.

**Tradeoff**: Lacks the "one-command setup" of containerized projects. Users need to install PostgreSQL and configure `.env` manually. Pre-commit hooks and a comprehensive AGENTS.md compensate with clear setup instructions.

## Safety Net Architecture

**Decision**: A secondary analysis agent (`safety_net` role) reviews files before they are committed, acting as a safety layer between agent execution and final output.

**Why**:

- **Analysis agents never fabricate files**: The safety net is an *analysis-only* agent — it reads files, evaluates correctness, and suggests fixes. It never writes files directly.
- **Defense in depth**: Even if the primary agent produces buggy or incomplete code, the safety net catches common issues before the file is committed.
- **Language-aware**: The safety net understands Python syntax and semantics, detecting issues like missing imports, undefined variables, and logic errors.

**Limitations**: The safety net is Python-specific. Non-Python files (`.gitignore`, `README.md`, etc.) are not analyzed.

## 3-Column Cockpit with Resizable Splitter (Sprint 25)

**Decision**: The Maestro tab uses a **3-column layout** with a `QSplitter`: left (execution panel: progress bar + subtask list), center (chat), right (detail tabs: Agentes, Diagrama, Log, Bash).

**Why**:

- **Resizable, not static**: `QSplitter` lets users adjust column widths while keeping default proportions. The center column naturally takes remaining space.
- **No layout thrashing**: Column proportions are stable during workflow execution — subtask status changes don't resize chat or detail panels.
- **Subtask dashboard**: Left panel shows a `QProgressBar` + subtask list with status icons (✅🔵❌⏳). Driven by `subtask_list` key in `emit_stats` payloads.
- **Split status/diagram**: Sprint 26 split the status log and Mermaid diagram into two separate `QTextBrowser` widgets in a `QSplitter` — prevents status messages from overwriting diagram content and vice versa.
- **Framework choice**: PySide6 (Qt for Python) was chosen over Flet/Flutter/Electron for native desktop performance, mature widget library, and deep customization options.

## Context Compression at 90%

**Decision**: When the conversation history exceeds **90%** of `MAX_CONTEXT_TOKENS` (default 128,000), the context is compressed before the next LLM call.

**Why**:

- **Prevent API errors**: Sending messages beyond the model's context window causes API errors. Compression at 90% leaves a 10% safety margin for the response.
- **Compression strategy**: `ContextManager.compress_history()` trims old messages, preserves system prompts, and keeps recent conversation turns intact. The compressed target is 70% of max tokens.
- **Transparent to the user**: Compression happens automatically before each `call()` and `call_stream()`. No user action required.

## PostgreSQL URL Rewriting

**Decision**: The database URL (`postgresql://`) is rewritten to `postgresql+asyncpg://` at engine creation time.

**Why**: SQLAlchemy's async engine requires the `asyncpg` driver prefix. Users configure a standard `postgresql://` URL in `.env`, and the system transparently adds the `+asyncpg` suffix. This keeps configuration simple and portable.

**Implementation**: `core/database.py:70-72`:
```python
async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://").replace(
    "postgres://", "postgresql+asyncpg://"
)
```

## Loop-Aware Async Engine

**Decision**: The async engine is recreated when the running event loop changes (e.g., per-test event loops in pytest-asyncio).

**Why**: asyncpg connections are bound to the event loop that created them. Reusing an engine across different event loops causes `"bound to a different loop"` errors. The loop-aware engine in `_get_async_engine()` detects loop changes and transparently recreates the engine and session factory.

**Implementation**: `core/database.py:55-83` — compares `_engine_loop` to `_current_running_loop()` and drops the stale engine on mismatch.
