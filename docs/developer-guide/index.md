# Developer Guide

This track covers **how to extend Morphix** with new tools, agents, workflows, and hooks.

## What You'll Learn

- [Adding Tools](adding-tools.md) — Create a new tool with decorator, spec, and implementation
- [Adding Agents](adding-agents.md) — Create a new agent profile
- [Adding Workflows](adding-workflows.md) — Create a new workflow strategy
- [Adding Hooks](adding-hooks.md) — Intercept tool calls with hooks
- [Contributing](contributing.md) — Setup, conventions, PR process
- [Testing Guide](testing-guide.md) — How to write tests for Morphix

## Prerequisites

Before extending Morphix, ensure you have a working development environment:

```bash
poetry install --with dev
cp example.env .env   # then fill in DATABASE_URL + at least one API key
```

See [Contributing](contributing.md) for full setup instructions.

## Key Concepts

### Layer boundaries

- `core/` — Business logic, no UI dependencies. Database, config, memory, paths.
- `llm/` — LLM abstraction: controller, provider (OpenAI/Ollama), parser, prompts.
- `agents/` — Agent system: registry, loader, profiles, base execution.
- `tools/` — Tool system: specs, registry, orchestrator, wrapper, loader + 11 tool implementations.
- `orchestration/` — Workflow orchestration: context, events, loop, router, workflows.
- `desktop/` — PySide6 GUI + `desktop/services/` for GUI business logic.

### Workspaces = PostgreSQL schemas

Every workspace is a **separate PostgreSQL schema** with its own tables. Switching workspaces calls `create_schema` + `create_tables_in_schema` + `set_async_schema`.

### Code conventions

- **Never hardcode paths.** Use `core.path_resolver.paths` for all filesystem paths.
- `.env` is loaded from project root by `run.py`.
- `sys.path.insert(0, ...)` in `run.py` ensures imports work from project root.
- No shared fixtures in `conftest.py`. Define mocks inline in each test module.
- For tests, use `ToolsRegistry()` instead of the global `tools_registry`.

## After Your Changes

Run the full check suite before submitting:

```bash
poetry run ruff check .
poetry run black --check .
poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/
poetry run pytest
```
