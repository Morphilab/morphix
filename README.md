**English** | [Español](README.es.md)

# Morphix — Multi-Agent Orchestration Platform

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![mypy](https://img.shields.io/badge/mypy-0%20errors-success)](https://github.com/morphilab/morphix)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Morphix** is an MIT-licensed, layered multi-agent orchestration platform for AI-assisted
software engineering. It coordinates multiple AI agents through a deterministic workflow
DSL engine ("the model chooses, the engine constrains"), backed by a clean architecture
with per-workspace PostgreSQL isolation and production-grade infrastructure patterns.

> **Documentation:** Full documentation is available at the [Morphix docs site](https://morphilab.github.io/morphix).
> To browse locally: `uv run mkdocs serve`.

---

## Project State

| Metric | Value |
|--------|-------|
| Python | **3.12.15** pinned via uv (`.python-version`, interpreter managed by uv — decoupled from system Python), `>=3.12,<3.14` |
| Tests | **2,752** test functions across **296** test modules (measured 2026-11-13 — recompute: `git grep -hE '^\s*(async )?def test_' -- 'tests/*.py' \| wc -l` / `ls tests/test_*.py \| wc -l`) |
| Version | **1.2.0** (`pyproject.toml`) |
| Type checking | mypy on `core/ llm/ agents/ tools/ orchestration/ desktop/ viewer/` &mdash; 0 errors |
| Default LLM | DeepInfra `deepseek-ai/DeepSeek-V4.1-Flash-1017` (all roles; needs `DEEPINFRA_API_KEY`), fallback &rarr; Ollama |
| Tools | **24** registered (23 spec'd + `ask_clarification`, interception-only) |
| Agents | **5** profiles |
| Workflows | **10** DSL presets |

---

## Why Morphix?

Most AI coding tools are tightly coupled monoliths. Morphix is designed as a **platform**:
a clean, layered architecture that separates concerns so each subsystem can be understood,
tested, and extended independently.

- **Layered architecture** &mdash; `core/` &rarr; `llm/` &rarr; `agents/` &rarr; `tools/` &rarr; `orchestration/` &rarr; `desktop/`, plus a standalone `research/` harness. Boundaries are enforced by convention; documented upward imports (MCP, bootstrap, workspace loaders, and others) preserve boot order, and there are no known import cycles. No UI leaks into business logic.
- **Per-workspace PostgreSQL schemas** &mdash; every workspace lives in its own schema with isolated tables. No data leakage between projects. The active schema is set via `search_path` on every async session.
- **Deterministic workflow DSL engine** &mdash; workflows are YAML documents (`version: 1`) compiled and validated before execution. Flow control (gates, loops, retries, parallel fan-out, human checkpoints) lives in the engine, never in the LLM; model outputs are validated against declared enums and fallbacks. **10 product presets** ship ready to run.
- **Production patterns** &mdash; circuit breaker with fallback, sliding-window rate limiter, RestrictedPython sandbox running in a confined subprocess, FAISS vector memory with self-healing, token budget, anti-distillation watermarking.
- **2,752 test functions (measured 2026-11-13), 0 mypy errors** &mdash; async tests with `pytest-asyncio`. Full CI pipeline with PostgreSQL service containers.
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

> **Coverage note:** a per-file coverage ratchet (`tests/test_coverage_ratchet.py` +
> `tests/_coverage_baseline.json`) compares `coverage.json` against a baseline. It
> fails if a measured file drops more than **1 percentage point** or the total drops;
> it skips when `coverage.json` is missing or stale. Treat the ratchet, not the
> aggregate number, as the contract.

---

## Screenshots

> Captured from a demo workspace with sample conversations and workflows.

![Morphix dashboard — workflows, agents, projects and bots](docs/assets/screenshots/dashboard.png)

*Dashboard — workflow presets, agent profiles, projects and bots in one place.*

![Morphix Maestro — multi-agent debate with per-agent blocks and live activity](docs/assets/screenshots/maestro-debate.png)

*Maestro — a multi-agent debate with per-agent blocks, a deterministic consensus and live activity/token telemetry.*

---

## Quick Start

```bash
# Prerequisites: PostgreSQL, uv (https://docs.astral.sh/uv/)
uv python install 3.12.15    # one-time: uv-managed interpreter, decoupled from system Python
uv sync --all-groups         # create .venv from uv.lock (dev + docs groups)
cp example.env .env          # edit DATABASE_URL + at least one API key (DEEPINFRA_API_KEY — the default provider; DeepSeek and others also supported)
uv run alembic upgrade head
uv run python run.py     # launch the desktop GUI
```

> Alembic migrations are **manual**. `startup_db()` only creates tables directly; run
> `uv run alembic upgrade head` for migration-based schema changes.

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
   LLM provider                Hooks (6 points) · Permission UI ·
   (DeepInfra/DeepSeek/         Token budget · Anti-distillation
   OpenAI/Grok/NVIDIA/Ollama)
```

### Layer Map

Morphix follows a layered architecture with clean boundaries; documented upward imports (MCP, bootstrap, workspace loaders, and others) preserve boot order:

| Layer | Directory | Role |
|-------|-----------|------|
| **Core** | `core/` | Business logic &mdash; database, config, memory (FAISS), Bot Mode domain, security, MCP, path resolution |
| **LLM** | `llm/` | AI abstraction &mdash; role-based model selection, DeepInfra (default), DeepSeek, OpenAI, Grok, NVIDIA pool, Ollama |
| **Agents** | `agents/` | Agent system &mdash; registry, loader, profiles (5 agents), execution, audit |
| **Tools** | `tools/` | Tool system &mdash; specs, registry, orchestrator with hooks, 23 specs + `ask_clarification` |
| **Orchestration** | `orchestration/` | Workflow orchestration &mdash; **DSL engine** (`dsl/`), agent loop, decomposer, aggregator, finalizer, Bot Mode runtime |
| **Desktop** | `desktop/` | PySide6 GUI &mdash; dashboard, multi-session Maestro, editor, analytics, history, bots, memoria |
| **Research** | `research/` | Standalone evaluation harness &mdash; batch runner + trajectory compressor; not wired into the product |

`viewer/` is a standalone file viewer (zero Morphix imports) used by the GUI and the
`file_view` tool.

### Workspaces Are PostgreSQL Schemas

Every workspace is a **separate PostgreSQL schema** with its own tables (conversations,
messages, workflows, users, paused sessions, bots, routines, group rooms, pending turns,
etc.). Schema names match `[a-z][a-z0-9_]*`. The active schema is set via `search_path` on
every async session. Switching workspaces runs `create_schema`,
`create_tables_in_schema`, and `set_async_schema`, then reloads agents, tools and hooks.

> `core/database.py` rewrites `postgresql://` &rarr; `postgresql+asyncpg://` for the async engine.

### Dynamic Loading

- **Global tools**: `tools/*.py`, loaded at startup via `load_global_tools()`.
- **Workspace tools**: `workspaces/<name>/tools/*.py`, loaded/cleared on workspace switch.
- **Agents**: templates in `templates/agents/*.yaml`, copied to `workspaces/<name>/agents/` on
  first switch, registered in the global `agents_registry`.
- **Workflows**: DSL documents in `templates/workflows/*.yaml`; lookup order is
  workspace copy &rarr; template-workspace copy (`templates/workspaces/<ws>/workflows/`)
  &rarr; global product template, resolved by `orchestration/loader.py`.

---

## Workflows (DSL Engine)

Workflows are **YAML documents with `version: 1`**, compiled (`dsl/compiler.py`), validated
(`dsl/validator.py`) and executed by the deterministic engine (`dsl/engine.py`). The LLM
never controls flow: decisions go through validated enums with declared fallbacks, and
loops/branches are bounded by the engine (`max_iter`, retries, gates).

Engine features **used by the shipped presets**: bounded retries (`retry_max`),
conditional gates (`when: if_blockers`), `loop` over items (sequential and `parallel`),
`until` a validated agent/tool/metric condition, deterministic per-step verification
(`ToolStep.verdict`), per-run test paths (`slug_path`, per-cycle paths), aggregate
verification scoped to what the run wrote (`files_del_run`), and an executable
conformance suite (`dsl/conformance.py`) that runs every preset against the real engine.

The DSL also ships a **user-facing extensibility surface** — `decide` branches, static
`parallel` branches, nested workflow includes, **body fragments** (`kind: fragment` —
shared step bodies expanded at compile time from `_`-prefixed catalog files), human
`checkpoint`s, `commit_after`, `decompose: dag`, and `on_error`. Body fragments are used
by `development`/`coordinated`; `checkpoint` and `query_contains` by the isolated
workspace presets; the remaining mechanisms are available for your own workflows. A
structural test (`test_capacidades_sin_uso_en_presets_son_las_conocidas`) keeps that
inventory honest.

**10 product presets** in `templates/workflows/`, **curated into 5 core** (the dashboard shows
the rest folded under "Experimentales"). The curation is by measured reliability
(measured in an E2E run on 2026-10-08), not opinion; experimental presets stay in the catalog because they
are the engine's live regression coverage (`edd` is the only production user of `until.metric`).

| Core preset | Description |
|--------|-------------|
| **tdd** | Test-driven loop with a preset-declared test path: red &rarr; implement &rarr; `until` green on that file. |
| **development** | Decompose &rarr; sequential subtasks (test-first, per-subtask `verdict`) &rarr; aggregate verification scoped to what the run wrote. |
| **bdd** | List-driven: stories &rarr; per-item mini-TDD on `tests/test_historia_$iter.py`. |
| **collaborative** | Multi-agent panel debate with moderator consensus; the only projectless preset. |
| **sdd** | Spec-first: spec &rarr; blocking-issue gates &rarr; implementation &rarr; traceability. |

| Experimental | Why it's not core |
|--------|-------------|
| **edd** | Eval-metric loop (`until.metric`) over a preset-seeded evals file; weaker/Ollama models may leave the skeleton incomplete, so the preset seeds it deterministically. |
| **coordinated** | `development` + parallel fan-out; its subtasks run concurrently, so it is reliable only when each one owns disjoint files (it closes Alta on independent modules) and degrades when they share a module/`__init__.py` (last write wins, a sibling's gate goes red). Kept experimental until the decompose can guarantee file ownership per subtask. |
| **domain_tdd** | `bdd` + a domain-model step; historically the most token-hungry preset. |
| **reflexion** | Generator &rarr; critic loop; often the most expensive per delivered value, while `collaborative` covers the debate archetype. |
| **long_horizon** | `sdd` with a 1.5M ceiling, 10 items and a 12-iteration loop for long tasks; experimental because it is expensive and its smoke still shows process degradations. |

Inspect and validate them with the CLI:

```bash
uv run python -m orchestration.dsl.cli list
uv run python -m orchestration.dsl.cli validate tdd --conformance   # validates AND executes against the real engine
uv run python -m orchestration.dsl.cli new --type=tdd               # scaffold a new workflow
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

| Agent | Role | Core tools | Best for |
|-------|------|------------|----------|
| **developer** | agent | file_manager, git_manager, bash_manager, lsp_manager, code_exec, test_runner, diff_editor, file_view, goal/todo/plan tools, delegate_task | Coding, building, testing |
| **analista** | analysis | file_manager (read), lsp_manager, code_search, file_view, test_runner, pdf_read, plan_mode/exit_plan_mode | Analysis, review, architecture design |
| **moderador** | reasoning | none | Debate moderation, consensus |
| **conversacional** | agent | none | Quick chat, fallback agent |
| **test_author** | testing | file_manager, file_view | Writing acceptance tests before the implementation |

Defaults: `DEFAULT_AGENT=developer`, `FALLBACK_AGENT=conversacional`. The full per-profile
tool lists live in `templates/agents/*.yaml`.

---

## Tools (24 Registered)

Registered via `@tools_registry.register("name")` and described as OpenAI function-calling specs
in `tools/specs.py` (23 entries in `TOOL_DEFINITIONS`). The **registered name** may differ from
the filename.

| Registered name | Source file | Actions / purpose |
|-----------------|-------------|-------------------|
| `file_manager` | `file_manager.py` | `write` / `read` / `append` / `delete` |
| `bash_manager` | `bash_manager.py` | Run shell commands (`command`, `cwd`, `timeout`); sanitized with a blocklist |
| `git_manager` | `git_manager.py` | `init` / `add` / `commit` / `log` / `diff` / `show` |
| `test_runner` | `test_runner.py` | Run test suites (pytest etc.) |
| `lsp_manager` | `lsp_manager.py` | `definition` / `hover` / `diagnostics` / `references` / `ruff_check` (jedi) |
| `code_exec` | `code_execution.py` | Execute Python in a RestrictedPython sandbox inside a confined subprocess |
| `diff_editor` | `diff_editor.py` | `apply` / `create` unified diffs (workspace-contained) |
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
| `delegate_task` | `delegation.py` | Delegate a subtask to in-process subagents with an isolated context |

> `ask_clarification` (`ask_clarification.py`) is the 24th registered tool but is **not** in
> `TOOL_DEFINITIONS` (23 specs); it is intercepted directly in the agent loop (`orchestration/loop.py`)
> rather than invoked via function-calling, and pauses the workflow with a persisted question.

Additional extensibility lives in `tools/kits/` and `tools/skills/`.

---

## Infrastructure Deep-Dive

### `core/` Subsystems

- **Circuit breaker** (`circuit_breaker.py`) &mdash; per-provider closed/open/half-open; opens after
  consecutive failures and falls back to Ollama. Guards both `call` and `call_stream`.
- **Rate limiter** (`rate_limiter.py`) &mdash; sliding-window per-minute and per-hour quotas.
- **Memory** (`core/memory/manager.py`, `core/faiss_indexer.py`,
  `core/embedding_provider.py`) &mdash; FAISS vector search + `MemoryManager` with an
  in-process LRU embedding cache. Self-healing (`self_healing_check()` daemon, run by the
  `DAEMON_MODE` loop) uses `SELF_HEAL_INTERVAL` (default 120s): quality critique, duplicate
  detection (FAISS sim > 92%), contradiction resolution, and pruning (unaccessed 30+ days).
- **Change tracker** (`change_tracker.py`) &mdash; undo/redo for file ops via `.undo`/`.redo`.
- **MCP** (`mcp/`) &mdash; Model Context Protocol client (connect external servers) + server
  (`uv run python -m core.mcp.server`) exposing the **23** function-calling tools from
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
  compression triggers at 90% of `MAX_CONTEXT_TOKENS` and targets 70%; `cache_manager`
  is prompt-cache telemetry, not a cache.
- **Hooks** (`hooks_registry.py`, `hook_loader.py`, `hooks/`) &mdash; generic registry; the **6**
  interception points dispatched by `tools/orchestrator.py` around every tool call are
  `on_before_tool`, `on_after_tool`, `on_tool_error`, `on_permission_denied`,
  `on_token_budget_exceeded`, `on_tools_disabled`.
- **Bootstrap / config / paths** (`bootstrap.py`, `config.py`, `path_resolver.py`,
  `feature_flags.py`) &mdash; startup, pydantic-settings, path resolution (**never
  hardcode paths &mdash; use `core.path_resolver.paths`**), feature flags.

### `llm/` Layer

- **Role-based config** &mdash; `settings.model_roles` maps roles (`default`, `fast`, `reasoning`,
  `agent`, `creative`, `critique`, plus dedicated `vision` and `deepinfra` roles) to provider/model/temperature.
  All default to DeepInfra `deepseek-ai/DeepSeek-V4.1-Flash-1017` (set `DEEPINFRA_API_KEY`).
- **Providers** &mdash; DeepInfra (product default), DeepSeek/OpenAI (OpenAI-compatible client),
  Grok/NVIDIA pool, and Ollama. Falls back to Ollama when `OFFLINE_MODE=true` or a
  connectivity check fails.
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
    "name": "example",
    "command": "example-mcp-server",
    "args": [],
    "tools_prefix": "example",
    "enabled": true
  }
]
```

Run Morphix itself as an MCP server: `uv run python -m core.mcp.server` (exposes the 23
function-calling tools; `ask_clarification` is interception-only and not exposed).

> **Note:** the `[project.scripts]` entries in `pyproject.toml` (`morphix-mcp`,
> `morphix-workflow`, `morphix-curator`) are **not installed** because the project uses
> `[tool.uv] package = false`. Always invoke via `uv run python -m ...` as shown above.

---

## Configuration

Key `.env` variables (see `example.env` and `core/config.py`):

```bash
# Required
DATABASE_URL=postgresql://user:pass@localhost:5432/morphix
DEEPINFRA_API_KEY=                  # product default provider (at least one LLM key required)

# Optional LLM
DEEPSEEK_API_KEY=                   # alternative providers (also NVIDIA pool / GROK / OPENAI)
OPENAI_API_KEY=                      # GROK_API_KEY also supported
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:120b-cloud
LLM_TIMEOUT=60
DEEPSEEK_STRICT_MODE=false
MAX_CONTEXT_TOKENS=128000

# Security (ENCRYPTION_KEY auto-generates in dev; REQUIRED in production)
ENCRYPTION_KEY=
PASSWORD_HASH=                      # optional GUI login gate

# Workspace / agents
ACTIVE_WORKSPACE=main
DEFAULT_AGENT=developer
FALLBACK_AGENT=conversacional
DEFAULT_WORKFLOW=development

# Feature flags
DARK_MODE=true                       # true = oscuro (papel); false = claro (papel claro)
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
| Run GUI | `uv run python run.py` |
| Run MCP server | `uv run python -m core.mcp.server` |
| Workflow DSL CLI | `uv run python -m orchestration.dsl.cli new\|validate\|list` |
| Validate preset + conformance run | `uv run python -m orchestration.dsl.cli validate tdd --conformance` |
| All tests (async + coverage) | `uv run pytest` |
| Single test | `uv run pytest tests/test_dsl_dispatch.py::TestDirectToolConDSL::test_direct_tool_respeta_ruta` |
| Lint | `uv run ruff check .` |
| Format | `uv run black .` |
| Typecheck | `uv run mypy core/ llm/ agents/ tools/ orchestration/ desktop/ viewer/` |
| Pre-commit (all hooks) | `uv run pre-commit run --all-files` |
| DB migrations | `uv run alembic upgrade head` |
| Health check | `uv run python -c "import asyncio; from core.health import run_health_check; r = asyncio.run(run_health_check()); print(r.format())"` |
| Documentation (local) | `uv run mkdocs serve` |

**Local check order:** `ruff check .` &rarr; `black --check .` &rarr; `mypy` &rarr; `pytest`.

> `run.py` loads the full `.env`. `pytest` does not load the whole file, but
> `tests/conftest.py` reads **only `DATABASE_URL`** from `.env` when it is not already in
> the environment, so the `_pg` tests run without a manual export when `.env` exists. API
> keys are never loaded into the test process. If `.env` is absent, export it explicitly:
> `export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)`.

---

## Documentation

Full documentation is built with **MkDocs Material** and available at the
[Morphix docs site](https://morphilab.github.io/morphix). To browse locally:

```bash
uv sync --group docs
uv run mkdocs serve
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
│   ├── memory/           # MemoryManager + self-healing (FAISS lives in core/faiss_indexer.py)
│   ├── sandbox/          # RestrictedPython in a confined subprocess runner
│   ├── security/         # undercover, anti-distillation, frustration detector
│   ├── hooks/            # global hook implementations
│   └── repositories/     # DB repositories (ConversationRepository, ...)
├── llm/                  # controller, provider, parser, prompts, offline
├── agents/               # registry, loader, profiles, base, service, audit
├── tools/                # 23 specs (+ ask_clarification), registry, orchestrator, wrapper, loader, kits, skills
├── orchestration/        # agent loop, decomposer, aggregator, finalizer, emitter, Bot Mode runtime
│   ├── dsl/              # workflow DSL: schema, compiler, validator, engine, registry/plugins, cli, conformance
│   ├── workflows/        # orchestrator.py (routing: direct-tool / bots / DSL)
│   └── bots_*.py         # bot runtime (wake, clock, groups_drive, runner, dispatch)
├── desktop/              # PySide6 GUI
│   ├── services/         # config, dashboard, history, workflow_runner, project, memoria, ...
│   └── widgets/          # reusable widgets
├── research/             # standalone evaluation harness (batch runner + trajectory compressor)
├── viewer/               # standalone file viewer (zero Morphix imports)
├── templates/            # agents/, workflows/, bots/ YAML + skills/ Markdown
├── workspaces/           # per-workspace configs + runtime data (gitignored)
├── docs/                 # MkDocs documentation source
├── alembic/              # DB migrations
├── tests/                # 296 test modules (2,752 test functions, measured 2026-11-13)
└── logs/                 # runtime logs (morphix.log)
```

---

## Development

- See the [documentation site](https://morphilab.github.io/morphix) for architecture, workflows, agents, tools, and development guides.
- See [docs/](docs/) for the full documentation source.
- See [CONTRIBUTING.md](.github/CONTRIBUTING.md) for developer setup and conventions.

**Test conventions:** `pytest-asyncio` with `asyncio_mode = "auto"`; mark async tests with
`@pytest.mark.asyncio`. `conftest.py` defines shared **autouse** fixtures that reset global
state after every test (DB engine, global singletons, thread census), and loads only
`DATABASE_URL` from `.env` when it is absent from the environment (API keys are never
loaded). Mocks are otherwise defined inline per module. CI provides PostgreSQL as a service
container.

---

## License

MIT &mdash; see [LICENSE](LICENSE) for the full text.
