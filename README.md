# Morphix — Multi-Agent Orchestration Platform

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![tests](https://img.shields.io/badge/tests-698%20pass-brightgreen)](https://github.com/morphilab/morphix)
[![mypy](https://img.shields.io/badge/mypy-0%20errors-success)](https://github.com/morphilab/morphix)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![sprints](https://img.shields.io/badge/sprints-26-lightgrey)](https://github.com/morphilab/morphix)

**Morphix** is an open-source, layered multi-agent orchestration platform for AI-assisted
software engineering. It coordinates multiple AI agents through four wired workflow
strategies, backed by a clean architecture with per-workspace PostgreSQL isolation and
production-grade infrastructure patterns.

> **Documentation:** Full documentation is available at the [Morphix docs site](https://morphilab.github.io/morphix).
> To browse locally: `poetry run mkdocs serve`.

---

## Project State

| Metric | Value |
|--------|-------|
| Python | **3.12** (`>=3.12,<3.14`), Poetry, `package-mode = false` |
| Tests | **~698** test functions across **78** test modules |
| Sprints / commits | **26** sprints (latest: sprint 26b) &middot; ~270 commits |
| Type checking | mypy on `core/ llm/ agents/ tools/ orchestration/ desktop/` &mdash; 0 errors |
| Default LLM | DeepSeek `deepseek-v4-flash` (all roles), fallback &rarr; Ollama (`phi3:mini`) |
| Tools | **12** registered |
| Agents | **5** profiles |
| Workflows | **4** wired routes |

---

## Why Morphix?

Most AI coding tools are tightly coupled monoliths. Morphix is designed as a **platform**:
a clean, layered architecture that separates concerns so each subsystem can be understood,
tested, and extended independently.

- **Clean layered architecture** &mdash; `core/` &rarr; `llm/` &rarr; `agents/` &rarr; `tools/` &rarr; `orchestration/` &rarr; `desktop/`. Each layer depends only on the layer below it. No circular imports. No UI leaking into business logic.
- **Per-workspace PostgreSQL schemas** &mdash; every workspace lives in its own schema with isolated tables. No data leakage between projects. The active schema is set via `search_path` on every async session.
- **4 workflow strategies** &mdash; development (full orchestration with Safety Net), coordinated (DAG-based parallel execution with blackboard), collaborative (multi-agent panel debate with moderator consensus), and TDD (test-driven loop).
- **Production patterns** &mdash; circuit breaker with fallback, sliding-window rate limiter, RestrictedPython sandbox, FAISS vector memory with auto-healing, token budget with context compression, anti-distillation watermarking.
- **~698 test functions, 0 mypy errors** &mdash; disciplined development across 26 sprints. Async tests with `pytest-asyncio`. Full CI pipeline with PostgreSQL service containers.
- **Dynamic extensibility** &mdash; tools as `.py` files loaded at runtime, agents as YAML profiles, hooks with 6 interception points, MCP integration for external tool servers.

> **Coverage note:** Test coverage is ~21%. The test suite uses mock-heavy patterns
> (387 MagicMock/AsyncMock, 238 `patch()` context managers) due to async LLM dependencies
> that require external API keys. Integration tests for orchestration paths are
> planned. See [ROADMAP.md](ROADMAP.md).

---

## Quick Start

```bash
# Prerequisites: Python 3.12, PostgreSQL, Poetry
poetry install --with dev
cp example.env .env          # edit DATABASE_URL + at least one API key (DEEPSEEK_API_KEY)
poetry run alembic upgrade head
poetry run python run.py     # launch the desktop GUI
```

> Alembic migrations are **manual**. `startup_db()` only creates tables directly; run
> `poetry run alembic upgrade head` for migration-based schema changes.

---

## Architecture

```
Desktop GUI (PySide6)
        │
        ▼
WorkflowOrchestrator.run_full_workflow()   ── routing precedence ──
   1. Direct tool command   "tool_name: action, key=val"
   2. TDD loop              (active workflow == "tdd")
   3. Collaborative         (template.type == "collaborative")
   4. Coordinated           (template.type == "coordinated")
   5. Development           (template.type == "development")
   6. Default               TaskAnalyzer → simple conversation | full orchestration
        │
        ▼
   Agent Loop (ReAct)  ←→  ToolOrchestrator ──→ Tools (file/git/bash/code/search/MCP)
        │                          │
   LLM provider                Hooks (pre/post/error) · Permission UI ·
   (DeepSeek/OpenAI/Ollama)    Token budget + cache · Anti-distillation
```

### Layer Map

Morphix follows a strict layered architecture with clean boundaries:

| Layer | Directory | Role |
|-------|-----------|------|
| **Core** | `core/` | Business logic &mdash; database, config, memory (FAISS), security, MCP, path resolution |
| **LLM** | `llm/` | AI abstraction &mdash; role-based model selection, DeepSeek/OpenAI/Ollama providers |
| **Agents** | `agents/` | Agent system &mdash; registry, loader, profiles (5 agents), execution, audit |
| **Tools** | `tools/` | Tool system &mdash; specs, registry, orchestrator with hooks, 12 implementations |
| **Orchestration** | `orchestration/` | Workflow orchestration &mdash; analyzer, decomposer, router, supervisor, 4 workflows |
| **Desktop** | `desktop/` | PySide6 GUI &mdash; dashboard, cockpit, editor, analytics, history |

### Workspaces Are PostgreSQL Schemas

Every workspace is a **separate PostgreSQL schema** with its own tables (`Conversation`,
`Message`, `Workflow`, `User`, `PausedSession`). Schema names match `[a-z][a-z0-9_]*`. The active
schema is set via `search_path` on every async session. Switching workspaces runs
`create_schema` + `create_tables_in_schema` + `set_async_schema`, then reloads agents/tools.

> `core/database.py` rewrites `postgresql://` &rarr; `postgresql+asyncpg://` for the async engine.

### Dynamic Loading

- **Global tools**: `tools/*.py`, loaded at startup via `load_global_tools()`.
- **Workspace tools**: `workspaces/<name>/tools/*.py`, loaded/cleared on workspace switch.
- **Agents**: templates in `templates/agents/*.yaml`, copied to `workspaces/<name>/agents/` on
  first switch, registered in the global `agents_registry`.
- **Workflows**: templates in `templates/workflows/*.yaml`, resolved by name (workspace copy
  wins over global) by `orchestration/loader.py`.

---

## Workflows (4 Wired)

| Workflow | Route trigger | Description |
|----------|---------------|-------------|
| **development** | `template.type == "development"` | Decompose &rarr; route &rarr; execute &rarr; supervise &rarr; aggregate. Full orchestration with **Safety Net** fallback (analysis agents never fabricate files). |
| **collaborative** | `template.type == "collaborative"` | Multi-agent panel debate (3 rounds) with moderator consensus. Per-round 120s timeout. |
| **coordinated** | `template.type == "coordinated"` | DAG-based parallel execution with shared **blackboard** (phase namespaces, cross-phase context). `max_parallel: 4`, per-subtask 180s timeout. |
| **TDD** | active workflow == `"tdd"` | Test-driven loop: write tests &rarr; run &rarr; fix &rarr; repeat. 300s per iteration. |

Plus the **direct tool command** fast path (`tool_name: action, key=val`) and the **default**
route that analyzes the task and picks *simple conversation* vs *full orchestration*.

---

## Agents (5 Profiles)

| Agent | Role | Tools | Best for |
|-------|------|-------|----------|
| **developer** | agent | file_manager, git_manager, bash_manager, lsp_manager, code_exec, test_runner, diff_editor | Coding, building, testing |
| **analista** | reasoning | file_manager (read), lsp_manager, code_search, web_search | Analysis, review, architecture |
| **architect** | reasoning | file_manager (read), lsp_manager, code_search, web_search | Architecture design, code review |
| **moderador** | reasoning | none | Debate moderation, consensus |
| **conversacional** | agent | none | Quick chat, fallback agent |

Defaults: `DEFAULT_AGENT=developer`, `FALLBACK_AGENT=conversacional`.

---

## Tools (12 Registered)

Registered via `@tools_registry.register("name")` and described as OpenAI function-calling specs
in `tools/specs.py`. The **registered name** may differ from the filename.

| Registered name | Source file | Actions / purpose |
|-----------------|-------------|-------------------|
| `file_manager` | `file_manager.py` | `write` / `read` / `append` / `delete` |
| `bash_manager` | `bash_manager.py` | Run shell commands (`command`, `cwd`, `timeout`); sanitized |
| `git_manager` | `git_manager.py` | `init` / `add` / `commit` / `log` / `diff` |
| `test_runner` | `test_runner.py` | Run test suites (pytest etc.) |
| `lsp_manager` | `lsp_manager.py` | `definition` / `hover` / `diagnostics` / `references` / `ruff_check` (jedi) |
| `code_exec` | `code_execution.py` | Execute Python in a RestrictedPython sandbox |
| `diff_editor` | `diff_editor.py` | `apply` / `create` unified diffs |
| `web_search` | `web_search.py` | Web search (Google CSE &mdash; needs `GOOGLE_API_KEY`/`GOOGLE_CX`) |
| `web_fetch` | `web_fetch.py` | Fetch + extract page content |
| `code_search` | `code_search.py` | Pattern search across the codebase |
| `pdf_read` | `pdf_reader.py` | Extract text from PDFs (pdfplumber) |
| `ask_clarification` | `ask_clarification.py` | Pause workflow & ask the user a question |

> `ask_clarification` is **not** in `TOOL_DEFINITIONS`; it is intercepted directly in the agent
> loop (`orchestration/loop.py`) rather than invoked via function-calling.

Additional extensibility lives in `tools/kits/` and `tools/skills/`.

---

## Infrastructure Deep-Dive

### `core/` Subsystems

- **Circuit breaker** (`circuit_breaker.py`) &mdash; per-provider closed/open/half-open; opens after
  consecutive failures and falls back to Ollama. Guards both `call` and `call_stream`.
- **Rate limiter** (`rate_limiter.py`) &mdash; sliding-window per-minute and per-hour quotas.
- **Memory** (`core/memory/`) &mdash; FAISS vector search + `MemoryManager`.
  `faiss_indexer.py`, `embedding_provider.py` (in `core/`) &mdash; vector indexing and embeddings.
  autoDream (`self_healing_check()` daemon, run by the `DAEMON_MODE` loop) &mdash;
  `SELF_HEAL_INTERVAL`, default 120s: quality critique, duplicate detection (FAISS sim > 92%),
  contradiction resolution (65&ndash;92%), pruning (unaccessed 30+ days).
- **Change tracker** (`change_tracker.py`) &mdash; undo/redo for file ops via `.undo`/`.redo`.
- **MCP** (`mcp/`) &mdash; Model Context Protocol client (connect external servers) + server
  (`morphix-mcp` exposes the **11** function-calling tools from `TOOL_DEFINITIONS` over stdio
  JSON-RPC; `ask_clarification` is interception-only and not exposed).
- **Sandbox** (`sandbox/`) &mdash; RestrictedPython executor with a `SAFE_MODULES` allowlist.
- **Security** (`security/`) &mdash; undercover mode, anti-distillation (rotating watermarks, pattern
  detection, escalation warn&rarr;throttle&rarr;honeypot&rarr;lock), frustration detector.
- **Health** (`health.py`) &mdash; `run_health_check()` runs 5 probe functions and emits **6 report
  rows**: Database, LLM, Redis, Memory Dir, Templates, Workspace.
- **Token budget & cache** (`token_counter.py`, `cache_manager.py`, `context_manager.py`) &mdash;
  conversation compression at 90% of `MAX_CONTEXT_TOKENS`; DeepSeek prompt-cache monitoring.
- **Hooks** (`hooks_registry.py`, `hook_loader.py`, `hooks/`) &mdash; generic registry; the **6**
  interception points dispatched by `tools/orchestrator.py` around every tool call are
  `on_before_tool`, `on_after_tool`, `on_tool_error`, `on_permission_denied`,
  `on_token_budget_exceeded`, `on_tools_disabled`.
- **Bootstrap / config / paths** (`bootstrap.py`, `config.py`, `path_resolver.py`,
  `feature_flags.py`) &mdash; startup, pydantic-settings, path resolution (**never
  hardcode paths &mdash; use `core.path_resolver.paths`**), feature flags.

### `llm/` Layer

- **Role-based config** &mdash; `settings.model_roles` maps roles (`default`, `fast`, `reasoning`,
  `agent`, `creative`, `critique`) to provider/model/temperature. All default to
  `deepseek-v4-flash`.
- **Providers** &mdash; DeepSeek/OpenAI (OpenAI-compatible client) and Ollama. Falls back to Ollama
  when `OFFLINE_MODE=true` or a connectivity check fails.
- **Strict mode** &mdash; `DEEPSEEK_STRICT_MODE` (default **false**) enables `strict=true` +
  `additionalProperties=false` on non-MCP tool schemas to force `required` compliance.
- **Parser / prompts / offline** &mdash; response parsing, prompt assembly, offline orchestration.

### Hooks Example

```python
from core.hooks_registry import hooks_registry, HookContext

@hooks_registry.register("on_before_tool")
def my_hook(ctx: HookContext) -> None:
    print(f"About to execute: {ctx.tool_name}")
```

### MCP &mdash; Connect External Servers

Edit `workspaces/<name>/mcp_servers.json` (or the global one). Tools register as
`mcp:<prefix>.<name>`:

```json
[
  {
    "name": "playwright",
    "command": "npx",
    "args": ["@playwright/mcp@latest"],
    "tools_prefix": "browser",
    "enabled": true
  }
]
```

Run Morphix itself as an MCP server: `poetry run morphix-mcp` (exposes the 11 function-calling
tools; `ask_clarification` is interception-only and not exposed).

---

## Configuration

Key `.env` variables (see `example.env` and `core/config.py`):

```bash
# Required
DATABASE_URL=postgresql://user:pass@localhost:5432/morphix
DEEPSEEK_API_KEY=sk-xxx              # at least one LLM key required

# Optional LLM / search
OPENAI_API_KEY=                      # GROK_API_KEY, GOOGLE_API_KEY, GOOGLE_CX (web_search)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=phi3:mini
LLM_TIMEOUT=60
DEEPSEEK_STRICT_MODE=false
MAX_CONTEXT_TOKENS=128000

# Security (auto-generated in dev, REQUIRED in production via MORPHIX_ENV=production)
ENCRYPTION_KEY=
PASSWORD_HASH=

# Workspace / agents
ACTIVE_WORKSPACE=main
DEFAULT_AGENT=developer
FALLBACK_AGENT=conversacional
DEFAULT_WORKFLOW=development

# Feature flags
DARK_MODE=true
OFFLINE_MODE=false
UNDERCOVER_MODE=true
DAEMON_MODE=true
SELF_HEAL_INTERVAL=120
CONTEXT_COMPRESSION=true
MAX_SUBTASKS=8
MAX_AGENT_ITERATIONS=8
TOOLS_ENABLED=true
ALLOW_CODE_EXECUTION=true

# Tool settings
TOOL_MAX_TOKENS_PER_WORKFLOW=8000
TOOL_ENABLE_TOKEN_BUDGET=true
TOOL_MAX_RETRIES=3
TOOL_BACKOFF_BASE=1.5

# Database pool
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_PRE_PING=true
DB_POOL_RECYCLE=3600

# Redis (optional)
REDIS_URL=redis://localhost:6379/0
```

> `ENCRYPTION_KEY` auto-generates in dev but **raises `ValueError` in production**
> (`MORPHIX_ENV=production`). CI sets `UNDERCOVER_MODE=false`, `DAEMON_MODE=false`,
> `OFFLINE_MODE=true` to disable features that need auth or external services.

---

## Commands

| Task | Command |
|------|---------|
| Run GUI | `poetry run python run.py` |
| Run MCP server | `poetry run morphix-mcp` |
| All tests (async + coverage) | `poetry run pytest` |
| Single test | `poetry run pytest tests/test_workflow_orchestrator.py::test_direct_tool_route` |
| Lint | `poetry run ruff check .` |
| Format | `poetry run black .` |
| Typecheck | `poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/` |
| Pre-commit (all hooks) | `poetry run pre-commit run --all-files` |
| DB migrations | `poetry run alembic upgrade head` |
| Health check | `poetry run python -c "import asyncio; from core.health import run_health_check; r = asyncio.run(run_health_check()); print(r.format())"` |
| Documentation (local) | `poetry run mkdocs serve` |

**Local check order:** `ruff check .` &rarr; `black --check .` &rarr; `mypy` &rarr; `pytest`.

---

## Documentation

Full documentation is built with **MkDocs Material** and available at the
[Morphix docs site](https://morphilab.github.io/morphix). To browse locally:

```bash
poetry install --with docs
poetry run mkdocs serve
```

The documentation covers:

- **Getting Started** &mdash; installation, configuration, first workflow
- **User Guide** &mdash; GUI overview, cockpit, workflows, agents, tools, workspaces
- **Architecture** &mdash; design decisions, data flow, workspace system, security model, memory system, MCP integration, per-layer deep-dives
- **Developer Guide** &mdash; adding tools, agents, workflows, hooks; contributing; testing guide
- **API Reference** &mdash; auto-generated from docstrings via mkdocstrings
- **Changelog** &mdash; full sprint history

---

## Project Structure

```
codemorphix/
├── core/                 # Business logic (no UI deps)
│   ├── mcp/              # MCP protocol (client + server)
│   ├── memory/           # FAISS + MemoryManager + autoDream
│   ├── security/         # undercover, anti-distillation, frustration detector
│   ├── sandbox/          # RestrictedPython executor
│   ├── hooks/            # global hook implementations
│   └── repositories/     # DB repositories (ConversationRepository, ...)
├── llm/                  # controller, provider, parser, prompts, offline
├── agents/               # registry, loader, profiles, base, service, audit
├── tools/                # 12 tools + specs, registry, orchestrator, wrapper, loader, kits, skills
├── orchestration/        # analyzer, decomposer, router, supervisor, aggregator, finalizer, loop, runner
│   ├── executor/         # subtask, plan, verify, post
│   └── workflows/        # orchestrator, collaborative, coordinated, tdd, blackboard
├── desktop/              # PySide6 GUI
│   ├── services/         # config, dashboard, analytics, history
│   └── widgets/          # reusable widgets
├── templates/            # agent + workflow YAML templates
├── workspaces/           # per-workspace configs + runtime data
├── docs/                 # MkDocs documentation source
├── alembic/              # DB migrations
├── tests/                # 78 test modules (~698 test functions)
└── logs/                 # runtime logs (morphix.log)
```

---

## Development

- See [AGENTS.md](AGENTS.md) for architecture details, conventions, and gotchas.
- See [CAPABILITIES.md](CAPABILITIES.md) for the testing guide and example prompts.
- See [PENDING.md](PENDING.md) and [CHANGELOG.md](CHANGELOG.md) for sprint history.
- See [docs/](docs/) for the full documentation source.

**Test conventions:** `pytest-asyncio` with `asyncio_mode = "auto"`; mark async tests with
`@pytest.mark.asyncio`. No shared fixtures in `conftest.py` &mdash; mocks are defined inline per module.
CI provides PostgreSQL as a service container.

---

## License

MIT &mdash; see [LICENSE](LICENSE) for the full text.
