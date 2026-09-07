# Morphix — Multi-Agent Orchestration Platform

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![mypy](https://img.shields.io/badge/mypy-0%20errors-success)](https://github.com/morphilab/morphix)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Morphix** is an open-source, layered multi-agent orchestration platform for AI-assisted
software engineering. It coordinates multiple AI agents through a deterministic workflow
DSL engine ("the model chooses, the engine constrains"), backed by a clean architecture
with per-workspace PostgreSQL isolation and production-grade infrastructure patterns.

> **Documentation:** Full documentation is available at the [Morphix docs site](https://morphilab.github.io/morphix).
> To browse locally: `poetry run mkdocs serve`.

---

## Project State

| Metric | Value |
|--------|-------|
| Python | **3.12** (`>=3.12,<3.14`), Poetry, `package-mode = false` |
| Tests | **1,937** test functions across **219** test modules (recompute: `git grep -hE "def test_" -- 'tests/*.py' \| wc -l` / `ls tests/test_*.py \| wc -l`) |
| Commits | ~900 (`git rev-list --count HEAD`) |
| Type checking | mypy on `core/ llm/ agents/ tools/ orchestration/ desktop/ viewer/` &mdash; 0 errors |
| Default LLM | DeepSeek `deepseek-v4-flash` (all roles), fallback &rarr; Ollama (`phi3:mini`) |
| Tools | **25** registered (24 spec'd + `ask_clarification`, interception-only) |
| Agents | **5** profiles |
| Workflows | **9** DSL presets |

---

## Why Morphix?

Most AI coding tools are tightly coupled monoliths. Morphix is designed as a **platform**:
a clean, layered architecture that separates concerns so each subsystem can be understood,
tested, and extended independently.

- **Clean layered architecture** &mdash; `core/` &rarr; `llm/` &rarr; `agents/` &rarr; `tools/` &rarr; `orchestration/` &rarr; `desktop/`. Each layer depends only on the layer below it. No circular imports. No UI leaking into business logic.
- **Per-workspace PostgreSQL schemas** &mdash; every workspace lives in its own schema with isolated tables. No data leakage between projects. The active schema is set via `search_path` on every async session.
- **Deterministic workflow DSL engine** &mdash; workflows are YAML documents (`version: 1`) compiled and validated before execution. Flow control (gates, loops, retries, parallel fan-out, human checkpoints) lives in the engine, never in the LLM; model outputs are validated against declared enums and fallbacks. **9 product presets** ship ready to run.
- **Production patterns** &mdash; circuit breaker with fallback, sliding-window rate limiter, RestrictedPython sandbox running in a confined subprocess, FAISS vector memory with self-healing, token budget, anti-distillation watermarking.
- **~1,900 test functions, 0 mypy errors** &mdash; async tests with `pytest-asyncio`. Full CI pipeline with PostgreSQL service containers.
- **Dynamic extensibility** &mdash; tools as `.py` files loaded at runtime, agents as YAML profiles, hooks with 6 interception points, MCP integration for external tool servers.

> ⚠️ **AI Disclosure / Divulgación de IA**
>
> **English:** This project was developed with assistance from artificial intelligence
> tools. Given the automated nature of some components, users are advised to review
> and test the code independently before integrating it into their own systems.
>
> **Español:** Este proyecto fue desarrollado con asistencia de herramientas de
> inteligencia artificial. Dada la naturaleza automatizada de algunos componentes,
> se recomienda que los usuarios revisen y prueben el código independientemente antes
> de integrarlo en sus propios sistemas.

> **Coverage note:** Test coverage is approximately **~79%** (do not treat the exact
> figure as a contract &mdash; it moves with every sprint). Regressions are guarded by a
> per-file coverage ratchet (`tests/test_coverage_ratchet.py` + `tests/_coverage_baseline.json`),
> which runs on every test execution and fails if any file's coverage drops.

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
WorkflowOrchestrator.run_full_workflow()   ── routing ──
   1. Direct tool command   "tool_name: action, key=val"
                            (allowlist read from the raw workflow doc — DSL-aware)
   2. Bot canonical chat    → bots_runner (Bot Mode identity, no workflow templates)
   3. Workflow DSL          doc with `version: 1` → compiler → validator → engine
                            (legacy YAML without `version: 1` gets an actionable
                             error pointing at the DSL CLI)
        │
        ▼
   Agent Loop (ReAct)  ←→  ToolOrchestrator ──→ Tools (file/git/bash/code/search/MCP)
        │                          │
   LLM provider                Hooks (pre/post/error) · Permission UI ·
   (DeepSeek/OpenAI/Ollama)    Token budget · Anti-distillation
```

### Layer Map

Morphix follows a strict layered architecture with clean boundaries:

| Layer | Directory | Role |
|-------|-----------|------|
| **Core** | `core/` | Business logic &mdash; database, config, memory (FAISS), Bot Mode domain, security, MCP, path resolution |
| **LLM** | `llm/` | AI abstraction &mdash; role-based model selection, DeepSeek/OpenAI/Ollama providers |
| **Agents** | `agents/` | Agent system &mdash; registry, loader, profiles (5 agents), execution, audit |
| **Tools** | `tools/` | Tool system &mdash; specs, registry, orchestrator with hooks, 25 tools |
| **Orchestration** | `orchestration/` | Workflow orchestration &mdash; **DSL engine** (`dsl/`), agent loop, decomposer, aggregator, finalizer, Bot Mode runtime |
| **Desktop** | `desktop/` | PySide6 GUI &mdash; dashboard, multi-session Maestro, editor, analytics, history, bots, memoria |

`viewer/` is a standalone file viewer (zero Morphix imports) used by the GUI and the
`file_view` tool.

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
- **Workflows**: DSL documents in `templates/workflows/*.yaml` (workspace copy wins over
  product templates, which win over globals), resolved by `orchestration/loader.py`.

---

## Workflows (DSL Engine)

Workflows are **YAML documents with `version: 1`**, compiled (`dsl/compiler.py`), validated
(`dsl/validator.py`) and executed by the deterministic engine (`dsl/engine.py`). The LLM
never controls flow: decisions go through validated enums with declared fallbacks, and
loops/branches are bounded by the engine (`max_iter`, retries, gates).

Engine features: retry with backoff, conditional gates (`when`), `loop` over items or
`until` a validated agent/metric condition, parallel stages by levels, nested includes,
human checkpoints &rarr; **persisted pauses with resume**, `commit_after` git commits, and
an executable conformance suite (`dsl/conformance.py`) that runs every preset against the
real engine.

**9 product presets** in `templates/workflows/`:

| Preset | Description |
|--------|-------------|
| **development** | Decompose &rarr; parallel execution &rarr; verify &rarr; aggregate. Full orchestration with per-subtask verification. |
| **tdd** | Test-driven loop: write tests &rarr; run (`test_runner`) &rarr; fix &rarr; repeat until the suite passes. |
| **sdd** / **bdd** / **domain_tdd** / **edd** | Spec-driven, behavior-driven, domain-driven and example-driven development variants. |
| **reflexion** | Generator &rarr; critic loop; the critic consumes the generator's output (`$last_output`). |
| **collaborative** | Multi-agent panel debate with moderator consensus. |
| **coordinated** | Dynamic parallel fan-out over decomposed subtasks with unified aggregation. |

Inspect and validate them with the CLI:

```bash
poetry run python -m orchestration.dsl.cli list
poetry run python -m orchestration.dsl.cli validate tdd --conformance   # validates AND executes against the real engine
poetry run python -m orchestration.dsl.cli new --type=tdd               # scaffold a new workflow
```

Plus the **direct tool command** fast path (`tool_name: action, key=val`) for single-tool
runs, and **Bot Mode canonical chat**, where conversations owned by a bot are served by
`bots_runner` with the bot's identity &mdash; no workflow involved.

---

## Feature Highlights

- **Multi-session Maestro** &mdash; N concurrent chat/workflow sessions (default 4, `MAESTRO_MAX_SESSIONS`) with per-run isolation: approval callbacks, token budgets and file-write collision tracking are ContextVar-scoped per run.
- **Bot Mode** &mdash; persistent AI bots with YAML-defined identity (`templates/bots/`): 1:1 async DMs between bots, shared group rooms with turn caps, and time-based routines (schedule into a dedicated conversation or deliver a real bot turn).
- **Persisted pauses & clarifications** &mdash; agents can stop mid-workflow and ask the user a question (`ask_clarification`); state survives restarts and resumes at the exact step (DSL and bot pauses).
- **Project Knowledge Base (PKB)** &mdash; curated markdown knowledge per workspace (`workspaces/<ws>/knowledge/`), queryable via the `project_docs` tool (`list/read/search/inject`).
- **Procedural skills** &mdash; on-demand skill loading (`load_skill`) with a local-skill approval gate (`core/skills_approval.py`): unapproved workspace skills are hidden from agents until approved.
- **Standalone viewer** &mdash; `viewer/viewer.py` renders markdown/HTML (sanitized)/PDF/text safely; the `file_view` tool opens files there without content ever returning to the agent.

---

## Agents (5 Profiles)

| Agent | Role | Tools | Best for |
|-------|------|-------|----------|
| **developer** | agent | file_manager, git_manager, bash_manager, lsp_manager, code_exec, test_runner, diff_editor | Coding, building, testing |
| **analista** | reasoning | file_manager (read), lsp_manager, code_search, web_search | Analysis, review |
| **architect** | reasoning | file_manager (read), lsp_manager, code_search, web_search | Architecture design, code review |
| **moderador** | reasoning | none | Debate moderation, consensus |
| **conversacional** | agent | none | Quick chat, fallback agent |

Defaults: `DEFAULT_AGENT=developer`, `FALLBACK_AGENT=conversacional`.

---

## Tools (25 Registered)

Registered via `@tools_registry.register("name")` and described as OpenAI function-calling specs
in `tools/specs.py` (24 entries in `TOOL_DEFINITIONS`). The **registered name** may differ from
the filename.

| Registered name | Source file | Actions / purpose |
|-----------------|-------------|-------------------|
| `file_manager` | `file_manager.py` | `write` / `read` / `append` / `delete` |
| `bash_manager` | `bash_manager.py` | Run shell commands (`command`, `cwd`, `timeout`); sanitized with a blocklist |
| `git_manager` | `git_manager.py` | `init` / `add` / `commit` / `log` / `diff` |
| `test_runner` | `test_runner.py` | Run test suites (pytest etc.) |
| `lsp_manager` | `lsp_manager.py` | `definition` / `hover` / `diagnostics` / `references` / `ruff_check` (jedi) |
| `code_exec` | `code_execution.py` | Execute Python in a RestrictedPython sandbox inside a confined subprocess |
| `diff_editor` | `diff_editor.py` | `apply` / `create` unified diffs (workspace-contained) |
| `web_search` | `web_search.py` | Web search (Google CSE &mdash; needs `GOOGLE_API_KEY`/`GOOGLE_CX`) |
| `web_fetch` | `web_fetch.py` | Fetch + extract page content |
| `code_search` | `code_search.py` | Pattern search across the codebase |
| `pdf_read` | `pdf_reader.py` | Extract text from PDFs (pdfplumber) |
| `memory_inspector` | `memory_inspector.py` | Inspect and manage persistent memory |
| `file_view` | `file_viewer.py` | Open a file in the standalone viewer (content never returns to the agent) |
| `project_docs` | `project_docs.py` | Project Knowledge Base: `list` / `read` / `search` / `inject` |
| `vision_analyze` | `vision_analyze.py` | Image analysis via the dedicated `vision` model role |
| `load_skill` | `skill_loader.py` | Load a procedural skill on demand (workspace skills require approval) |
| `goal_create` / `goal_get` / `goal_update` / `goal_round` | `goal_todo.py` | Goal tracking and iteration rounds |
| `todo_write` / `todo_get` | `goal_todo.py` | Todo list management |
| `plan_mode` / `exit_plan_mode` | `goal_todo.py` | Explicit planning mode before implementation |

> `ask_clarification` (`ask_clarification.py`) is the 25th registered tool but is **not** in
> `TOOL_DEFINITIONS`; it is intercepted directly in the agent loop (`orchestration/loop.py`)
> rather than invoked via function-calling, and pauses the workflow with a persisted question.

Additional extensibility lives in `tools/kits/` and `tools/skills/`.

---

## Infrastructure Deep-Dive

### `core/` Subsystems

- **Circuit breaker** (`circuit_breaker.py`) &mdash; per-provider closed/open/half-open; opens after
  consecutive failures and falls back to Ollama. Guards both `call` and `call_stream`.
- **Rate limiter** (`rate_limiter.py`) &mdash; sliding-window per-minute and per-hour quotas.
- **Memory** (`core/memory/`) &mdash; FAISS vector search + `MemoryManager`
  (`faiss_indexer.py`, `embedding_provider.py` in `core/`) with an in-process LRU embedding
  cache. Self-healing (`self_healing_check()` daemon, run by the `DAEMON_MODE` loop) &mdash;
  `SELF_HEAL_INTERVAL`, default 120s: quality critique, duplicate detection (FAISS sim > 92%),
  contradiction resolution, pruning (unaccessed 30+ days).
- **Change tracker** (`change_tracker.py`) &mdash; undo/redo for file ops via `.undo`/`.redo`.
- **MCP** (`mcp/`) &mdash; Model Context Protocol client (connect external servers) + server
  (`poetry run python -m core.mcp.server`) exposing the **24** function-calling tools from
  `TOOL_DEFINITIONS` over stdio JSON-RPC; `ask_clarification` is interception-only and not exposed.
- **Sandbox** (`sandbox/`) &mdash; RestrictedPython executor running in a **confined child
  process** (`sandbox/runner.py`): the child never imports `core.*` (no settings or secrets in
  its memory), memory is capped child-scoped (`RLIMIT_AS`), timeouts kill the child
  (`SIGKILL`), and concurrency is bounded (`MAX_CONCURRENT_CHILDREN`).
- **Security** (`security/`) &mdash; undercover mode, anti-distillation (rotating watermarks, pattern
  detection, escalation warn&rarr;throttle&rarr;honeypot&rarr;lock), frustration detector.
- **Health** (`health.py`) &mdash; `run_health_check()` emits **6 report rows**: Database, LLM,
  Memory Dir, Templates, Workspace, Embeddings.
- **Token budget & cache** (`token_counter.py`, `cache_manager.py`, `context_manager.py`) &mdash;
  conversation compression at 90% of `MAX_CONTEXT_TOKENS`; `cache_manager` is DeepSeek
  prompt-cache telemetry, not a cache.
- **Hooks** (`hooks_registry.py`, `hook_loader.py`, `hooks/`) &mdash; generic registry; the **6**
  interception points dispatched by `tools/orchestrator.py` around every tool call are
  `on_before_tool`, `on_after_tool`, `on_tool_error`, `on_permission_denied`,
  `on_token_budget_exceeded`, `on_tools_disabled`.
- **Bootstrap / config / paths** (`bootstrap.py`, `config.py`, `path_resolver.py`,
  `feature_flags.py`) &mdash; startup, pydantic-settings, path resolution (**never
  hardcode paths &mdash; use `core.path_resolver.paths`**), feature flags.

### `llm/` Layer

- **Role-based config** &mdash; `settings.model_roles` maps roles (`default`, `fast`, `reasoning`,
  `agent`, `creative`, `critique`, plus a dedicated `vision` role) to provider/model/temperature.
  All default to `deepseek-v4-flash`.
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

Run Morphix itself as an MCP server: `poetry run python -m core.mcp.server` (exposes the 24
function-calling tools; `ask_clarification` is interception-only and not exposed).

> **Note:** the `[project.scripts]` entries in `pyproject.toml` (`morphix-mcp`,
> `morphix-workflow`) are **not installed** because the project uses `package-mode = false`.
> Always invoke via `poetry run python -m ...` as shown above.

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
DAEMON_MODE=false                    # heartbeat/self-heal daemon (default false; true in example.env)
SELF_HEAL_INTERVAL=120
CONTEXT_COMPRESSION=true
MAX_SUBTASKS=8
MAX_AGENT_ITERATIONS=8
TOOLS_ENABLED=true
ALLOW_CODE_EXECUTION=true

# Tool settings
TOOL_MAX_TOKENS_PER_WORKFLOW=80000
TOOL_ENABLE_TOKEN_BUDGET=true
TOOL_MAX_RETRIES=3
TOOL_BACKOFF_BASE=1.5

# Database pool
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10
DB_POOL_PRE_PING=true
DB_POOL_RECYCLE=3600
```

> `ENCRYPTION_KEY` auto-generates in dev but **raises `ValueError` in production**
> (`MORPHIX_ENV=production`). CI sets `UNDERCOVER_MODE=false`, `DAEMON_MODE=false`,
> `OFFLINE_MODE=true` to disable features that need auth or external services.

---

## Commands

| Task | Command |
|------|---------|
| Run GUI | `poetry run python run.py` |
| Run MCP server | `poetry run python -m core.mcp.server` |
| Workflow DSL CLI | `poetry run python -m orchestration.dsl.cli new\|validate\|list` |
| Validate preset + conformance run | `poetry run python -m orchestration.dsl.cli validate tdd --conformance` |
| All tests (async + coverage) | `poetry run pytest` |
| Single test | `poetry run pytest tests/test_workflow_orchestrator.py::test_direct_tool_route` |
| Lint | `poetry run ruff check .` |
| Format | `poetry run black .` |
| Typecheck | `poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/ viewer/` |
| Pre-commit (all hooks) | `poetry run pre-commit run --all-files` |
| DB migrations | `poetry run alembic upgrade head` |
| Health check | `poetry run python -c "import asyncio; from core.health import run_health_check; r = asyncio.run(run_health_check()); print(r.format())"` |
| Documentation (local) | `poetry run mkdocs serve` |

**Local check order:** `ruff check .` &rarr; `black --check .` &rarr; `mypy` &rarr; `pytest`.

> `run.py` loads `.env` from the project root; plain `pytest` does not. The PostgreSQL e2e
> tests (suffixed `_pg`) are **skipped unless `DATABASE_URL` is exported** in the pytest
> process &mdash; to exercise them: `export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)`.

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
- **User Guide** &mdash; GUI overview, workflows, agents, tools, workspaces
- **Bot Mode** &mdash; bots, DMs, group rooms, routines (`docs/bot-mode.md`)
- **Architecture** &mdash; design decisions, data flow, workspace system, security model, memory system, MCP integration, per-layer deep-dives
- **Developer Guide** &mdash; adding tools, agents, workflows, hooks; contributing; testing guide
- **API Reference** &mdash; auto-generated from docstrings via mkdocstrings
- **Changelog** &mdash; release notes and version history

---

## Project Structure

```
morphix/
├── core/                 # Business logic (no UI deps)
│   ├── bots*.py          # Bot Mode domain (identity, DMs, groups, routines, protocol)
│   ├── mcp/              # MCP protocol (client + server)
│   ├── memory/           # FAISS + MemoryManager + self-healing
│   ├── sandbox/          # RestrictedPython in a confined subprocess runner
│   ├── security/         # undercover, anti-distillation, frustration detector
│   ├── hooks/            # global hook implementations
│   └── repositories/     # DB repositories (ConversationRepository, ...)
├── llm/                  # controller, provider, parser, prompts, offline
├── agents/               # registry, loader, profiles, base, service, audit
├── tools/                # 25 tools + specs, registry, orchestrator, wrapper, loader, kits, skills
├── orchestration/        # agent loop, decomposer, aggregator, finalizer, emitter, Bot Mode runtime
│   ├── dsl/              # workflow DSL: schema, compiler, validator, engine, registry/plugins, cli, conformance
│   ├── workflows/        # orchestrator.py (routing: direct-tool / bots / DSL)
│   └── bots_*.py         # bot runtime (wake, clock, groups_drive, runner, dispatch)
├── desktop/              # PySide6 GUI
│   ├── services/         # config, dashboard, history, workflow_runner, project, memoria, ...
│   └── widgets/          # reusable widgets
├── viewer/               # standalone file viewer (zero Morphix imports)
├── templates/            # agents/, workflows/, bots/, skills/ YAML templates
├── workspaces/           # per-workspace configs + runtime data (gitignored)
├── docs/                 # MkDocs documentation source
├── alembic/              # DB migrations
├── tests/                # 219 test modules (~1,937 test functions)
└── logs/                 # runtime logs (morphix.log)
```

---

## Development

- See the [documentation site](https://morphilab.github.io/morphix) for architecture, workflows, agents, tools, and development guides.
- See [docs/](docs/) for the full documentation source.
- See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for developer setup and conventions.

**Test conventions:** `pytest-asyncio` with `asyncio_mode = "auto"`; mark async tests with
`@pytest.mark.asyncio`. No shared fixtures in `conftest.py` &mdash; mocks are defined inline per module.
CI provides PostgreSQL as a service container.

---

## License

MIT &mdash; see [LICENSE](LICENSE) for the full text.
