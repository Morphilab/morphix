# AGENTS.md — Morphix

## Setup & prerequisites

- **Python 3.12** (required; `<3.14`). Poetry for deps.
- **PostgreSQL** required. Copy `.env` from `example.env` and set `DATABASE_URL`.
- Redis is optional (cache). Ollama is optional (offline mode).

```bash
poetry install --with dev
cp example.env .env   # then fill in DATABASE_URL + at least one API key
```

## Common commands

| Task | Command |
|------|---------|
| Run GUI | `poetry run python run.py` |
| Run MCP server | `poetry run morphix-mcp` |
| All tests (async + coverage) | `poetry run pytest` |
| Single test | `poetry run pytest tests/test_workflow_orchestrator.py::test_direct_tool_route` |
| Lint (ruff) | `poetry run ruff check .` |
| Format (black) | `poetry run black .` |
| Typecheck (mypy) | `poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/` |
| Pre-commit (all hooks) | `poetry run pre-commit run --all-files` |
| DB migrations | `poetry run alembic upgrade head` |
| Full local check order | `ruff check .` → `black --check .` → `mypy` → `pytest` |

**Pre-commit** runs black + ruff (with `--fix`), mypy, plus generic checks (trailing whitespace, YAML syntax, large files). It auto-fixes on commit. Mypy is also in CI.

## Architecture (must-know)

### Workspaces = PostgreSQL schemas

Every workspace is a **separate PostgreSQL schema** with its own tables (Conversation, Message, Workflow, User). Schema names follow `[a-z][a-z0-9_]*`. The current schema is set via `search_path` on every async session. Switching workspaces calls `create_schema` + `create_tables_in_schema` + `set_async_schema`.

### Agent & workflow loading

- Templates live in `templates/agents/*.yaml` and `templates/workflows/*.yaml`.
- On first workspace switch, templates are copied to `workspaces/<name>/agents/` and `workspaces/<name>/workflows/`.
- Agents are registered in the global `agents_registry` (an `AgentsRegistry`).
- `unload_workspace_agents()` clears workspace agents from the registry before loading new ones.

### Tools are .py files loaded dynamically

- Global tools: `tools/*.py` — loaded at startup via `load_global_tools()`.
- Workspace tools: `workspaces/<name>/tools/*.py` — loaded/cleared on workspace switch.
- Registration uses `@tools_registry.register("name")` decorator.
- `tools_registry` is the legacy global instance; for tests use `ToolsRegistry()`.

### Layer boundaries

- `core/` — business logic, no UI dependencies. The single source of truth. Database, config, memory, security, MCP, path resolution.
- `llm/` — LLM abstraction layer. controller (models), provider (OpenAI/Ollama), parser, prompts, offline.
- `agents/` — agent system. registry, loader, profiles, base execution, service, audit.
- `tools/` — tool system + implementations. specs, registry, orchestrator, wrapper, loader + 12 tool modules.
- `orchestration/` — workflow orchestration. context, events, loop, router, supervisor, decomposer, analyzer, aggregator, finalizer, executor/ (subtask, plan, verify, post), workflows/ (orchestrator, collaborative, coordinated, tdd, blackboard).
- `desktop/` — PySide6 GUI + `desktop/services/` for GUI business logic (config, dashboard, analytics, history).
- `orchestration/events.py` was the former `features/maestro/events.py` — maintained as a backward-compat re-export from `orchestration/context.py`.

### Workflow orchestrator (5 routes)

`WorkflowOrchestrator.run_full_workflow()` dispatches to one of:
1. **Direct tool** — command format `tool_name: action, key=val` → `_parse_direct_tool_command()`
2. **TDD loop** — active workflow is `"tdd"`
3. **Simple conversation** — `TaskAnalyzer` returns `requires_full_orchestration: false`
4. **Collaborative** — multi-agent debate with moderator
5. **Full orchestration** — decompose → route → execute → supervise → aggregate (development + coordinated workflows)

### LLM provider

Uses role-based config from `settings.model_roles` (default: **`deepseek-v4-flash`** for all roles). Falls back to Ollama if `offline_mode=true` or a connectivity check fails. `DEEPSEEK_STRICT_MODE` (default `false`) enables strict tool schemas on non-MCP tools.

### Key features (sprints 21-23)

- **Clarification requests**: Agent can pause workflow and ask user questions via `ask_clarification` tool. State persisted in `PausedSession` table, survives app restarts. Resume injects answer and continues from pause point.
- **Conversation continuity**: Follow-up messages in existing conversations have full context. `load_conversation()` includes agent/tool messages. Decomposer and TaskAnalyzer receive `is_follow_up` flag — adapts subtasks for modification vs creation.
- **Dev dashboard**: QProgressBar + subtask list with status icons (✅🔵❌⏳) in left panel. Driven by `subtask_list` key in `emit_stats` payloads.

## Test conventions

- **`pytest-asyncio`** with `asyncio_mode = "auto"` — mark async tests with `@pytest.mark.asyncio`.
- No shared fixtures in `conftest.py`. Define mocks inline in each test module.
- Tests are heavily async-mock-based (`unittest.mock.AsyncMock`, `MagicMock`).
- Tests that import `WorkflowOrchestrator` need extensive internal patching (see `mock_orchestrator_deps` fixture in `test_workflow_orchestrator.py`).
- CI provides PostgreSQL as a service container.
- Coverage runs on `core/`, `llm/`, `agents/`, `tools/`, `orchestration/` with `--cov-report=term-missing`.

## Gotchas

- **Do NOT hardcode paths.** Use `core.path_resolver.paths` for all filesystem paths.
- **`.env` is loaded from project root** by `run.py` (which calls `load_dotenv` explicitly). For non-GUI contexts, ensure `.env` is on `PYTHONPATH` or loaded manually.
- **Alembic migrations are manual.** `startup_db()` only creates tables directly; it does not run Alembic. Run `poetry run alembic upgrade head` explicitly for migration-based schema changes.
- **`ENCRYPTION_KEY`** auto-generates in dev but raises `ValueError` in production (`MORPHIX_ENV=production`).
- **`pyproject.toml` has `package-mode = false`** — this is a Poetry project but not a distributable package.
- **`sys.path.insert(0, ...)`** in `run.py` ensures imports work from project root regardless of CWD.
- CI variables `UNDERCOVER_MODE=false`, `DAEMON_MODE=false`, `OFFLINE_MODE=true` disable features that need auth or external services.
- `core/database.py` rewrites `postgresql://` → `postgresql+asyncpg://` for the async engine.
- **`VERBOSE_LOGGING` is read via `os.getenv()` before Settings init** in `run.py:16` — this is intentional to enable debug logging during early bootstrap before pydantic-settings is available.
- **Registered tool names differ from filenames:** `code_execution.py` → `code_exec`, `pdf_reader.py` → `pdf_read`. There are **12** registered tools (the 11 in `tools/specs.py` `TOOL_DEFINITIONS` + `ask_clarification`, which is interception-only and has no function-calling spec).
- **9 upward imports from `core/`** — `core/mcp/` imports from `tools/` (5 instances in `server.py` and `client.py`), `core/workspaces.py` imports from `agents/` and `tools/`, `core/git_operations.py` imports from `tools/`, and `core/hooks/audit.py` imports from `agents/`. The MCP subsystem lives in `core/` for boot ordering but conceptually belongs at the orchestration level. These are 7 lazy imports (function-scoped) and 2 module-level. No import cycles detected. Documented compromise, not a bug.

### Known issues

**Fixed** (templates/agents/architect.yaml + workspaces/main copy added; guard test `tests/test_template_consistency.py`):
- ✅ `coordinated.yaml` `default_phases` `architect` agent — agent profile created + added to `agents.allowed`.
- `refactoring.yaml` removed — orphan workflow with no Python wiring.
- ✅ `development.yaml` phantom `browser` tool — removed from the global template.
- ✅ `orchestrator.py` `_run_simple_conversation` hardcoded `workspace="main"` — now `get_global_workspaces().current`.

**Test suite** — the 3 stale tests + 1 isolation bug found during verification are now **fixed** (test-only; production code was correct):
- ✅ `test_decompose_with_kwargs` — pass `project_context` (prompt placeholder).
- ✅ `test_context_snapshot` — populate `_phases` (sprint-25 storage; `_data` obsolete).
- ✅ `test_coordinated_workflow_e2e` — mock `decompose_task_with_phases` (sprint-25 phase-first flow).
- ✅ `test_get_encoding_loads_and_caches` — reset global `token_counter._enc` at start (isolation).

**Open** — `test_workflow_orchestrator.py::test_development_route` passes in isolation but can raise `OSError: [Errno 22]` under full-suite load. Root cause: pytest-asyncio function-scoped loops create a fresh epoll fd per test; across ~676 loops the churn intermittently corrupts the process epoll/fd state (`EpollSelector.poll()` → EINVAL; pytest-asyncio logs `Error cleaning up asyncio loop: [Errno 22]`). Test-infra scaling artifact, **not a product bug** (the loop-aware engine in `core/database.py` rules out cross-loop asyncpg reuse). **Full suite: 675 pass / 1 flake.**

## Project state

**26 sprints (latest 26b), ~698 test functions across 78 modules, ~270 commits, 0 mypy errors, 0 exclusions.** Full suite: **675 pass / 1 environmental flake** (`test_development_route` under full-suite load; see Known issues).

### Validation (May 27, 2026 — 5 sessions, 10 conversations)

| Session | Conversations | Workflows | Crashes | bash cmd* | Safety Net | Verdict |
|---------|:---:|------------|:---:|:---:|:---:|---|
| Pre-fix baseline | 3 | coordinated, dev×2 | 0 | 4 | 100% (4/4) | 4 fixes in sprint 19 |
| Post-fix #1 (conv_temp) | 1 | coordinated | 0 | 0 | 100% | analyst fix ✅ |
| Post-fix #2 (gen_pass) | 1 | coordinated | 0 | 0 | 100% | decomposer fix ✅ |
| Post-fix #3 (conv 4) | 1 | development | 0 | 0 | N/A | bash fast-fail ✅ |
| Sprint 20 (conv 5-6) | 2 | development | 0 | 0 | 87.5% (7/8) | All fixes ✅ |

> *bash_manager `requires 'command'` — reduced 87% (30→4→0).

### Fixes verified

| Fix | Sprint | Result |
|-----|--------|--------|
| bash_manager wrapper fast-fail | 19 | 0 reintentos, 0 fallos reales |
| Supervisor analyst preservation | 19 | 0 analyst en nivel 1 |
| Safety Net WARNING logs | 19 | 100% visibilidad |
| sqlite3 SAFE_MODULES | 19 | 0 errores de import |
| Aggregator reads disk | 18 | Código completo al prompt |
| Decomposer 3-5 subtareas | 20 | Granularidad mejorada |
| Export strip watermarks | 20 | 0 watermarks en exports |
| Export reads disk | 20 | Archivos reales en export |
| diff_editor path alias | 20 | 0 errores de parámetro |
| Watermark skip flag | 20 | Exports limpios |
| `ast` + `io` SAFE_MODULES | 20 | Desplegados |
| python3 auto-rewrite | 17 | 0 errores `python: not found` |

All production crashes resolved. All 3 workflows have timeouts. Circuit breaker on both `call` and `call_stream`.
Tool skills/kits deployed. Mypy passes on all directories including `desktop/`.
Health check CLI at `core/health.py`. Signal handling + graceful shutdown + config validation.
Full sprint history in `CHANGELOG.md`. Final state summary in `PENDING.md`.

### New features (sprints 21-23)

| Sprint | Feature | Archivos | Líneas | Key files |
|--------|---------|:---:|:---:|-----------|
| 21 | **Clarification requests** | 8 | +530 | `tools/ask_clarification.py`, `orchestration/loop.py`, `orchestration/workflows/orchestrator.py` |
| 22 | **Conversation continuity** | 7 | +186 | `desktop/maestro_tab.py`, `orchestration/decomposer.py`, `orchestration/analyzer.py` |
| 23 | **Dev dashboard** | 2 | +79 | `desktop/maestro_tab.py`, `orchestration/workflows/orchestrator.py` |
