# Testing Guide

This guide covers how to write and run tests for Morphix.

## Test framework

Morphix uses **pytest** with **pytest-asyncio** in `asyncio_mode = "auto"`. All async tests are automatically detected — no need for `@pytest.mark.asyncio` in theory, but we explicitly mark them for clarity and to avoid surprises with fixtures.

```python
import pytest


@pytest.mark.asyncio
async def test_something_async():
    result = await some_async_function()
    assert result == "expected"
```

## Mocking

Use `unittest.mock` for mocking. For async functions, use `AsyncMock`:

```python
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_with_mocks():
    mock_service = AsyncMock(return_value="mocked result")

    with patch("agents.service.AgentsService.execute_agent", mock_service):
        from orchestration.workflows.orchestrator import some_function
        result = await some_function("query")
        assert "mocked result" in result
        mock_service.assert_called_once()
```

### Mocking pattern: inline mocks only

**Do not add fixtures to `conftest.py`.** Define all mocks inline in each test module. This keeps tests self-contained and avoids fixture-ordering issues.

```python
# tests/test_my_feature.py

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_feature_a():
    """Each test defines its own mocks — no conftest.py fixtures."""
    mock_db = AsyncMock()
    mock_db.execute.return_value = MagicMock()

    with patch("core.database.get_async_session", return_value=mock_db):
        # test code here
        pass


@pytest.mark.asyncio
async def test_feature_b():
    """Mocks are scoped to each test function."""
    mock_llm = AsyncMock(return_value="response")
    # ...
```

## Testing tools

Always use a fresh `ToolsRegistry()` instance, **not** the global `tools_registry`:

```python
from tools.registry import ToolsRegistry


@pytest.mark.asyncio
async def test_my_tool():
    reg = ToolsRegistry()

    @reg.register("my_tool")
    async def my_tool(param: str = "default", **kwargs) -> str:
        return f"got: {param}"

    tool = reg.get_tool("my_tool")
    assert tool is not None
    result = await tool(param="hello")
    assert result == "got: hello"
```

This pattern prevents test pollution — each test gets its own isolated registry.

## Testing agents

Use `AgentsRegistry()` directly:

```python
from agents.registry import AgentsRegistry


@pytest.mark.asyncio
async def test_agent_registration():
    reg = AgentsRegistry()

    reg.register_workspace_agent(
        "test_agent",
        AsyncMock(return_value="agent output"),
        {"name": "test_agent", "type": "agent", "tools": []},
    )

    agent = reg.get_agent("test_agent")
    assert agent is not None

    profile = reg.get_profile("test_agent")
    assert profile["type"] == "agent"
```

## Testing WorkflowOrchestrator

Tests that import `WorkflowOrchestrator` need **internal patching** — the orchestrator touches many subsystems (LLM, tools, database, agents). Patch ONLY names that exist on the module today: `load_workflow_document`, `_run_dsl_workflow`, `_resume_dsl`, `get_global_workspaces`, `finalize_workflow`. A patch to a removed name (e.g., the retired analyzer/router/supervisor) dies in `AttributeError` — ruff strips unused imports, so a stale patch target fails at runtime, not at import.

```python
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _session(query: str = "tarea de prueba"):
    ctx = SimpleNamespace(
        query=query,
        active_workflow="demo_dsl",
        conversation_history=[],
        conversation_id=None,
        project_root=".",
        allowed_tools=[],
        skills_enabled=False,
        is_follow_up=False,
        cancelled=False,
        workspace="main",
        last_clarification="",
        policy=None,
        force_agent=None,
    )
    events = SimpleNamespace(
        on_stream_chunk=None,
        on_system_message=AsyncMock(),
        on_assistant_message=AsyncMock(),
        on_stats_update=AsyncMock(),
        on_approval_required=None,
        on_agent_stream=None,
    )
    return SimpleNamespace(context=ctx, events=events, emitter=None)


@pytest.fixture
def fake_ws():
    ws = SimpleNamespace(current="main")
    with patch(
        "orchestration.workflows.orchestrator.get_global_workspaces",
        return_value=ws,
    ):
        yield ws


@pytest.mark.asyncio
async def test_dispatch_dsl_runs_and_finalizes(fake_ws):
    from orchestration.workflows.orchestrator import WorkflowOrchestrator

    doc = {
        "version": 1,
        "name": "mini",
        "agents": {"allowed": ["developer"]},
        "steps": [{"id": "paso1", "kind": "agent", "agent": "developer", "goal": "haz"}],
    }
    session = _session()
    with (
        patch(
            "orchestration.workflows.orchestrator.load_workflow_document",
            return_value=doc,
        ),
        patch(
            "orchestration.loop.execute_agent_loop",
            new_callable=AsyncMock,
            return_value={"status": "completed", "result": "RESULTADO FINAL"},
        ),
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            new_callable=AsyncMock,
        ) as mock_finalize,
    ):
        out = await WorkflowOrchestrator.run_full_workflow(session, persist=True)

    assert out == "RESULTADO FINAL"
    assert mock_finalize.await_count == 1
```

The dispatcher is a router, not an executor: bot canonical chat is dispatched by patching `core.bots_chat.canonical_owner_of`, and the DSL route by returning a `version: 1` document from `load_workflow_document`. See `tests/test_dsl_dispatch.py` for the canonical patterns and `tests/test_dsl_conformance.py` for running whole presets against the real engine.

## Running tests

```bash
# Run all tests
poetry run pytest

# Run a single test file
poetry run pytest tests/test_file_manager.py

# Run a single test function
poetry run pytest tests/test_file_manager.py::test_write_and_read

# Run tests matching a pattern
poetry run pytest -k "file_manager"

# Run with verbose output
poetry run pytest -v

# Run and stop on first failure
poetry run pytest -x

# Run only the last failed tests
poetry run pytest --lf
```

### End-to-end tests against real PostgreSQL

The `_pg` e2e tests are **skipped unless `DATABASE_URL` is exported in the pytest process** — `run.py` loads `.env`, but pytest does NOT. To exercise them:

```bash
export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
poetry run pytest
```

### Full-suite runs on this machine

Once the embeddings model is cached, run full suites offline to avoid a native segfault when Qt(`processEvents`) interleaves with a slow HuggingFace download:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 poetry run pytest
```

## Coverage

Coverage is wired into the default `addopts` (`pyproject.toml`) — every `pytest` run covers the six main source directories and emits `coverage.json` (consumed by the per-file coverage ratchet test):

```
--cov=core --cov=llm --cov=agents --cov=tools --cov=orchestration --cov=desktop --cov-report=term-missing --cov-report=json:coverage.json
```

Target: maintaining or improving coverage across all six directories (a ratchet guard fails the suite on any per-file regression).

## Test structure

```
tests/
├── conftest.py                     # Engine isolation + global singleton reset (autouse)
├── test_file_manager.py            # One file per module under test
├── test_dsl_dispatch.py            # Orchestrator dispatch wiring (load_workflow_document/_run_dsl_workflow)
├── test_dsl_conformance.py         # Every product preset executed against the real engine
├── test_dsl_engine.py              # DSL engine primitives (loops, gates, pauses)
├── test_agent_loop.py              # Agent loop tests
├── test_template_consistency.py    # Guard tests for template validity
└── ...
```

Each test file maps roughly to one source module. The filename convention is `test_<module_name>.py`.

## Template consistency tests

Always add a template consistency test when creating new agents, tools, or workflows:

```python
def test_all_workflow_tools_exist():
    """Every tool referenced by a workflow template must be registered."""
    from tools.specs import TOOL_DEFINITIONS
    from core.path_resolver import paths
    import yaml

    workflows_dir = paths.templates_dir() / "workflows"
    for wf_file in workflows_dir.glob("*.yaml"):
        workflow = yaml.safe_load(wf_file.read_text())
        allowed_tools = workflow.get("tools", {}).get("allowed", [])
        for tool_name in allowed_tools:
            assert tool_name in TOOL_DEFINITIONS, (
                f"Tool '{tool_name}' in {wf_file.name} not found in TOOL_DEFINITIONS"
            )
```

## Known test considerations

### Ambient epoll flakes under full-suite load

Under full-suite load (~1900+ test functions with function-scoped asyncio loops), a few tests can intermittently raise `OSError: [Errno 22]` / `Error cleaning up asyncio loop`. This is an **environmental artifact** of pytest-asyncio creating/destroying event loops per test function — not a product bug. Affected tests pass reliably in isolation and as a whole file. The DB engine has loop-hardening (`core/database.py`) that prevents cross-loop asyncpg reuse. If a suite run hangs, check for orphaned pytest processes before retrying (`pgrep -fa "pytes[t]"`) — killing runs with `timeout -s KILL`/`pkill -9` leaves asyncpg connections `idle in transaction` that block later `DROP SCHEMA` operations.

### AsyncMock vs MagicMock

- Use `AsyncMock` for async functions and coroutines.
- Use `MagicMock` for classes, instances, and sync functions.
- `patch()` auto-creates `AsyncMock` for async targets when used as a context manager with async functions.

### Test isolation between runs

The `conftest.py` contains autouse fixtures for isolation:

```python
@pytest.fixture(autouse=True)
async def isolated_db_engine():
    yield
    # Deterministic per-socket teardown (_hard_close_engine) — the pool is
    # never left to the GC, which would close sockets with a dead loop.

@pytest.fixture(autouse=True)
def _reset_global_singletons():
    # Guarantees a clean baseline for registries/global state between tests
```

This ensures each test gets a fresh database engine with its sockets closed deterministically, preventing cross-test connection pool contamination and asyncpg handshakes against dead event loops.
