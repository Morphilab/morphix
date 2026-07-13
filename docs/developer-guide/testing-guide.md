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
        from orchestration.orchestrator import some_function
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

Tests that import `WorkflowOrchestrator` need **extensive internal patching**. The orchestrator touches many subsystems (LLM, tools, database, agents). Use a helper fixture pattern within your test module:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def mock_orchestrator_deps():
    """Create all mocks needed for WorkflowOrchestrator tests."""
    return {
        "TaskAnalyzer": AsyncMock(return_value={
            "primary_type": "development",
            "requires_full_orchestration": True,
        }),
        "decompose_task": AsyncMock(return_value=[
            {"description": "Create app.py", "agent": "developer"},
            {"description": "Write tests", "agent": "developer"},
        ]),
        "agent_router": AsyncMock(return_value="developer"),
        "WorkflowSupervisor": MagicMock(),
        "execute_subtask_safe": AsyncMock(return_value={
            "status": "completed",
            "result": "file created",
            "files_written": ["app.py"],
        }),
        "ResultAggregator": AsyncMock(return_value="Final aggregated result"),
        "finalize_workflow": AsyncMock(),
        "emit_system": AsyncMock(),
        "emit_stats": AsyncMock(),
    }


@pytest.mark.asyncio
async def test_full_orchestration():
    mocks = mock_orchestrator_deps()

    with (
        patch("orchestration.workflows.orchestrator.TaskAnalyzer", mocks["TaskAnalyzer"]),
        patch("orchestration.workflows.orchestrator.decompose_task", mocks["decompose_task"]),
        patch("orchestration.workflows.orchestrator.agent_router", mocks["agent_router"]),
        patch(
            "orchestration.workflows.orchestrator.WorkflowSupervisor",
            mocks["WorkflowSupervisor"],
        ),
        patch(
            "orchestration.workflows.orchestrator.execute_subtask_safe",
            mocks["execute_subtask_safe"],
        ),
        patch(
            "orchestration.workflows.orchestrator.ResultAggregator",
            mocks["ResultAggregator"],
        ),
        patch(
            "orchestration.workflows.orchestrator.finalize_workflow",
            mocks["finalize_workflow"],
        ),
        patch("orchestration.workflows.orchestrator.emit_system", mocks["emit_system"]),
        patch("orchestration.workflows.orchestrator.emit_stats", mocks["emit_stats"]),
    ):
        from orchestration.workflows.orchestrator import WorkflowOrchestrator
        from orchestration.events import Session, WorkflowContext, WorkflowEvents

        ctx = WorkflowContext(
            query="Build a hello world app",
            conversation_history=[],
            workspace="main",
            project_root="test_project",
        )

        mock_events = MagicMock(spec=WorkflowEvents)

        session = Session(context=ctx, events=mock_events)

        result = await WorkflowOrchestrator._run_full_orchestration(
            query=ctx.query,
            conversation_history=ctx.conversation_history,
            task_analysis=mocks["TaskAnalyzer"].return_value,
            ctx=ctx,
            events=mock_events,
            project_root=ctx.project_root,
            workspace=ctx.workspace,
            allowed_agents=["developer"],
            workflow_allowed_tools=["file_manager"],
            start_time=0,
        )

        assert result is not None
        assert "Final aggregated result" in result
```

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

# Run with coverage report
poetry run pytest --cov=core --cov=llm --cov=agents --cov=tools --cov=orchestration --cov-report=term-missing
```

## Coverage

Coverage runs on the five main source directories:

```bash
poetry run pytest \
  --cov=core \
  --cov=llm \
  --cov=agents \
  --cov=tools \
  --cov=orchestration \
  --cov-report=term-missing
```

Target: maintaining or improving coverage across all five directories.

## Test structure

```
tests/
├── conftest.py                     # Per-test engine isolation only
├── test_file_manager.py            # One file per module under test
├── test_hello_world.py             # New tool tests
├── test_workflow_orchestrator.py   # Orchestrator tests (heavy patching)
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

### `test_development_route` flake

Under full-suite load (~676 tests with function-scoped asyncio loops), `test_development_route` can intermittently raise `OSError: [Errno 22]`. This is an **environmental artifact** of pytest-asyncio creating/destroying event loops per test function — not a product bug. The test passes reliably in isolation. The DB engine has loop-hardening (`core/database.py`) that prevents cross-loop asyncpg reuse.

### AsyncMock vs MagicMock

- Use `AsyncMock` for async functions and coroutines.
- Use `MagicMock` for classes, instances, and sync functions.
- `patch()` auto-creates `AsyncMock` for async targets when used as a context manager with async functions.

### Test isolation between runs

The `conftest.py` contains a per-test engine reset fixture:

```python
@pytest.fixture(autouse=True)
async def reset_engine_after_test():
    yield
    from core.database import dispose_engine
    dispose_engine()
```

This ensures each test gets a fresh database engine, preventing cross-test connection pool contamination.
